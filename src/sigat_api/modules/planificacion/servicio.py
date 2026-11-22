"""Casos de uso de planificación. ``aplicar_resultado_lote`` implementa
``sincronizacion.puertos.PuertoResultadoPlanificacion``."""

from uuid import UUID

from sigat_api.compartido.contratos import OperacionCampo, ResultadoAplicacion
from sigat_api.compartido.errores import NoImplementado
from sigat_api.compartido.esquemas import Pagina
from sigat_api.compartido.tipos import Actor, EstadoPlanificacion
from sigat_api.modules.planificacion import esquemas as e
from sigat_api.modules.planificacion.repositorio import RepositorioPlanificacion


class ServicioPlanificacion:
    def __init__(self, repo: RepositorioPlanificacion) -> None:
        self._repo = repo

    async def listar(
        self, actor: Actor, bandeja: str, estado: EstadoPlanificacion | None, cursor: str | None
    ) -> Pagina[e.Planificacion]:
        """bandeja: 'recibidas' (técnico/productor), 'creadas' (agrónomo) o 'todas' (las dos)."""
        raise NoImplementado("Planificaciones")

    async def obtener(self, actor: Actor, plan_id: UUID) -> e.PlanificacionDetalle:
        """Visible para creador y destinatario (y el agrónomo del predio). Otro -> 404."""
        raise NoImplementado("Planificación")

    async def crear(self, actor: Actor, s: e.NuevaPlanificacion) -> e.PlanificacionDetalle:
        """Reglas: solo AGRONOMO_SUPERVISOR (C01: creador = actor). Destinatario PRODUCTOR: documento
        o observación >= 10 caracteres; sin plan_id_origen. Destinatario TÉCNICO: >= 1 lote y
        plan_id_origen de una planificación ACEPTADA. fecha_fin >= fecha_inicio; horas en pareja."""
        raise NoImplementado("Crear planificación")

    async def aceptar(self, actor: Actor, plan_id: UUID, s: e.Aceptacion) -> e.PlanificacionDetalle:
        """Solo el PRODUCTOR destinatario y solo si pendiente (409 ``PLANIFICACION_YA_RESPONDIDA``).
        Al técnico: 422 ``TECNICO_NO_RESPONDE``."""
        raise NoImplementado("Aceptar planificación")

    async def rechazar(self, actor: Actor, plan_id: UUID, s: e.Rechazo) -> e.PlanificacionDetalle:
        raise NoImplementado("Rechazar planificación")

    async def registrar_resultado_lote(
        self, actor: Actor, plan_id: UUID, lote_id: int, s: e.EntradaResultadoLote
    ) -> e.PlanificacionDetalle:
        """En línea. Solo el técnico destinatario, actividad pendiente, lote no resuelto antes."""
        raise NoImplementado("Resultado de actividad en lote")

    async def aplicar_resultado_lote(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion:
        """Offline (entidad ``planificacion_lote``; registro_id = plal_sync_id; liga plal_oper_id)."""
        raise NoImplementado("Resultado de actividad por sincronización")

    async def destinatarios(self, actor: Actor, tipo: str) -> list[e.Destinatario]:
        raise NoImplementado("Destinatarios")

    async def lotes_asignables(self, actor: Actor, destinatario: int | None) -> list[e.LoteAsignable]:
        raise NoImplementado("Lotes asignables")

    async def actividades_tecnico(
        self, actor: Actor, usua_id: int | None, pred_id: int | None, cursor: str | None
    ) -> Pagina[e.ActividadTecnico]:
        raise NoImplementado("Informe de actividades")

    async def contar_pendientes(self, actor: Actor) -> int:
        """Total de planificaciones en estado pendiente creadas por el agronomo"""
        return await self._repo.contar_por_estado(actor.usua_id, "pendiente")

    async def contar_en_curso(self, actor: Actor) -> int:
        """Total de planificaciones en estado pendiente creadas por el agronomo"""
        return await self._repo.contar_por_estado(actor.usua_id, "aceptada")
