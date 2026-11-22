"""``ServicioLotes.listar`` contra PostgreSQL REAL (repositorio + ``sigat.fn_lote_app``).

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Usa una base TEMPORAL que se borra al final.
La base es de solo lectura para estas pruebas: ningún test modifica datos, por lo que el orden de
ejecución no importa.

Escenario de datos:

* Predio 1 (Ana): lotes 11 y 12, y lote 13 DADO DE BAJA (no debe aparecer para nadie).
  El lote 12 tiene 2 trampas vigentes, 1 retirada y un periodo de trampeo abierto.
  El lote 11 tiene un periodo de trampeo cerrado y ninguna trampa vigente.
* Predio 2 (Bruno): lote 21 (sin cultivo, variedad ni trampas) y lote 22 (Durazno / Rubidoux).
* Predio 3 (Ana): lote 31.
* Gil (AGRONOMO_SUPERVISOR): asignado a los predios 1 y 2; su asignación al 3 venció el 2026-02-01.
* Adela (ADMINISTRADOR): ve todos los lotes vigentes.
* Tania (TECNICO): actividad PENDIENTE sobre el lote 21 (visible) y actividad COMPLETADA sobre el
  lote 11 (historial: ya no es visible). Según ``v_asignacion_lote_tecnico`` la vigencia de un
  técnico depende del estado de la planificación (``pendiente``), no de las fechas.
* Sara (productora sin predios) y Tomás (técnico sin planificaciones) no ven ningún lote.

Las planificaciones se crean siguiendo el ciclo real exigido por los triggers
(``fn_planificacion_valida``, ``fn_planificacion_lote_valida``): el agrónomo crea la planificación
al productor, el productor la acepta y el agrónomo deriva la actividad al técnico.
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.repositorio import RepositorioConfiguracion
from sigat_api.modules.configuracion.servicio import ServicioLotes

pytestmark = pytest.mark.integracion

TECNICO, ANA, BRUNO, GIL, SARA, ADMIN, TANIA = 1, 2, 3, 4, 5, 6, 7

DATOS = r"""
SET search_path = sigat, public;

INSERT INTO region (regi_id, regi_descripcion) VALUES (1, 'Región de prueba');
INSERT INTO departamento (depa_id, depa_descripcion, regi_id, depa_codigo) VALUES (1, 'Depto', 1, '99');
INSERT INTO municipio (muni_id, muni_descripcion, depa_id, muni_codigo) VALUES (1, 'Muni', 1, '001');
INSERT INTO centro_acopio (cent_id, cent_descripcion, depa_id) VALUES (1, 'Centro', 1);
INSERT INTO persona (pers_cc, pers_nombre, pers_apellido) VALUES
  (1001, 'Adela', 'Admin'), (1002, 'Tomás', 'Técnico'), (1003, 'Tania', 'Técnica'),
  (2001, 'Ana', 'Productora'), (2002, 'Bruno', 'Productor'),
  (3001, 'Gil', 'Agrónomo'), (3002, 'Sara', 'SinPredio');
INSERT INTO administrador (admi_id, pers_cc, cent_id) VALUES (1, 1001, 1);
INSERT INTO tecnico (tecn_id, pers_cc, cent_id, admi_id, tecn_fecha_registro, esta_id) VALUES
  (1, 1002, 1, 1, DATE '2026-01-01', (SELECT esta_id FROM estado_registro WHERE esta_descripcion = 'activo')),
  (2, 1003, 1, 1, DATE '2026-01-01', (SELECT esta_id FROM estado_registro WHERE esta_descripcion = 'activo'));
INSERT INTO productor (pers_cc, prod_fecha_registro, prod_tecnico_registro_id, muni_id) VALUES
  (2001, DATE '2026-01-01', 1, 1), (2002, DATE '2026-01-01', 1, 1);
INSERT INTO usuario (usua_id, pers_cc, usua_usuario, usua_requiere_reinicio, rol_id, esta_id)
SELECT v.id, v.cc, v.usu, true, r.rol_id, e.esta_id
  FROM (VALUES (1, 1002::bigint, 'tecnico.prueba', 'TECNICO'),
               (2, 2001::bigint, 'ana.prueba', 'PRODUCTOR'),
               (3, 2002::bigint, 'bruno.prueba', 'PRODUCTOR'),
               (4, 3001::bigint, 'gil.prueba', 'AGRONOMO_SUPERVISOR'),
               (5, 3002::bigint, 'sara.prueba', 'PRODUCTOR'),
               (6, 1001::bigint, 'admin.prueba', 'ADMINISTRADOR'),
               (7, 1003::bigint, 'tania.prueba', 'TECNICO')) v(id, cc, usu, rol)
  JOIN rol r ON r.rol_codigo = v.rol
  JOIN estado_registro e ON e.esta_descripcion = 'activo';

