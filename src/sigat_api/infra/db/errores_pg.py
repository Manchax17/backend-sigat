"""Traducción de errores de PostgreSQL a errores de dominio.

La BD SIGAT hace cumplir muchas reglas con CHECK, UNIQUE, EXCLUDE y disparadores que
lanzan ``RAISE EXCEPTION`` con mensajes en español. El backend valida ANTES (mensajes
claros), pero si la BD rechaza, se traduce aquí y nunca se filtra el texto crudo del
controlador al cliente (SEGURIDAD S-02 del móvil): solo el mensaje del disparador, que la BD
escribe para personas, o un texto fijo por restricción.
"""

from typing import Any

from sqlalchemy.exc import DBAPIError

from sigat_api.compartido.errores import Conflicto, ErrorDominio, Prohibido, ReglaNegocio

# SQLSTATE -> (clase de error, código estable)
_MAPA: dict[str, tuple[type[ErrorDominio], str]] = {
    "23505": (Conflicto, "DUPLICADO"),  # unique_violation
    "23P01": (Conflicto, "SOLAPAMIENTO_VIGENCIA"),  # exclusion_violation (EXCLUDE gist)
    "23503": (ReglaNegocio, "REFERENCIA_INVALIDA"),  # foreign_key_violation
    "23514": (ReglaNegocio, "RESTRICCION_INCUMPLIDA"),  # check_violation
    "23502": (ReglaNegocio, "CAMPO_OBLIGATORIO"),  # not_null_violation
    "23000": (ReglaNegocio, "REGLA_BD"),  # integrity_constraint_violation (RAISE de disparadores)
    "P0001": (ReglaNegocio, "REGLA_BD"),  # raise_exception sin ERRCODE
    "42501": (Prohibido, "PERMISO_BD"),  # insufficient_privilege
}

# Restricción -> (clase, código, mensaje para personas). Se amplía al implementar cada módulo.
_RESTRICCIONES: dict[str, tuple[type[ErrorDominio], str, str]] = {
    "ux_usuario_usuario_minusculas": (Conflicto, "USUARIO_DUPLICADO", "Ese nombre de usuario ya existe."),
    "usuario_usua_usuario_key": (Conflicto, "USUARIO_DUPLICADO", "Ese nombre de usuario ya existe."),
    "ux_persona_correo_minusculas": (Conflicto, "CORREO_DUPLICADO", "Ese correo ya está registrado."),
    "persona_pkey": (Conflicto, "CEDULA_DUPLICADA", "Ya existe una persona con esa cédula."),
    "usuario_pers_cc_key": (Conflicto, "CEDULA_DUPLICADA", "Esa cédula ya tiene un usuario."),
    "persona_correo_formato": (ReglaNegocio, "CORREO_INVALIDO", "El correo no tiene un formato válido."),
    "persona_telefono_formato": (
        ReglaNegocio,
        "TELEFONO_INVALIDO",
        "El teléfono debe tener de 7 a 15 dígitos (puede empezar con +).",
    ),
    "usuario_usuario_formato": (
        ReglaNegocio,
        "USUARIO_INVALIDO",
        "El usuario debe tener de 3 a 64 caracteres: letras, números, punto, guion, guion bajo o @.",
    ),
    # trampas y su ubicación
    "trampa_tram_codigo_key": (Conflicto, "CODIGO_TRAMPA_DUPLICADO", "Duplicidad de código de trampa."),
    "trampa_codigo_formato": (
        ReglaNegocio,
        "CODIGO_TRAMPA_INVALIDO",
        "El código debe tener el formato Divipola-ruta-número, por ejemplo 54518-602-001",
    ),
    "ubicacion_trampa_sin_solape": (
        Conflicto,
        "UBICACION_SOLAPADA",
        "La trampa ya tiene una ubicación en ese periodo.",
    ),
    "trampa_atra_id_fkey": (ReglaNegocio, "ATRAYENTE_INVALIDO", "El atrayente no existe."),
    "trampa_esta_id_fkey": (ReglaNegocio, "ESTADO_INVALIDO", "El estado no existe."),
    "trampa_tram_tecnico_estado_id_fkey": (ReglaNegocio, "TECNICO_INVALIDO", "El técnico no existe."),
    "trampa_tram_tecnico_actualizacion_id_fkey": (
        ReglaNegocio,
        "TECNICO_INVALIDO",
        "El técnico no existe.",
    ),
    "ubicacion_trampa_tecn_id_fkey": (ReglaNegocio, "TECNICO_INVALIDO", "El técnico no existe."),
    "ubicacion_trampa_vigi_id_fkey": (ReglaNegocio, "VIGILANCIA_INVALIDA", "La vigilancia no existe."),
    "ubicacion_trampa_vigencia_valida": (
        ReglaNegocio,
        "UBICACION_FECHA_INVALIDA",
        "La nueva ubicación debe empezar después de la ubicación vigente.",
    ),
    "trampa_tram_tecnico_registro_id_fkey": (ReglaNegocio, "TECNICO_INVALIDO", "El técnico no existe."),
    "centro_acopio_cent_descripcion_key": (
        Conflicto,
        "DUPLICADO",
        "Ya existe un centro de acopio con ese nombre.",
    ),
    # P-04 (bd/sql/16_regla_cosecha_abierta.sql): lo lanza trg_cosecha_una_abierta con SQLSTATE 23505.
    "cosecha_una_abierta_por_lote": (
        Conflicto,
        "COSECHA_ABIERTA_EXISTENTE",
        "El lote ya tiene una cosecha abierta: ciérrela o anúlela antes de registrar otra.",
    ),
}

# Disparadores cuyo mensaje ya está escrito para personas (se muestra tal cual).
_SQLSTATE_CON_MENSAJE = {"23000", "P0001"}


def _error_postgres(error: DBAPIError) -> Any:
    """La excepción original de asyncpg (tiene ``sqlstate``, ``constraint_name`` y ``message``)."""
    orig = getattr(error, "orig", None)
    # Primero la de asyncpg (``__cause__``): el adaptador de SQLAlchemy no trae constraint_name.
    for candidato in (getattr(orig, "__cause__", None), orig):
        if candidato is not None and getattr(candidato, "sqlstate", None):
            return candidato
    return orig


def traducir(error: DBAPIError) -> ErrorDominio:
    """Convierte el error del controlador en un ``ErrorDominio`` con mensaje apto para personas.
    El detalle técnico completo se registra solo en el log (``api/errores.py``) con la traza."""
    pg = _error_postgres(error)
    sqlstate = getattr(pg, "sqlstate", None) or ""
    restriccion = getattr(pg, "constraint_name", None)
    if restriccion and restriccion in _RESTRICCIONES:
        clase, codigo, mensaje = _RESTRICCIONES[restriccion]
        return clase(mensaje, codigo=codigo)
    clase, codigo = _MAPA.get(sqlstate, (ReglaNegocio, "ERROR_BD"))
    if sqlstate in _SQLSTATE_CON_MENSAJE:
        mensaje = str(getattr(pg, "message", "") or "").strip()
        if mensaje:
            return clase(mensaje, codigo=codigo)
    return clase("La base de datos rechazó la operación.", codigo=codigo)
