"""Acceso a datos de planificación. La BD valida autoría y roles: fijar sigat.usuario_actual."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# v_planificacion no trae tipo, arch_id_documento ni lote_ids; lote_codigo puede ser nulo y el contrato pide list[str].
_SELECT_PLANIFICACION = """
SELECT v.plan_id,
       CASE WHEN r.rol_codigo = 'TECNICO' THEN 'tecnico' ELSE 'productor' END AS tipo,
       v.plan_actividad, v.plan_fecha_inicio, v.plan_fecha_fin, v.plan_hora_desde, v.plan_hora_hasta,
       v.plan_estado, v.plan_observaciones, v.plan_observacion_respuesta, v.plan_creada_en,
       v.plan_respondida_en, v.usua_id_destinatario, v.destinatario, v.rol_destinatario,
       v.usua_id_creador, v.creador, p.arch_id_documento, v.documento,
       COALESCE(lo.codigos, '{}') AS lotes,
       COALESCE(lo.ids, '{}') AS lote_ids,
       v.plan_id_origen, v.usua_id_respuesta, v.respondio,
       v.lotes_pendientes, v.lotes_ejecutados, v.lotes_no_ejecutados
  FROM sigat.v_planificacion v
  JOIN sigat.planificacion p ON p.plan_id = v.plan_id
  JOIN sigat.usuario ud ON ud.usua_id = v.usua_id_destinatario
  JOIN sigat.rol r ON r.rol_id = ud.rol_id
  LEFT JOIN LATERAL (
       SELECT array_agg(COALESCE(l.lote_codigo, CAST(l.lote_id AS text))
                        ORDER BY l.lote_codigo NULLS LAST, l.lote_id) AS codigos,
              array_agg(l.lote_id ORDER BY l.lote_codigo NULLS LAST, l.lote_id) AS ids
         FROM sigat.planificacion_lote pl
         JOIN sigat.lote l ON l.lote_id = pl.lote_id
        WHERE pl.plan_id = v.plan_id
  ) lo ON true
"""

# Misma condición de acceso que fn_planificacion_lote_valida exige a cada lote.
_SELECT_DESTINATARIOS = """
SELECT u.usua_id,
       pe.pers_nombre || ' ' || pe.pers_apellido AS nombre,
       r.rol_descripcion || ' · ' || string_agg(DISTINCT pr.pred_nombre, ', ' ORDER BY pr.pred_nombre)
           AS subtitulo,
       CASE WHEN r.rol_codigo = 'TECNICO' THEN 'tecnico' ELSE 'productor' END AS tipo
  FROM sigat.v_acceso_predio a
  JOIN sigat.predio pr ON pr.pred_id = a.pred_id
  JOIN sigat.usuario u ON u.usua_id = a.usua_id
  JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
  JOIN sigat.rol r ON r.rol_id = u.rol_id
  JOIN sigat.estado_registro e ON e.esta_id = u.esta_id
 WHERE a.pred_id IN (SELECT pred_id FROM sigat.v_acceso_predio WHERE usua_id = :agronomo)
   AND r.rol_codigo = ANY (CAST(:roles AS text[]))
   AND e.esta_descripcion = 'activo'
   AND u.usua_eliminado_en IS NULL
   AND (CAST(:usua AS integer) IS NULL OR u.usua_id = CAST(:usua AS integer))
 GROUP BY u.usua_id, pe.pers_nombre, pe.pers_apellido, r.rol_codigo, r.rol_descripcion
 ORDER BY nombre, u.usua_id
"""

_ROLES_POR_TIPO = {"productor": ["PRODUCTOR"], "tecnico": ["TECNICO"]}


class RepositorioPlanificacion:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    async def listar(
        self,
        usua_id: int,
        bandeja: str,
        estado: str | None,
        despues_de: tuple[datetime, UUID] | None,
        limite: int,
    ) -> list[dict[str, Any]]:
        """Más reciente primero; ``despues_de`` = (plan_creada_en, plan_id) de la última fila entregada."""
        filas = await self._con.execute(
            text(
                _SELECT_PLANIFICACION
                + """
 WHERE CASE CAST(:bandeja AS text)
            WHEN 'recibidas' THEN v.usua_id_destinatario = :usua
            WHEN 'creadas' THEN v.usua_id_creador = :usua
            ELSE v.usua_id_destinatario = :usua OR v.usua_id_creador = :usua
       END
   AND (CAST(:estado AS text) IS NULL OR v.plan_estado = CAST(:estado AS text))
   AND (CAST(:creada AS timestamptz) IS NULL
        OR (v.plan_creada_en, v.plan_id) < (CAST(:creada AS timestamptz), CAST(:plan AS uuid)))
 ORDER BY v.plan_creada_en DESC, v.plan_id DESC
 LIMIT :limite