INSERT INTO predio (pred_id, pred_nombre, muni_id, pred_area, pers_cc, pred_fecha_registro, pred_tecnico_registro_id)
  VALUES (1, 'Predio Ana', 1, 10, 2001, DATE '2026-01-01', 1),
         (2, 'Predio Bruno', 1, 10, 2002, DATE '2026-01-01', 1),
         (3, 'Predio Ana 2', 1, 5, 2001, DATE '2026-01-01', 1);

INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro, lote_tecnico_registro_id) VALUES
  (11, 'L11', 'Lote 1 · Norte', 2, 1, DATE '2026-01-01', 1),
  (12, 'L12', 'Lote 2 · Centro', 2, 1, DATE '2026-01-01', 1),
  (21, 'L21', 'Lote único', 2, 2, DATE '2026-01-01', 1),
  (31, 'L31', 'Lote del predio 3', 1, 3, DATE '2026-01-01', 1);

-- Variedad de cultivo (el catálogo base solo siembra la especie 'Durazno').
INSERT INTO variedad_cultivo (vari_id, espc_id, vari_descripcion)
  SELECT 1, espc_id, 'Rubidoux'
    FROM especie_cultivo
   WHERE espc_descripcion = 'Durazno';

-- Lote con cultivo y variedad.
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro,
                  lote_tecnico_registro_id, espc_id, vari_id)
  SELECT 22, 'L22', 'Lote con cultivo', 2, 2, DATE '2026-01-01', 1, v.espc_id, v.vari_id
    FROM variedad_cultivo v
   ORDER BY v.vari_id
   LIMIT 1;

-- Lote dado de baja: no debe listarse para nadie.
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro,
                  lote_tecnico_registro_id, lote_fecha_baja)
  VALUES (13, 'L13', 'Lote de baja', 1, 1, DATE '2026-01-01', 1, DATE '2026-02-01');

INSERT INTO usuario_predio (usua_id, pred_id, usup_desde, usup_hasta) VALUES
  (4, 1, DATE '2026-01-01', NULL),
  (4, 2, DATE '2026-01-01', NULL),
  (4, 3, DATE '2026-01-01', DATE '2026-02-01'),  -- asignación vencida: no da acceso
  (7, 1, DATE '2026-01-01', NULL),               -- requisito de fn_planificacion_lote_valida;
  (7, 2, DATE '2026-01-01', NULL);               -- no da visibilidad de lotes por sí solo

DO $$
DECLARE
  v_plan_prod_1 uuid;
  v_plan_prod_2 uuid;
  v_plan_tec_11 uuid;
  v_plan_tec_21 uuid;
BEGIN
  -- Gil (4, AGRONOMO_SUPERVISOR) crea las planificaciones raíz para Ana (2) y Bruno (3).
  PERFORM set_config('sigat.usuario_actual', '4', true);
  INSERT INTO planificacion (usua_id_destinatario, usua_id_creador, plan_actividad,
                             plan_fecha_inicio, plan_fecha_fin, plan_estado)
  VALUES (2, 4, 'Plan predio 1', current_date - 40, current_date + 30, 'pendiente')
  RETURNING plan_id INTO v_plan_prod_1;
  INSERT INTO planificacion_lote (plan_id, lote_id) VALUES (v_plan_prod_1, 11);

  INSERT INTO planificacion (usua_id_destinatario, usua_id_creador, plan_actividad,
                             plan_fecha_inicio, plan_fecha_fin, plan_estado)
  VALUES (3, 4, 'Plan predio 2', current_date - 1, current_date + 30, 'pendiente')
  RETURNING plan_id INTO v_plan_prod_2;
  INSERT INTO planificacion_lote (plan_id, lote_id) VALUES (v_plan_prod_2, 21);

  -- Ana y Bruno aceptan sus planificaciones.
  PERFORM set_config('sigat.usuario_actual', '2', true);
  UPDATE planificacion SET plan_estado = 'aceptada' WHERE plan_id = v_plan_prod_1;
  PERFORM set_config('sigat.usuario_actual', '3', true);
  UPDATE planificacion SET plan_estado = 'aceptada' WHERE plan_id = v_plan_prod_2;

  -- Gil deriva las actividades a Tania (7, TECNICO): nacen 'pendiente'.
  PERFORM set_config('sigat.usuario_actual', '4', true);
  INSERT INTO planificacion (usua_id_destinatario, usua_id_creador, plan_actividad,
                             plan_fecha_inicio, plan_fecha_fin, plan_estado, plan_id_origen)
  VALUES (7, 4, 'Visita vigente', current_date - 1, current_date + 30, 'pendiente', v_plan_prod_2)
  RETURNING plan_id INTO v_plan_tec_21;
  INSERT INTO planificacion_lote (plan_id, lote_id) VALUES (v_plan_tec_21, 21);

  INSERT INTO planificacion (usua_id_destinatario, usua_id_creador, plan_actividad,
                             plan_fecha_inicio, plan_fecha_fin, plan_estado, plan_id_origen)
  VALUES (7, 4, 'Visita completada', current_date - 40, current_date - 10, 'pendiente', v_plan_prod_1)
  RETURNING plan_id INTO v_plan_tec_11;
  INSERT INTO planificacion_lote (plan_id, lote_id) VALUES (v_plan_tec_11, 11);

  -- Tania ejecuta el lote 11 y completa esa actividad: pasa a historial (deja de ser vigente).
  PERFORM set_config('sigat.usuario_actual', '7', true);
  UPDATE planificacion_lote SET plal_estado = 'ejecutada' WHERE plan_id = v_plan_tec_11 AND lote_id = 11;
  UPDATE planificacion SET plan_estado = 'completada' WHERE plan_id = v_plan_tec_11;

  PERFORM set_config('sigat.usuario_actual', '', true);
