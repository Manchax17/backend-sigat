"""Vistas del productor (migración 0004, ``bd/sql/13_vistas_productor.sql``) contra PostgreSQL REAL.

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Usa bases TEMPORALES que se borran al final.

Escenario de datos (dos productores, un técnico):

* Predio 1 (Ana): lotes 11, 12 y 13 (el 13 sin actividad). Predio 2 (Bruno): lote 21.
* Trampas T1, T2 en el lote 11; T3 en el 12; T4 en el 11 hasta el 2026-03-01 y desde ese día en el 12;
  T5 en el 21.
* Visita V1 (2026-03-10, lotes 11 y 12): L1 en T1 corregida (2 versiones), L2 en T2 no leída, L5 otra
  lectura de T1, L3 en T3, L4 en T4 anulada en su versión 2.
* Visita V3 (2026-03-01 03:00 UTC = 2026-02-28 en Bogotá, lotes 11 y 12) sin lecturas.
* Visita V2 (predio 2, lote 21) con una lectura en T5.
* Aplicaciones: A1 (lote 11) corregida de producto de catálogo a texto; A2 (lote 11, más reciente) anulada;
  A3 (lote 12) con responsable escrito; A4 (lote 21).
* Cosecha C1 (lote 11) con 2 versiones: la vigente cambia la fecha esperada y añade lo real.
"""

import asyncio
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[2]
SQL_VISTAS = RAIZ / "bd" / "sql" / "13_vistas_productor.sql"

TECNICO, ANA, BRUNO = 1, 2, 3

