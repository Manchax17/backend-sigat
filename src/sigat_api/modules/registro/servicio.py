from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sigat_api.compartido.contratos import OperacionCampo, ResultadoAplicacion
from sigat_api.compartido.errores import Conflicto, NoEncontrado, NoImplementado, ReglaNegocio
from sigat_api.compartido.esquemas import Pagina
from sigat_api.compartido.tipos import AccionSync, Actor, ClasificacionOperacion, EntidadSync, EstadoRevision
from sigat_api.infra.reloj import Reloj
from sigat_api.modules.registro import dominio
from sigat_api.modules.registro import esquemas as e
from sigat_api.modules.registro.cursor import TAMANO_PAGINA, codificar, decodificar
from sigat_api.modules.registro.dominio import MENSAJES, MotivoRechazo
from sigat_api.modules.registro.puertos import PuertoConfiguracion, PuertoOperaciones
from sigat_api.modules.registro.repositorio import RepositorioRegistro
from sigat_api.modules.registro.validador import ValidadorInvariantes

LIMITE_ACTIVIDAD_RECIENTE = 10


class ServicioRegistro:
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
            "lect_linaje": op.linaje or dominio.linaje_de(actor),
        }
        if fila.get("ubic_id") is None:
            fila["ubic_id"] = pertenencia.ubic_id
        fila["lect_anulada"] = op.accion == AccionSync.ANULAR
        if op.accion == AccionSync.ANULAR:
            fila["lect_motivo_anulacion"] = op.datos.get("lect_motivo_anulacion")
        else:
            fila.pop("lect_motivo_anulacion", None)

        await self._repo.insertar_version("lectura_trampa", fila)
        return ResultadoAplicacion(clasificacion)

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


# ------------------------------------------------------------------ revisión: reglas puras
# Transiciones permitidas de visi_estado_revision. 'aceptado' es final: una visita aceptada no se
# vuelve a resolver (si un dato estaba mal, se corrige o anula LA LECTURA, que es otra versión).
TRANSICIONES_REVISION: dict[EstadoRevision, frozenset[EstadoRevision]] = {
    EstadoRevision.PENDIENTE: frozenset({EstadoRevision.EN_REVISION, EstadoRevision.ACEPTADO}),
    EstadoRevision.EN_REVISION: frozenset({EstadoRevision.ACEPTADO}),
    EstadoRevision.ACEPTADO: frozenset(),
}


def validar_transicion(actual: EstadoRevision, destino: EstadoRevision) -> None:
    """Lanza 409 si el estado actual no permite pasar a ``destino``."""
    if destino in TRANSICIONES_REVISION[actual]:
        return
    if actual == EstadoRevision.ACEPTADO:
        raise Conflicto(
            "La visita ya fue aceptada; no se puede resolver de nuevo.", codigo="REVISION_YA_RESUELTA"
        )
    if actual == destino:
        raise Conflicto("La visita ya está en revisión.", codigo="REVISION_YA_EN_REVISION")
    raise Conflicto(
        f"No se puede pasar de '{actual}' a '{destino}'.", codigo="TRANSICION_REVISION_NO_PERMITIDA"
    )


def advertencias_revision(fila: dict[str, Any], umbral_deriva_seg: int) -> list[str]:
    """Avisos para quien revisa. No bloquean nada: el dato de campo nunca se descarta."""
    avisos: list[str] = []
    if fila.get("lote_sin_asignacion"):
        avisos.append("El técnico visitó un lote que no tenía asignado ese día.")
    if fila.get("trampa_fuera_del_lote"):
        avisos.append("Hay lecturas tomadas fuera del lote de la trampa.")
    deriva = fila.get("deriva_reloj_seg")
    if deriva is not None and abs(deriva) > umbral_deriva_seg:
        avisos.append(f"El reloj del dispositivo tenía una diferencia de {deriva} s con el servidor.")
    return avisos


