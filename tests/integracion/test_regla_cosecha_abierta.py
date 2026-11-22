"""Regla P-04, una sola cosecha abierta por lote (migración 0007, ``bd/sql/16_regla_cosecha_abierta.sql``).

Contra PostgreSQL REAL. Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``); cada base es TEMPORAL
y se borra al final. Cada prueba usa sus propios lotes para no depender del orden de ejecución.

Abierta = la versión vigente (mayor ``cose_version``) sin ``cose_fecha_real`` y no anulada.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from sigat_api.compartido.errores import Conflicto
from sigat_api.infra.db.errores_pg import traducir

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[2]
ARCHIVO_SQL = RAIZ / "bd" / "sql" / "16_regla_cosecha_abierta.sql"
RESTRICCION = "cosecha_una_abierta_por_lote"
DISPOSITIVO = "00000000-0000-0000-0000-0000000000d1"
# Cada operación actualiza dispositivo.disp_ultima_sync (fn_dispositivo_sync, 04_funciones.sql): dos
# transacciones del MISMO dispositivo ya se serializan por esa fila. Las pruebas de concurrencia usan
# dos dispositivos para medir solo el bloqueo de P-04.
DISPOSITIVO_2 = "00000000-0000-0000-0000-0000000000d2"

BASE = """
SET search_path = sigat, public;
INSERT INTO region (regi_id, regi_descripcion) VALUES (1, 'Región de prueba');
INSERT INTO departamento (depa_id, depa_descripcion, regi_id, depa_codigo) VALUES (1, 'Depto', 1, '99');
INSERT INTO municipio (muni_id, muni_descripcion, depa_id, muni_codigo) VALUES (1, 'Muni', 1, '001');
INSERT INTO centro_acopio (cent_id, cent_descripcion, depa_id) VALUES (1, 'Centro', 1);
INSERT INTO persona (pers_cc, pers_nombre, pers_apellido) VALUES
  (1001, 'Adela', 'Admin'), (1002, 'Tomás', 'Técnico'), (2001, 'Ana', 'Productora');
INSERT INTO administrador (admi_id, pers_cc, cent_id) VALUES (1, 1001, 1);
INSERT INTO tecnico (tecn_id, pers_cc, cent_id, admi_id, tecn_fecha_registro, esta_id)
  VALUES (1, 1002, 1, 1, DATE '2026-01-01', (SELECT esta_id FROM estado_registro WHERE esta_descripcion = 'activo'));
INSERT INTO productor (pers_cc, prod_fecha_registro, prod_tecnico_registro_id, muni_id)
  VALUES (2001, DATE '2026-01-01', 1, 1);
INSERT INTO usuario (usua_id, pers_cc, usua_usuario, usua_requiere_reinicio, rol_id, esta_id)
SELECT 2, 2001, 'ana.prueba', true, r.rol_id, e.esta_id
  FROM rol r, estado_registro e WHERE r.rol_codigo = 'PRODUCTOR' AND e.esta_descripcion = 'activo';
INSERT INTO predio (pred_id, pred_nombre, muni_id, pred_area, pers_cc, pred_fecha_registro, pred_tecnico_registro_id)
  VALUES (1, 'Predio Ana', 1, 10, 2001, DATE '2026-01-01', 1);
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro, lote_tecnico_registro_id)
SELECT n, 'L' || n, 'Lote ' || n, 1, 1, DATE '2026-01-01', 1 FROM generate_series(101, 120) n;
INSERT INTO dispositivo (disp_id, disp_plataforma) VALUES ('00000000-0000-0000-0000-0000000000d1', 'android'), ('00000000-0000-0000-0000-0000000000d2', 'android');
"""

# Una versión de cosecha con su operación de sincronización (toda fila de dominio exige oper_id).
INSERTAR = """
WITH o AS (
  INSERT INTO sigat.operacion_sync (disp_id, oper_cliente_id, usua_id, oper_entidad, oper_accion, oper_registro_id,
                                    oper_version, oper_hash_contenido, oper_clasificacion, oper_t_captura)
  VALUES ($7::uuid, gen_random_uuid(), 2, 'cosecha',
          CASE WHEN $2::int = 1 THEN 'crear' ELSE 'corregir' END, $1::uuid, $2::int,
          md5(random()::text) || md5(random()::text),
          CASE WHEN $2::int = 1 THEN 'alta_nueva' ELSE 'correccion' END, TIMESTAMPTZ '2026-03-10 12:00-05')
  RETURNING oper_id)