END $$;

-- Trampas y periodos para probar trampas_vigentes y vigilancia_activa (lotes 11 y 12).
INSERT INTO vigilancia (vigi_id, vigi_descripcion, vigi_codigo) VALUES (1, 'Detección', 1);

INSERT INTO programa_inspeccion (prog_id, pred_id, prog_version, prog_vigente_desde,
                                 prog_periodo_lectura_dias, prog_distancia_min_trampas_m)
VALUES (1, 1, 1, DATE '2026-01-01', 7, 50);

INSERT INTO periodo_trampeo (peri_id, lote_id, vigi_id, prog_id, peri_inicio, peri_fin, peri_estado) VALUES
  (1, 12, 1, 1, current_date - 7, current_date, 'abierto'),
  (2, 11, 1, 1, DATE '2026-01-01', DATE '2026-01-14', 'cerrado');

INSERT INTO trampa (tram_id, tram_codigo, tipt_id, atra_id, esta_id, tram_fecha_registro, tram_tecnico_registro_id)
SELECT v.id, v.cod, t.tipt_id, a.atra_id, e.esta_id, DATE '2026-01-01', 1
  FROM (VALUES (1, '99001-602-001', 'Jackson'),   -- impar
               (2, '99001-602-002', 'McPhail'),   -- par
               (3, '99001-602-003', 'Jackson')) v(id, cod, tipo)
  JOIN tipo_trampa t ON t.tipt_descripcion = v.tipo
  JOIN atrayente a ON a.atra_descripcion = 'Plug'
  JOIN estado_registro e ON e.esta_descripcion = 'activo';

INSERT INTO ubicacion_trampa (tram_id, pred_id, lote_id, ubic_fecha, ubic_fecha_fin, tecn_id, vigi_id,
                              ubic_long, ubic_lat) VALUES
  (1, 1, 12, DATE '2026-01-01', NULL,              1, 1, -72.50000, 7.89000),  -- vigente
  (2, 1, 12, DATE '2026-01-01', NULL,              1, 1, -72.50100, 7.89100),  -- vigente
  (3, 1, 12, DATE '2026-01-01', DATE '2026-02-01', 1, 1, -72.50200, 7.89200);  -- retirada: no suma
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


def listar(
    h: SimpleNamespace,
    bd: str,
    usua: int,
    pred_id: int | None = None,
    indicadores: bool = False,
) -> list[e.Lote]:
    """Llama al servicio real con el repositorio real sobre la base temporal."""

    async def _correr() -> list[e.Lote]:
        motor = create_async_engine(h.url(bd))
        try:
            async with motor.connect() as con:
                svc = ServicioLotes(RepositorioConfiguracion(con), cola=None)  # type: ignore[arg-type]
                return await svc.listar(SimpleNamespace(usua_id=usua), pred_id, indicadores)  # type: ignore[arg-type]
        finally:
            await motor.dispose()

    return asyncio.run(_correr())


def ids(lotes: list[e.Lote]) -> list[int]:
    return [lote.lote_id for lote in lotes]


def lote_por_id(lotes: list[e.Lote], lote_id: int) -> e.Lote:
    return next(lote for lote in lotes if lote.lote_id == lote_id)


