"""Cursor opaco de las consultas de historial (paginación por clave, no por desplazamiento).

Las listas del historial se ordenan por instante descendente y se desempatan por identificador.
El cursor guarda la clave de la última fila entregada; la página siguiente pide lo que está
estrictamente "después" de esa clave. Así una fila que llega por sincronización mientras el
usuario pagina no desplaza ni repite filas (con OFFSET sí pasaría).

El contenido no es secreto (solo fechas e identificadores que el cliente ya recibió), pero el
cliente no debe construirlo: lo trata como texto opaco.
"""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from sigat_api.compartido.errores import ErrorDominio

TAMANO_PAGINA = 50


class CursorInvalido(ErrorDominio):
    estado_http = 400
    codigo = "CURSOR_INVALIDO"
    titulo = "El cursor de paginación no es válido"


def codificar(instante: datetime, identificador: UUID | str, desempate: int | None = None) -> str:
    """Clave de la última fila: (instante, identificador[, desempate])."""
    clave: list[object] = [instante.isoformat(), str(identificador)]
    if desempate is not None:
        clave.append(desempate)
    return base64.urlsafe_b64encode(json.dumps(clave).encode()).decode().rstrip("=")


def decodificar(cursor: str | None) -> tuple[datetime, UUID, int | None] | None:
    """Devuelve la clave o None si no hay cursor. Un cursor manipulado -> 400 ``CURSOR_INVALIDO``."""
    if not cursor:
        return None
    try:
        relleno = "=" * (-len(cursor) % 4)
        clave = json.loads(base64.urlsafe_b64decode(cursor + relleno))
        instante = datetime.fromisoformat(clave[0])
        identificador = UUID(clave[1])
        desempate = int(clave[2]) if len(clave) > 2 else None
    except (binascii.Error, ValueError, TypeError, IndexError, KeyError) as exc:
        raise CursorInvalido("Pida de nuevo la primera página sin cursor.") from exc
    return instante, identificador, desempate