INSERT INTO sigat.cosecha (cose_registro_id, cose_version, lote_id, cose_fecha_esperada, cose_volumen_esperado,
                           cose_fecha_real, cose_volumen_real, cose_volumen_uni_id, usua_id, disp_id, oper_id,
                           cose_t_observacion, cose_t_captura, cose_anulada, cose_motivo_anulacion)
SELECT $1::uuid, $2::int, $3::int, $4::date, 100, $5::date, CASE WHEN $5::date IS NULL THEN NULL ELSE 90 END,
       (SELECT unid_id FROM sigat.unidad_medida WHERE unid_simbolo = 'kg'), 2, $7::uuid, o.oper_id,
       TIMESTAMPTZ '2026-03-02 08:00-05', TIMESTAMPTZ '2026-03-02 08:05-05', $6::boolean,
       CASE WHEN $6::boolean THEN 'Registro equivocado' END
  FROM o
"""

# Dos altas abiertas del mismo lote en UNA sola sentencia INSERT ... SELECT (oper_id por fila).
OPERACION_TEMPORAL = """
CREATE FUNCTION pg_temp.op(p_registro uuid) RETURNS bigint LANGUAGE sql AS $$
  INSERT INTO sigat.operacion_sync (disp_id, oper_cliente_id, usua_id, oper_entidad, oper_accion, oper_registro_id,
                                    oper_version, oper_hash_contenido, oper_clasificacion, oper_t_captura)
  VALUES ('00000000-0000-0000-0000-0000000000d1', gen_random_uuid(), 2, 'cosecha', 'crear', p_registro, 1,
          md5(random()::text) || md5(random()::text), 'alta_nueva', TIMESTAMPTZ '2026-03-10 12:00-05')
  RETURNING oper_id $$
"""
DOS_ALTAS = """
INSERT INTO sigat.cosecha (cose_registro_id, cose_version, lote_id, cose_fecha_esperada, cose_volumen_esperado,
                           cose_volumen_uni_id, usua_id, disp_id, oper_id, cose_t_observacion, cose_t_captura)
SELECT v.reg, 1, 104, DATE '2026-12-01', 100, (SELECT unid_id FROM sigat.unidad_medida WHERE unid_simbolo = 'kg'),
       2, '00000000-0000-0000-0000-0000000000d1', pg_temp.op(v.reg), TIMESTAMPTZ '2026-03-02 08:00-05', TIMESTAMPTZ '2026-03-02 08:05-05'
  FROM (VALUES (1, $1::uuid), (2, $2::uuid)) v(orden, reg)
 ORDER BY v.orden
"""

ABIERTAS = """
SELECT c.cose_registro_id::text FROM sigat.cosecha c
 WHERE c.lote_id = $1 AND c.cose_fecha_real IS NULL AND NOT c.cose_anulada
   AND NOT EXISTS (SELECT 1 FROM sigat.cosecha s
                    WHERE s.cose_registro_id = c.cose_registro_id AND s.cose_version > c.cose_version)