# ------------------------------------------------------------------ construcción de esquemas
# Columnas que el repositorio trae solo para calcular advertencias (no son del contrato Visita).
_SOLO_REVISION = frozenset({"lote_sin_asignacion", "trampa_fuera_del_lote"})


def _visita(fila: dict[str, Any]) -> e.Visita:
    return e.Visita(**{k: v for k, v in fila.items() if k not in _SOLO_REVISION})


def _registro_revision(fila: dict[str, Any], umbral_deriva_seg: int) -> e.RegistroRevision:
    return e.RegistroRevision(
        visi_id=fila["visi_id"],
        usua_id=fila["usua_id"],
        tecnico=fila["tecnico"],
        pred_id=fila["pred_id"],
        pred_nombre=fila["pred_nombre"],
        observada_el=fila["t_inicio_obs"],
        recibida_el=fila["t_sincronizacion"],
        lotes=fila["lotes"],
        trampas_leidas=fila["trampas_leidas"],
        estado_revision=fila["estado_revision"],
        motivo_revision=fila["motivo_revision"],
        advertencias=advertencias_revision(fila, umbral_deriva_seg),
    )


def _accion(version: int, anulada: bool) -> AccionSync:
    """R-T3: la versión 1 es el alta; las siguientes, corrección o anulación."""
    if version == 1:
        return AccionSync.CREAR
    return AccionSync.ANULAR if anulada else AccionSync.CORREGIR


def _historial(registro_id: UUID, versiones: list[dict[str, Any]]) -> e.HistorialVersiones:
    return e.HistorialVersiones(
        entidad=EntidadSync.LECTURA_TRAMPA.value,
        registro_id=registro_id,
        t_observacion=versiones[0]["t_observacion"],
        versiones=[
            e.VersionRegistro(
                version=v["version"],
                accion=_accion(v["version"], v["anulada"]),
                anulada=v["anulada"],
                motivo=v["motivo"],
                autor=v["autor"],
                usua_id=v["usua_id"],
                t_sincronizacion=v["t_sincronizacion"],
                valores=v["valores"],
            )
            for v in versiones
        ],
    )


def _pagina[T](
    items: list[T], filas: list[dict[str, Any]], clave: Callable[[dict[str, Any]], str]
) -> Pagina[T]:
    """El repositorio pide ``TAMANO_PAGINA + 1`` filas: si llega la sobrante, hay página siguiente
    y el cursor es la clave de la última fila entregada."""
    if len(filas) > TAMANO_PAGINA:
        return Pagina[T](items=items[:TAMANO_PAGINA], siguiente=clave(filas[TAMANO_PAGINA - 1]))
    return Pagina[T](items=items, siguiente=None)


def _solo_propias(actor: Actor) -> bool:
    """El técnico consulta su propio trabajo; los demás roles, lo de sus lotes visibles."""
    return actor.rol.value == "TECNICO"


def _validar_rango(desde: datetime | None, hasta: datetime | None) -> None:
    if desde is not None and hasta is not None and desde > hasta:
        raise ReglaNegocio(
            "La fecha 'desde' no puede ser posterior a 'hasta'.", codigo="RANGO_FECHAS_INVALIDO"
        )


def _visita_no_encontrada() -> NoEncontrado:
    return NoEncontrado("La visita no existe o no es visible para usted.", codigo="VISITA_NO_ENCONTRADA")


def _lectura_no_encontrada() -> NoEncontrado:
    return NoEncontrado("La lectura no existe o no es visible para usted.", codigo="LECTURA_NO_ENCONTRADA")


