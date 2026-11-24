import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


class RepositorioSincronizacion:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    @asynccontextmanager
    async def punto_de_guarda(self) -> AsyncIterator[None]:
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
        payload = fila.get("payload")
        oper_id = await self._con.scalar(
            text(
                """
INSERT INTO sigat.operacion_sync (
    disp_id, oper_cliente_id, usua_id, oper_entidad, oper_accion, oper_registro_id,
    oper_version, oper_hash_contenido, oper_clasificacion, oper_motivo, oper_t_captura,
    oper_deriva_reloj_seg, oper_payload, oper_resuelve_oper_id
) VALUES (
    :disp_id, :cliente_id, :usua_id, :entidad, :accion, :registro_id,
    :version, :hash, :clasificacion, :motivo, :t_captura,
    :deriva, CAST(:payload AS jsonb), :resuelve
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
                "resuelve": fila.get("resuelve"),
            },
        )
        return int(oper_id)

    async def operaciones_de_usuario(self, usua_id: int) -> list[dict[str, Any]]:
        filas = await self._con.execute(
            text(
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
 ORDER BY o.oper_t_recepcion DESC, o.oper_id DESC
"""
            ),
            {"usua_id": usua_id},
        )
        resultado = []
        for fila in filas.mappings():
            datos = dict(fila)
            if isinstance(datos.get("datos"), str):
                datos["datos"] = json.loads(datos["datos"])
            datos["datos"] = {k: v for k, v in (datos.get("datos") or {}).items() if not k.startswith("_")}
            resultado.append(datos)
        return resultado
