"""Acceso a datos de sincronización."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection


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

    async def operacion(self, oper_id: int) -> dict[str, Any] | None:
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT oper_id, disp_id, oper_cliente_id, usua_id, oper_entidad, oper_accion,
       oper_registro_id, oper_version, oper_hash_contenido, oper_clasificacion,
       oper_motivo, oper_t_captura, oper_deriva_reloj_seg, oper_payload,
       oper_resuelve_oper_id
  FROM sigat.operacion_sync
 WHERE oper_id = :oper
"""
                    ),
                    {"oper": oper_id},
                )
            )
            .mappings()
            .first()
        )
        if fila is None:
            return None
        datos = dict(fila)
        if isinstance(datos.get("oper_payload"), str):
            datos["oper_payload"] = json.loads(datos["oper_payload"])
        return datos

    async def ya_resuelta(self, oper_id: int) -> bool:
        return bool(
            await self._con.scalar(
                text("SELECT 1 FROM sigat.operacion_sync WHERE oper_resuelve_oper_id = :oper LIMIT 1"),
                {"oper": oper_id},
            )
        )

    async def fijar_clasificacion(
        self, oper_id: int, clasificacion: str, motivo: str | None, payload: dict[str, Any] | None
    ) -> None:
        """Se inserta la operación con la clasificación ya decidida (tabla append-only):
        el orden real es decidir -> insertar operación -> insertar dominio en la misma transacción.

        Se usa solo dentro del mismo SAVEPOINT y para corregir la clasificación PROVISIONAL de una
        fila recién insertada (el histórico de dominio nunca se modifica).
        """
        await self._con.execute(
            text(
                """
UPDATE sigat.operacion_sync
   SET oper_clasificacion = :clasificacion,
       oper_motivo = :motivo,
       oper_payload = CAST(:payload AS jsonb)
 WHERE oper_id = :oper
"""
            ),
            {
                "clasificacion": clasificacion,
                "motivo": motivo,
                "payload": json.dumps(payload) if payload is not None else None,
                "oper": oper_id,
            },
        )

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
   AND o.oper_resuelve_oper_id IS NULL
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
            datos["datos"] = {k: v for k, v in (datos.get("datos") or {}).items() if not k.startswith("_")}
            resultado.append(datos)
        return resultado

    async def cambios_desde(
        self,
        usua_id: int,
        desde: tuple[datetime, str] | None,
        limite: int,
    ) -> list[dict[str, Any]]:
        """Cambios del servidor para los lotes visibles, en orden (t, id) ascendente."""
        desde_t = desde[0] if desde else None
        desde_id = desde[1] if desde else ""
        filas = await self._con.execute(
            text(
                """
WITH vis AS (
    SELECT lote_id FROM sigat.fn_lotes_visibles(:usua)
), ult AS (
    SELECT DISTINCT ON (lect_registro_id) *
      FROM sigat.lectura_trampa
     ORDER BY lect_registro_id, lect_version DESC
)
SELECT c.tipo, c.id, c.accion, c.t
  FROM (
      SELECT 'lectura'::text AS tipo,
             u.lect_id::text AS id,
             CASE WHEN u.lect_anulada THEN 'invalidado'
                  WHEN u.lect_version > 1 THEN 'actualizado'
                  ELSE 'creado' END AS accion,
             u.lect_t_sincronizacion AS t
        FROM ult u
        JOIN sigat.ubicacion_trampa ut ON ut.ubic_id = u.ubic_id
       WHERE ut.lote_id IN (SELECT lote_id FROM vis)
      UNION ALL
      SELECT 'revision'::text,
             v.visi_id::text,
             'actualizado'::text,
             coalesce(v.visi_revisada_en, v.visi_t_sincronizacion)
        FROM sigat.visita v
       WHERE v.visi_estado_revision <> 'pendiente'
         AND EXISTS (
             SELECT 1 FROM sigat.visita_lote vl
              WHERE vl.visi_id = v.visi_id
                AND vl.lote_id IN (SELECT lote_id FROM vis))
  ) c
 WHERE CAST(:desde AS timestamptz) IS NULL
    OR (c.t, c.id) > (CAST(:desde AS timestamptz), CAST(:desde_id AS text))
 ORDER BY c.t ASC, c.id ASC
 LIMIT :limite
"""
            ),
            {"usua": usua_id, "desde": desde_t, "desde_id": desde_id, "limite": limite},
        )
        return [dict(f) for f in filas.mappings()]