DATOS = r"""
SET search_path = sigat, public;

INSERT INTO region (regi_id, regi_descripcion) VALUES (1, 'Región de prueba');
INSERT INTO departamento (depa_id, depa_descripcion, regi_id, depa_codigo) VALUES (1, 'Depto', 1, '99');
INSERT INTO municipio (muni_id, muni_descripcion, depa_id, muni_codigo) VALUES (1, 'Muni', 1, '001');
INSERT INTO centro_acopio (cent_id, cent_descripcion, depa_id) VALUES (1, 'Centro', 1);
INSERT INTO persona (pers_cc, pers_nombre, pers_apellido) VALUES
  (1001, 'Adela', 'Admin'), (1002, 'Tomás', 'Técnico'), (2001, 'Ana', 'Productora'), (2002, 'Bruno', 'Productor');
INSERT INTO administrador (admi_id, pers_cc, cent_id) VALUES (1, 1001, 1);
INSERT INTO tecnico (tecn_id, pers_cc, cent_id, admi_id, tecn_fecha_registro, esta_id)
  VALUES (1, 1002, 1, 1, DATE '2026-01-01', (SELECT esta_id FROM estado_registro WHERE esta_descripcion = 'activo'));
INSERT INTO productor (pers_cc, prod_fecha_registro, prod_tecnico_registro_id, muni_id) VALUES
  (2001, DATE '2026-01-01', 1, 1), (2002, DATE '2026-01-01', 1, 1);
INSERT INTO usuario (usua_id, pers_cc, usua_usuario, usua_requiere_reinicio, rol_id, esta_id)
SELECT v.id, v.cc, v.usu, true, r.rol_id, e.esta_id
  FROM (VALUES (1, 1002::bigint, 'tecnico.prueba', 'TECNICO'),
               (2, 2001::bigint, 'ana.prueba', 'PRODUCTOR'),
               (3, 2002::bigint, 'bruno.prueba', 'PRODUCTOR')) v(id, cc, usu, rol)
  JOIN rol r ON r.rol_codigo = v.rol
  JOIN estado_registro e ON e.esta_descripcion = 'activo';

INSERT INTO predio (pred_id, pred_nombre, muni_id, pred_area, pers_cc, pred_fecha_registro, pred_tecnico_registro_id)
  VALUES (1, 'Predio Ana', 1, 10, 2001, DATE '2026-01-01', 1), (2, 'Predio Bruno', 1, 10, 2002, DATE '2026-01-01', 1);
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro, lote_tecnico_registro_id) VALUES
  (11, 'L11', 'Lote 1 · Norte', 2, 1, DATE '2026-01-01', 1),
  (12, 'L12', 'Lote 2 · Centro', 2, 1, DATE '2026-01-01', 1),
  (13, 'L13', 'Lote 3 · Sur', 2, 1, DATE '2026-01-01', 1),
  (21, 'L21', 'Lote único', 2, 2, DATE '2026-01-01', 1);

INSERT INTO vigilancia (vigi_id, vigi_descripcion) VALUES (1, 'Vigilancia de prueba');
INSERT INTO condicion_trampa (cond_id, cond_descripcion) VALUES (1, 'Buena');
INSERT INTO trampa (tram_id, tram_codigo, tipt_id, atra_id, esta_id, tram_fecha_registro, tram_tecnico_registro_id)
SELECT v.id, v.cod, t.tipt_id, (SELECT min(atra_id) FROM atrayente), e.esta_id, DATE '2026-01-01', 1
  FROM (VALUES (1, '54125-R1-1'), (2, '54125-R1-3'), (3, '54125-R1-5'), (4, '54125-R1-7'), (5, '54125-R1-9')) v(id, cod)
  JOIN tipo_trampa t ON t.tipt_descripcion = 'Jackson'
  JOIN estado_registro e ON e.esta_descripcion = 'activo';
INSERT INTO ubicacion_trampa (ubic_id, tram_id, pred_id, lote_id, ubic_fecha, ubic_fecha_fin, tecn_id, vigi_id, ubic_long, ubic_lat) VALUES
  (1, 1, 1, 11, DATE '2026-01-01', NULL,              1, 1, -72.5, 7.9),
  (2, 2, 1, 11, DATE '2026-01-01', NULL,              1, 1, -72.5, 7.9),
  (3, 3, 1, 12, DATE '2026-01-01', NULL,              1, 1, -72.5, 7.9),
  (4, 4, 1, 11, DATE '2026-01-01', DATE '2026-03-01', 1, 1, -72.5, 7.9),
  (5, 4, 1, 12, DATE '2026-03-01', NULL,              1, 1, -72.5, 7.9),
  (6, 5, 2, 21, DATE '2026-01-01', NULL,              1, 1, -72.5, 7.9);

INSERT INTO dispositivo (disp_id, disp_plataforma) VALUES ('00000000-0000-0000-0000-0000000000d1', 'android');

-- Operación de sincronización para cada registro de dominio (todas exigen oper_id).
CREATE FUNCTION pg_temp.op(p_entidad text, p_usua integer, p_registro uuid, p_version integer) RETURNS bigint
LANGUAGE sql AS $$
  INSERT INTO sigat.operacion_sync (disp_id, oper_cliente_id, usua_id, oper_entidad, oper_accion, oper_registro_id,
                                    oper_version, oper_hash_contenido, oper_clasificacion, oper_t_captura)
  VALUES ('00000000-0000-0000-0000-0000000000d1', gen_random_uuid(), p_usua, p_entidad,
          CASE WHEN p_version = 1 THEN 'crear' ELSE 'corregir' END, p_registro, p_version,
          md5(random()::text) || md5(random()::text),
          CASE WHEN p_version = 1 THEN 'alta_nueva' ELSE 'correccion' END, TIMESTAMPTZ '2026-03-10 12:00-05')
  RETURNING oper_id $$;

INSERT INTO visita (visi_id, disp_id, oper_id, usua_id, pred_id, visi_t_inicio_obs, visi_t_fin_obs, visi_t_captura) VALUES
  ('00000000-0000-0000-0000-00000000b001', '00000000-0000-0000-0000-0000000000d1',
   pg_temp.op('visita', 1, '00000000-0000-0000-0000-00000000b001', 1), 1, 1,
   '2026-03-10 09:00-05', '2026-03-10 10:00-05', '2026-03-10 10:05-05'),
  ('00000000-0000-0000-0000-00000000b002', '00000000-0000-0000-0000-0000000000d1',
   pg_temp.op('visita', 1, '00000000-0000-0000-0000-00000000b002', 1), 1, 2,
   '2026-03-10 11:00-05', '2026-03-10 11:30-05', '2026-03-10 11:35-05'),
  ('00000000-0000-0000-0000-00000000b003', '00000000-0000-0000-0000-0000000000d1',
   pg_temp.op('visita', 1, '00000000-0000-0000-0000-00000000b003', 1), 1, 1,
   '2026-03-01 03:00+00', '2026-03-01 03:30+00', '2026-03-01 03:35+00');
INSERT INTO visita_lote (visi_id, lote_id, pred_id) VALUES
  ('00000000-0000-0000-0000-00000000b001', 11, 1), ('00000000-0000-0000-0000-00000000b001', 12, 1),
  ('00000000-0000-0000-0000-00000000b002', 21, 2),
  ('00000000-0000-0000-0000-00000000b003', 11, 1), ('00000000-0000-0000-0000-00000000b003', 12, 1);

-- Lecturas (en orden: la versión 2 exige que exista la 1).
INSERT INTO lectura_trampa (lect_registro_id, lect_version, visi_id, tram_id, ubic_id, lect_leida, lect_especimenes,
                            lect_justificacion, cond_id, usua_id, disp_id, oper_id, lect_t_observacion, lect_t_captura,
                            lect_anulada, lect_motivo_anulacion)
SELECT v.reg::uuid, v.ver, v.visi::uuid, v.tram, v.ubic, v.leida, v.esp, v.just, 1, 1,
       '00000000-0000-0000-0000-0000000000d1', pg_temp.op('lectura_trampa', 1, v.reg::uuid, v.ver),
       v.obs::timestamptz, v.obs::timestamptz + interval '1 minute', v.anulada, v.motivo
  FROM (VALUES
    (1, '00000000-0000-0000-0000-00000000c001', 1, '00000000-0000-0000-0000-00000000b001', 1, 1, true, 3, NULL, '2026-03-10 09:10-05', false, NULL),
    (2, '00000000-0000-0000-0000-00000000c001', 2, '00000000-0000-0000-0000-00000000b001', 1, 1, true, 4, NULL, '2026-03-10 09:10-05', false, NULL),
    (3, '00000000-0000-0000-0000-00000000c002', 1, '00000000-0000-0000-0000-00000000b001', 2, 2, false, NULL, 'Trampa caída', '2026-03-10 09:15-05', false, NULL),
    (4, '00000000-0000-0000-0000-00000000c005', 1, '00000000-0000-0000-0000-00000000b001', 1, 1, true, 0, NULL, '2026-03-10 09:20-05', false, NULL),
    (5, '00000000-0000-0000-0000-00000000c003', 1, '00000000-0000-0000-0000-00000000b001', 3, 3, true, 1, NULL, '2026-03-10 09:25-05', false, NULL),
    (6, '00000000-0000-0000-0000-00000000c004', 1, '00000000-0000-0000-0000-00000000b001', 4, 5, true, 2, NULL, '2026-03-10 09:30-05', false, NULL),
    (7, '00000000-0000-0000-0000-00000000c004', 2, '00000000-0000-0000-0000-00000000b001', 4, 5, true, 2, NULL, '2026-03-10 09:30-05', true, 'Lectura duplicada'),
    (8, '00000000-0000-0000-0000-00000000c006', 1, '00000000-0000-0000-0000-00000000b002', 5, 6, true, 5, NULL, '2026-03-10 11:10-05', false, NULL)
  ) v(orden, reg, ver, visi, tram, ubic, leida, esp, just, obs, anulada, motivo)
 ORDER BY v.orden;

INSERT INTO producto_fitosanitario (prof_id, prof_nombre) VALUES (1, 'Cipermetrina');
INSERT INTO aplicacion_fitosanitaria (apli_registro_id, apli_version, lote_id, prof_id, apli_producto_texto, apli_dosis,
                                      apli_dosis_uni_id, apli_area_tratada, apli_area_uni_id, pers_cc_responsable,
                                      apli_responsable_nombre, usua_id, disp_id, oper_id, apli_t_observacion, apli_t_captura,
                                      apli_anulada, apli_motivo_anulacion)
SELECT v.reg::uuid, v.ver, v.lote, v.prof, v.texto, 2.0, (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'mL/L'),
       1.2, (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'ha'), v.resp_cc, v.resp_nombre, v.usua,
       '00000000-0000-0000-0000-0000000000d1', pg_temp.op('aplicacion_fitosanitaria', v.usua, v.reg::uuid, v.ver),
       v.obs::timestamptz, v.obs::timestamptz + interval '1 hour', v.anulada, v.motivo
  FROM (VALUES
    (1, '00000000-0000-0000-0000-00000000e001', 1, 11, 1, NULL, 2001::bigint, NULL, 2, '2026-03-01 08:00-05', false, NULL),
    (2, '00000000-0000-0000-0000-00000000e001', 2, 11, NULL, 'Caldo bordelés', 2001::bigint, NULL, 2, '2026-03-01 08:00-05', false, NULL),
    (3, '00000000-0000-0000-0000-00000000e002', 1, 11, 1, NULL, 2001::bigint, NULL, 2, '2026-03-05 08:00-05', false, NULL),
    (4, '00000000-0000-0000-0000-00000000e002', 2, 11, 1, NULL, 2001::bigint, NULL, 2, '2026-03-05 08:00-05', true, 'Registro equivocado'),
    (5, '00000000-0000-0000-0000-00000000e003', 1, 12, NULL, 'Azufre', NULL, 'Jornalero Uno', 2, '2026-02-01 08:00-05', false, NULL),
    (6, '00000000-0000-0000-0000-00000000e004', 1, 21, 1, NULL, 2002::bigint, NULL, 3, '2026-02-15 08:00-05', false, NULL)
  ) v(orden, reg, ver, lote, prof, texto, resp_cc, resp_nombre, usua, obs, anulada, motivo)
 ORDER BY v.orden;

INSERT INTO cosecha (cose_registro_id, cose_version, lote_id, cose_fecha_esperada, cose_volumen_esperado, cose_fecha_real,
                     cose_volumen_real, cose_volumen_uni_id, usua_id, disp_id, oper_id, cose_t_observacion, cose_t_captura)
SELECT '00000000-0000-0000-0000-00000000f001'::uuid, v.ver, 11, v.esperada, 120, v.real_f, v.real_v,
       (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'kg'), 2, '00000000-0000-0000-0000-0000000000d1',
       pg_temp.op('cosecha', 2, '00000000-0000-0000-0000-00000000f001', v.ver),
       TIMESTAMPTZ '2026-03-02 08:00-05', TIMESTAMPTZ '2026-03-02 08:05-05'
  FROM (VALUES (1, DATE '2026-12-05', NULL::date, NULL::numeric),
               (2, DATE '2026-12-07', DATE '2026-12-08', 100::numeric)) v(ver, esperada, real_f, real_v)
 ORDER BY v.ver;
"""


