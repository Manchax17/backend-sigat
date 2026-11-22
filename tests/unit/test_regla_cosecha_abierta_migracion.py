"""Migración 0007 (regla P-04, una sola cosecha abierta por lote): coherencia sin base de datos."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy.exc import DBAPIError

from sigat_api.compartido.errores import Conflicto
from sigat_api.infra.db.errores_pg import traducir

RAIZ = Path(__file__).resolve().parents[2]
SQL = RAIZ / "bd" / "sql"
ARCHIVO_SQL = SQL / "16_regla_cosecha_abierta.sql"
MIGRACION = RAIZ / "migrations" / "versions" / "0007_regla_cosecha_abierta.py"


def _cargar(ruta: Path, nombre: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None
    assert spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _sin_comentarios(texto: str) -> str:
    return re.sub(r"--.*", "", texto)


def test_cadena_de_revisiones() -> None:
    m = _cargar(MIGRACION, "migracion_0007")
    assert (m.revision, m.down_revision) == ("0007", "0006")
    assert m.ARCHIVO == ARCHIVO_SQL


def test_es_la_unica_cabeza() -> None:
    """Ninguna otra revisión cuelga de la 0006 (evita dos cabezas de Alembic)."""
    hijos = []
    for archivo in (RAIZ / "migrations" / "versions").glob("*.py"):
        m = _cargar(archivo, "rev_" + archivo.stem)
        if m.down_revision == "0006":
            hijos.append(m.revision)
    assert hijos == ["0007"]


def test_no_es_destructiva_y_esta_registrada() -> None:
    """Mismo chequeo que la CI (scripts/revisar_migraciones.py)."""
    revisor = _cargar(RAIZ / "scripts" / "revisar_migraciones.py", "revisar_migraciones")
    destructivas, problemas = revisor.revisar(
        ["migrations/versions/0007_regla_cosecha_abierta.py", "bd/sql/16_regla_cosecha_abierta.sql"]
    )
    assert destructivas == []
    assert problemas == []


def test_instalacion_por_psql_incluye_el_archivo_despues_de_la_semilla_de_permisos() -> None:
    """Sin esto, Alembic y psql dejarían esquemas distintos (tests/integracion/test_migraciones.py)."""
    lineas = [x.strip() for x in (SQL / "00_instalar_todo.sql").read_text(encoding="utf-8").splitlines()]
    assert (
        lineas.index(r"\ir 16_regla_cosecha_abierta.sql") == lineas.index(r"\ir 15_semilla_permisos.sql") + 1
    )
    assert lineas.index(r"\ir 16_regla_cosecha_abierta.sql") < lineas.index(r"\ir 06_validacion.sql")


def test_el_sql_solo_agrega_la_funcion_y_el_disparador() -> None:
    """Fuera de los cuerpos ``$$ … $$`` no hay DDL que cambie tablas ni DML que toque filas."""
    texto = _sin_comentarios(ARCHIVO_SQL.read_text(encoding="utf-8"))
    fuera = re.sub(r"\$\$.*?\$\$", "$$", texto, flags=re.S)
    assert re.findall(r"^\s*(CREATE OR REPLACE (?:FUNCTION|TRIGGER)|COMMENT ON|DO|SET)\b", fuera, re.M)
    assert not re.search(r"\b(DROP|ALTER|TRUNCATE|CREATE\s+(?:TABLE|INDEX|UNIQUE))\b", fuera)
    # Ninguna sentencia, fuera o dentro de las funciones, modifica filas (``BEFORE INSERT ON`` es solo
    # el evento del disparador).
    assert not re.search(MODIFICA_FILAS, texto)


MODIFICA_FILAS = re.compile(
    r"\bINSERT\s+INTO\b|\bUPDATE\s+[\w.\"]+\s+SET\b|\bDELETE\s+FROM\b|\bTRUNCATE\b", re.I
)


def test_el_error_de_la_bd_usa_la_restriccion_que_traduce_la_api() -> None:
    texto = ARCHIVO_SQL.read_text(encoding="utf-8")
    assert "ERRCODE = 'unique_violation'" in texto
    assert "CONSTRAINT = 'cosecha_una_abierta_por_lote'" in texto
    assert "pg_advisory_xact_lock" in texto


def _error(sqlstate: str, restriccion: str | None, mensaje: str = "texto crudo") -> DBAPIError:
    pg = SimpleNamespace(sqlstate=sqlstate, constraint_name=restriccion, message=mensaje)
    return DBAPIError("INSERT ...", None, pg)  # type: ignore[arg-type]


def test_errores_pg_traduce_p04_a_409_con_codigo_propio() -> None:
    err = traducir(
        _error("23505", "cosecha_una_abierta_por_lote", "El lote 7 ya tiene una cosecha abierta (registro x)")
    )
    assert isinstance(err, Conflicto)
    assert (err.estado_http, err.codigo) == (409, "COSECHA_ABIERTA_EXISTENTE")
    assert err.detalle == "El lote ya tiene una cosecha abierta: ciérrela o anúlela antes de registrar otra."


def test_errores_pg_sin_cambios_para_otros_duplicados() -> None:
    """La entrada nueva no cambia la traducción genérica de 23505."""
    err = traducir(_error("23505", "otra_restriccion"))
    assert isinstance(err, Conflicto)
    assert (err.estado_http, err.codigo) == (409, "DUPLICADO")
