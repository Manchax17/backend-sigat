"""Acceso a datos de sincronización."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado


class RepositorioSincronizacion:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    @asynccontextmanager
    async def punto_de_guarda(self) -> AsyncIterator[None]:
        """SAVEPOINT por operación: una que falla no tumba a las demás del lote (ADR-04)."""
        async with self._con.begin_nested():
            yield

    async def dispositivo(self, disp_id: UUID) -> dict[str, Any] | None:
        fila = (
            (
                await self._con.execute(
                    text(
                        "SELECT disp_id, disp_nombre, disp_plataforma, disp_version_app, disp_activo"
                        " FROM sigat.dispositivo WHERE disp_id = :d"
                    ),
                    {"d": disp_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila else None

    async def upsert_dispositivo(
        self, disp_id: UUID | None, plataforma: str, version_app: str | None, nombre: str | None
    ) -> UUID:
        """Crea el dispositivo o actualiza versión y nombre del existente (activo)."""
        if disp_id is not None:
            await self._con.execute(
                text(
                    "UPDATE sigat.dispositivo SET disp_version_app = coalesce(:v, disp_version_app),"
                    " disp_nombre = coalesce(:n, disp_nombre) WHERE disp_id = :d"
                ),
                {"v": version_app, "n": nombre, "d": disp_id},
            )
            return disp_id
        nuevo = await self._con.scalar(
            text(
                "INSERT INTO sigat.dispositivo (disp_plataforma, disp_version_app, disp_nombre)"
                " VALUES (:p, :v, :n) RETURNING disp_id"
            ),
            {"p": plataforma, "v": version_app, "n": nombre},
        )
        return UUID(str(nuevo))

    async def operacion_existente(self, disp_id: UUID, cliente_id: UUID) -> dict[str, Any] | None:
        """Busca por la clave única (disp_id, oper_cliente_id)."""
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT oper_id, oper_entidad, oper_accion, oper_registro_id, oper_version,
       oper_hash_contenido, oper_clasificacion, oper_motivo
  FROM sigat.operacion_sync
 WHERE disp_id = :disp_id AND oper_cliente_id = :cliente_id
"""
                    ),
                    {"disp_id": disp_id, "cliente_id": cliente_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila else None

    async def registrar_reenvio(self, oper_id: int, hash_hex: str, resultado: str) -> None:
        """INSERT recepcion_sync (idempotente_repetida | conflicto_contenido)."""
        await self._con.execute(
            text(
                """
INSERT INTO sigat.recepcion_sync (oper_id, rece_hash, rece_resultado)
VALUES (:oper_id, :hash, :resultado)
"""
            ),
            {"oper_id": oper_id, "hash": hash_hex, "resultado": resultado},
        )

    async def insertar_operacion(self, fila: dict[str, Any]) -> int:
        """INSERT operacion_sync. ``oper_payload`` SOLO si la clasificación es revisar/rechazada."""
        payload = fila.get("payload")
        oper_id = await self._con.scalar(
            text(
                """
INSERT INTO sigat.operacion_sync (
    disp_id, oper_cliente_id, usua_id, oper_entidad, oper_accion, oper_registro_id,
    oper_version, oper_hash_contenido, oper_clasificacion, oper_motivo, oper_t_captura,
    oper_deriva_reloj_seg, oper_payload
) VALUES (
    :disp_id, :cliente_id, :usua_id, :entidad, :accion, :registro_id,
    :version, :hash, :clasificacion, :motivo, :t_captura,
    :deriva, CAST(:payload AS jsonb)
)
RETURNING oper_id
"""
            ),
            {
                "disp_id": fila["disp_id"],
                "cliente_id": fila["cliente_id"],
                "usua_id": fila["usua_id"],
                "entidad": str(fila["entidad"]),
                "accion": str(fila["accion"]),
                "registro_id": fila["registro_id"],
                "version": fila["version"],
                "hash": fila["hash"],
                "clasificacion": str(fila["clasificacion"]),
                "motivo": fila.get("motivo"),
                "t_captura": fila["t_captura"],
                "deriva": fila.get("deriva"),
                "payload": json.dumps(payload) if payload is not None else None,
            },
        )
        return int(oper_id)

    async def fijar_clasificacion(
        self, oper_id: int, clasificacion: str, motivo: str | None, payload: dict[str, Any] | None
    ) -> None:
        """Se inserta la operación con la clasificación ya decidida (tabla append-only):
        el orden real es decidir -> insertar operación -> insertar dominio en la misma transacción."""
        raise NoImplementado("Clasificación")

    async def pendientes_de_usuario(self, usua_id: int, clasificaciones: list[str]) -> list[dict[str, Any]]:
        """Operaciones revisar/rechazada sin otra que las resuelva (oper_resuelve_oper_id)."""
        if not clasificaciones:
            return []
        consulta = text(
            """
SELECT o.oper_id,
       o.oper_cliente_id AS cliente_id,
       o.oper_entidad AS entidad,
       o.oper_accion AS accion,
       o.oper_registro_id AS registro_id,
       o.oper_version AS version,
       o.oper_clasificacion AS clasificacion,
       o.oper_motivo AS motivo,
       o.oper_t_captura AS t_captura,
       o.oper_t_recepcion AS t_recepcion,
       coalesce(o.oper_payload, '{}'::jsonb) AS datos
  FROM sigat.operacion_sync o
 WHERE o.usua_id = :usua_id
   AND o.oper_clasificacion::text IN :clasificaciones
   AND NOT EXISTS (
       SELECT 1 FROM sigat.operacion_sync r WHERE r.oper_resuelve_oper_id = o.oper_id
   )
 ORDER BY o.oper_t_recepcion DESC, o.oper_id DESC
"""
        ).bindparams(bindparam("clasificaciones", expanding=True))
        filas = await self._con.execute(consulta, {"usua_id": usua_id, "clasificaciones": clasificaciones})
        resultado = []
        for fila in filas.mappings():
            datos = dict(fila)
            if isinstance(datos.get("datos"), str):
                datos["datos"] = json.loads(datos["datos"])
            resultado.append(datos)
        return resultado

    async def cambios_desde(
        self, usua_id: int, disp_id: UUID, desde: datetime | None, limite: int
    ) -> list[dict[str, Any]]:
        raise NoImplementado("Cambios desde cursor")
