"""Migraciones: instalación desde cero, actualización de una base existente y compatibilidad.

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Cada prueba crea y borra sus propias bases.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integracion
SQL = Path(__file__).resolve().parents[2] / "bd" / "sql"

# Objetos que una instalación por psql (proyecto de BD) y una por Alembic deben tener iguales.
ESTRUCTURA = """
SELECT 'tabla', table_schema || '.' || table_name FROM information_schema.tables
 WHERE table_schema = 'sigat'
UNION ALL
SELECT 'columna', table_name || '.' || column_name || ':' || data_type || ':' || is_nullable
  FROM information_schema.columns WHERE table_schema = 'sigat'
UNION ALL
SELECT 'restriccion', conrelid::regclass || '.' || conname || ':' || contype::text
  FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'sigat'
UNION ALL
SELECT 'indice', indexname FROM pg_indexes WHERE schemaname = 'sigat'
UNION ALL
SELECT 'funcion', p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'sigat'
UNION ALL
SELECT 'disparador', tgrelid::regclass || '.' || tgname FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'sigat' AND NOT t.tgisinternal
UNION ALL
SELECT 'catalogo', 'categoria_notificacion.' || cate_codigo FROM sigat.categoria_notificacion
"""


def _estructura(h: SimpleNamespace, nombre: str) -> set[tuple[object, ...]]:
    return set(h.consulta(nombre, ESTRUCTURA))


def test_alembic_desde_cero_construye_el_mismo_esquema_que_psql(h: SimpleNamespace) -> None:
    por_alembic, por_psql = h.crear_bd(), h.crear_bd()
    try:
        r = h.alembic(por_alembic, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        r = h.psql(por_psql, "-f", "00_instalar_todo.sql")
        assert r.returncode == 0, r.stderr
        a, p = _estructura(h, por_alembic), _estructura(h, por_psql)
        assert len(a) > 300
        assert a == p, {"solo_alembic": sorted(a - p)[:10], "solo_psql": sorted(p - a)[:10]}
        assert h.consulta(por_psql, "SELECT count(*) FROM sigat.usuario") == [(0,)]
    finally:
        h.borrar_bd(por_alembic)
        h.borrar_bd(por_psql)


TABLAS_Y_VISTAS = "SELECT table_type, table_name FROM information_schema.tables WHERE table_schema='sigat'"
# Las únicas TABLAS o VISTAS que las migraciones del backend agregan a ``sigat`` sobre una base v9 (las
# demás siembran datos, crean el esquema ``api`` o, como la 0007, solo agregan funciones y disparadores;
# la 0008 agrega estas cuatro vistas de informe local).
VISTAS_0004 = {
    ("VIEW", "v_actividad_lote_detalle"),
    ("VIEW", "v_aplicacion_ultima_lote"),
    ("VIEW", "v_cosecha_lote_vigente"),
}
VISTAS_0008 = {
    ("VIEW", "v_ultima_sincronizacion"),
    ("VIEW", "v_operacion_confirmada"),
    ("VIEW", "v_mtd_por_lote_escenario"),
    ("VIEW", "v_resumen_productor"),
}


def test_actualizar_base_v9_existente_no_pierde_datos(h: SimpleNamespace) -> None:
    """Base instalada con el proyecto de BD (v9, antes de este backend) y con datos propios.

    Se comparan NOMBRES de tablas y vistas (no un conteo): ninguna tabla ni vista existente desaparece
    y lo único nuevo son las vistas de la 0004. ``information_schema.tables`` incluye las vistas."""
    nombre = h.crear_bd()
    try:
        archivos = ("01", "02", "03", "04", "08", "09", "10", "11")
        for prefijo in archivos:
            [archivo] = sorted(SQL.glob(prefijo + "_*.sql"))
            r = h.psql(nombre, "-f", archivo.name)
            assert r.returncode == 0, (archivo.name, r.stderr)
        # Datos que ya existían (el v9 sin la categoría de la v9.1).
        h.psql(
            nombre, "-c", "DELETE FROM sigat.categoria_notificacion WHERE cate_codigo = 'solicitudes_cuenta'"
        )
        h.psql(nombre, "-c", "INSERT INTO sigat.region (regi_descripcion) VALUES ('Región existente')")
        antes = h.consulta(nombre, "SELECT regi_id, regi_descripcion FROM sigat.region ORDER BY 1")
        objetos_antes = set(h.consulta(nombre, TABLAS_Y_VISTAS))

        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr

        assert h.consulta(nombre, "SELECT regi_id, regi_descripcion FROM sigat.region ORDER BY 1") == antes
        objetos_despues = set(h.consulta(nombre, TABLAS_Y_VISTAS))
        assert objetos_antes <= objetos_despues, {"desaparecen": sorted(objetos_antes - objetos_despues)}
        assert objetos_despues - objetos_antes == VISTAS_0004 | VISTAS_0008
        assert {o for o in objetos_despues if o[0] == "BASE TABLE"} == {
            o for o in objetos_antes if o[0] == "BASE TABLE"
        }
        assert h.consulta(
            nombre, "SELECT 1 FROM sigat.categoria_notificacion WHERE cate_codigo = 'solicitudes_cuenta'"
        ) == [(1,)]
        # Volver a aplicar no cambia nada.
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        assert h.consulta(nombre, "SELECT version_num FROM api.alembic_version") == [("0008",)]
    finally:
        h.borrar_bd(nombre)


def test_esquema_sigat_de_otra_version_se_rechaza_sin_tocarlo(h: SimpleNamespace) -> None:
    """Un ``sigat`` que no es v9 (p. ej. v8) no se modifica: Alembic se detiene y explica qué hacer."""
    nombre = h.crear_bd()
    try:
        h.psql(nombre, "-c", "CREATE SCHEMA sigat; CREATE TABLE sigat.usuario (usua_id int PRIMARY KEY)")
        h.psql(nombre, "-c", "INSERT INTO sigat.usuario VALUES (7)")
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode != 0
        assert "M8_v8_a_v9" in r.stderr
        assert h.consulta(nombre, "SELECT usua_id FROM sigat.usuario") == [(7,)]
        assert h.consulta(
            nombre, "SELECT count(*) FROM information_schema.tables WHERE table_schema='sigat'"
        ) == [(1,)]
    finally:
        h.borrar_bd(nombre)


def test_downgrade_no_borra_el_esquema_institucional(h: SimpleNamespace) -> None:
    """Bajar a 0002 conserva TODAS las tablas y vistas institucionales. Lo único que desaparece son
    las vistas que agregan la 0004 y la 0008 (sus downgrades eliminan solo sus propios objetos)."""
    nombre = h.crear_bd()
    try:
        assert h.alembic(nombre, "upgrade", "head").returncode == 0
        antes = set(h.consulta(nombre, TABLAS_Y_VISTAS))
        r = h.alembic(nombre, "downgrade", "0002")
        assert r.returncode == 0, r.stderr
        despues = set(h.consulta(nombre, TABLAS_Y_VISTAS))
        assert despues <= antes
        assert antes - despues == VISTAS_0004 | VISTAS_0008
        assert sum(1 for tipo, _ in despues if tipo == "BASE TABLE") == 74
    finally:
        h.borrar_bd(nombre)