"""
            ),
            {
                "usua": usua_id,
                "bandeja": bandeja,
                "estado": estado,
                "creada": despues_de[0] if despues_de else None,
                "plan": despues_de[1] if despues_de else None,
                "limite": limite,
            },
        )
        return [dict(f) for f in filas.mappings()]

    async def obtener(self, plan_id: UUID) -> dict[str, Any] | None:
        fila = (
            (
                await self._con.execute(
                    text(_SELECT_PLANIFICACION + " WHERE v.plan_id = :plan"), {"plan": plan_id}
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila is not None else None

    async def resultados_lote(self, plan_id: UUID) -> list[dict[str, Any]]:
        """Resultado de cada lote con su número de evidencias."""
        filas = await self._con.execute(
            text(
                """
SELECT pl.lote_id, l.lote_codigo, pl.plal_sync_id, pl.plal_estado, pl.plal_observacion,
       pl.plal_resuelta_en, pl.visi_id,
       (SELECT count(*) FROM sigat.evidencia ev
         WHERE ev.plan_id = pl.plan_id AND ev.lote_id = pl.lote_id) AS evidencias
  FROM sigat.planificacion_lote pl
  JOIN sigat.lote l ON l.lote_id = pl.lote_id
 WHERE pl.plan_id = :plan
 ORDER BY l.lote_codigo NULLS LAST, pl.lote_id
"""
            ),
            {"plan": plan_id},
        )
        return [dict(f) for f in filas.mappings()]

    async def agronomo_del_predio(self, usua_id: int, plan_id: UUID) -> bool:
        """Algún lote de la planificación es de un predio al que el usuario tiene acceso."""
        return bool(
            await self._con.scalar(
                text(
                    """
SELECT EXISTS (
       SELECT 1 FROM sigat.planificacion_lote pl
         JOIN sigat.lote l ON l.lote_id = pl.lote_id
         JOIN sigat.v_acceso_predio a ON a.pred_id = l.pred_id AND a.usua_id = :usua
        WHERE pl.plan_id = :plan)
"""
                ),
                {"usua": usua_id, "plan": plan_id},
            )
        )

    async def insertar(self, fila: dict[str, Any], lote_ids: list[int]) -> UUID:
        """planificacion + planificacion_lote en un SAVEPOINT: si un disparador rechaza, no queda nada."""
        async with self._con.begin_nested():
            plan_id = await self._con.scalar(
                text(
                    """
INSERT INTO sigat.planificacion (usua_id_destinatario, usua_id_creador, plan_actividad,
                                 plan_fecha_inicio, plan_fecha_fin, plan_hora_desde, plan_hora_hasta,
                                 plan_observaciones, arch_id_documento, plan_documento_clase,
                                 plan_id_origen)
VALUES (:usua_id_destinatario, :usua_id_creador, :plan_actividad, :plan_fecha_inicio,
        :plan_fecha_fin, :plan_hora_desde, :plan_hora_hasta, :plan_observaciones,
        :arch_id_documento, :plan_documento_clase, :plan_id_origen)
RETURNING plan_id
"""
                ),
                fila,
            )
            for lote_id in lote_ids:
                await self._con.execute(
                    text("INSERT INTO sigat.planificacion_lote (plan_id, lote_id) VALUES (:plan, :lote)"),
                    {"plan": plan_id, "lote": lote_id},
                )
        return UUID(str(plan_id))

    async def responder(self, plan_id: UUID, estado: str, observacion: str | None) -> int:
        """UPDATE plan_estado/plan_observacion_respuesta; la BD fija usua_id_respuesta y respondida_en."""
        res = await self._con.execute(
            text(
                """
UPDATE sigat.planificacion
   SET plan_estado = :estado,
       plan_observacion_respuesta = :observacion
 WHERE plan_id = :plan
   AND plan_estado = 'pendiente'
"""
            ),
            {"plan": plan_id, "estado": estado, "observacion": observacion},
        )
        return int(res.rowcount or 0)

    async def registrar_resultado_lote(
        self,
        plan_id: UUID,
        lote_id: int,
        estado: str,
        observacion: str | None,
        visi_id: UUID | None,
        oper_id: int | None,
    ) -> bool:
        """UPDATE planificacion_lote con plal_estado, plal_observacion, visi_id, oper_id.
        Si ya no quedan lotes pendientes en la actividad, actualiza la actividad a 'completada'.
        Devuelve True si la actividad quedó completada."""
        async with self._con.begin_nested():
            res = await self._con.execute(
                text(
                    """
UPDATE sigat.planificacion_lote
   SET plal_estado = :estado,
       plal_observacion = :observacion,
       visi_id = :visi_id,
       plal_oper_id = :oper_id
 WHERE plan_id = :plan
   AND lote_id = :lote
   AND plal_estado = 'pendiente'
