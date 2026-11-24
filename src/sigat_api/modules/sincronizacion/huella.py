import hashlib
from datetime import UTC, datetime
from typing import Any

import rfc8785


def calcular(contenido: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(contenido)).hexdigest()


def instante_canonico(valor: datetime) -> str:
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def contenido_canonico(op: Any) -> dict[str, Any]:
    return {
        "entidad": str(op.entidad),
        "accion": str(op.accion),
        "registro_id": str(op.registro_id),
        "version": op.version,
        "linaje": str(op.linaje) if op.linaje else None,
        "t_observacion": instante_canonico(op.t_observacion),
        "datos": op.datos,
    }