def _dsn(h: SimpleNamespace, nombre: str) -> str:
    return str(h.url(nombre)).replace("postgresql+asyncpg://", "postgresql://", 1)


def ejecutar(h: SimpleNamespace, nombre: str, sql: str) -> None:
    """Ejecuta varias sentencias en una transacción (los datos de prueba necesitan escribir)."""

    async def _correr() -> None:
        con = await asyncpg.connect(_dsn(h, nombre))
        try:
            async with con.transaction():
                await con.execute(sql)
        finally:
            await con.close()

    asyncio.run(_correr())


@pytest.fixture(scope="module")
def bd(h: SimpleNamespace) -> Iterator[str]:
    """Base temporal en la cabeza de Alembic, con el escenario de datos cargado."""
    nombre = h.crear_bd()
    try:
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        ejecutar(h, nombre, DATOS)
        yield nombre
    finally:
        h.borrar_bd(nombre)


def _filas(h: SimpleNamespace, bd: str, sql: str) -> list[tuple[object, ...]]:
    return h.consulta(bd, sql)


V1, V2, V3 = (f"00000000-0000-0000-0000-00000000b00{i}" for i in (1, 2, 3))


# ------------------------------------------------------------------ estructura
COLUMNAS_ACTIVIDAD = [
    ("lote_id", "integer"),
    ("pred_id", "integer"),
    ("lote_codigo", "character varying"),
    ("lote_descripcion", "character varying"),
    ("tipo", "text"),
    ("registro_id", "uuid"),
    ("t_observacion", "timestamp with time zone"),
    ("fecha_observacion_local", "date"),
    ("usua_id", "integer"),
    ("responsable", "text"),
    ("rol_codigo_responsable", "character varying"),
    ("rol_responsable", "character varying"),
    ("titulo", "text"),
    ("trampas_en_lote", "bigint"),
    ("trampas_revisadas", "bigint"),
    ("trampas_leidas", "bigint"),
    ("producto", "character varying"),
    ("cose_fecha_esperada", "date"),
    ("detalle", "text"),
    ("linaje", "text"),
    ("es_inspeccion_oficial", "boolean"),
]
COLUMNAS_COSECHA = [
    ("lote_id", "integer"),
    ("pred_id", "integer"),
    ("lote_codigo", "character varying"),
    ("lote_descripcion", "character varying"),
    ("cose_registro_id", "uuid"),
    ("cose_version", "integer"),
    ("cose_t_observacion", "timestamp with time zone"),
    ("cose_fecha_esperada", "date"),
    ("cose_volumen_esperado", "numeric"),
    ("cose_fecha_real", "date"),
    ("cose_volumen_real", "numeric"),
    ("cose_volumen_uni_id", "integer"),
    ("volumen_unidad", "character varying"),
    ("abierta", "boolean"),
    ("cosechas_abiertas", "bigint"),
    ("cose_linaje", "text"),
]
COLUMNAS_APLICACION = [
    ("lote_id", "integer"),
    ("pred_id", "integer"),
    ("lote_codigo", "character varying"),
    ("lote_descripcion", "character varying"),
    ("apli_registro_id", "uuid"),
    ("apli_version", "integer"),
    ("apli_t_observacion", "timestamp with time zone"),
    ("apli_fecha_local", "date"),
    ("dias_desde_aplicacion", "integer"),
    ("prof_id", "integer"),
    ("producto", "character varying"),
    ("apli_dosis", "numeric"),
    ("apli_dosis_uni_id", "integer"),
    ("dosis_unidad", "character varying"),
    ("apli_area_tratada", "numeric"),
    ("apli_area_uni_id", "integer"),
    ("area_unidad", "character varying"),
    ("pers_cc_responsable", "bigint"),
    ("responsable", "text"),
    ("apli_linaje", "text"),
]


@pytest.mark.parametrize(
    ("vista", "esperadas"),
    [
        ("v_actividad_lote_detalle", COLUMNAS_ACTIVIDAD),
        ("v_aplicacion_ultima_lote", COLUMNAS_APLICACION),
        ("v_cosecha_lote_vigente", COLUMNAS_COSECHA),
    ],
)
def test_columnas_y_tipos(bd: str, h: SimpleNamespace, vista: str, esperadas: list[tuple[str, str]]) -> None:
    columnas = _filas(
        h,
        bd,
        "SELECT column_name, data_type FROM information_schema.columns "  # noqa: S608 (constantes de la prueba)
        f"WHERE table_schema = 'sigat' AND table_name = '{vista}' ORDER BY ordinal_position",
    )
    assert columnas == esperadas


def test_linaje_conserva_el_dominio(bd: str, h: SimpleNamespace) -> None:
    dominios = _filas(
        h,
        bd,
        "SELECT table_name, column_name, domain_name FROM information_schema.columns "
        "WHERE table_schema = 'sigat' AND column_name IN ('linaje', 'apli_linaje') "
        "AND table_name IN ('v_actividad_lote_detalle', 'v_aplicacion_ultima_lote') ORDER BY 1",
    )
    assert dominios == [
        ("v_actividad_lote_detalle", "linaje", "d_linaje"),
        ("v_aplicacion_ultima_lote", "apli_linaje", "d_linaje"),
    ]


def test_funciones_filtradas_existen(bd: str, h: SimpleNamespace) -> None:
    funciones = _filas(
        h,
        bd,
        "SELECT p.proname, pg_get_function_identity_arguments(p.oid), p.provolatile::text FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'sigat' "
        "AND p.proname IN ('fn_actividad_lote_detalle', 'fn_aplicacion_ultima_lote') ORDER BY 1",
    )
    assert funciones == [
        ("fn_actividad_lote_detalle", "p_usua integer", "s"),
        ("fn_aplicacion_ultima_lote", "p_usua integer", "s"),
    ]


# ------------------------------------------------------------------ F1: v_actividad_lote_detalle
def test_una_fila_por_fila_de_v_actividad_lote(bd: str, h: SimpleNamespace) -> None:
    """Ningún JOIN multiplica filas ni las pierde."""
    [(base, detalle, distintas)] = _filas(
        h,
        bd,
        "SELECT (SELECT count(*) FROM sigat.v_actividad_lote), count(*), "
        "count(DISTINCT (lote_id, tipo, registro_id)) FROM sigat.v_actividad_lote_detalle",
    )
    assert base == detalle == distintas == 9  # 5 vigilancias (lote x visita) + 3 aplicaciones + 1 cosecha


