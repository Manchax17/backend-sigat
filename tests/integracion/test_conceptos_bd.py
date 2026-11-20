"""Consulta de conceptos agronómicos (issue #9) contra PostgreSQL REAL.

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Usa una base TEMPORAL que se borra al final.
Ejecuta el servicio con el repositorio real, dentro de una transacción con
``sigat.usuario_actual`` = actor, igual que la API.

Escenario (lote 11 de durazno, predio de Ana; la agrónoma está asignada a ese predio):

* Plantado el 2025-12-01. Regla fenológica: días 0-99 Floración, 100-400 Cuajado
  (2026-03-10 = día 99, 2026-03-12 = día 101). Ana declaró Floración el 2026-03-08.
* P1 (03-01 a 03-07, cerrado): MTD v1 = 2,1 sin umbral -> concepto v1 ``no_emitido`` (emitido el 03-07);
  recálculo v2 = 1,4 con umbral 0,5 -> concepto v2 ``no_apto`` (emitido el 03-20).
* P2 (03-08 a 03-14, abierto): MTD 0,071429 con umbral 0,5 -> concepto ``apto`` con efecto el 03-12,
  emitido el 03-12 a las 21:00 de Bogotá (03-13 02:00 UTC: prueba la zona horaria de "ese día").
* Lote 21 (predio de Bruno): sin fecha de plantación, sin periodos.
"""

import asyncio
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sigat_api.compartido.errores import NoEncontrado
from sigat_api.compartido.tipos import Actor, Cliente, ResultadoConcepto, ResultadoContraste, RolCodigo
from sigat_api.modules.concepto.repositorio import RepositorioConcepto
from sigat_api.modules.concepto.servicio import SIN_CONCEPTO, ServicioConcepto

pytestmark = pytest.mark.integracion

BRUNO = Actor(usua_id=3, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)
AGRONOMO = Actor(usua_id=4, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)

