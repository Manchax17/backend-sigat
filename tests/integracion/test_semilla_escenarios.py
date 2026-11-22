"""Semilla provisional de ``escenario_trampeo`` (migración 0005) contra PostgreSQL REAL.

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Cada prueba usa bases TEMPORALES que se borran.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

pytestmark = pytest.mark.integracion

SQL_SEMILLA = Path(__file__).resolve().parents[2] / "bd" / "sql" / "14_semilla_escenarios_provisionales.sql"
FUENTE = (
    "PROVISIONAL: RN-002, Tabla 1 OIEA 2005 (informe de consumo de datos §10.1). "
    "Pendiente de validación institucional ICA/Agronomía (P-02)."
)
ESPERADO = [
    ("Área infestada", 1, "1.0000", None, FUENTE),
    ("Supresión", 2, "0.1000", "1.0000", FUENTE),
    ("Erradicación", 3, "0.0000", "0.1000", FUENTE),
    ("Exclusión", 4, "0.0000", "0.0000", FUENTE),
]
ESCENARIOS = (
    "SELECT esce_descripcion, esce_orden, esce_mtd_desde_defecto::text, esce_mtd_hasta_defecto::text, esce_fuente "
    "FROM sigat.escenario_trampeo ORDER BY esce_orden, esce_descripcion"
)


def _ejecutar(h: SimpleNamespace, nombre: str, sql: str) -> None:
    async def _correr() -> None:
        con = await asyncpg.connect(str(h.url(nombre)).replace("postgresql+asyncpg://", "postgresql://", 1))
        try:
            async with con.transaction():
                await con.execute(sql)
        finally:
            await con.close()

    asyncio.run(_correr())


def _version(h: SimpleNamespace, nombre: str) -> list[tuple[object, ...]]:
    return h.consulta(nombre, "SELECT version_num FROM api.alembic_version")


def test_instalacion_limpia_siembra_los_4_escenarios_provisionales(
    bd_limpia: str, h: SimpleNamespace
) -> None:
    assert h.consulta(bd_limpia, ESCENARIOS) == ESPERADO
    assert _version(h, bd_limpia) == [("0008",)]


def test_reaplicar_no_duplica(h: SimpleNamespace) -> None:
    """Ejecutar el SQL otra vez (base instalada con 00_instalar_todo.sql) y bajar/subir la migración
    deja exactamente los mismos 4 escenarios; el downgrade no borra filas."""
    nombre = h.crear_bd()
    try:
        assert h.alembic(nombre, "upgrade", "head").returncode == 0
        _ejecutar(h, nombre, SQL_SEMILLA.read_text(encoding="utf-8"))
        assert h.consulta(nombre, ESCENARIOS) == ESPERADO
        r = h.alembic(nombre, "downgrade", "0004")
        assert r.returncode == 0, r.stderr
        assert h.consulta(nombre, ESCENARIOS) == ESPERADO  # el downgrade no borra
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        assert h.consulta(nombre, ESCENARIOS) == ESPERADO
        assert _version(h, nombre) == [("0008",)]
    finally:
        h.borrar_bd(nombre)


@pytest.mark.parametrize(
    ("previo", "mensajes"),
    [
        pytest.param(
            "INSERT INTO sigat.escenario_trampeo (esce_descripcion, esce_orden) VALUES ('Oficial', 1)",
            ["no esperado: Oficial", "falta: Área infestada", "falta: Exclusión"],
            id="carga_distinta",
        ),
        pytest.param(
            "INSERT INTO sigat.escenario_trampeo (esce_descripcion, esce_orden, esce_mtd_desde_defecto, "  # noqa: S608 (constante de la prueba)
            "esce_mtd_hasta_defecto, esce_fuente) VALUES "
            f"('Área infestada', 1, 1.0000, NULL, '{FUENTE}'), ('Supresión', 2, 0.1000, 1.0000, '{FUENTE}')",
            ["falta: Erradicación", "falta: Exclusión"],
            id="carga_parcial",
        ),
        pytest.param(
            "INSERT INTO sigat.escenario_trampeo (esce_descripcion, esce_orden, esce_mtd_desde_defecto, "
            "esce_mtd_hasta_defecto, esce_fuente) VALUES "
            "('Área infestada', 1, 1.0000, NULL, 'Resolución ICA'), ('Supresión', 2, 0.1000, 1.0000, 'Resolución ICA'), "
            "('Erradicación', 3, 0.0000, 0.1000, 'Resolución ICA'), ('Exclusión', 4, 0.0000, 0.0000, 'Resolución ICA')",
            ["no esperado: Supresión", "fuente Resolución ICA", "falta: Supresión"],
            id="carga_oficial_mismos_valores_otra_fuente",
        ),
    ],
)
def test_datos_previos_distintos_detienen_la_migracion_sin_tocar_nada(
    h: SimpleNamespace, previo: str, mensajes: list[str]
) -> None:
    nombre = h.crear_bd()
    try:
        assert h.alembic(nombre, "upgrade", "0004").returncode == 0
        _ejecutar(h, nombre, previo)
        antes = h.consulta(nombre, ESCENARIOS)
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode != 0
        assert "NO coinciden con la semilla provisional" in r.stderr
        for mensaje in mensajes:
            assert mensaje in r.stderr, mensaje
        assert h.consulta(nombre, ESCENARIOS) == antes  # nada completado, modificado ni borrado
        assert _version(h, nombre) == [("0004",)]
    finally:
        h.borrar_bd(nombre)