def _vigilancia(h: SimpleNamespace, bd: str, visita: str) -> dict[object, tuple[object, ...]]:
    filas = _filas(
        h,
        bd,
        "SELECT lote_id, trampas_en_lote, trampas_revisadas, trampas_leidas, detalle, titulo, responsable, "  # noqa: S608 (constantes de la prueba)
        "rol_codigo_responsable, linaje, es_inspeccion_oficial, fecha_observacion_local::text "
        f"FROM sigat.v_actividad_lote_detalle WHERE tipo = 'vigilancia' AND registro_id = '{visita}'",
    )
    return {f[0]: f[1:] for f in filas}


def test_visita_de_varios_lotes_cuenta_solo_las_trampas_de_cada_lote(bd: str, h: SimpleNamespace) -> None:
    """Lote 11: T1 (corregida y con otra lectura) y T2 (no leída) = 2 de 2, 1 leída.
    Lote 12: T3 leída; T4 se reubicó aquí pero su lectura vigente está anulada = 1 de 2."""
    v1 = _vigilancia(h, bd, V1)
    assert v1[11][:4] == (2, 2, 1, "2 de 2 trampas revisadas")
    assert v1[12][:4] == (2, 1, 1, "1 de 2 trampas revisadas")
    # La consulta original (count(lectura_trampa) por visita) habría dado 7 en ambos lotes.
    [(sin_corregir,)] = _filas(h, bd, f"SELECT count(*) FROM sigat.lectura_trampa WHERE visi_id = '{V1}'")  # noqa: S608 (constantes de la prueba)
    assert sin_corregir == 7


def test_trampas_del_lote_segun_el_dia_de_la_visita_en_la_zona_del_predio(
    bd: str, h: SimpleNamespace
) -> None:
    """V3 es el 2026-03-01 03:00 UTC = 2026-02-28 en Bogotá: T4 aún estaba en el lote 11."""
    v3 = _vigilancia(h, bd, V3)
    assert v3[11][:4] == (3, 0, 0, "0 de 3 trampas revisadas")
    assert v3[12][:4] == (1, 0, 0, "0 de 1 trampas revisadas")
    assert v3[11][-1] == "2026-02-28"


def test_vigilancia_responsable_y_linaje_desconocido(bd: str, h: SimpleNamespace) -> None:
    fila = _vigilancia(h, bd, V2)[21]
    assert fila[:4] == (1, 1, 1, "1 de 1 trampas revisadas")
    assert fila[4:9] == ("Visita de vigilancia", "Tomás Técnico", "TECNICO", None, None)


def test_aplicacion_usa_la_version_vigente_y_excluye_anuladas(bd: str, h: SimpleNamespace) -> None:
    filas = _filas(
        h,
        bd,
        "SELECT lote_id, detalle, producto, linaje, es_inspeccion_oficial, responsable, trampas_en_lote "
        "FROM sigat.v_actividad_lote_detalle WHERE tipo = 'aplicacion' ORDER BY lote_id",
    )
    assert filas == [
        (11, "Caldo bordelés", "Caldo bordelés", "autorreporte", False, "Ana Productora", None),
        (12, "Azufre", "Azufre", "autorreporte", False, "Ana Productora", None),
        (21, "Cipermetrina", "Cipermetrina", "autorreporte", False, "Bruno Productor", None),
    ]


def test_cosecha_fecha_esperada_vigente_en_formato_fijo(bd: str, h: SimpleNamespace) -> None:
    [fila] = _filas(
        h,
        bd,
        "SELECT titulo, detalle, cose_fecha_esperada::text, linaje FROM sigat.v_actividad_lote_detalle "
        "WHERE tipo = 'cosecha'",
    )
    assert fila == ("Cosecha", "Cosecha esperada · 07 dic 2026", "2026-12-07", "autorreporte")


# Locales candidatos para lc_time. No se instala ninguno: se usan los que tenga el servidor.
LOCALES_CANDIDATOS = (
    "C", "C.UTF-8", "POSIX", "en_US.UTF-8", "en_US.utf8", "es_ES.UTF-8", "es_ES.utf8", "es_CO.UTF-8", "es_CO.utf8",
)  # fmt: skip


def _familia(lc: str) -> str:
    """'en_US.UTF-8' y 'en_US.utf8' son el mismo locale; 'C', 'C.UTF-8' y 'POSIX', la misma familia."""
    base = lc.split(".")[0].lower()
    return "c" if base in {"c", "posix"} else base


def test_detalle_de_fecha_no_depende_del_idioma_del_servidor(
    bd: str, h: SimpleNamespace, record_property: Callable[[str, object], None]
) -> None:
    """Prueba el texto de la fecha con CADA locale disponible y deja registro de cuáles se probaron.

    No omite en silencio: los locales que el servidor no tiene se registran como no disponibles. Exige al
    menos dos familias distintas (por ejemplo 'C' y 'en_US'); si no las hay, la prueba se marca como
    omitida con el motivo, en vez de darse por completa.
    """

    async def _correr() -> tuple[dict[str, tuple[str, str]], list[str]]:
        con = await asyncpg.connect(_dsn(h, bd))
        probados: dict[str, tuple[str, str]] = {}
        no_disponibles: list[str] = []
        try:
            for lc in LOCALES_CANDIDATOS:
                try:
                    await con.execute(f"SET lc_time = '{lc}'")
                except asyncpg.PostgresError:
                    no_disponibles.append(lc)
                    continue
                efectivo = await con.fetchval("SHOW lc_time")
                detalle = await con.fetchval(
                    "SELECT detalle FROM sigat.v_actividad_lote_detalle WHERE tipo = 'cosecha'"
                )
                probados[lc] = (efectivo, detalle)
            return probados, no_disponibles
        finally:
            await con.close()

    probados, no_disponibles = asyncio.run(_correr())
    record_property("locales_probados", sorted(probados))
    record_property("locales_no_disponibles", no_disponibles)
    print(f"lc_time probados: {sorted(probados)}; no disponibles en el servidor: {no_disponibles}")

    # El SET se aplicó de verdad: SHOW lc_time devuelve el locale pedido en cada caso.
    assert all(efectivo == lc for lc, (efectivo, _) in probados.items())
    familias = {_familia(lc) for lc in probados}
    if len(familias) < 2:
        pytest.skip(
            f"Solo hay {len(familias)} familia(s) de locale en el servidor ({sorted(probados)}): "
            "no se puede comprobar la independencia del idioma."
        )
    assert {detalle for _, detalle in probados.values()} == {"Cosecha esperada · 07 dic 2026"}