DATOS = r"""
SET search_path = sigat, public;

INSERT INTO region (regi_id, regi_descripcion) VALUES (1, 'Región de prueba');
INSERT INTO departamento (depa_id, depa_descripcion, regi_id, depa_codigo) VALUES (1, 'Depto', 1, '99');
INSERT INTO municipio (muni_id, muni_descripcion, depa_id, muni_codigo) VALUES (1, 'Muni', 1, '001');
INSERT INTO centro_acopio (cent_id, cent_descripcion, depa_id) VALUES (1, 'Centro', 1);
INSERT INTO persona (pers_cc, pers_nombre, pers_apellido) VALUES
  (1001, 'Adela', 'Admin'), (1002, 'Tomás', 'Técnico'), (2001, 'Ana', 'Productora'),
  (2002, 'Bruno', 'Productor'), (3001, 'Gloria', 'Agrónoma');
INSERT INTO administrador (admi_id, pers_cc, cent_id) VALUES (1, 1001, 1);
INSERT INTO tecnico (tecn_id, pers_cc, cent_id, admi_id, tecn_fecha_registro, esta_id)
  VALUES (1, 1002, 1, 1, DATE '2026-01-01', (SELECT esta_id FROM estado_registro WHERE esta_descripcion = 'activo'));
INSERT INTO productor (pers_cc, prod_fecha_registro, prod_tecnico_registro_id, muni_id) VALUES
  (2001, DATE '2026-01-01', 1, 1), (2002, DATE '2026-01-01', 1, 1);
INSERT INTO usuario (usua_id, pers_cc, usua_usuario, usua_requiere_reinicio, rol_id, esta_id)
SELECT v.id, v.cc, v.usu, true, r.rol_id, e.esta_id
  FROM (VALUES (1, 1002::bigint, 'tecnico.prueba', 'TECNICO'),
               (2, 2001::bigint, 'ana.prueba', 'PRODUCTOR'),
               (3, 2002::bigint, 'bruno.prueba', 'PRODUCTOR'),
               (4, 3001::bigint, 'agronoma.prueba', 'AGRONOMO_SUPERVISOR')) v(id, cc, usu, rol)
  JOIN rol r ON r.rol_codigo = v.rol
  JOIN estado_registro e ON e.esta_descripcion = 'activo';

INSERT INTO predio (pred_id, pred_nombre, muni_id, pred_area, pers_cc, pred_fecha_registro, pred_tecnico_registro_id)
  VALUES (1, 'Predio Ana', 1, 10, 2001, DATE '2026-01-01', 1), (2, 'Predio Bruno', 1, 10, 2002, DATE '2026-01-01', 1);
INSERT INTO usuario_predio (usua_id, pred_id, usup_desde) VALUES (4, 1, DATE '2026-01-01');
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, espc_id, lote_fecha_plantacion,
                  lote_fecha_registro, lote_tecnico_registro_id) VALUES
  (11, 'L11', 'Lote 1 · Norte', 2, 1, (SELECT espc_id FROM especie_cultivo WHERE espc_descripcion = 'Durazno'),
   DATE '2025-12-01', DATE '2026-01-01', 1),
  (21, 'L21', 'Lote único', 2, 2, NULL, NULL, DATE '2026-01-01', 1);

INSERT INTO fenologia (feno_id, feno_descripcion) VALUES (1, 'Floración'), (2, 'Cuajado');
INSERT INTO fenologia_regla (espc_id, feno_id, fenr_dias_desde, fenr_dias_hasta, fenr_vigente_desde)
SELECT espc_id, v.feno, v.desde, v.hasta, DATE '2025-01-01'
  FROM especie_cultivo, (VALUES (1, 0, 99), (2, 100, 400)) v(feno, desde, hasta)
 WHERE espc_descripcion = 'Durazno';

INSERT INTO vigilancia (vigi_id, vigi_descripcion) VALUES (1, 'Vigilancia de prueba');
INSERT INTO norma (norm_id, norm_codigo, norm_titulo, norm_emisor) VALUES (1, 'RES-PRUEBA', 'Norma de prueba', 'ICA');
INSERT INTO programa_inspeccion (prog_id, pred_id, prog_version, prog_vigente_desde, prog_periodo_lectura_dias,
                                 prog_distancia_min_trampas_m)
  VALUES (1, 1, 1, DATE '2026-01-01', 7, 50);
INSERT INTO periodo_trampeo (peri_id, lote_id, vigi_id, prog_id, peri_inicio, peri_fin, peri_estado) VALUES
  (1, 11, 1, 1, DATE '2026-03-01', DATE '2026-03-07', 'cerrado'),
  (2, 11, 1, 1, DATE '2026-03-08', DATE '2026-03-14', 'abierto');

INSERT INTO indice_mtd (indi_id, peri_id, indi_version, indi_moscas, indi_trampas, indi_dias_exposicion,
                        indi_umbral_aplicado, norm_id, indi_resultado_contraste, esce_id, indi_calculado_en,
                        indi_motivo_recalculo) VALUES
  (101, 1, 1, 21, 1, 10, NULL, NULL, 'sin_umbral', NULL, '2026-03-07 09:00-05', NULL),
  (102, 1, 2, 14, 1, 10, 0.5, 1, 'igual_o_superior_umbral', (SELECT min(esce_id) FROM escenario_trampeo),
   '2026-03-20 09:00-05', 'Corrección de lectura'),
  (103, 2, 1, 1, 2, 7, 0.5, 1, 'por_debajo_umbral', NULL, '2026-03-12 20:00-05', NULL);

INSERT INTO concepto_aptitud (conc_id, lote_id, peri_id, indi_id, conc_version, conc_resultado, conc_fecha_efecto,
                              norm_id, conc_emitido_en, conc_motivo) VALUES
  (201, 11, 1, 101, 1, 'no_emitido', DATE '2026-03-05', NULL, '2026-03-07 10:00-05', NULL),
  (202, 11, 1, 102, 2, 'no_apto', DATE '2026-03-05', 1, '2026-03-20 10:00-05', 'Recálculo por corrección'),
  (203, 11, 2, 103, 1, 'apto', DATE '2026-03-12', 1, '2026-03-12 21:00-05', NULL);

INSERT INTO dispositivo (disp_id, disp_plataforma) VALUES ('00000000-0000-0000-0000-0000000000d1', 'android');
INSERT INTO etapa_declarada (etap_registro_id, lote_id, feno_id, etap_linaje, usua_id, disp_id, oper_id,
                             etap_t_observacion, etap_t_captura)
SELECT '00000000-0000-0000-0000-00000000a002', 11, 1, 'autorreporte', 2, '00000000-0000-0000-0000-0000000000d1',
       o.oper_id, '2026-03-08 08:00-05', '2026-03-08 08:05-05'
  FROM (INSERT INTO operacion_sync (disp_id, oper_cliente_id, usua_id, oper_entidad, oper_accion, oper_registro_id,
                                    oper_version, oper_hash_contenido, oper_clasificacion, oper_t_captura)
        VALUES ('00000000-0000-0000-0000-0000000000d1', gen_random_uuid(), 2, 'etapa_declarada', 'crear',
                '00000000-0000-0000-0000-00000000a002', 1, md5('a') || md5('b'), 'alta_nueva',
                TIMESTAMPTZ '2026-03-08 08:05-05')
        RETURNING oper_id) o;
"""


