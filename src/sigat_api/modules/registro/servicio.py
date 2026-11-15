"""Casos de uso del núcleo de registro."""

from datetime import datetime
from uuid import UUID

from sigat_api.compartido.contratos import OperacionCampo, ResultadoAplicacion
from sigat_api.compartido.errores import NoImplementado
from sigat_api.compartido.esquemas import Pagina
from sigat_api.compartido.tipos import (
    AccionSync,
    Actor,
    ClasificacionOperacion,
    EntidadSync,
    EstadoRevision,
    Linaje,
)
from sigat_api.infra.reloj import Reloj
from sigat_api.modules.registro import dominio
from sigat_api.modules.registro import esquemas as e
from sigat_api.modules.registro.dominio import MENSAJES, MotivoRechazo
from sigat_api.modules.registro.puertos import PuertoConfiguracion, PuertoOperaciones
from sigat_api.modules.registro.repositorio import RepositorioRegistro
from sigat_api.modules.registro.validador import ValidadorInvariantes


class ServicioRegistro:
    """Escritura. Implementa estructuralmente ``sincronizacion.puertos.PuertoRegistro``."""

    def __init__(
        self,
        repo: RepositorioRegistro,
        validador: ValidadorInvariantes,
        operaciones: PuertoOperaciones,
        configuracion: PuertoConfiguracion,
        reloj: Reloj,
    ) -> None:
        self._repo = repo
        self._validador = validador
        self._operaciones = operaciones
        self._configuracion = configuracion
        self._reloj = reloj

    async def aplicar(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion:
        """Aplica UNA operación dentro de la transacción del lote de sincronización.

        1. ``validador.validar`` -> si hay motivo: ``rechazada`` (no toca el histórico).
        2. ``repo.estado_previo`` + ``dominio.clasificar``.
        3. ``revisar`` -> NO inserta en la tabla de dominio (queda solo en operacion_sync con payload).
        4. alta/corrección -> ``repo.insertar_version`` (o ``insertar_visita``) ligado a ``op.oper_id``.
        5. Si es lectura oficial (o su corrección/anulación): devolver ``periodos_afectados`` para
           que Sincronización encole el reproceso de MTD y concepto (RF-054, ADR-13).
        """
        ahora = self._reloj.ahora()
        motivo = await self._validador.validar(op, ahora, actor)
        if motivo is not None:
            return self._rechazo(motivo)

        if op.entidad == EntidadSync.VISITA:
            return await self._aplicar_visita(actor, op)
        if op.entidad == EntidadSync.LECTURA_TRAMPA:
            return await self._aplicar_lectura(actor, op)

        return ResultadoAplicacion(
            ClasificacionOperacion.RECHAZADA,
            motivo="Esta entidad todavía no se sincroniza desde el móvil.",
            codigo="ENTIDAD_NO_SOPORTADA",
        )

    async def _aplicar_visita(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion:
        previo = await self._repo.estado_previo("visita", op.registro_id, None, None, op.t_observacion)
        clasificacion, motivo = dominio.decidir(op.accion, op.version, op.t_observacion, previo)
        if motivo is not None:
            return self._rechazo(motivo)
        if clasificacion not in (
            ClasificacionOperacion.ALTA_NUEVA,
            ClasificacionOperacion.ALTA_RETROACTIVA,
        ):
            return ResultadoAplicacion(clasificacion)

        lote_ids = [int(x) for x in op.datos["lote_ids"]]
        fila = {
            "visi_id": op.registro_id,
            "disp_id": op.disp_id,
            "oper_id": op.oper_id,
            "usua_id": actor.usua_id,
            "pred_id": int(op.datos["pred_id"]),
            "ruta_id": op.datos.get("ruta_id"),
            "visi_t_inicio_obs": _instante(op.datos["visi_t_inicio_obs"]),
            "visi_t_fin_obs": _instante(op.datos["visi_t_fin_obs"]),
            "visi_t_captura": op.t_captura,
            "visi_deriva_reloj_seg": op.deriva_reloj_seg,
        }
        await self._repo.insertar_visita(fila, lote_ids)
        return ResultadoAplicacion(clasificacion)

    async def _aplicar_lectura(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion:
        tram_id = int(op.datos["tram_id"])
        pertenencia = await self._configuracion.pertenencia_trampa(tram_id, op.t_observacion)
        if pertenencia is None or pertenencia.lote_id is None:
            return self._rechazo(MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE)

        previo = await self._repo.estado_previo(
            "lectura_trampa", op.registro_id, pertenencia.lote_id, tram_id, op.t_observacion
        )
        clasificacion, motivo = dominio.decidir(op.accion, op.version, op.t_observacion, previo)
        if motivo is not None:
            return self._rechazo(motivo)
        if clasificacion == ClasificacionOperacion.REVISAR:
            return ResultadoAplicacion(
                clasificacion,
                motivo="Ya existe otra lectura de esta trampa el mismo día; la decidirá el agrónomo.",
                codigo="SOLAPAMIENTO",
            )

        anterior: dict[str, object] = {}
        if op.version > 1:
            anterior = await self._repo.lectura_vigente(op.registro_id) or {}
        datos = dict(anterior)
        datos.update({k: v for k, v in op.datos.items() if k in anterior or k in _DATOS_LECTURA})

        fila: dict[str, object] = {
            **datos,
            "lect_registro_id": op.registro_id,
            "lect_version": op.version,
            "usua_id": actor.usua_id,
            "disp_id": op.disp_id,
            "oper_id": op.oper_id,
            "lect_t_observacion": op.t_observacion,
            "lect_t_captura": op.t_captura,
            "lect_deriva_reloj_seg": op.deriva_reloj_seg,
            "lect_linaje": op.linaje or _linaje_de(actor),
        }
        if fila.get("ubic_id") is None:
            fila["ubic_id"] = pertenencia.ubic_id
        fila["lect_anulada"] = op.accion == AccionSync.ANULAR
        if op.accion == AccionSync.ANULAR:
            fila["lect_motivo_anulacion"] = op.datos.get("lect_motivo_anulacion")
        else:
            fila.pop("lect_motivo_anulacion", None)

        await self._repo.insertar_version("lectura_trampa", fila)

        periodos: list[int] = []
        if op.linaje in (None, Linaje.INSPECCION_OFICIAL) and actor.rol.value in (
            "TECNICO",
            "AGRONOMO_SUPERVISOR",
            "ADMINISTRADOR",
        ):
            periodo = await self._repo.periodo_de(pertenencia.lote_id, op.t_observacion.date())
            if periodo is not None:
                periodos.append(int(periodo["peri_id"]))
        return ResultadoAplicacion(
            clasificacion,
            periodos_afectados=periodos,
            fecha_efecto=op.t_observacion if periodos else None,
        )

    @staticmethod
    def _rechazo(motivo: MotivoRechazo) -> ResultadoAplicacion:
        return ResultadoAplicacion(
            ClasificacionOperacion.RECHAZADA,
            motivo=MENSAJES[motivo],
            codigo=motivo.value,
        )

    # --- escrituras en línea de la web (pasan por el mismo camino) ---------------------------
    async def registrar_aplicacion(self, actor: Actor, s: e.NuevaAplicacion) -> e.Aplicacion:
        """Productor desde la web (``registerApplication``). Construye una ``OperacionCampo`` con
        ``PuertoOperaciones.registrar_operacion_en_linea`` y llama a ``aplicar``. Linaje autorreporte."""
        raise NoImplementado("Registrar aplicación")

    async def registrar_cosecha(self, actor: Actor, s: e.NuevaCosecha) -> e.Cosecha:
        """Web ``saveHarvest``: si ya existe la cosecha del lote, el front usa ``nueva_version_cosecha``."""
        raise NoImplementado("Registrar cosecha")

    async def nueva_version_aplicacion(
        self, actor: Actor, registro_id: UUID, s: e.NuevaVersionAutorreporte
    ) -> e.Aplicacion:
        """Solo el autor. ``version_esperada`` != vigente -> 412."""
        raise NoImplementado("Corregir aplicación")

    async def nueva_version_cosecha(
        self, actor: Actor, registro_id: UUID, s: e.NuevaVersionAutorreporte
    ) -> e.Cosecha:
        raise NoImplementado("Corregir cosecha")

    async def corregir_lectura(self, actor: Actor, registro_id: UUID, s: e.CorreccionLectura) -> e.Lectura:
        """Agrónomo (web 'corregir', motivo obligatorio). Versión n+1 con mismo t_observacion y
        ``lect_motivo_correccion`` (BD v9); su operación es 'corregir' con el dispositivo web de la sesión.
        Dispara el reproceso del periodo. El estado pasa a 'corregido' en v_lectura_revision."""
        raise NoImplementado("Corregir lectura")

    async def anular_lectura(self, actor: Actor, registro_id: UUID, s: e.AnulacionRegistro) -> e.Lectura:
        """Agrónomo (web 'rechazar'). Versión n+1 anulada; sale del MTD; dispara reproceso."""
        raise NoImplementado("Anular lectura")


class ServicioRevision:
    """Revisión del agrónomo/supervisor SOBRE LA VISITA (BD: visi_estado_revision).

    Web y móvil deben revisar lo mismo (CONCORDANCIA §4.2): la web muestra lecturas,
    pero la decisión 'aceptar' es de la visita; corregir/rechazar una lectura es una
    versión nueva de esa lectura (``ServicioRegistro``).
    """

    def __init__(self, repo: RepositorioRegistro, reloj: Reloj) -> None:
        self._repo = repo
        self._reloj = reloj

    async def listar(
        self, actor: Actor, estado: EstadoRevision | None, cursor: str | None
    ) -> Pagina[e.RegistroRevision]:
        raise NoImplementado("Registros por revisar")

    async def obtener(self, actor: Actor, visi_id: UUID) -> e.RevisionDetalle:
        raise NoImplementado("Detalle de revisión")

    async def aceptar(self, actor: Actor, visi_id: UUID) -> e.RevisionDetalle:
        """pendiente|en_revision -> aceptado (UPDATE de las columnas de revisión de la visita).
        Las lecturas sin corrección ni anulación pasan a 'confirmado' en v_lectura_revision (la web
        'Confirmar' llama aquí). Ya aceptada -> 409 ``REVISION_YA_RESUELTA``."""
        raise NoImplementado("Aceptar registro")

    async def marcar_en_revision(
        self, actor: Actor, visi_id: UUID, s: e.ObservacionRevision
    ) -> e.RevisionDetalle:
        """pendiente -> en_revision con motivo (notifica al técnico)."""
        raise NoImplementado("Marcar en revisión")

    async def contar_por_revisar(self, actor: Actor) -> int:
        """Total de visitas pendientes de revisar en los predios del agronomo."""
        return await self._repo.contar_por_revisar(actor.usua_id)


class ServicioConsultaRegistro:
    """Lecturas para pantallas. Siempre filtradas por fn_lotes_visibles."""

    def __init__(self, repo: RepositorioRegistro) -> None:
        self._repo = repo

    async def visitas(
        self,
        actor: Actor,
        pred_id: int | None,
        desde: datetime | None,
        hasta: datetime | None,
        cursor: str | None,
    ) -> Pagina[e.Visita]:
        """Técnico: solo las suyas. Móvil ``HistoryRepository.getVisits``."""
        raise NoImplementado("Visitas")

    async def visita(self, actor: Actor, visi_id: UUID) -> e.VisitaDetalle:
        raise NoImplementado("Visita")

    async def lecturas(
        self,
        actor: Actor,
        lote_id: int | None,
        tram_id: int | None,
        desde: datetime | None,
        cursor: str | None,
    ) -> Pagina[e.Lectura]:
        raise NoImplementado("Lecturas")

    async def versiones_lectura(self, actor: Actor, registro_id: UUID) -> e.HistorialVersiones:
        raise NoImplementado("Versiones de lectura")

    async def aplicaciones(
        self, actor: Actor, lote_id: int | None, cursor: str | None
    ) -> Pagina[e.Aplicacion]:
        raise NoImplementado("Aplicaciones")

    async def cosechas(self, actor: Actor, lote_id: int | None, cursor: str | None) -> Pagina[e.Cosecha]:
        raise NoImplementado("Cosechas")

    async def actividad(
        self,
        actor: Actor,
        lote_id: int | None,
        tipo: str | None,
        linaje: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        cursor: str | None,
    ) -> Pagina[e.EntradaActividad]:
        """Historial unificado: web History/ProducerHistory/timeline, móvil getActivity/getLotHistory.
        Orden: t_observacion DESC (R-T1 invertido para pantalla)."""
        raise NoImplementado("Actividad")

    async def actividad_reciente(self, actor: Actor, lote_id: int) -> list[e.ActividadReciente]:
        raise NoImplementado("Actividad reciente")


# Columnas de dominio que el cliente puede aportar en una lectura (nombres de la BD).
_DATOS_LECTURA = frozenset(
    {
        "visi_id",
        "tram_id",
        "ubic_id",
        "lect_leida",
        "lect_especimenes",
        "lect_justificacion",
        "cond_id",
        "lect_fecha_cambio_cuerpo",
        "lect_fecha_cambio_atrayente",
        "atra_id",
        "lect_observaciones",
        "lect_precision_m",
        "lect_coincide_lote",
        "lect_motivo_correccion",
    }
)


def _instante(valor: object) -> datetime:
    if isinstance(valor, datetime):
        return valor
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))


def _linaje_de(actor: Actor) -> Linaje:
    return Linaje.AUTORREPORTE if actor.rol.value == "PRODUCTOR" else Linaje.INSPECCION_OFICIAL