# ------------------------------------------------------------------ F3: v_aplicacion_ultima_lote
def test_ultima_aplicacion_por_lote(bd: str, h: SimpleNamespace) -> None:
    filas = _filas(
        h,
        bd,
        "SELECT lote_id, apli_registro_id::text, apli_version, apli_fecha_local::text, producto, dosis_unidad, "
        "area_unidad, responsable, apli_linaje FROM sigat.v_aplicacion_ultima_lote ORDER BY lote_id",
    )
    assert filas == [
        # A2 (más reciente) está anulada: queda A1 en su versión 2.
        (11, "00000000-0000-0000-0000-00000000e001", 2, "2026-03-01", "Caldo bordelés", "mL/L", "ha",
         "Ana Productora", "autorreporte"),
        (12, "00000000-0000-0000-0000-00000000e003", 1, "2026-02-01", "Azufre", "mL/L", "ha",
         "Jornalero Uno", "autorreporte"),
        (13, None, None, None, None, None, None, None, None),  # lote sin aplicaciones
        (21, "00000000-0000-0000-0000-00000000e004", 1, "2026-02-15", "Cipermetrina", "mL/L", "ha",
         "Bruno Productor", "autorreporte"),
    ]  # fmt: skip


def test_dias_desde_la_aplicacion_en_la_zona_del_predio(bd: str, h: SimpleNamespace) -> None:
    [(correcto, nulo)] = _filas(
        h,
        bd,
        "SELECT bool_and(dias_desde_aplicacion = (now() AT TIME ZONE 'America/Bogota')::date - apli_fecha_local) "
        "FILTER (WHERE apli_registro_id IS NOT NULL), "
        "bool_and(dias_desde_aplicacion IS NULL) FILTER (WHERE apli_registro_id IS NULL) "
        "FROM sigat.v_aplicacion_ultima_lote",
    )
    assert correcto is True
    assert nulo is True


# ------------------------------------------------------------------ seguridad: separación por productor
def test_cada_productor_ve_solo_sus_lotes_en_el_historial(bd: str, h: SimpleNamespace) -> None:
    def lotes(usua: int) -> set[object]:
        return {f[0] for f in _filas(h, bd, f"SELECT lote_id FROM sigat.fn_actividad_lote_detalle({usua})")}  # noqa: S608 (constantes de la prueba)

    assert lotes(ANA) == {11, 12}
    assert lotes(BRUNO) == {21}
    assert lotes(TECNICO) == set()  # sin asignaciones vigentes no ve ningún lote


def test_cada_productor_ve_solo_sus_lotes_en_la_ultima_aplicacion(bd: str, h: SimpleNamespace) -> None:
    def lotes(usua: int) -> set[object]:
        return {f[0] for f in _filas(h, bd, f"SELECT lote_id FROM sigat.fn_aplicacion_ultima_lote({usua})")}  # noqa: S608 (constantes de la prueba)

    assert lotes(ANA) == {11, 12, 13}
    assert lotes(BRUNO) == {21}
    assert lotes(TECNICO) == set()


def test_funcion_y_vista_coinciden_para_los_lotes_visibles(bd: str, h: SimpleNamespace) -> None:
    [(diferencias,)] = _filas(
        h,
        bd,
        "SELECT count(*) FROM ((SELECT * FROM sigat.fn_actividad_lote_detalle(2)) EXCEPT ALL "
        "(SELECT * FROM sigat.v_actividad_lote_detalle WHERE pred_id = 1)) d",
    )
    assert diferencias == 0


# ------------------------------------------------------------------ migración
def test_reaplicar_el_sql_es_idempotente(bd: str, h: SimpleNamespace) -> None:
    """Una base instalada con 00_instalar_todo.sql ya tiene las vistas: la 0004 debe pasar igual."""
    antes = _filas(h, bd, "SELECT count(*) FROM sigat.v_actividad_lote_detalle")
    ejecutar(h, bd, SQL_VISTAS.read_text(encoding="utf-8"))
    assert _filas(h, bd, "SELECT count(*) FROM sigat.v_actividad_lote_detalle") == antes


OBJETOS = """
SELECT 'vista', table_name FROM information_schema.views
 WHERE table_schema = 'sigat'
   AND table_name IN ('v_actividad_lote_detalle', 'v_aplicacion_ultima_lote', 'v_cosecha_lote_vigente', 'v_actividad_lote')
UNION ALL
SELECT 'funcion', p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'sigat'
   AND p.proname IN ('fn_actividad_lote_detalle', 'fn_aplicacion_ultima_lote', 'fn_cosecha_lote_vigente', 'fn_actividad_lote')
"""


def test_downgrade_elimina_solo_sus_objetos_y_se_puede_reaplicar(h: SimpleNamespace) -> None:
    nombre = h.crear_bd()
    try:
        assert h.alembic(nombre, "upgrade", "head").returncode == 0
        completo = set(h.consulta(nombre, OBJETOS))
        assert len(completo) == 8
        r = h.alembic(nombre, "downgrade", "0003")
        assert r.returncode == 0, r.stderr
        assert set(h.consulta(nombre, OBJETOS)) == {
            ("vista", "v_actividad_lote"),
            ("funcion", "fn_actividad_lote"),
        }
        assert h.consulta(nombre, "SELECT version_num FROM api.alembic_version") == [("0003",)]
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        assert set(h.consulta(nombre, OBJETOS)) == completo
        assert h.consulta(nombre, "SELECT version_num FROM api.alembic_version") == [("0008",)]
    finally:
        h.borrar_bd(nombre)


# ================================================================== casos especiales (revisión I-5)
# Base PROPIA (bd_casos): los datos de arriba más los de CASOS, para no alterar los resultados esperados
# de las pruebas anteriores. Se cargan en la MISMA transacción que DATOS (reutiliza pg_temp.op).
#
# * Predio 3 de Ana en Asia/Tokyo: lote 31, trampa T6 instalada el 2026-03-11 (hora local). Visita V4 a las
#   20:00 UTC del 2026-03-10 = 05:00 del 11 en Tokio (en Bogotá seguiría siendo el 10). Aplicación A8 a la
#   misma hora.
# * A5: corregida del lote 12 al 11. A6/A7: lote 13, misma apli_t_observacion; A7 se capturó después.
# * C2: cosecha del lote 12 anulada en su versión 2.
# * Lote 13 dado de baja el 2026-04-01.
# * Usuarios: Gil (agrónomo, asignado a los predios 1 y 2; su asignación al 3 venció), Sara (productora sin
#   predios) y Carla (productora sin predios propios, asignada al predio 2 de Bruno).
GIL, SARA, CARLA = 4, 5, 6

