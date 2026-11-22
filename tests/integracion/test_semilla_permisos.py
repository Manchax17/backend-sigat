"""Semilla de permiso / rol_permiso (migración 0006) contra PostgreSQL REAL.

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Cada prueba usa bases TEMPORALES que se borran.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from sigat_api.modules.identidad.permisos import PERMISOS

pytestmark = pytest.mark.integracion

SQL_SEMILLA = Path(__file__).resolve().parents[2] / "bd" / "sql" / "15_semilla_permisos.sql"
PARES = """
SELECT p.perm_codigo, r.rol_codigo
  FROM sigat.rol_permiso rp
  JOIN sigat.permiso p ON p.perm_id = rp.perm_id
  JOIN sigat.rol r ON r.rol_id = rp.rol_id
"""
ESPERADO = {(codigo, rol.value) for codigo, roles in PERMISOS.items() for rol in roles}
CONTEO = "SELECT (SELECT count(*) FROM sigat.permiso), (SELECT count(*) FROM sigat.rol_permiso)"


def _ejecutar(h: SimpleNamespace, nombre: str, sql: str) -> None:
    async def _correr() -> None:
        con = await asyncpg.connect(str(h.url(nombre)).replace("postgresql+asyncpg://", "postgresql://", 1))
        try:
            async with con.transaction():
                await con.execute(sql)
        finally:
            await con.close()

    asyncio.run(_correr())


def _pares(h: SimpleNamespace, nombre: str) -> set[tuple[object, ...]]:
    return set(h.consulta(nombre, PARES))


def _version(h: SimpleNamespace, nombre: str) -> list[tuple[object, ...]]:
    return h.consulta(nombre, "SELECT version_num FROM api.alembic_version")


def test_instalacion_limpia_siembra_exactamente_la_matriz_de_permisos_py(
    bd_limpia: str, h: SimpleNamespace
) -> None:
    assert h.consulta(bd_limpia, CONTEO) == [(25, 54)]
    assert {c for (c,) in h.consulta(bd_limpia, "SELECT perm_codigo FROM sigat.permiso")} == set(PERMISOS)
    assert _pares(h, bd_limpia) == ESPERADO
    assert _version(h, bd_limpia) == [("0008",)]


def test_rol_permiso_por_rol_coincide_con_permisos_py(bd_limpia: str, h: SimpleNamespace) -> None:
    """Comprobación rol a rol (más legible si alguna vez falla)."""
    por_rol: dict[str, set[str]] = {}
    for codigo, rol in _pares(h, bd_limpia):
        por_rol.setdefault(str(rol), set()).add(str(codigo))
    esperado: dict[str, set[str]] = {}
    for codigo, roles in PERMISOS.items():
        for rol in roles:
            esperado.setdefault(rol.value, set()).add(codigo)
    assert por_rol == esperado
    assert {r: len(c) for r, c in por_rol.items()} == {
        "ADMINISTRADOR": 15,
        "AGRONOMO_SUPERVISOR": 18,
        "TECNICO": 10,
        "PRODUCTOR": 11,
    }
    assert "visitas.consultar" not in por_rol["PRODUCTOR"]


def test_cada_par_queda_en_la_bitacora_sin_usuario(bd_limpia: str, h: SimpleNamespace) -> None:
    assert h.consulta(
        bd_limpia,
        "SELECT count(*), count(usua_id) FROM sigat.bitacora_auditoria "
        "WHERE bita_tabla = 'rol_permiso' AND bita_accion = 'INSERT'",
    ) == [(54, 0)]


def test_reaplicar_no_duplica_y_el_downgrade_no_borra(h: SimpleNamespace) -> None:
    nombre = h.crear_bd()
    try:
        assert h.alembic(nombre, "upgrade", "head").returncode == 0
        _ejecutar(
            h, nombre, SQL_SEMILLA.read_text(encoding="utf-8")
        )  # base instalada con 00_instalar_todo.sql
        assert h.consulta(nombre, CONTEO) == [(25, 54)]
        r = h.alembic(nombre, "downgrade", "0005")
        assert r.returncode == 0, r.stderr
        assert h.consulta(nombre, CONTEO) == [(25, 54)]
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        assert h.consulta(nombre, CONTEO) == [(25, 54)]
        assert _pares(h, nombre) == ESPERADO
        assert _version(h, nombre) == [("0008",)]
    finally:
        h.borrar_bd(nombre)


@pytest.mark.parametrize(
    ("previo", "mensajes"),
    [
        pytest.param(
            "INSERT INTO sigat.permiso (perm_codigo, perm_descripcion) VALUES ('predio.leer', 'Consultar predios')",
            ["permiso no esperado: predio.leer", "falta el permiso: usuarios.administrar"],
            id="permiso_ajeno_a_la_api",
        ),
        pytest.param(
            "INSERT INTO sigat.permiso (perm_codigo, perm_descripcion) VALUES ('predios.consultar', 'Otra descripción')",
            [
                "permiso no esperado: predios.consultar (Otra descripción)",
                "falta el permiso: predios.consultar",
            ],
            id="misma_clave_otra_descripcion",
        ),
        pytest.param(
            "INSERT INTO sigat.permiso (perm_codigo, perm_descripcion) VALUES ('visitas.consultar', 'Consultar visitas');"
            "INSERT INTO sigat.rol_permiso (rol_id, perm_id) SELECT r.rol_id, p.perm_id FROM sigat.rol r, sigat.permiso p "
            "WHERE r.rol_codigo = 'PRODUCTOR' AND p.perm_codigo = 'visitas.consultar'",
            ["par no esperado: visitas.consultar -> PRODUCTOR", "falta el par: visitas.consultar -> TECNICO"],
            id="par_no_autorizado_al_productor",
        ),
    ],
)
def test_datos_previos_distintos_detienen_la_migracion_sin_tocar_nada(
    h: SimpleNamespace, previo: str, mensajes: list[str]
) -> None:
    nombre = h.crear_bd()
    try:
        assert h.alembic(nombre, "upgrade", "0005").returncode == 0
        _ejecutar(h, nombre, previo)
        permisos_antes = h.consulta(
            nombre, "SELECT perm_codigo, perm_descripcion FROM sigat.permiso ORDER BY 1"
        )
        pares_antes = _pares(h, nombre)
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode != 0
        assert "NO coinciden con la matriz de permisos.py" in r.stderr
        for mensaje in mensajes:
            assert mensaje in r.stderr, mensaje
        assert (
            h.consulta(nombre, "SELECT perm_codigo, perm_descripcion FROM sigat.permiso ORDER BY 1")
            == permisos_antes
        )
        assert _pares(h, nombre) == pares_antes  # nada completado, modificado ni borrado
        assert _version(h, nombre) == [("0005",)]
    finally:
        h.borrar_bd(nombre)
