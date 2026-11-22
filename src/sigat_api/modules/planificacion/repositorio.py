"""Acceso a datos de planificación. La BD valida autoría y roles: fijar sigat.usuario_actual."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado


class RepositorioPlanificacion:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    async def listar(self, usua_id: int, bandeja: str, estado: str | None, cursor: str | None) -> Any:
        """v_planificacion WHERE usua_id_destinatario = :u (recibidas) / usua_id_creador = :u (creadas)."""
        raise NoImplementado("Planificaciones")

    async def obtener(self, plan_id: UUID) -> dict[str, Any] | None:
        raise NoImplementado("Planificación")

    async def resultados_lote(self, plan_id: UUID) -> list[dict[str, Any]]:
        raise NoImplementado("Resultados por lote")

    async def insertar(self, fila: dict[str, Any], lote_ids: list[int]) -> UUID:
        raise NoImplementado("Alta de planificación")

    async def responder(self, plan_id: UUID, estado: str, observacion: str | None) -> None:
        """UPDATE plan_estado/plan_observacion_respuesta; la BD fija usua_id_respuesta y respondida_en."""
        raise NoImplementado("Respuesta")

    async def registrar_resultado_lote(
        self,
        plan_id: UUID,
        lote_id: int,
        estado: str,
        observacion: str | None,
        visi_id: UUID | None,
        oper_id: int | None,
    ) -> None:
        raise NoImplementado("Resultado por lote")

    async def destinatarios(self, tipo: str, usua_id_agronomo: int) -> list[dict[str, Any]]:
        raise NoImplementado("Destinatarios")

    async def lotes_asignables(self, usua_id_agronomo: int, destinatario: int | None) -> list[dict[str, Any]]:
        raise NoImplementado("Lotes asignables")

    async def actividades_tecnico(self, filtros: dict[str, Any], cursor: str | None) -> Any:
        raise NoImplementado("Actividades de técnicos")

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
