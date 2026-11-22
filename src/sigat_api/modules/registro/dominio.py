"""MOTOR TEMPORAL (Arquitectura §4). Reglas puras, sin BD: es lo más probado del proyecto.

Único hogar de la regla del orden temporal (Arquitectura §9.2, regla 3). Ningún
otro módulo ordena, versiona ni decide si un dato es retroactivo.

Tres relojes:
* t_observacion  – cuándo se observó en el predio. Orden canónico. INMUTABLE entre versiones.
* t_captura      – cuándo se digitó en el dispositivo. Solo auditoría.
* t_sincronizacion – lo asigna el SERVIDOR (disparador). Nunca ordena el dominio.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sigat_api.compartido.tipos import AccionSync, Actor, ClasificacionOperacion, Linaje, RolCodigo


class MotivoRechazo(StrEnum):
    """Invariantes de Arquitectura §5.3 (mensajes al usuario en ``MENSAJES``)."""

    FECHA_FUTURA = "FECHA_FUTURA"
    FUERA_DE_PERIODO = "FUERA_DE_PERIODO"
    TRAMPA_NO_PERTENECE_AL_LOTE = "TRAMPA_NO_PERTENECE_AL_LOTE"
    VALOR_NO_VALIDO = "VALOR_NO_VALIDO"
    LOTE_INACTIVO = "LOTE_INACTIVO"
    VERSION_INVALIDA = "VERSION_INVALIDA"
    T_OBSERVACION_CAMBIADA = "T_OBSERVACION_CAMBIADA"
    REGISTRO_INEXISTENTE = "REGISTRO_INEXISTENTE"
    SIN_PERMISO_SOBRE_LOTE = "SIN_PERMISO_SOBRE_LOTE"
    LINAJE_NO_PERMITIDO = "LINAJE_NO_PERMITIDO"
    REFERENCIA_PENDIENTE = "REFERENCIA_PENDIENTE"


MENSAJES: dict[MotivoRechazo, str] = {
    MotivoRechazo.FECHA_FUTURA: "La fecha de medición no puede ser futura",
    MotivoRechazo.FUERA_DE_PERIODO: "La fecha no está dentro del periodo de la trampa",
    MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE: "La trampa no pertenece a este lote",
    MotivoRechazo.VALOR_NO_VALIDO: "Valor no válido",
    MotivoRechazo.LOTE_INACTIVO: "El lote no estaba activo en esa fecha",
    MotivoRechazo.VERSION_INVALIDA: "La versión no es la siguiente a la vigente",
    MotivoRechazo.T_OBSERVACION_CAMBIADA: "La fecha de observación no se puede cambiar; anule y cree un registro nuevo",
    MotivoRechazo.REGISTRO_INEXISTENTE: "El registro que se corrige no existe en el servidor",
    MotivoRechazo.SIN_PERMISO_SOBRE_LOTE: "No tiene acceso a este lote",
    MotivoRechazo.LINAJE_NO_PERMITIDO: "El rol no puede registrar datos con ese linaje",
    MotivoRechazo.REFERENCIA_PENDIENTE: "Falta un registro del que depende (p. ej. la visita); reenvíe en orden",
}


@dataclass(frozen=True, slots=True)
class EstadoPrevio:
    """Lo que el servidor sabe del registro lógico y de su serie ANTES de aplicar la operación."""

    version_vigente: int | None  # None = el registro lógico no existe
    t_observacion_original: datetime | None
    ultima_t_observacion_serie: datetime | None  # última observación sincronizada de la misma serie
    hay_solapamiento: bool  # R-T5: otra medición del mismo tipo cubre esa ventana (lote+trampa)


def decidir(
    accion: AccionSync, version: int, t_observacion: datetime, previo: EstadoPrevio
) -> tuple[ClasificacionOperacion, MotivoRechazo | None]:
    if accion == AccionSync.CREAR:
        if previo.version_vigente is not None:
            return ClasificacionOperacion.RECHAZADA, MotivoRechazo.VERSION_INVALIDA
        if previo.hay_solapamiento:
            return ClasificacionOperacion.REVISAR, None
        if (
            previo.ultima_t_observacion_serie is not None
            and t_observacion < previo.ultima_t_observacion_serie
        ):
            return ClasificacionOperacion.ALTA_RETROACTIVA, None
        return ClasificacionOperacion.ALTA_NUEVA, None

    if previo.version_vigente is None:
        return ClasificacionOperacion.RECHAZADA, MotivoRechazo.REGISTRO_INEXISTENTE
    if version != previo.version_vigente + 1:
        return ClasificacionOperacion.RECHAZADA, MotivoRechazo.VERSION_INVALIDA
    if previo.t_observacion_original is not None and t_observacion != previo.t_observacion_original:
        return ClasificacionOperacion.RECHAZADA, MotivoRechazo.T_OBSERVACION_CAMBIADA
    if previo.hay_solapamiento:
        return ClasificacionOperacion.REVISAR, None
    return ClasificacionOperacion.CORRECCION, None


def clasificar(
    accion: AccionSync, version: int, t_observacion: datetime, previo: EstadoPrevio
) -> ClasificacionOperacion:
    return decidir(accion, version, t_observacion, previo)[0]


def orden_canonico(
    t_observacion: datetime, t_captura: datetime, registro_id: str
) -> tuple[datetime, datetime, str]:
    """R-T1: t_observacion ascendente; desempata (t_captura, identificador). La llegada no participa."""
    return (t_observacion, t_captura, registro_id)


def linaje_de(actor: Actor) -> Linaje:
    return Linaje.AUTORREPORTE if actor.rol == RolCodigo.PRODUCTOR else Linaje.INSPECCION_OFICIAL
