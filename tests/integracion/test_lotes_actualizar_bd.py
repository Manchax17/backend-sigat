"""``ServicioLotes.actualizar`` contra PostgreSQL REAL. Cada prueba hace rollback: no deja rastro.

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Crea su PROPIA base temporal.

NOTA: ``DATOS``, ``_dsn``, ``ejecutar`` y el fixture ``bd`` son copia de
``test_lotes_listar_bd.py``. Se duplican a propósito: los módulos de prueba no pueden
importarse entre sí y así cada archivo corre de forma independiente. Si cambia el escenario
de datos en uno, hay que replicarlo en el otro.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from sigat_api.compartido.errores import NoEncontrado, ReglaNegocio
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.repositorio import RepositorioConfiguracion
from sigat_api.modules.configuracion.servicio import ServicioLotes

pytestmark = pytest.mark.integracion

GIL, ADMIN = 4, 6

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


def correr(
    h: SimpleNamespace,
    bd: str,
    usua: int,
    prueba: Callable[[ServicioLotes, AsyncConnection, Any], Awaitable[Any]],
) -> Any:
    """Abre una transacción, fija el usuario (lo que hace sesion.transaccion en producción) y
    ejecuta ``prueba``. Al cerrar la conexión todo se revierte."""

    async def _correr() -> Any:
        motor = create_async_engine(h.url(bd))
        try:
            async with motor.connect() as con:
                await con.execute(
                    text("SELECT set_config('sigat.usuario_actual', :u, true)"), {"u": str(usua)}
                )
                svc = ServicioLotes(RepositorioConfiguracion(con), cola=None)  # type: ignore[arg-type]
                return await prueba(svc, con, SimpleNamespace(usua_id=usua))
        finally:
            await motor.dispose()

    return asyncio.run(_correr())


# ------------------------------------------------------------------ camino feliz y auditoría
def test_actualiza_campos_permitidos_y_deja_auditoria(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc, con, actor):
        contar = text("SELECT count(*) FROM sigat.bitacora_auditoria WHERE usua_id = :u")
        antes = await con.scalar(contar, {"u": actor.usua_id})

        detalle = await svc.actualizar(
            actor, 11, e.ActualizacionLote(lote_descripcion="Norte (editado)", lote_area=3.5)
        )

        fila = (
            await con.execute(
                text(
                    "SELECT lote_fecha_actualizacion, lote_tecnico_actualizacion_id "
                    "FROM sigat.lote WHERE lote_id = 11"
                )
            )
        ).one()
        despues = await con.scalar(contar, {"u": actor.usua_id})
        return detalle, fila, antes, despues

    detalle, fila, antes, despues = correr(h, bd, GIL, prueba)

    assert detalle.lote_descripcion == "Norte (editado)"
    assert float(detalle.lote_area) == 3.5
    assert fila.lote_fecha_actualizacion == datetime.now(ZoneInfo("America/Bogota")).date()
    assert (
        fila.lote_tecnico_actualizacion_id is None
    )  # no se atribuye al técnico: la autoría va a la bitácora
    assert despues == antes + 1  # trg_aud_lote registró el UPDATE con el usuario del actor


def test_admin_puede_actualizar_cualquier_lote_vigente(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc, con, actor):
        return await svc.actualizar(actor, 31, e.ActualizacionLote(lote_descripcion="Editado por admin"))

    assert correr(h, bd, ADMIN, prueba).lote_descripcion == "Editado por admin"


def test_no_modifica_campos_que_no_se_enviaron(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc, con, actor):
        antes = await svc.obtener(actor, 12)
        despues = await svc.actualizar(actor, 12, e.ActualizacionLote(lote_descripcion="Solo esto"))
        return antes, despues

    antes, despues = correr(h, bd, GIL, prueba)
    assert despues.lote_descripcion == "Solo esto"
    assert despues.model_dump(exclude={"lote_descripcion"}) == antes.model_dump(exclude={"lote_descripcion"})


# ------------------------------------------------------------------ acceso
@pytest.mark.parametrize("lote_id", [31, 999])  # 31: asignación vencida de Gil; 999: inexistente
def test_lote_no_visible_o_inexistente_devuelve_404(bd: str, h: SimpleNamespace, lote_id: int) -> None:
    async def prueba(svc, con, actor):
        with pytest.raises(NoEncontrado):
            await svc.actualizar(actor, lote_id, e.ActualizacionLote(lote_descripcion="No debería"))
        return await con.scalar(text("SELECT lote_descripcion FROM sigat.lote WHERE lote_id = 31"))

    assert correr(h, bd, GIL, prueba) == "Lote del predio 3"  # no se tocó


def test_body_vacio_es_422(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc, con, actor):
        with pytest.raises(ReglaNegocio) as exc:
            await svc.actualizar(actor, 11, e.ActualizacionLote())
        return exc.value.codigo

    assert correr(h, bd, GIL, prueba) == "ACTUALIZACION_VACIA"


# ------------------------------------------------------------------ especie / variedad
def test_asigna_especie_y_variedad_validas(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc, con, actor):
        espc, vari = (
            await con.execute(text("SELECT espc_id, vari_id FROM sigat.variedad_cultivo LIMIT 1"))
        ).one()
        return await svc.actualizar(actor, 11, e.ActualizacionLote(espc_id=espc, vari_id=vari))

    detalle = correr(h, bd, GIL, prueba)
    assert (detalle.cultivo, detalle.variedad) == ("Durazno", "Rubidoux")


def test_variedad_sin_especie_es_422_antes_de_llegar_a_la_bd(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc, con, actor):
        with pytest.raises(ReglaNegocio) as exc:
            await svc.actualizar(actor, 11, e.ActualizacionLote(vari_id=1))
        return exc.value.codigo

    assert correr(h, bd, GIL, prueba) == "VARIEDAD_SIN_ESPECIE"


def test_variedad_inexistente_la_rechaza_la_fk_de_la_bd(bd: str, h: SimpleNamespace) -> None:
    """A este nivel (sin ``sesion.transaccion``) el error llega crudo; en la API lo traduce
    ``errores_pg.traducir`` a 422."""

    async def prueba(svc, con, actor):
        espc = await con.scalar(text("SELECT espc_id FROM sigat.variedad_cultivo LIMIT 1"))
        with pytest.raises(IntegrityError) as exc:
            await svc.actualizar(actor, 11, e.ActualizacionLote(espc_id=espc, vari_id=999))
        return exc.value.orig.sqlstate  # type: ignore[union-attr]

    assert correr(h, bd, GIL, prueba) == "23503"


# ------------------------------------------------------------------ baja
def test_dar_de_baja_responde_el_detalle_y_el_lote_sale_del_listado(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc, con, actor):
        detalle = await svc.actualizar(actor, 12, e.ActualizacionLote(lote_fecha_baja=date(2026, 6, 1)))
        listado = [lote.lote_id for lote in await svc.listar(actor, 1, False)]
        return detalle, listado

    detalle, listado = correr(h, bd, GIL, prueba)
    assert detalle.lote_id == 12
    assert detalle.lote_fecha_baja == date(2026, 6, 1)
    assert 12 not in listado


def test_baja_anterior_al_registro_la_rechaza_el_check(bd: str, h: SimpleNamespace) -> None:
    """``lote_fechas_validas``: la baja no puede ser anterior al registro (2026-01-01)."""

    async def prueba(svc, con, actor):
        with pytest.raises(IntegrityError) as exc:
            await svc.actualizar(actor, 12, e.ActualizacionLote(lote_fecha_baja=date(2025, 12, 31)))
        return exc.value.orig.sqlstate  # type: ignore[union-attr]

    assert correr(h, bd, GIL, prueba) == "23514"