class ServicioRevision:
    """Revisión del agrónomo/supervisor SOBRE LA VISITA (BD: visi_estado_revision).

    Web y móvil deben revisar lo mismo (CONCORDANCIA §4.2): la web muestra lecturas,
    pero la decisión 'aceptar' es de la visita; corregir/rechazar una lectura es una
    versión nueva de esa lectura (``ServicioRegistro``).

    Trazabilidad: cada decisión guarda quién (``visi_revisada_por``), cuándo (``visi_revisada_en``,
    reloj del servidor) y por qué (``visi_motivo_revision``); además ``trg_aud_visita`` deja el
    antes/después en la bitácora de auditoría.
    """

    def __init__(self, repo: RepositorioRegistro, reloj: Reloj, umbral_deriva_seg: int = 300) -> None:
        self._repo = repo
        self._reloj = reloj
        self._umbral_deriva_seg = umbral_deriva_seg

    async def listar(
        self, actor: Actor, estado: EstadoRevision | None, cursor: str | None
    ) -> Pagina[e.RegistroRevision]:
        """Sin ``estado``: lo que falta por resolver (pendiente y en_revision)."""
        filas = await self._repo.registros_por_revisar(
            actor.usua_id, estado.value if estado else None, decodificar(cursor), TAMANO_PAGINA + 1
        )
        items = [_registro_revision(f, self._umbral_deriva_seg) for f in filas]
        return _pagina(items, filas, lambda f: codificar(f["t_inicio_obs"], f["visi_id"]))

    async def obtener(self, actor: Actor, visi_id: UUID) -> e.RevisionDetalle:
        fila = await self._repo.registro_revision(actor.usua_id, visi_id)
        if fila is None:
            raise _visita_no_encontrada()
        return await self._detalle(fila)

    async def aceptar(self, actor: Actor, visi_id: UUID) -> e.RevisionDetalle:
        """pendiente|en_revision -> aceptado (UPDATE de las columnas de revisión de la visita).
        Las lecturas sin corrección ni anulación pasan a 'confirmado' en v_lectura_revision (la web
        'Confirmar' llama aquí). Ya aceptada -> 409 ``REVISION_YA_RESUELTA``.

        Se conserva el motivo de la observación previa, si la hubo: explica por qué estuvo en
        revisión antes de aceptarse."""
        fila = await self._repo.registro_revision(actor.usua_id, visi_id, bloquear=True)
        if fila is None:
            raise _visita_no_encontrada()
        validar_transicion(EstadoRevision(fila["estado_revision"]), EstadoRevision.ACEPTADO)
        await self._repo.actualizar_revision_visita(
            visi_id,
            EstadoRevision.ACEPTADO.value,
            fila["motivo_revision"],
            actor.usua_id,
            self._reloj.ahora(),
        )
        return await self.obtener(actor, visi_id)

    async def marcar_en_revision(
        self, actor: Actor, visi_id: UUID, s: e.ObservacionRevision
    ) -> e.RevisionDetalle:
        """pendiente -> en_revision con motivo. Ya en revisión o aceptada -> 409.
        El aviso al técnico corresponde al módulo de notificaciones (no forma parte de esta tarea)."""
        fila = await self._repo.registro_revision(actor.usua_id, visi_id, bloquear=True)
        if fila is None:
            raise _visita_no_encontrada()
        validar_transicion(EstadoRevision(fila["estado_revision"]), EstadoRevision.EN_REVISION)
        await self._repo.actualizar_revision_visita(
            visi_id,
            EstadoRevision.EN_REVISION.value,
            s.motivo.strip(),
            actor.usua_id,
            self._reloj.ahora(),
        )
        return await self.obtener(actor, visi_id)

    async def contar_por_revisar(self, actor: Actor) -> int:
        """Total de visitas pendientes de revisar en los predios del agronomo."""
        return await self._repo.contar_por_revisar(actor.usua_id)

    async def _detalle(self, fila: dict[str, Any]) -> e.RevisionDetalle:
        """Encabezado + última versión de cada lectura + historial de las que se corrigieron."""
        lecturas = await self._repo.lecturas_de_visita(fila["visi_id"])
        versiones = [
            _historial(
                lect["lect_registro_id"],
                await self._repo.versiones(EntidadSync.LECTURA_TRAMPA.value, lect["lect_registro_id"]),
            )
            for lect in lecturas
            if lect["lect_version"] > 1
        ]
        return e.RevisionDetalle(
            **_registro_revision(fila, self._umbral_deriva_seg).model_dump(),
            lecturas=[e.Lectura(**lect) for lect in lecturas],
            versiones=versiones,
        )


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
        _validar_rango(desde, hasta)
        filas = await self._repo.visitas(
            actor.usua_id,
            _solo_propias(actor),
            {"pred_id": pred_id, "desde": desde, "hasta": hasta},
            decodificar(cursor),
            TAMANO_PAGINA + 1,
        )
        items = [_visita(f) for f in filas]
        return _pagina(items, filas, lambda f: codificar(f["t_inicio_obs"], f["visi_id"]))

    async def visita(self, actor: Actor, visi_id: UUID) -> e.VisitaDetalle:
        fila = await self._repo.visita(actor.usua_id, _solo_propias(actor), visi_id)
        if fila is None:
            raise _visita_no_encontrada()
        lecturas = await self._repo.lecturas_de_visita(visi_id)
        return e.VisitaDetalle(
            **_visita(fila).model_dump(), lecturas=[e.Lectura(**lect) for lect in lecturas]
        )

    async def lecturas(
        self,
        actor: Actor,
        lote_id: int | None,
        tram_id: int | None,
        desde: datetime | None,
        cursor: str | None,
    ) -> Pagina[e.Lectura]:
        """Versión vigente de cada lectura (las anuladas no salen; su historial sí, en versiones)."""
        filas = await self._repo.lecturas(
            actor.usua_id,
            _solo_propias(actor),
            {"lote_id": lote_id, "tram_id": tram_id, "desde": desde},
            decodificar(cursor),
            TAMANO_PAGINA + 1,
        )
        items = [e.Lectura(**f) for f in filas]
        return _pagina(items, filas, lambda f: codificar(f["t_observacion"], f["lect_registro_id"]))

    async def versiones_lectura(self, actor: Actor, registro_id: UUID) -> e.HistorialVersiones:
        """RF-052: todas las versiones, de la original a la vigente; nunca se sobrescriben."""
        if not await self._repo.lectura_visible(actor.usua_id, _solo_propias(actor), registro_id):
            raise _lectura_no_encontrada()
        versiones = await self._repo.versiones(EntidadSync.LECTURA_TRAMPA.value, registro_id)
        if not versiones:
            raise _lectura_no_encontrada()
        return _historial(registro_id, versiones)

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
        Orden: t_observacion DESC (R-T1 invertido para pantalla). Lote no visible -> 404."""
        _validar_rango(desde, hasta)
        if lote_id is not None:
            await self._exigir_lote_visible(actor, lote_id)
        filtros: dict[str, Any] = {
            "lote_id": lote_id,
            "tipo": tipo,
            "linaje": str(linaje) if linaje is not None else None,
            "desde": desde,
            "hasta": hasta,
        }
        filas = await self._repo.actividad(actor.usua_id, filtros, decodificar(cursor), TAMANO_PAGINA + 1)
        items = [e.EntradaActividad(**f) for f in filas]
        return _pagina(items, filas, lambda f: codificar(f["t_observacion"], f["registro_id"], f["lote_id"]))

    async def actividad_reciente(self, actor: Actor, lote_id: int) -> list[e.ActividadReciente]:
        """Últimos eventos del lote (máximo ``LIMITE_ACTIVIDAD_RECIENTE``). Lote no visible -> 404."""
        await self._exigir_lote_visible(actor, lote_id)
        filas = await self._repo.actividad_reciente(actor.usua_id, lote_id, LIMITE_ACTIVIDAD_RECIENTE)
        return [e.ActividadReciente(**f) for f in filas]

    async def _exigir_lote_visible(self, actor: Actor, lote_id: int) -> None:
        if not await self._repo.lote_visible(actor.usua_id, lote_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")


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