"""
                ),
                {
                    "plan": plan_id,
                    "lote": lote_id,
                    "estado": estado,
                    "observacion": observacion,
                    "visi_id": visi_id,
                    "oper_id": oper_id,
                },
            )
            if res.rowcount == 0:
                return False

            quedan_pendientes = await self._con.scalar(
                text(
                    """
SELECT EXISTS (
       SELECT 1 FROM sigat.planificacion_lote
        WHERE plan_id = :plan AND plal_estado = 'pendiente'
)
"""
                ),
                {"plan": plan_id},
            )
            if not quedan_pendientes:
                await self._con.execute(
                    text(
                        """
UPDATE sigat.planificacion
   SET plan_estado = 'completada'
 WHERE plan_id = :plan
   AND plan_estado = 'pendiente'
"""
                    ),
                    {"plan": plan_id},
                )
                return True
            return False

    async def destinatarios(
        self, tipo: str | None, usua_id_agronomo: int, usua_id: int | None = None
    ) -> list[dict[str, Any]]:
        """``tipo`` None = ambos; ``usua_id`` acota a un usuario (validación al crear)."""
        roles = _ROLES_POR_TIPO.get(tipo or "", ["PRODUCTOR", "TECNICO"])
        filas = await self._con.execute(
            text(_SELECT_DESTINATARIOS),
            {"agronomo": usua_id_agronomo, "roles": roles, "usua": usua_id},
        )
        return [dict(f) for f in filas.mappings()]

    async def lotes_asignables(self, usua_id_agronomo: int, destinatario: int | None) -> list[dict[str, Any]]:
        """Lotes vigentes del agrónomo; con ``destinatario``, solo de predios a los que este tiene acceso."""
        filas = await self._con.execute(
            text(
                """
SELECT l.lote_id, l.lote_codigo, l.lote_descripcion, p.pred_id, p.pred_nombre
  FROM sigat.lote l
  JOIN sigat.predio p ON p.pred_id = l.pred_id
 WHERE l.lote_id IN (SELECT lote_id FROM sigat.fn_lotes_visibles(:agronomo))
   AND (l.lote_fecha_baja IS NULL OR l.lote_fecha_baja > CURRENT_DATE)
   AND (CAST(:destinatario AS integer) IS NULL
        OR l.pred_id IN (SELECT pred_id FROM sigat.v_acceso_predio
                          WHERE usua_id = CAST(:destinatario AS integer)))
 ORDER BY p.pred_nombre, l.lote_codigo NULLS LAST, l.lote_id
"""
            ),
            {"agronomo": usua_id_agronomo, "destinatario": destinatario},
        )
        return [dict(f) for f in filas.mappings()]

    async def actividades_tecnico(
        self,
        usua_id_actor: int,
        usua_id: int | None,
        pred_id: int | None,
        despues_de: tuple[date, UUID, int] | None,
        limite: int,
    ) -> list[dict[str, Any]]:
        """Informe de actividades de técnicos usando fn_actividad_tecnico(:actor).
        Orden: plan_fecha_inicio DESC, plan_id DESC, lote_id DESC."""
        filas = await self._con.execute(
            text(
                """
SELECT plan_id, plan_id_origen, planificacion_aprobada, aprobada_el,
       plan_actividad, plan_fecha_inicio, plan_fecha_fin, actividad_estado,
       usua_id, tecnico, lote_id, lote_codigo, pred_id,
       resultado, justificacion, resuelta_en, visi_id, evidencias
  FROM sigat.fn_actividad_tecnico(:actor)
 WHERE (CAST(:usua AS integer) IS NULL OR usua_id = CAST(:usua AS integer))
   AND (CAST(:predio AS integer) IS NULL OR pred_id = CAST(:predio AS integer))
   AND (CAST(:inicio AS date) IS NULL
        OR (plan_fecha_inicio, plan_id, lote_id) < (CAST(:inicio AS date), CAST(:plan AS uuid), CAST(:lote AS integer)))
 ORDER BY plan_fecha_inicio DESC, plan_id DESC, lote_id DESC
 LIMIT :limite
"""
            ),
            {
                "actor": usua_id_actor,
                "usua": usua_id,
                "predio": pred_id,
                "inicio": despues_de[0] if despues_de else None,
                "plan": despues_de[1] if despues_de else None,
                "lote": despues_de[2] if despues_de else None,
                "limite": limite,
            },
        )
        return [dict(f) for f in filas.mappings()]

    async def contar_por_estado(self, usua_id_creador: int, estado: str) -> int:
        """Cuenta planificaciones creadas por el diagrama en un estado dado"""
        sql = """
        SELECT count(*)::int
        FROM sigat.planificacion
        WHERE usua_id_creador = :u
          AND plan_estado = :estado
        """
        resultado = await self._con.scalar(text(sql), {"u": usua_id_creador, "estado": estado})
        return int(resultado or 0)