# ------------------------------------------------------------------ visibilidad
@pytest.mark.parametrize(
    ("usuario", "esperado"),
    [
        (ANA, [11, 12, 31]),  # dueña de los predios 1 y 3
        (BRUNO, [21, 22]),
        (GIL, [11, 12, 21, 22]),  # agrónomo: predios 1 y 2; la asignación al 3 venció
        (ADMIN, [11, 12, 21, 22, 31]),  # administrador: todos los vigentes (sin el 13 de baja)
        (TANIA, [21]),  # técnico: solo la actividad pendiente (la del lote 11 está completada)
        (SARA, []),  # productora sin predios
        (TECNICO, []),  # técnico sin planificaciones
        (999, []),  # usuario inexistente: falla cerrado
    ],
)
def test_listar_devuelve_solo_los_lotes_visibles_ordenados(
    bd: str, h: SimpleNamespace, usuario: int, esperado: list[int]
) -> None:
    assert ids(listar(h, bd, usuario)) == esperado


def test_listar_excluye_lotes_dados_de_baja(bd: str, h: SimpleNamespace) -> None:
    """El lote 13 existe pero tiene ``lote_fecha_baja``: ni siquiera el administrador lo ve."""
    for usuario in (ADMIN, ANA, GIL):
        assert 13 not in ids(listar(h, bd, usuario))


# ------------------------------------------------------------------ filtro por predio
@pytest.mark.parametrize(
    ("usuario", "pred_id", "esperado"),
    [
        (GIL, 1, [11, 12]),
        (GIL, 2, [21, 22]),
        (GIL, 3, []),  # su asignación al predio 3 venció
        (GIL, 999, []),  # predio inexistente
        (ANA, 3, [31]),
        (ANA, 2, []),  # predio de otro productor: no se revela
        (SARA, 1, []),
        (ADMIN, 2, [21, 22]),
        (TANIA, 1, []),  # su actividad sobre el lote 11 está completada
        (TANIA, 2, [21]),
    ],
)
def test_listar_filtra_por_predio_sin_saltarse_la_visibilidad(
    bd: str, h: SimpleNamespace, usuario: int, pred_id: int, esperado: list[int]
) -> None:
    assert ids(listar(h, bd, usuario, pred_id)) == esperado


# ------------------------------------------------------------------ contenido
def test_listar_trae_los_campos_del_contrato_sin_cultivo(bd: str, h: SimpleNamespace) -> None:
    lote = lote_por_id(listar(h, bd, GIL, pred_id=2), 21)
    assert lote.lote_codigo == "L21"
    assert lote.lote_descripcion == "Lote único"
    assert float(lote.lote_area) == 2.0
    assert (lote.pred_id, lote.pred_nombre) == (2, "Predio Bruno")
    assert lote.productor == "Bruno Productor"
    assert lote.municipio == "Muni"
    assert (lote.cultivo, lote.variedad) == (None, None)
    assert lote.vigilancia_activa is False
    assert lote.trampas_vigentes == 0


def test_listar_trae_cultivo_y_variedad_cuando_el_lote_los_tiene(bd: str, h: SimpleNamespace) -> None:
    lote = lote_por_id(listar(h, bd, GIL, pred_id=2), 22)
    assert (lote.cultivo, lote.variedad) == ("Durazno", "Rubidoux")


def test_listar_cuenta_trampas_vigentes_y_vigilancia_activa(bd: str, h: SimpleNamespace) -> None:
    """Lote 12: 2 ubicaciones vigentes, 1 retirada y periodo 'abierto'.
    Lote 11: 0 trampas vigentes y solo un periodo 'cerrado'."""
    lotes = listar(h, bd, GIL, pred_id=1)

    lote_12 = lote_por_id(lotes, 12)
    assert lote_12.trampas_vigentes == 2
    assert lote_12.vigilancia_activa is True

    lote_11 = lote_por_id(lotes, 11)
    assert lote_11.trampas_vigentes == 0
    assert lote_11.vigilancia_activa is False


# ------------------------------------------------------------------ indicadores
def test_listar_con_indicadores_los_deja_en_null_mientras_no_tengan_fuente(
    bd: str, h: SimpleNamespace
) -> None:
    """PENDIENTE DE REVISIÓN: ``IndicadoresLote`` no tiene fuente en la BD (ver docstring de listar).
    Si se define cómo calcularlos, esta prueba debe cambiar."""
    lotes = listar(h, bd, GIL, indicadores=True)
    assert ids(lotes) == [11, 12, 21, 22]
    assert all(lote.indicadores is None for lote in lotes)
