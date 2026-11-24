"""Casos de uso de planificación. ``aplicar_resultado_lote`` implementa
``sincronizacion.puertos.PuertoResultadoPlanificacion``."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sigat_api.compartido.contratos import OperacionCampo, ResultadoAplicacion
from sigat_api.compartido.errores import (
    Conflicto,
    NoEncontrado,
    NoImplementado,
    Prohibido,
    ReglaNegocio,
)
from sigat_api.compartido.esquemas import Pagina
from sigat_api.compartido.tipos import Actor, EstadoPlanificacion, RolCodigo
from sigat_api.modules.planificacion import esquemas as e
from sigat_api.modules.planificacion.cursor import TAMANO_PAGINA, CursorInvalido, codificar, decodificar
from sigat_api.modules.planificacion.repositorio import RepositorioPlanificacion

MINIMO_OBSERVACION = 10
MINIMO_ACTIVIDAD = 3


def _actividad(s: e.NuevaPlanificacion) -> str:
    actividad = s.plan_actividad.strip()
    if len(actividad) < MINIMO_ACTIVIDAD:
        raise ReglaNegocio(
            f"Describa la actividad con al menos {MINIMO_ACTIVIDAD} caracteres.", codigo="ACTIVIDAD_INVALIDA"
        )
    return actividad


def _validar_fechas_y_horario(s: e.NuevaPlanificacion) -> None:
    if s.plan_fecha_fin is not None and s.plan_fecha_fin < s.plan_fecha_inicio:
        raise ReglaNegocio(
            "La fecha de finalización no puede ser anterior a la de inicio.", codigo="FECHAS_INVALIDAS"
        )
    if (s.plan_hora_desde is None) != (s.plan_hora_hasta is None):
        raise ReglaNegocio("Indique la hora de inicio y la de fin, o ninguna.", codigo="HORARIO_INCOMPLETO")
    if (
        s.plan_hora_desde is not None
        and s.plan_hora_hasta is not None
        and s.plan_hora_hasta <= s.plan_hora_desde
    ):
        raise ReglaNegocio("La hora de fin debe ser posterior a la de inicio.", codigo="HORARIO_INVALIDO")


def _validar_para_productor(s: e.NuevaPlanificacion, observaciones: str | None) -> None:
    if s.plan_id_origen is not None:
        raise ReglaNegocio(
            "Una planificación para un productor no sale de otra planificación.",
            codigo="ORIGEN_NO_PERMITIDO",
        )
    if s.arch_id_documento is None and len(observaciones or "") < MINIMO_OBSERVACION:
        raise ReglaNegocio(
            f"Adjunte el documento de instrucciones o escriba una observación de al menos "
            f"{MINIMO_OBSERVACION} caracteres.",
            codigo="DOCUMENTO_U_OBSERVACION_REQUERIDO",
        )


def _validar_contra_origen(s: e.NuevaPlanificacion, origen: dict[str, Any], lote_ids: list[int]) -> None:
    if origen["tipo"] != "productor" or origen["plan_estado"] != EstadoPlanificacion.ACEPTADA:
        raise ReglaNegocio(
            "La actividad debe salir de una planificación aceptada por el productor.",
            codigo="ORIGEN_NO_ACEPTADO",
        )
    fin_origen = origen["plan_fecha_fin"]
    if s.plan_fecha_inicio < origen["plan_fecha_inicio"] or (
        fin_origen is not None and (s.plan_fecha_fin or s.plan_fecha_inicio) > fin_origen
    ):
        raise ReglaNegocio(
            "Las fechas de la actividad deben estar dentro de las de la planificación aceptada.",
            codigo="FECHAS_FUERA_DEL_ORIGEN",
        )
    fuera = [lote for lote in lote_ids if lote not in origen["lote_ids"]]
    if fuera:
        raise ReglaNegocio(
            f"Estos lotes no están en la planificación aceptada: {fuera}.", codigo="LOTE_NO_EN_ORIGEN"
        )


class ServicioPlanificacion:
    def __init__(self, repo: RepositorioPlanificacion) -> None:
        self._repo = repo

    async def listar(
        self, actor: Actor, bandeja: str, estado: EstadoPlanificacion | None, cursor: str | None
    ) -> Pagina[e.Planificacion]:
        """bandeja: 'recibidas' (técnico/productor), 'creadas' (agrónomo) o 'todas' (las dos)."""
        filas = await self._repo.listar(
            actor.usua_id,
            bandeja,
            estado.value if estado else None,
            _clave_listado(cursor),
            TAMANO_PAGINA + 1,
        )
        items = [e.Planificacion(**fila) for fila in filas[:TAMANO_PAGINA]]
        siguiente = None
        if len(filas) > TAMANO_PAGINA:
            ultima = items[-1]
            siguiente = codificar(ultima.plan_creada_en.isoformat(), str(ultima.plan_id))
        return Pagina[e.Planificacion](items=items, siguiente=siguiente)

    async def obtener(self, actor: Actor, plan_id: UUID) -> e.PlanificacionDetalle:
        """Visible para creador y destinatario (y el agrónomo del predio). Otro -> 404."""
        fila = await self._repo.obtener(plan_id)
        if fila is None or not await self._puede_ver(actor, fila):
            raise NoEncontrado(
                "La planificación no existe o no es visible para usted.", codigo="PLANIFICACION_NO_ENCONTRADA"
            )
        resultados = await self._repo.resultados_lote(plan_id)
        return e.PlanificacionDetalle(**fila, resultados_lote=[e.ResultadoLote(**r) for r in resultados])

    async def _puede_ver(self, actor: Actor, fila: dict[str, Any]) -> bool:
        if actor.usua_id in (fila["usua_id_creador"], fila["usua_id_destinatario"]):
            return True
        return actor.rol == RolCodigo.AGRONOMO_SUPERVISOR and await self._repo.agronomo_del_predio(
            actor.usua_id, fila["plan_id"]
        )

    async def crear(self, actor: Actor, s: e.NuevaPlanificacion) -> e.PlanificacionDetalle:
        """Reglas: solo AGRONOMO_SUPERVISOR (C01: creador = actor). Destinatario PRODUCTOR: documento
        o observación >= 10 caracteres; sin plan_id_origen. Destinatario TÉCNICO: >= 1 lote y
        plan_id_origen de una planificación ACEPTADA. fecha_fin >= fecha_inicio; horas en pareja."""
        if actor.rol != RolCodigo.AGRONOMO_SUPERVISOR:
            raise Prohibido("Solo el agrónomo crea planificaciones.", codigo="SOLO_AGRONOMO_CREA")
        actividad = _actividad(s)
        _validar_fechas_y_horario(s)
        tipo = await self._tipo_destinatario(actor, s.usua_id_destinatario)
        lote_ids = list(dict.fromkeys(s.lote_ids))
        await self._exigir_lotes_asignables(actor, s.usua_id_destinatario, lote_ids)
        observaciones = (s.plan_observaciones or "").strip() or None
        if tipo == "productor":
            _validar_para_productor(s, observaciones)
        else:
            await self._validar_para_tecnico(actor, s, lote_ids)

        plan_id = await self._repo.insertar(
            {
                "usua_id_destinatario": s.usua_id_destinatario,
                "usua_id_creador": actor.usua_id,
                "plan_actividad": actividad,
                "plan_fecha_inicio": s.plan_fecha_inicio,
                "plan_fecha_fin": s.plan_fecha_fin,
                "plan_hora_desde": s.plan_hora_desde,
                "plan_hora_hasta": s.plan_hora_hasta,
                "plan_observaciones": observaciones,
                "arch_id_documento": s.arch_id_documento,
                "plan_documento_clase": "documento_instruccion" if s.arch_id_documento else None,
                "plan_id_origen": s.plan_id_origen,
            },
            lote_ids,
        )
        return await self.obtener(actor, plan_id)

    async def _tipo_destinatario(self, actor: Actor, usua_id: int) -> str:
        filas = await self._repo.destinatarios(None, actor.usua_id, usua_id)
        if not filas:
            raise ReglaNegocio(
                "El destinatario debe ser un productor o técnico activo de sus predios.",
                codigo="DESTINATARIO_INVALIDO",
            )
        return str(filas[0]["tipo"])

    async def _exigir_lotes_asignables(self, actor: Actor, destinatario: int, lote_ids: list[int]) -> None:
        asignables = {f["lote_id"] for f in await self._repo.lotes_asignables(actor.usua_id, destinatario)}
        fuera = [lote for lote in lote_ids if lote not in asignables]
        if fuera:
            raise ReglaNegocio(
                f"Estos lotes no se pueden asignar a este destinatario: {fuera}.", codigo="LOTE_NO_ASIGNABLE"
            )

    async def _validar_para_tecnico(self, actor: Actor, s: e.NuevaPlanificacion, lote_ids: list[int]) -> None:
        if not lote_ids:
            raise ReglaNegocio("Asigne al menos un lote a la actividad.", codigo="LOTES_REQUERIDOS")
        if s.plan_id_origen is None:
            raise ReglaNegocio(
                "La actividad de un técnico debe salir de una planificación aceptada (plan_id_origen).",
                codigo="ORIGEN_REQUERIDO",
            )
        origen = await self._repo.obtener(s.plan_id_origen)
        if origen is None or not await self._puede_ver(actor, origen):
            raise ReglaNegocio("La planificación de origen no existe.", codigo="ORIGEN_INVALIDO")
        _validar_contra_origen(s, origen, lote_ids)

    async def aceptar(self, actor: Actor, plan_id: UUID, s: e.Aceptacion) -> e.PlanificacionDetalle:
        """Solo el PRODUCTOR destinatario y solo si pendiente (409 ``PLANIFICACION_YA_RESPONDIDA``).
        Al técnico: 422 ``TECNICO_NO_RESPONDE``."""
        plan = await self._repo.obtener(plan_id)
        if plan is None or actor.usua_id != plan["usua_id_destinatario"]:
            raise NoEncontrado(
                "La planificación no existe o no es visible para usted.", codigo="PLANIFICACION_NO_ENCONTRADA"
            )
        if plan["tipo"] == "tecnico":
            raise ReglaNegocio(
                "Las actividades dirigidas a un técnico no se responden por este endpoint.",
                codigo="TECNICO_NO_RESPONDE",
            )
        if plan["plan_estado"] != EstadoPlanificacion.PENDIENTE:
            raise Conflicto("La planificación ya fue respondida.", codigo="PLANIFICACION_YA_RESPONDIDA")

        observacion = (s.observacion or "").strip() or None
        actualizadas = await self._repo.responder(plan_id, EstadoPlanificacion.ACEPTADA.value, observacion)
        if actualizadas == 0:
            raise Conflicto("La planificación ya fue respondida.", codigo="PLANIFICACION_YA_RESPONDIDA")
        return await self.obtener(actor, plan_id)

    async def rechazar(self, actor: Actor, plan_id: UUID, s: e.Rechazo) -> e.PlanificacionDetalle:
        """Solo el PRODUCTOR destinatario y solo si pendiente (409 ``PLANIFICACION_YA_RESPONDIDA``).
        Al técnico: 422 ``TECNICO_NO_RESPONDE``."""
        plan = await self._repo.obtener(plan_id)
        if plan is None or actor.usua_id != plan["usua_id_destinatario"]:
            raise NoEncontrado(
                "La planificación no existe o no es visible para usted.", codigo="PLANIFICACION_NO_ENCONTRADA"
            )
        if plan["tipo"] == "tecnico":
            raise ReglaNegocio(
                "Las actividades dirigidas a un técnico no se responden por este endpoint.",
                codigo="TECNICO_NO_RESPONDE",
            )
        if plan["plan_estado"] != EstadoPlanificacion.PENDIENTE:
            raise Conflicto("La planificación ya fue respondida.", codigo="PLANIFICACION_YA_RESPONDIDA")

        motivo = s.motivo.strip()
        actualizadas = await self._repo.responder(plan_id, EstadoPlanificacion.RECHAZADA.value, motivo)
        if actualizadas == 0:
            raise Conflicto("La planificación ya fue respondida.", codigo="PLANIFICACION_YA_RESPONDIDA")
        return await self.obtener(actor, plan_id)

    async def registrar_resultado_lote(
        self, actor: Actor, plan_id: UUID, lote_id: int, s: e.EntradaResultadoLote
    ) -> e.PlanificacionDetalle:
        """En línea. Solo el técnico destinatario, actividad pendiente, lote no resuelto antes."""
        plan = await self._repo.obtener(plan_id)
        if plan is None or actor.usua_id != plan["usua_id_destinatario"]:
            raise NoEncontrado(
                "La planificación no existe o no es visible para usted.", codigo="PLANIFICACION_NO_ENCONTRADA"
            )
        if plan["tipo"] != "tecnico":
            raise ReglaNegocio(
                "Solo las actividades asignadas a un técnico registran resultado por lote.",
                codigo="SOLO_ACTIVIDAD_TECNICO",
            )
        if plan["plan_estado"] != EstadoPlanificacion.PENDIENTE:
            raise Conflicto(
                "La actividad ya está completada: sus resultados no se modifican.",
                codigo="ACTIVIDAD_NO_PENDIENTE",
            )
        if lote_id not in plan["lote_ids"]:
            raise NoEncontrado("El lote no pertenece a esta actividad.", codigo="LOTE_NO_EN_ACTIVIDAD")

        resultados = await self._repo.resultados_lote(plan_id)
        lote_res = next((r for r in resultados if r["lote_id"] == lote_id), None)
        if lote_res is None:
            raise NoEncontrado("El lote no pertenece a esta actividad.", codigo="LOTE_NO_EN_ACTIVIDAD")
        if lote_res["plal_estado"] != "pendiente":
            raise Conflicto("El lote ya tiene un resultado registrado.", codigo="LOTE_YA_RESUELTO")

        observacion: str | None = None
        if s.plal_estado == "no_ejecutada":
            obs = (s.plal_observacion or "").strip()
            if not obs:
                raise ReglaNegocio(
                    "Describa el motivo por el cual no se ejecutó la actividad en el lote.",
                    codigo="JUSTIFICACION_REQUERIDA",
                )
            if s.visi_id is not None:
                raise ReglaNegocio(
                    "Una actividad no ejecutada no puede tener visita asociada.",
                    codigo="VISITA_NO_PERMITIDA",
                )
            observacion = obs
        else:
            observacion = (s.plal_observacion or "").strip() or None

        await self._repo.registrar_resultado_lote(
            plan_id, lote_id, s.plal_estado, observacion, s.visi_id, oper_id=None
        )
        return await self.obtener(actor, plan_id)

    async def aplicar_resultado_lote(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion:
        """Offline (entidad ``planificacion_lote``; registro_id = plal_sync_id; liga plal_oper_id)."""
        raise NoImplementado("Resultado de actividad por sincronización")

    async def destinatarios(self, actor: Actor, tipo: str) -> list[e.Destinatario]:
        """Usuarios activos del tipo pedido con acceso a algún predio del agrónomo."""
        return [e.Destinatario(**f) for f in await self._repo.destinatarios(tipo, actor.usua_id)]

    async def lotes_asignables(self, actor: Actor, destinatario: int | None) -> list[e.LoteAsignable]:
        """Lotes vigentes del agrónomo; con ``destinatario``, solo los de predios a los que este accede."""
        return [e.LoteAsignable(**f) for f in await self._repo.lotes_asignables(actor.usua_id, destinatario)]

    async def actividades_tecnico(
        self, actor: Actor, usua_id: int | None, pred_id: int | None, cursor: str | None
    ) -> Pagina[e.ActividadTecnico]:
        """Informe de actividades de técnicos (realizadas y no realizadas)."""
        filas = await self._repo.actividades_tecnico(
            actor.usua_id,
            usua_id,
            pred_id,
            _clave_actividades(cursor),
            TAMANO_PAGINA + 1,
        )
        items = [e.ActividadTecnico(**fila) for fila in filas[:TAMANO_PAGINA]]
        siguiente = None
        if len(filas) > TAMANO_PAGINA:
            ultima = items[-1]
            siguiente = codificar(
                ultima.plan_fecha_inicio.isoformat(),
                str(ultima.plan_id),
                ultima.lote_id,
            )
        return Pagina[e.ActividadTecnico](items=items, siguiente=siguiente)

    async def contar_pendientes(self, actor: Actor) -> int:
        """Total de planificaciones en estado pendiente creadas por el agronomo"""
        return await self._repo.contar_por_estado(actor.usua_id, "pendiente")

    async def contar_en_curso(self, actor: Actor) -> int:
        """Total de planificaciones en estado pendiente creadas por el agronomo"""
        return await self._repo.contar_por_estado(actor.usua_id, "aceptada")


def _clave_listado(cursor: str | None) -> tuple[datetime, UUID] | None:
    clave = decodificar(cursor, 2)
    if clave is None:
        return None
    try:
        return datetime.fromisoformat(str(clave[0])), UUID(str(clave[1]))
    except ValueError as exc:
        raise CursorInvalido("Pida de nuevo la primera página sin cursor.") from exc


def _clave_actividades(cursor: str | None) -> tuple[date, UUID, int] | None:
    clave = decodificar(cursor, 3)
    if clave is None:
        return None
    try:
        return date.fromisoformat(str(clave[0])), UUID(str(clave[1])), int(clave[2])
    except (ValueError, TypeError) as exc:
        raise CursorInvalido("Pida de nuevo la primera página sin cursor.") from exc
