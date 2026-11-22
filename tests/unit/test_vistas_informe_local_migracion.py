"""Migración 0008 (vistas de informe local): coherencia sin base de datos."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

RAIZ = Path(__file__).resolve().parents[2]
SQL = RAIZ / "bd" / "sql"
MIGRACION = RAIZ / "migrations" / "versions" / "0008_vistas_informe_local.py"
VISTAS = (
    "v_ultima_sincronizacion",
    "v_operacion_confirmada",
    "v_mtd_por_lote_escenario",
    "v_resumen_productor",
)


def _cargar(ruta: Path, nombre: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None
    assert spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_cadena_de_revisiones() -> None:
    m = _cargar(MIGRACION, "migracion_0008")
    assert (m.revision, m.down_revision) == ("0008", "0007")
    assert m.ARCHIVO == SQL / "17_vistas_informe_local.sql"


def test_no_es_destructiva_y_esta_registrada() -> None:
    """Mismo chequeo que la CI (scripts/revisar_migraciones.py)."""
    revisor = _cargar(RAIZ / "scripts" / "revisar_migraciones.py", "revisar_migraciones")
    destructivas, problemas = revisor.revisar(
        ["migrations/versions/0008_vistas_informe_local.py", "bd/sql/17_vistas_informe_local.sql"]
    )
    assert destructivas == []
    assert problemas == []


def test_instalacion_por_psql_incluye_el_archivo_despues_de_la_validacion() -> None:
    """Sin esto, Alembic y psql dejarían esquemas distintos (tests/integracion/test_migraciones.py)."""
    lineas = [x.strip() for x in (SQL / "00_instalar_todo.sql").read_text(encoding="utf-8").splitlines()]
    assert lineas.index(r"\ir 17_vistas_informe_local.sql") == lineas.index(r"\ir 06_validacion.sql") + 1


def test_el_sql_solo_agrega_objetos() -> None:
    texto = (SQL / "17_vistas_informe_local.sql").read_text(encoding="utf-8")
    sentencias = re.findall(r"^\s*(CREATE OR REPLACE (?:VIEW|FUNCTION)|COMMENT ON)\b", texto, re.M)
    assert sentencias
    sin_comentarios = re.sub(r"--.*", "", texto)
    # Los literales de cadena no son DML (p. ej. TG_OP = 'INSERT' dentro del disparador).
    sin_literales = re.sub(r"'([^']|'')*'", "''", sin_comentarios)
    assert not re.search(r"\b(DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE)\b", sin_literales)


def test_el_backend_nunca_lee_las_vistas_sin_filtrar() -> None:
    """Las vistas devuelven todas las filas. El código debe consultar solo con el filtro del token."""
    usos = []
    for archivo in (RAIZ / "src").rglob("*.py"):
        for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            for vista in VISTAS:
                if vista in linea.replace("fn_" + vista.removeprefix("v_"), ""):
                    usos.append(f"{archivo.relative_to(RAIZ)}:{numero}")
    assert usos == []