"""


def _dsn(h: SimpleNamespace, nombre: str) -> str:
    return str(h.url(nombre)).replace("postgresql+asyncpg://", "postgresql://", 1)


def _correr[T](h: SimpleNamespace, nombre: str, trabajo: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
    async def _uno() -> T:
        con = await asyncpg.connect(_dsn(h, nombre))
        try:
            return await trabajo(con)
        finally:
            await con.close()

    return asyncio.run(_uno())


async def _cosecha(
    con: asyncpg.Connection,
    reg: uuid.UUID,
    ver: int,
    lote: int,
    *,
    cerrada: bool = False,
    anulada: bool = False,
    esperada: str = "2026-12-01",
    disp: str = DISPOSITIVO,
) -> None:
    await con.execute(
        INSERTAR,
        reg,
        ver,
        lote,
        _fecha(esperada),
        _fecha("2026-12-03") if cerrada else None,
        anulada,
        uuid.UUID(disp),
    )


def _fecha(texto: str) -> date:
    return date.fromisoformat(texto)


async def _abiertas(con: asyncpg.Connection, lote: int) -> list[str]:
    return sorted(f[0] for f in await con.fetch(ABIERTAS, lote))


def _nuevo() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="module")
def bd(h: SimpleNamespace) -> Iterator[str]:
    nombre = h.crear_bd()
    try:
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr

        async def _base(con: asyncpg.Connection) -> None:
            await con.execute(BASE)

        _correr(h, nombre, _base)
        yield nombre
    finally:
        h.borrar_bd(nombre)


# ------------------------------------------------------------------ regla básica


def test_segunda_cosecha_abierta_en_el_mismo_lote_se_rechaza(bd: str, h: SimpleNamespace) -> None:
    c1, c2, c3 = _nuevo(), _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, c1, 1, 101)
        with pytest.raises(asyncpg.UniqueViolationError) as e:
            await _cosecha(con, c2, 1, 101)
        assert e.value.constraint_name == RESTRICCION
        assert "P-04" in e.value.message
        # Otro lote no se ve afectado.
        await _cosecha(con, c3, 1, 102)
        assert await _abiertas(con, 101) == [str(c1)]
        assert await _abiertas(con, 102) == [str(c3)]
        # La fila rechazada no quedó (ni la operación de su CTE: la sentencia entera se revierte).
        assert await con.fetchval("SELECT count(*) FROM sigat.cosecha WHERE cose_registro_id = $1", c2) == 0

    _correr(h, bd, _t)


def test_cosecha_cerrada_o_anulada_desde_el_alta_no_cuenta(bd: str, h: SimpleNamespace) -> None:
    abierta, cerrada, anulada = _nuevo(), _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, abierta, 1, 103)
        await _cosecha(con, cerrada, 1, 103, cerrada=True)  # registro de una cosecha ya hecha
        await _cosecha(con, anulada, 1, 103, anulada=True)
        assert await _abiertas(con, 103) == [str(abierta)]

    _correr(h, bd, _t)


def test_dos_altas_abiertas_en_la_misma_sentencia_se_rechazan(bd: str, h: SimpleNamespace) -> None:
    """El disparador ve las filas que la misma sentencia insertó antes (como trg_cosecha_version)."""
    a, b = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await con.execute(OPERACION_TEMPORAL)
        with pytest.raises(asyncpg.UniqueViolationError) as e:
            await con.execute(DOS_ALTAS, a, b)
        assert e.value.constraint_name == RESTRICCION
        assert await _abiertas(con, 104) == []

    _correr(h, bd, _t)


# ------------------------------------------------------------------ versionamiento


def test_corregir_la_cosecha_abierta_se_permite(bd: str, h: SimpleNamespace) -> None:
    """Una corrección (versión n+1) de la cosecha abierta no abre otra: siempre se permite."""
    c = _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, c, 1, 105, esperada="2026-12-01")
        await _cosecha(con, c, 2, 105, esperada="2026-12-10")
        await _cosecha(con, c, 3, 105, esperada="2026-12-15")
        assert await _abiertas(con, 105) == [str(c)]
        assert (
            await con.fetchval(
                "SELECT cose_fecha_esperada::text FROM sigat.cosecha WHERE cose_registro_id = $1 AND cose_version = 3",
                c,
            )
            == "2026-12-15"
        )
        # Las versiones anteriores siguen intactas (append-only).
        assert await con.fetchval("SELECT count(*) FROM sigat.cosecha WHERE cose_registro_id = $1", c) == 3

    _correr(h, bd, _t)


def test_cerrar_la_abierta_permite_abrir_otra(bd: str, h: SimpleNamespace) -> None:
    c1, c2 = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, c1, 1, 106)
        await _cosecha(con, c1, 2, 106, cerrada=True)
        await _cosecha(con, c2, 1, 106)
        assert await _abiertas(con, 106) == [str(c2)]

    _correr(h, bd, _t)


def test_anular_la_abierta_permite_abrir_otra(bd: str, h: SimpleNamespace) -> None:
    c1, c2 = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, c1, 1, 107)
        await _cosecha(con, c1, 2, 107, anulada=True)
        await _cosecha(con, c2, 1, 107)
        assert await _abiertas(con, 107) == [str(c2)]

    _correr(h, bd, _t)


def test_reabrir_una_cerrada_se_rechaza_si_hay_otra_abierta(bd: str, h: SimpleNamespace) -> None:
    vieja, nueva = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, vieja, 1, 108)
        await _cosecha(con, vieja, 2, 108, cerrada=True)
        await _cosecha(con, nueva, 1, 108)
        # Corregir la cerrada quitándole lo real la volvería a abrir: hay otra abierta.
        with pytest.raises(asyncpg.UniqueViolationError) as e:
            await _cosecha(con, vieja, 3, 108)
        assert e.value.constraint_name == RESTRICCION
        # Una corrección que la mantiene cerrada sí se permite.
        await _cosecha(con, vieja, 3, 108, cerrada=True, esperada="2026-11-30")
        # Cerrada la otra, reabrir ya se permite.
        await _cosecha(con, nueva, 2, 108, cerrada=True)
        await _cosecha(con, vieja, 4, 108)
        assert await _abiertas(con, 108) == [str(vieja)]

    _correr(h, bd, _t)


def test_reactivar_una_anulada_se_rechaza_si_hay_otra_abierta(bd: str, h: SimpleNamespace) -> None:
    vieja, nueva = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, vieja, 1, 109)
        await _cosecha(con, vieja, 2, 109, anulada=True)
        await _cosecha(con, nueva, 1, 109)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _cosecha(con, vieja, 3, 109)
        assert await _abiertas(con, 109) == [str(nueva)]

    _correr(h, bd, _t)


def test_mover_una_abierta_a_un_lote_con_otra_abierta_se_rechaza(bd: str, h: SimpleNamespace) -> None:
    movida, residente = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, movida, 1, 110)
        await _cosecha(con, residente, 1, 111)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _cosecha(con, movida, 2, 111)  # corrección que la cambia de lote
        # A un lote sin abiertas sí se puede mover; el lote de origen queda libre.
        await _cosecha(con, movida, 2, 112)
        assert await _abiertas(con, 110) == []
        assert await _abiertas(con, 112) == [str(movida)]

    _correr(h, bd, _t)


def test_version_fuera_de_secuencia_la_rechaza_el_disparador_de_version(bd: str, h: SimpleNamespace) -> None:
    """P-04 no tapa el error de versión: una v3 sin v2 se rechaza con el mensaje de trg_cosecha_version."""
    c, otra = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        await _cosecha(con, otra, 1, 113)
        await _cosecha(con, c, 1, 113, cerrada=True)
        with pytest.raises(asyncpg.IntegrityConstraintViolationError) as e:
            await _cosecha(con, c, 3, 113)
        assert e.value.sqlstate == "23000"
        assert "Versión 3 inválida" in e.value.message

    _correr(h, bd, _t)


# ------------------------------------------------------------------ datos históricos


def test_datos_historicos_que_incumplen_se_conservan_y_se_pueden_corregir(
    bd: str, h: SimpleNamespace
) -> None:
    """Dos abiertas cargadas antes de la regla: se pueden corregir, cerrar y anular, pero no abrir una tercera."""
    a, b, tercera = _nuevo(), _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> None:
        async with con.transaction():  # simula datos anteriores a la 0007
            await con.execute("ALTER TABLE sigat.cosecha DISABLE TRIGGER trg_cosecha_una_abierta")
            await _cosecha(con, a, 1, 114)
            await _cosecha(con, b, 1, 114)
            await con.execute("ALTER TABLE sigat.cosecha ENABLE TRIGGER trg_cosecha_una_abierta")
        assert await _abiertas(con, 114) == sorted([str(a), str(b)])
        await _cosecha(con, a, 2, 114, esperada="2026-12-20")  # corrección: permitida
        with pytest.raises(asyncpg.UniqueViolationError):
            await _cosecha(con, tercera, 1, 114)
        await _cosecha(con, a, 3, 114, cerrada=True)
        with pytest.raises(asyncpg.UniqueViolationError):  # aún queda b abierta
            await _cosecha(con, tercera, 1, 114)
        await _cosecha(con, b, 2, 114, anulada=True)
        await _cosecha(con, tercera, 1, 114)
        assert await _abiertas(con, 114) == [str(tercera)]

    _correr(h, bd, _t)


# ------------------------------------------------------------------ concurrencia


def _dos_conexiones[T](
    h: SimpleNamespace, nombre: str, trabajo: Callable[[asyncpg.Connection, asyncpg.Connection], Awaitable[T]]
) -> T:
    async def _uno() -> T:
        a = await asyncpg.connect(_dsn(h, nombre))
        b = await asyncpg.connect(_dsn(h, nombre))
        try:
            return await trabajo(a, b)
        finally:
            await a.close()
            await b.close()

    return asyncio.run(_uno())


def test_dos_aperturas_simultaneas_en_el_mismo_lote_solo_una_gana(bd: str, h: SimpleNamespace) -> None:
    """A abre y no confirma; B intenta abrir en el mismo lote y ESPERA el bloqueo; A confirma y B se rechaza."""
    ca, cb = _nuevo(), _nuevo()

    async def _t(a: asyncpg.Connection, b: asyncpg.Connection) -> None:
        ta, tb = a.transaction(), b.transaction()
        await ta.start()
        await tb.start()
        await _cosecha(a, ca, 1, 115)
        intento_b = asyncio.create_task(_cosecha(b, cb, 1, 115, disp=DISPOSITIVO_2))
        await asyncio.sleep(0.7)
        assert not intento_b.done(), "B debió quedar esperando el bloqueo consultivo del lote"
        espera = await a.fetchval(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted AND pid = $1",
            b.get_server_pid(),
        )
        assert espera == 1
        await ta.commit()
        with pytest.raises(asyncpg.UniqueViolationError) as e:
            await intento_b
        assert e.value.constraint_name == RESTRICCION
        await tb.rollback()
        assert await _abiertas(a, 115) == [str(ca)]

    _dos_conexiones(h, bd, _t)


def test_si_la_primera_se_revierte_la_segunda_entra(bd: str, h: SimpleNamespace) -> None:
    ca, cb = _nuevo(), _nuevo()

    async def _t(a: asyncpg.Connection, b: asyncpg.Connection) -> None:
        ta, tb = a.transaction(), b.transaction()
        await ta.start()
        await tb.start()
        await _cosecha(a, ca, 1, 116)
        intento_b = asyncio.create_task(_cosecha(b, cb, 1, 116, disp=DISPOSITIVO_2))
        await asyncio.sleep(0.5)
        assert not intento_b.done()
        await ta.rollback()
        await intento_b
        await tb.commit()
        assert await _abiertas(a, 116) == [str(cb)]

    _dos_conexiones(h, bd, _t)


def test_aperturas_simultaneas_en_lotes_distintos_no_se_bloquean(bd: str, h: SimpleNamespace) -> None:
    ca, cb = _nuevo(), _nuevo()

    async def _t(a: asyncpg.Connection, b: asyncpg.Connection) -> None:
        ta, tb = a.transaction(), b.transaction()
        await ta.start()
        await tb.start()
        await _cosecha(a, ca, 1, 117)
        await asyncio.wait_for(_cosecha(b, cb, 1, 118, disp=DISPOSITIVO_2), timeout=5)
        await ta.commit()
        await tb.commit()
        assert await _abiertas(a, 117) == [str(ca)]
        assert await _abiertas(a, 118) == [str(cb)]

    _dos_conexiones(h, bd, _t)


# ------------------------------------------------------------------ traducción del error a la API


def test_el_error_real_se_traduce_a_409_cosecha_abierta_existente(bd: str, h: SimpleNamespace) -> None:
    c1, c2 = _nuevo(), _nuevo()

    async def _t(con: asyncpg.Connection) -> asyncpg.PostgresError:
        await _cosecha(con, c1, 1, 119)
        try:
            await _cosecha(con, c2, 1, 119)
        except asyncpg.UniqueViolationError as e:
            return e
        raise AssertionError("se esperaba el rechazo de P-04")

    original = _correr(h, bd, _t)
    err = traducir(DBAPIError("INSERT INTO sigat.cosecha ...", None, original))
    assert isinstance(err, Conflicto)
    assert (err.estado_http, err.codigo) == (409, "COSECHA_ABIERTA_EXISTENTE")
    assert "registro" not in err.detalle  # no expone identificadores internos


# ------------------------------------------------------------------ migración


TABLA_COSECHA = "SELECT md5(string_agg(c::text, '|' ORDER BY cose_id)) FROM sigat.cosecha c"
OBJETOS = """
SELECT 'funcion', p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'sigat' AND p.proname = 'fn_cosecha_una_abierta'
UNION ALL
SELECT 'disparador', t.tgname FROM pg_trigger t WHERE t.tgrelid = 'sigat.cosecha'::regclass AND NOT t.tgisinternal
"""
DISPARADORES_PREVIOS = {
    ("disparador", "trg_aud_cosecha"),
    ("disparador", "trg_cosecha_notruncate"),
    ("disparador", "trg_cosecha_ro"),
    ("disparador", "trg_cosecha_version"),
}


def test_actualizar_a_0007_con_datos_que_incumplen_no_toca_filas_y_avisa(h: SimpleNamespace) -> None:
    """Base en 0006 con un lote que ya tiene 2 abiertas: la 0007 se aplica, no cambia ninguna fila y el SQL
    avisa con un WARNING que nombra el lote."""
    nombre = h.crear_bd()
    try:
        r = h.alembic(nombre, "upgrade", "0006")
        assert r.returncode == 0, r.stderr
        a, b, cerrada = _nuevo(), _nuevo(), _nuevo()

        async def _datos(con: asyncpg.Connection) -> None:
            await con.execute(BASE)
            await _cosecha(con, a, 1, 101)
            await _cosecha(con, b, 1, 101)  # sin la regla todavía: entra
            await _cosecha(con, cerrada, 1, 102)
            await _cosecha(con, cerrada, 2, 102, cerrada=True)

        _correr(h, nombre, _datos)
        antes = h.consulta(nombre, TABLA_COSECHA)
        assert set(h.consulta(nombre, OBJETOS)) == DISPARADORES_PREVIOS

        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        assert h.consulta(nombre, "SELECT version_num FROM api.alembic_version") == [("0008",)]
        assert h.consulta(nombre, TABLA_COSECHA) == antes
        assert set(h.consulta(nombre, OBJETOS)) == DISPARADORES_PREVIOS | {
            ("funcion", "fn_cosecha_una_abierta"),
            ("disparador", "trg_cosecha_una_abierta"),
        }

        # El aviso: se vuelve a ejecutar el SQL (idempotente) escuchando los mensajes del servidor.
        async def _avisos(con: asyncpg.Connection) -> list[str]:
            mensajes: list[str] = []
            con.add_log_listener(lambda _c, m: mensajes.append(f"{m.severity}: {m.message}"))
            await con.execute(ARCHIVO_SQL.read_text(encoding="utf-8"))
            return mensajes

        avisos = _correr(h, nombre, _avisos)
        assert any(
            m.startswith("WARNING: P-04: los lotes 101 ya tienen más de una cosecha abierta") for m in avisos
        )
        assert h.consulta(nombre, TABLA_COSECHA) == antes
    finally:
        h.borrar_bd(nombre)


def test_downgrade_elimina_solo_la_regla_y_se_puede_reaplicar(h: SimpleNamespace) -> None:
    nombre = h.crear_bd()
    try:
        assert h.alembic(nombre, "upgrade", "head").returncode == 0
        completo = set(h.consulta(nombre, OBJETOS))
        r = h.alembic(nombre, "downgrade", "0006")
        assert r.returncode == 0, r.stderr
        assert set(h.consulta(nombre, OBJETOS)) == DISPARADORES_PREVIOS
        assert h.consulta(nombre, "SELECT version_num FROM api.alembic_version") == [("0006",)]
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        assert set(h.consulta(nombre, OBJETOS)) == completo
    finally:
        h.borrar_bd(nombre)