CASOS = r"""
INSERT INTO predio (pred_id, pred_nombre, muni_id, pred_area, pers_cc, pred_zona_horaria, pred_fecha_registro,
                    pred_tecnico_registro_id)
  VALUES (3, 'Predio Tokio', 1, 5, 2001, 'Asia/Tokyo', DATE '2026-01-01', 1);
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro, lote_tecnico_registro_id)
  VALUES (31, 'L31', 'Lote Tokio', 1, 3, DATE '2026-01-01', 1);
INSERT INTO trampa (tram_id, tram_codigo, tipt_id, atra_id, esta_id, tram_fecha_registro, tram_tecnico_registro_id)
SELECT 6, '54125-R1-11', t.tipt_id, (SELECT min(atra_id) FROM atrayente), e.esta_id, DATE '2026-01-01', 1
  FROM tipo_trampa t, estado_registro e WHERE t.tipt_descripcion = 'Jackson' AND e.esta_descripcion = 'activo';
INSERT INTO ubicacion_trampa (ubic_id, tram_id, pred_id, lote_id, ubic_fecha, tecn_id, vigi_id, ubic_long, ubic_lat)
  VALUES (7, 6, 3, 31, DATE '2026-03-11', 1, 1, 139.7, 35.6);
INSERT INTO visita (visi_id, disp_id, oper_id, usua_id, pred_id, visi_t_inicio_obs, visi_t_fin_obs, visi_t_captura)
  VALUES ('00000000-0000-0000-0000-00000000b004', '00000000-0000-0000-0000-0000000000d1',
          pg_temp.op('visita', 1, '00000000-0000-0000-0000-00000000b004', 1), 1, 3,
          '2026-03-10 20:00+00', '2026-03-10 20:30+00', '2026-03-10 20:35+00');
INSERT INTO visita_lote (visi_id, lote_id, pred_id) VALUES ('00000000-0000-0000-0000-00000000b004', 31, 3);
INSERT INTO lectura_trampa (lect_registro_id, lect_version, visi_id, tram_id, ubic_id, lect_leida, lect_especimenes,
                            cond_id, usua_id, disp_id, oper_id, lect_t_observacion, lect_t_captura)
  VALUES ('00000000-0000-0000-0000-00000000c007', 1, '00000000-0000-0000-0000-00000000b004', 6, 7, true, 0, 1, 1,
          '00000000-0000-0000-0000-0000000000d1',
          pg_temp.op('lectura_trampa', 1, '00000000-0000-0000-0000-00000000c007', 1),
          '2026-03-10 20:05+00', '2026-03-10 20:06+00');

INSERT INTO aplicacion_fitosanitaria (apli_registro_id, apli_version, lote_id, apli_producto_texto, apli_dosis,
                                      apli_dosis_uni_id, apli_area_tratada, apli_area_uni_id, apli_responsable_nombre,
                                      usua_id, disp_id, oper_id, apli_t_observacion, apli_t_captura)
SELECT v.reg::uuid, v.ver, v.lote, v.txt, 1, (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'mL/L'), 1,
       (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'ha'), 'Jornalero Dos', 2,
       '00000000-0000-0000-0000-0000000000d1', pg_temp.op('aplicacion_fitosanitaria', 2, v.reg::uuid, v.ver),
       v.obs::timestamptz, v.cap::timestamptz
  FROM (VALUES
    (1, '00000000-0000-0000-0000-00000000e005', 1, 12, 'Movida v1', '2026-03-20 08:00-05', '2026-03-20 09:00-05'),
    (2, '00000000-0000-0000-0000-00000000e005', 2, 11, 'Movida v2', '2026-03-20 08:00-05', '2026-03-20 10:00-05'),
    (3, '00000000-0000-0000-0000-00000000e006', 1, 13, 'Empate captura temprana', '2026-03-15 08:00-05', '2026-03-15 09:00-05'),
    (4, '00000000-0000-0000-0000-00000000e007', 1, 13, 'Empate captura tardía', '2026-03-15 08:00-05', '2026-03-15 11:00-05'),
    (5, '00000000-0000-0000-0000-00000000e008', 1, 31, 'Aplicación en Tokio', '2026-03-10 20:00+00', '2026-03-10 21:00+00')
  ) v(orden, reg, ver, lote, txt, obs, cap)
 ORDER BY v.orden;

INSERT INTO cosecha (cose_registro_id, cose_version, lote_id, cose_fecha_esperada, cose_volumen_esperado,
                     cose_volumen_uni_id, usua_id, disp_id, oper_id, cose_t_observacion, cose_t_captura,
                     cose_anulada, cose_motivo_anulacion)
SELECT '00000000-0000-0000-0000-00000000f002'::uuid, v.ver, 12, DATE '2026-11-01', 10,
       (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'kg'), 2, '00000000-0000-0000-0000-0000000000d1',
       pg_temp.op('cosecha', 2, '00000000-0000-0000-0000-00000000f002', v.ver),
       TIMESTAMPTZ '2026-03-03 08:00-05', TIMESTAMPTZ '2026-03-03 08:01-05', v.anulada, v.motivo
  FROM (VALUES (1, false, NULL), (2, true, 'Registro duplicado')) v(ver, anulada, motivo)
 ORDER BY v.ver;

UPDATE lote SET lote_fecha_baja = DATE '2026-04-01' WHERE lote_id = 13;

INSERT INTO persona (pers_cc, pers_nombre, pers_apellido) VALUES
  (3001, 'Gil', 'Agrónomo'), (3002, 'Sara', 'SinPredio'), (3003, 'Carla', 'Asignada');
INSERT INTO usuario (usua_id, pers_cc, usua_usuario, usua_requiere_reinicio, rol_id, esta_id)
SELECT v.id, v.cc, v.usu, true, r.rol_id, e.esta_id
  FROM (VALUES (4, 3001::bigint, 'gil.prueba', 'AGRONOMO_SUPERVISOR'),
               (5, 3002::bigint, 'sara.prueba', 'PRODUCTOR'),
               (6, 3003::bigint, 'carla.prueba', 'PRODUCTOR')) v(id, cc, usu, rol)
  JOIN rol r ON r.rol_codigo = v.rol
  JOIN estado_registro e ON e.esta_descripcion = 'activo';
INSERT INTO usuario_predio (usua_id, pred_id, usup_desde, usup_hasta) VALUES
  (4, 1, DATE '2026-01-01', NULL),
  (4, 2, DATE '2026-01-01', NULL),
  (4, 3, DATE '2026-01-01', DATE '2026-02-01'),  -- asignación vencida: no da acceso
  (6, 2, DATE '2026-01-01', NULL);
"""


@pytest.fixture(scope="module")
def bd_casos(h: SimpleNamespace) -> Iterator[str]:
    nombre = h.crear_bd()
    try:
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        ejecutar(h, nombre, DATOS + CASOS)
        yield nombre
    finally:
        h.borrar_bd(nombre)


V4 = "00000000-0000-0000-0000-00000000b004"


def test_casos_predio_con_otra_zona_horaria(bd_casos: str, h: SimpleNamespace) -> None:
    """El día de la visita se toma en la zona DEL PREDIO (Asia/Tokyo), no en la de Bogotá."""
    [fila] = h.consulta(
        bd_casos,
        "SELECT lote_id, trampas_en_lote, trampas_revisadas, trampas_leidas, detalle, "  # noqa: S608 (constantes de la prueba)
        "fecha_observacion_local::text, (t_observacion AT TIME ZONE 'America/Bogota')::date::text "
        f"FROM sigat.v_actividad_lote_detalle WHERE registro_id = '{V4}'",
    )
    # En Bogotá sería el 2026-03-10, un día antes de instalar la trampa en el lote.
    assert fila == (31, 1, 1, 1, "1 de 1 trampas revisadas", "2026-03-11", "2026-03-10")
    [(fecha, dias_ok)] = h.consulta(
        bd_casos,
        "SELECT apli_fecha_local::text, "
        "dias_desde_aplicacion = (now() AT TIME ZONE 'Asia/Tokyo')::date - DATE '2026-03-11' "
        "FROM sigat.v_aplicacion_ultima_lote WHERE lote_id = 31",
    )
    assert (fecha, dias_ok) == ("2026-03-11", True)