def _dsn(h: SimpleNamespace, nombre: str) -> str:
    return str(h.url(nombre)).replace("postgresql+asyncpg://", "postgresql://", 1)


def _cargar(h: SimpleNamespace, nombre: str) -> None:
    async def _correr() -> None:
        con = await asyncpg.connect(_dsn(h, nombre))
        try:
            async with con.transaction():
                await con.execute(DATOS)
        finally:
            await con.close()

    asyncio.run(_correr())


@pytest.fixture(scope="module")
def bd(h: SimpleNamespace) -> Iterator[str]:
    nombre = h.crear_bd()
    try:
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        _cargar(h, nombre)
        yield nombre
    finally:
        h.borrar_bd(nombre)


def consulta(h: SimpleNamespace, bd: str, actor: Actor, metodo: str, *args: Any) -> Any:
    """Una transacción por llamada con ``sigat.usuario_actual`` = actor, como ``api.dependencias.conexion``."""

    async def _correr() -> Any:
        motor = create_async_engine(h.url(bd))
        try:
            async with motor.begin() as con:
                await con.execute(
                    text("SELECT set_config('sigat.usuario_actual', CAST(:u AS text), true)"),
                    {"u": str(actor.usua_id)},
                )
                svc = ServicioConcepto(RepositorioConcepto(con), "America/Bogota")
                return await getattr(svc, metodo)(actor, *args)
        finally:
            await motor.dispose()

    return asyncio.run(_correr())


# ------------------------------------------------------------------ visibilidad
@pytest.mark.parametrize(
    ("metodo", "args"),
    [
        ("resumen_lote", (11,)),
        ("serie_mtd", (11, None, None)),
        ("periodos", (11,)),
        ("versiones_mtd", (11, 1)),
        ("conceptos", (11,)),
        ("concepto_a_fecha", (11, date(2026, 3, 12))),
        ("etapa", (11, None)),
    ],
)
def test_lote_de_otro_productor_da_404(
    h: SimpleNamespace, bd: str, metodo: str, args: tuple[Any, ...]
) -> None:
    with pytest.raises(NoEncontrado):
        consulta(h, bd, BRUNO, metodo, *args)


def test_lote_inexistente_y_periodo_de_otro_lote_dan_404(h: SimpleNamespace, bd: str) -> None:
    with pytest.raises(NoEncontrado):
        consulta(h, bd, AGRONOMO, "resumen_lote", 999)
    with pytest.raises(NoEncontrado):
        consulta(h, bd, AGRONOMO, "versiones_mtd", 11, 999)


# ------------------------------------------------------------------ resumen
def test_resumen_con_informacion_real(h: SimpleNamespace, bd: str) -> None:
    r = consulta(h, bd, AGRONOMO, "resumen_lote", 11)

    assert r.lote_descripcion == "Lote 1 · Norte"
    assert r.mtd == Decimal("0.071429")  # el del periodo más reciente
    assert r.contraste == ResultadoContraste.POR_DEBAJO_UMBRAL
    assert r.umbral == Decimal("0.5")
    assert r.concepto == ResultadoConcepto.APTO
    assert r.concepto_fecha_efecto == date(2026, 3, 12)
    assert r.periodo_actual is not None
    assert r.periodo_actual.peri_id == 2
    assert [(p.peri_id, p.mtd) for p in r.serie] == [(1, Decimal("1.4")), (2, Decimal("0.071429"))]
    assert r.etapa.declarada == "Floración"
    # sin visitas, lecturas ni trampas: ceros y nulos, no valores ficticios
    assert (r.inspecciones, r.lecturas, r.trampas_vigentes, r.trampas_con_capturas) == (0, 0, 0, 0)
    assert r.ultima_visita is None
    assert r.cosecha_esperada is None
    assert r.revision_pendiente is None


def test_resumen_de_lote_sin_informacion(h: SimpleNamespace, bd: str) -> None:
    # Bruno sí ve su lote 21, que no tiene periodos ni MTD.
    r = consulta(h, bd, BRUNO, "resumen_lote", 21)
    assert r.mtd is None
    assert r.umbral is None
    assert r.concepto is None
    assert r.periodo_actual is None
    assert r.serie == []
    assert r.etapa.derivada is None
    assert r.etapa.discrepancia is False


