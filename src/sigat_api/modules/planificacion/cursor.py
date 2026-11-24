"""Cursor opaco de las listas de planificación (paginación por clave, no por desplazamiento).

Mismo diseño que el cursor de ``registro`` (los módulos no se importan entre sí): la lista se
ordena por una clave estable y el cursor guarda la clave de la última fila entregada; la página
siguiente pide lo que está estrictamente "después". Así una planificación creada mientras el
usuario pagina no desplaza ni repite filas (con OFFSET sí pasaría).

El contenido no es secreto (fechas e identificadores que el cliente ya recibió), pero el cliente
no debe construirlo: lo trata como texto opaco.
"""

import base64
import binascii
import json

from sigat_api.compartido.errores import ErrorDominio

TAMANO_PAGINA = 50

Parte = str | int


class CursorInvalido(ErrorDominio):
    estado_http = 400
    codigo = "CURSOR_INVALIDO"
    titulo = "El cursor de paginación no es válido"


def codificar(*partes: Parte) -> str:
    """Clave de la última fila entregada, en el mismo orden que el ``ORDER BY`` de la consulta."""
    return base64.urlsafe_b64encode(json.dumps(list(partes)).encode()).decode().rstrip("=")


def decodificar(cursor: str | None, partes: int) -> list[Parte] | None:
    """Devuelve la clave (``partes`` elementos) o None si no hay cursor.

    Un cursor manipulado o de otra lista -> 400 ``CURSOR_INVALIDO``.
    """
    if not cursor:
        return None
    try:
        relleno = "=" * (-len(cursor) % 4)
        clave = json.loads(base64.urlsafe_b64decode(cursor + relleno))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CursorInvalido("Pida de nuevo la primera página sin cursor.") from exc
    if (
        not isinstance(clave, list)
        or len(clave) != partes
        or not all(isinstance(p, str | int) and not isinstance(p, bool) for p in clave)
    ):
        raise CursorInvalido("Pida de nuevo la primera página sin cursor.")
    return clave