def _lotes(h: SimpleNamespace, bd: str, funcion: str, usua: object) -> set[object]:
    return {f[0] for f in h.consulta(bd, f"SELECT lote_id FROM sigat.{funcion}({usua})")}  # noqa: S608 (constantes de la prueba)


@pytest.mark.parametrize(
    "funcion", ["fn_actividad_lote_detalle", "fn_aplicacion_ultima_lote", "fn_cosecha_lote_vigente"]
)
def test_casos_visibilidad_por_tipo_de_usuario(bd_casos: str, h: SimpleNamespace, funcion: str) -> None:
    """Varios predios propios, asignación a un predio ajeno, agrónomo (con una asignación vencida) y
    usuario sin predios."""
    esperado = {
        ANA: {11, 12, 13, 31},  # dueña de los predios 1 y 3
        BRUNO: {21},
        CARLA: {21},  # sin predios propios: solo el asignado (de Bruno)
        GIL: {11, 12, 13, 21},  # predios 1 y 2; la asignación al 3 venció el 2026-02-01
        SARA: set(),  # productora sin predios
        TECNICO: set(),
    }
    for usua, lotes in esperado.items():
        assert _lotes(h, bd_casos, funcion, usua) == lotes, (funcion, usua)


def test_casos_la_funcion_solo_filtra_no_transforma(bd_casos: str, h: SimpleNamespace) -> None:
    """Para el agrónomo (dos predios), las filas de la función son exactamente las de la vista en esos predios."""
    [(dif_a, dif_b)] = h.consulta(
        bd_casos,
        "SELECT (SELECT count(*) FROM ((SELECT * FROM sigat.fn_actividad_lote_detalle(4)) EXCEPT ALL "
        "        (SELECT * FROM sigat.v_actividad_lote_detalle WHERE pred_id IN (1, 2))) d), "
        "       (SELECT count(*) FROM ((SELECT * FROM sigat.v_actividad_lote_detalle WHERE pred_id IN (1, 2)) "
        "        EXCEPT ALL (SELECT * FROM sigat.fn_actividad_lote_detalle(4))) d)",
    )
    assert (dif_a, dif_b) == (0, 0)


@pytest.mark.parametrize(
    "funcion", ["fn_actividad_lote_detalle", "fn_aplicacion_ultima_lote", "fn_cosecha_lote_vigente"]
)
@pytest.mark.parametrize("argumento", ["", "NULL", "999"])
def test_casos_parametro_nulo_o_usuario_inexistente(
    bd_casos: str, h: SimpleNamespace, funcion: str, argumento: str
) -> None:
    """Sin argumento (fn_usuario_actual() es nulo fuera de una sesión de la API), NULL o un id inexistente:
    0 filas. Falla cerrada, nunca «todos los lotes»."""
    assert _lotes(h, bd_casos, funcion, argumento) == set()


def test_casos_lote_dado_de_baja_sigue_visible(bd_casos: str, h: SimpleNamespace) -> None:
    """Comportamiento ACTUAL (decisión pendiente G-06): un lote con lote_fecha_baja sigue en F3 con su
    última aplicación y en el historial F1. Si se decide ocultarlo, esta prueba debe cambiar."""
    assert h.consulta(bd_casos, "SELECT lote_fecha_baja::text FROM sigat.lote WHERE lote_id = 13") == [
        ("2026-04-01",)
    ]
    assert h.consulta(bd_casos, "SELECT producto FROM sigat.v_aplicacion_ultima_lote WHERE lote_id = 13") == [
        ("Empate captura tardía",)
    ]
    assert h.consulta(
        bd_casos,
        "SELECT count(*) FROM sigat.v_actividad_lote_detalle WHERE lote_id = 13 AND tipo = 'aplicacion'",
    ) == [(2,)]


def test_casos_aplicacion_corregida_que_cambia_de_lote(bd_casos: str, h: SimpleNamespace) -> None:
    """La versión vigente (v2, lote 11) manda: no queda rastro en el lote 12 de la v1."""
    reg = "00000000-0000-0000-0000-00000000e005"
    assert h.consulta(
        bd_casos,
        f"SELECT lote_id, detalle FROM sigat.v_actividad_lote_detalle WHERE registro_id = '{reg}'",  # noqa: S608 (constantes de la prueba)
    ) == [(11, "Movida v2")]
    ultima = dict(
        h.consulta(bd_casos, "SELECT lote_id, apli_registro_id::text FROM sigat.v_aplicacion_ultima_lote")
    )
    assert ultima[11] == reg  # 2026-03-20 es la más reciente del lote 11
    assert ultima[12] == "00000000-0000-0000-0000-00000000e003"  # sigue siendo A3, no la v1 de A5


def test_casos_empate_de_t_observacion_lo_decide_t_captura(bd_casos: str, h: SimpleNamespace) -> None:
    """Orden canónico R-T1: misma apli_t_observacion -> gana la de apli_t_captura mayor."""
    assert h.consulta(
        bd_casos,
        "SELECT apli_registro_id::text, producto FROM sigat.v_aplicacion_ultima_lote WHERE lote_id = 13",
    ) == [("00000000-0000-0000-0000-00000000e007", "Empate captura tardía")]


def test_casos_cosecha_anulada_no_aparece(bd_casos: str, h: SimpleNamespace) -> None:
    assert h.consulta(
        bd_casos,
        "SELECT lote_id, registro_id::text, detalle FROM sigat.v_actividad_lote_detalle WHERE tipo = 'cosecha'",
    ) == [(11, "00000000-0000-0000-0000-00000000f001", "Cosecha esperada · 07 dic 2026")]
    # La cosecha anulada existe en la tabla (2 versiones), pero no en el historial.
    assert h.consulta(
        bd_casos,
        "SELECT count(*), bool_or(cose_anulada) FROM sigat.cosecha "
        "WHERE cose_registro_id = '00000000-0000-0000-0000-00000000f002'",
    ) == [(2, True)]


def test_casos_valores_nulos_segun_el_tipo_de_actividad(bd_casos: str, h: SimpleNamespace) -> None:
    """Por tipo, si (trampas_en_lote, trampas_revisadas, trampas_leidas, producto, cose_fecha_esperada,
    linaje, es_inspeccion_oficial) son NULL en TODAS las filas; detalle, titulo y responsable nunca lo son."""
    assert h.consulta(
        bd_casos,
        "SELECT tipo, count(*), bool_and(trampas_en_lote IS NULL), bool_and(trampas_revisadas IS NULL), "
        "bool_and(trampas_leidas IS NULL), bool_and(producto IS NULL), bool_and(cose_fecha_esperada IS NULL), "
        "bool_and(linaje IS NULL), bool_and(es_inspeccion_oficial IS NULL), "
        "bool_and(detalle IS NOT NULL AND titulo IS NOT NULL AND responsable IS NOT NULL) "
        "FROM sigat.v_actividad_lote_detalle GROUP BY tipo ORDER BY tipo",
    ) == [
        ("aplicacion", 7, True, True, True, False, True, False, False, True),
        ("cosecha", 1, True, True, True, True, False, False, False, True),
        ("vigilancia", 6, False, False, False, True, True, True, True, True),
    ]


