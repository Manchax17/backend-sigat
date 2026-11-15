"""Huella de contenido para la idempotencia (R-T4, RN-010).

Algoritmo (el MÓVIL debe implementar EXACTAMENTE el mismo):

    contenido = {
        "entidad": ..., "accion": ..., "registro_id": ..., "version": ...,
        "linaje": ..., "t_observacion": <ISO 8601 UTC con milisegundos y sufijo Z>,
        "datos": {...}
    }
    hash_contenido = sha256( JCS(contenido) ).hexdigest()   # JCS = RFC 8785

* NO entran: cliente_id, t_captura, deriva_reloj_seg, confirma_deriva_reloj
  (un reenvío con otra hora de captura sigue siendo el mismo contenido).
* Mismo (disp_id, cliente_id) + mismo hash  -> idempotente_repetida (recepcion_sync).
* Mismo (disp_id, cliente_id) + hash distinto -> conflicto_contenido (recepcion_sync), no se aplica.

Normalización del instante (decisión del Sprint 2): ``2026-11-14T10:00:00-05:00`` y
``2026-11-14T15:00:00Z`` producen el MISMO texto canónico ``2026-11-14T15:00:00.000Z``.
Así Python y Dart no dependen de cómo cada uno imprime la zona horaria. Los vectores
compartidos viven en ``tests/vectores_huella.json`` (backend) y ``test/vectores_huella.json``
(móvil) y deben ser idénticos.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

import rfc8785


def calcular(contenido: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(contenido)).hexdigest()


def instante_canonico(valor: datetime) -> str:
    """UTC, milisegundos y ``Z``: el mismo formato que ``DateTime.toUtc().toIso8601String()``."""
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def contenido_canonico(op: Any) -> dict[str, Any]:
    """Arma el diccionario del docstring a partir de ``OperacionSync``."""
    return {
        "entidad": str(op.entidad),
        "accion": str(op.accion),
        "registro_id": str(op.registro_id),
        "version": op.version,
        "linaje": str(op.linaje) if op.linaje else None,
        "t_observacion": instante_canonico(op.t_observacion),
        "datos": op.datos,
    }