# ------------------------------------------------------------------ serie, periodos, versiones, conceptos
def test_serie_filtra_por_solapamiento_de_fechas(h: SimpleNamespace, bd: str) -> None:
    serie = consulta(h, bd, AGRONOMO, "serie_mtd", 11, date(2026, 3, 8), None)
    assert [p.peri_id for p in serie] == [2]
    p1 = consulta(h, bd, AGRONOMO, "serie_mtd", 11, None, date(2026, 3, 7))[0]
    assert (p1.moscas, p1.trampas, p1.dias_exposicion) == (14, 1, Decimal("10"))
    assert p1.escenario is not None  # el escenario se publica con su descripción


def test_periodos_del_lote(h: SimpleNamespace, bd: str) -> None:
    periodos = consulta(h, bd, AGRONOMO, "periodos", 11)
    assert [(p.peri_id, p.peri_estado, p.prog_id) for p in periodos] == [(1, "cerrado", 1), (2, "abierto", 1)]


def test_versiones_del_mtd_conservan_el_valor_anterior_y_el_motivo(h: SimpleNamespace, bd: str) -> None:
    versiones = consulta(h, bd, AGRONOMO, "versiones_mtd", 11, 1)
    assert [
        (v.indi_version, v.indi_valor, v.indi_resultado_contraste, v.indi_motivo_recalculo) for v in versiones
    ] == [
        (1, Decimal("2.1"), ResultadoContraste.SIN_UMBRAL, None),
        (2, Decimal("1.4"), ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL, "Corrección de lectura"),
    ]


def test_conceptos_devuelve_todas_las_versiones_con_su_mtd_y_norma(h: SimpleNamespace, bd: str) -> None:
    conceptos = consulta(h, bd, AGRONOMO, "conceptos", 11)
    assert [(c.conc_id, c.peri_id, c.conc_version, c.conc_resultado, c.norma, c.mtd) for c in conceptos] == [
        (201, 1, 1, ResultadoConcepto.NO_EMITIDO, None, Decimal("2.1")),
        (202, 1, 2, ResultadoConcepto.NO_APTO, "RES-PRUEBA", Decimal("1.4")),
        (203, 2, 1, ResultadoConcepto.APTO, "RES-PRUEBA", Decimal("0.071429")),
    ]


# ------------------------------------------------------------------ RF-024
@pytest.mark.regla
@pytest.mark.parametrize(
    ("fecha", "conc_id", "inicio"),
    [
        (date(2026, 3, 4), None, SIN_CONCEPTO),  # antes de la primera fecha de efecto
        # El 03-10 solo se conocía la v1 de P1 (la v2 se emitió el 03-20): no cambia el pasado.
        (date(2026, 3, 10), 201, "No emitido: no había umbral"),
        # Emitido el 03-12 a las 21:00 de Bogotá (03-13 en UTC): cuenta para "ese día" en Colombia.
        (date(2026, 3, 12), 203, "Apto"),
        (date(2026, 3, 25), 203, "Apto"),
    ],
)
def test_concepto_a_fecha_devuelve_el_que_regia_ese_dia(
    h: SimpleNamespace, bd: str, fecha: date, conc_id: int | None, inicio: str
) -> None:
    r = consulta(h, bd, AGRONOMO, "concepto_a_fecha", 11, fecha)
    assert r.fecha_consulta == fecha
    assert (r.concepto.conc_id if r.concepto else None) == conc_id
    assert r.explicacion.startswith(inicio)


# ------------------------------------------------------------------ RN-012
@pytest.mark.regla
@pytest.mark.parametrize(
    ("fecha", "derivada", "declarada", "discrepancia"),
    [
        (date(2026, 3, 7), "Floración", None, False),  # todavía no había declaración
        (date(2026, 3, 10), "Floración", "Floración", False),
        (date(2026, 3, 12), "Cuajado", "Floración", True),
    ],
)
def test_etapa_derivada_y_declarada(
    h: SimpleNamespace, bd: str, fecha: date, derivada: str, declarada: str | None, discrepancia: bool
) -> None:
    etapa = consulta(h, bd, AGRONOMO, "etapa", 11, fecha)
    assert (etapa.derivada, etapa.declarada, etapa.discrepancia) == (derivada, declarada, discrepancia)
