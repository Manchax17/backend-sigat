"""CONTRATO DE SINCRONIZACIÓN (Arquitectura §7.1, ADR-04, R-T1…R-T6).

La unidad de intercambio es la OPERACIÓN idempotente, no el registro. Un lote
puede aceptarse de forma PARCIAL: la respuesta es por operación, nunca todo o nada.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from sigat_api.compartido.esquemas import Esquema
from sigat_api.compartido.tipos import AccionSync, ClasificacionOperacion, EntidadSync, Linaje, ResultadoSync


class OperacionSync(Esquema):
    cliente_id: UUID = Field(
        ..., description="operacion_sync.oper_cliente_id. Con disp_id = clave de idempotencia."
    )
    entidad: EntidadSync
    accion: AccionSync
    registro_id: UUID = Field(
        ...,
        description=(
            "Identificador lógico (igual en todas las versiones): *_registro_id; visi_id para 'visita'; "
            "plal_sync_id para 'planificacion_lote'; evid_id para 'evidencia'."
        ),
    )
    version: int = Field(..., ge=1, description="1 = alta; n+1 = corrección/anulación.")
    linaje: Linaje | None = Field(None, description="Obligatorio salvo en 'visita' y 'planificacion_lote'.")
    t_observacion: datetime = Field(..., description="Con zona horaria. Inmutable entre versiones (ADR-02).")
    t_captura: datetime = Field(..., description="Reloj del dispositivo al guardar.")
    deriva_reloj_seg: int | None = Field(None, description="Deriva medida al iniciar la visita (R-T6).")
    confirma_deriva_reloj: bool = Field(
        False, description="El usuario confirmó enviar pese a la deriva (R-T6)."
    )
    hash_contenido: str = Field(
        ..., pattern=r"^[0-9a-f]{64}$", description="SHA-256 hex del JSON canónico RFC 8785 (ver huella.py)."
    )
    datos: dict[str, Any] = Field(
        ...,
        description=(
            "Columnas de dominio con su nombre en la BD (p. ej. lect_especimenes, tram_id, visi_id). "
            "El servidor IGNORA y rechaza: usua_id, disp_id, oper_id, *_t_sincronizacion."
        ),
    )


class LoteSincronizacion(Esquema):
    disp_id: UUID = Field(..., description="Debe coincidir con el dispositivo del token.")
    enviado_en: datetime = Field(..., description="Reloj del dispositivo al enviar (para medir deriva).")
    operaciones: list[OperacionSync] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Se procesan EN ORDEN: la visita antes que sus lecturas; el dato antes que su evidencia.",
    )


class ResultadoOperacion(Esquema):
    cliente_id: UUID
    oper_id: int | None
    resultado: ResultadoSync
    motivo: str | None = Field(
        None, description="Texto para el usuario cuando es revisar/rechazada/conflicto."
    )
    codigo: str | None = Field(None, description="Código estable (dominio.MotivoRechazo o del servidor).")
    registro_id: UUID
    version: int
    reproceso: Literal["encolado", "no_aplica"] = "no_aplica"
    fecha_efecto: datetime | None = Field(None, description="t_observacion que disparó la reemisión.")
    reintentable: bool = Field(
        ...,
        description=(
            "true SOLO ante errores técnicos (no se aplicó nada). revisar/rechazada/conflicto_contenido son "
            "DECISIONES de negocio: reintentarlas automáticamente es un bucle (Arquitectura §7.1)."
        ),
    )


class RespuestaLote(Esquema):
    resultados: list[ResultadoOperacion]
    cursor: str = Field(..., description="Instante del servidor al confirmar el lote.")
    servidor_utc: datetime


class OperacionPendiente(Esquema):
    """Operación recibida del usuario, con su clasificación y motivo."""

    oper_id: int
    cliente_id: UUID
    entidad: EntidadSync
    accion: AccionSync
    registro_id: UUID
    version: int
    clasificacion: ClasificacionOperacion
    motivo: str | None
    t_captura: datetime
    t_recepcion: datetime
    datos: dict[str, Any]