# ================================================================== F2: v_cosecha_lote_vigente
# Base PROPIA (bd_cosechas): DATOS (C1 en el lote 11: v1 abierta, v2 cerrada) más COSECHAS.
# Regla P-04 (decisión del responsable): UNA SOLA cosecha abierta por lote.
# * Lote 11: además de C1 (cerrada), C3 y C10 abiertas -> VIOLA P-04 (2 abiertas); se elige la más
#   reciente (C10) y cosechas_abiertas = 2 lo deja a la vista.
# * Lote 12: C4 y C5 cerradas (C5 más reciente); C11 abierta pero ANULADA en su versión 2.
# * Lote 13: C6 abierta (antigua) y C7 cerrada (más reciente) -> gana la abierta.
# * Lote 21: sin cosechas.
# Desde la revisión 0007 la base RECHAZA abrir C10 (trg_cosecha_una_abierta). El lote 11 representa
# datos HISTÓRICOS cargados antes de la regla, que la 0007 conserva sin tocar; para reproducirlos, solo
# este INSERT se hace con ese disparador desactivado (dentro de la transacción de ``ejecutar``) y se
# vuelve a activar enseguida. Los demás disparadores (versión, append-only, auditoría) siguen activos.
# La regla en sí se prueba en tests/integracion/test_regla_cosecha_abierta.py.
COSECHAS = r"""
ALTER TABLE cosecha DISABLE TRIGGER trg_cosecha_una_abierta;
INSERT INTO cosecha (cose_registro_id, cose_version, lote_id, cose_fecha_esperada, cose_volumen_esperado, cose_fecha_real,
                     cose_volumen_real, cose_volumen_uni_id, usua_id, disp_id, oper_id, cose_t_observacion, cose_t_captura,
                     cose_anulada, cose_motivo_anulacion)
SELECT v.reg::uuid, v.ver, v.lote, v.esperada::date, 100, v.real_f::date, v.real_v::numeric,
       (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'kg'), 2, '00000000-0000-0000-0000-0000000000d1',
       pg_temp.op('cosecha', 2, v.reg::uuid, v.ver), v.obs::timestamptz, v.obs::timestamptz + interval '1 minute',
       v.anulada, v.motivo
  FROM (VALUES
    (1, '00000000-0000-0000-0000-0000000f0003', 1, 11, '2026-12-20', NULL, NULL, '2026-03-25 08:00-05', false, NULL),
    (2, '00000000-0000-0000-0000-0000000f0010', 1, 11, '2026-12-28', NULL, NULL, '2026-03-26 08:00-05', false, NULL),
    (3, '00000000-0000-0000-0000-0000000f0004', 1, 12, '2026-06-01', '2026-06-03', '50', '2026-03-01 08:00-05', false, NULL),
    (4, '00000000-0000-0000-0000-0000000f0005', 1, 12, '2026-07-01', '2026-07-02', '60', '2026-03-20 08:00-05', false, NULL),
    (5, '00000000-0000-0000-0000-0000000f0011', 1, 12, '2026-09-01', NULL, NULL, '2026-03-27 08:00-05', false, NULL),
    (6, '00000000-0000-0000-0000-0000000f0011', 2, 12, '2026-09-01', NULL, NULL, '2026-03-27 08:00-05', true, 'Duplicada'),
    (7, '00000000-0000-0000-0000-0000000f0006', 1, 13, '2026-10-01', NULL, NULL, '2026-02-01 08:00-05', false, NULL),
    (8, '00000000-0000-0000-0000-0000000f0007', 1, 13, '2026-05-01', '2026-05-02', '40', '2026-03-28 08:00-05', false, NULL)
  ) v(orden, reg, ver, lote, esperada, real_f, real_v, obs, anulada, motivo)
 ORDER BY v.orden;
ALTER TABLE cosecha ENABLE TRIGGER trg_cosecha_una_abierta;
"""


@pytest.fixture(scope="module")
def bd_cosechas(h: SimpleNamespace) -> Iterator[str]:
    nombre = h.crear_bd()
    try:
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        ejecutar(h, nombre, DATOS + COSECHAS)
        yield nombre
    finally:
        h.borrar_bd(nombre)


def test_cosecha_vigente_por_lote(bd_cosechas: str, h: SimpleNamespace) -> None:
    """Una fila por lote: la abierta si existe (P-04), si no la más reciente; anuladas fuera; lote sin
    cosechas en NULL."""
    assert h.consulta(
        bd_cosechas,
        "SELECT lote_id, cose_registro_id::text, cose_version, cose_fecha_esperada::text, cose_fecha_real::text, "
        "volumen_unidad, abierta, cosechas_abiertas FROM sigat.v_cosecha_lote_vigente ORDER BY lote_id",
    ) == [
        (11, "00000000-0000-0000-0000-0000000f0010", 1, "2026-12-28", None, "kg", True, 2),  # viola P-04
        (12, "00000000-0000-0000-0000-0000000f0005", 1, "2026-07-01", "2026-07-02", "kg", False, 0),
        (13, "00000000-0000-0000-0000-0000000f0006", 1, "2026-10-01", None, "kg", True, 1),
        (21, None, None, None, None, None, None, 0),
    ]


def test_cosechas_historicas_no_dejan_la_regla_p04_desactivada(bd_cosechas: str, h: SimpleNamespace) -> None:
    """COSECHAS desactiva trg_cosecha_una_abierta solo para cargar los datos históricos: debe quedar activo."""
    assert h.consulta(
        bd_cosechas,
        "SELECT tgenabled::text FROM pg_trigger WHERE tgrelid = 'sigat.cosecha'::regclass "
        "AND tgname = 'trg_cosecha_una_abierta'",
    ) == [("O",)]


def test_cosecha_vigente_usa_la_ultima_version(bd: str, h: SimpleNamespace) -> None:
    """En la base principal de la prueba, C1 (lote 11) tiene v1 abierta y v2 cerrada: manda la v2."""
    assert h.consulta(
        bd,
        "SELECT lote_id, cose_version, cose_fecha_esperada::text, cose_fecha_real::text, cose_volumen_real, abierta, "
        "cosechas_abiertas, cose_linaje FROM sigat.v_cosecha_lote_vigente WHERE lote_id = 11",
    ) == [(11, 2, "2026-12-07", "2026-12-08", Decimal("100.000"), False, 0, "autorreporte")]


def test_cosecha_vigente_separa_productores(bd_cosechas: str, h: SimpleNamespace) -> None:
    def lotes(usua: object) -> set[object]:
        sql = f"SELECT lote_id FROM sigat.fn_cosecha_lote_vigente({usua})"  # noqa: S608 (constantes de la prueba)
        return {f[0] for f in h.consulta(bd_cosechas, sql)}

    assert lotes(ANA) == {11, 12, 13}
    assert lotes(BRUNO) == {21}
    assert lotes(TECNICO) == set()
    assert lotes("NULL") == set()
    assert lotes(999) == set()
