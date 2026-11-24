"""Planificación (issue #10) contra PostgreSQL real, con los disparadores fn_planificacion_*.

Usuarios: técnico Tomás (1, asignado al predio 1), productora Ana (2, dueña del predio 1),
productor Bruno (3, dueño del predio 2), agrónoma Gloria (4, predio 1), agrónomo Hugo (5, predio 2),
agrónoma Iris (6, predio 1).
Predio 1: lotes 11 y 12. Predio 2: lote 21.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from sigat_api.compartido.errores import Conflicto, NoEncontrado, ReglaNegocio
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.modules.planificacion import esquemas as e
from sigat_api.modules.planificacion import servicio as modulo_servicio
from sigat_api.modules.planificacion.repositorio import RepositorioPlanificacion
from sigat_api.modules.planificacion.servicio import ServicioPlanificacion

pytestmark = pytest.mark.integracion

TECNICO = Actor(usua_id=1, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
ANA = Actor(usua_id=2, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)
BRUNO = Actor(usua_id=3, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)
GLORIA = Actor(usua_id=4, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
HUGO = Actor(usua_id=5, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
IRIS = Actor(usua_id=6, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)

DATOS = r"""
SET search_path = sigat, public;

INSERT INTO region (regi_id, regi_descripcion) VALUES (1, 'Región de prueba');
INSERT INTO departamento (depa_id, depa_descripcion, regi_id, depa_codigo) VALUES (1, 'Depto', 1, '99');
INSERT INTO municipio (muni_id, muni_descripcion, depa_id, muni_codigo) VALUES (1, 'Muni', 1, '001');
INSERT INTO centro_acopio (cent_id, cent_descripcion, depa_id) VALUES (1, 'Centro', 1);
INSERT INTO persona (pers_cc, pers_nombre, pers_apellido) VALUES
  (1001, 'Adela', 'Admin'), (1002, 'Tomás', 'Técnico'), (2001, 'Ana', 'Productora'),
  (2002, 'Bruno', 'Productor'), (3001, 'Gloria', 'Agrónoma'), (3002, 'Hugo', 'Agrónomo'),
  (3003, 'Iris', 'Agrónoma');
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
               (4, 3001::bigint, 'gloria.prueba', 'AGRONOMO_SUPERVISOR'),
               (5, 3002::bigint, 'hugo.prueba', 'AGRONOMO_SUPERVISOR'),
               (6, 3003::bigint, 'iris.prueba', 'AGRONOMO_SUPERVISOR')) v(id, cc, usu, rol)
  JOIN rol r ON r.rol_codigo = v.rol
  JOIN estado_registro e ON e.esta_descripcion = 'activo';

INSERT INTO predio (pred_id, pred_nombre, muni_id, pred_area, pers_cc, pred_fecha_registro, pred_tecnico_registro_id)
  VALUES (1, 'Predio Ana', 1, 10, 2001, DATE '2026-01-01', 1), (2, 'Predio Bruno', 1, 10, 2002, DATE '2026-01-01', 1);
INSERT INTO usuario_predio (usua_id, pred_id, usup_desde) VALUES
  (1, 1, DATE '2026-01-01'), (4, 1, DATE '2026-01-01'), (5, 2, DATE '2026-01-01'), (6, 1, DATE '2026-01-01');
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro, lote_tecnico_registro_id)
  VALUES (11, 'L11', 'Lote 11', 1, 1, DATE '2026-01-01', 1), (12, 'L12', 'Lote 12', 1, 1, DATE '2026-01-01', 1),
         (21, 'L21', 'Lote 21', 1, 2, DATE '2026-01-01', 1);
"""


def _dsn(h: SimpleNamespace, nombre: str) -> str:
    return str(h.url(nombre)).replace("postgresql+asyncpg://", "postgresql://", 1)


def ejecutar(h: SimpleNamespace, nombre: str, sql: str) -> None:
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
    nombre = h.crear_bd()
    try:
        r = h.alembic(nombre, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        ejecutar(h, nombre, DATOS)
        yield nombre
    finally:
        h.borrar_bd(nombre)


async def como(con: AsyncConnection, actor: Actor) -> None:
    """Lo que hace ``sesion.transaccion`` en producción: fija el actor de la transacción."""
    await con.execute(text("SELECT set_config('sigat.usuario_actual', :u, true)"), {"u": str(actor.usua_id)})


def correr(
    h: SimpleNamespace, bd: str, prueba: Callable[[ServicioPlanificacion, AsyncConnection], Awaitable[Any]]
) -> Any:
    """Una transacción por prueba; al cerrar la conexión todo se revierte."""

    async def _correr() -> Any:
        motor = create_async_engine(h.url(bd))
        try:
            async with motor.connect() as con:
                return await prueba(ServicioPlanificacion(RepositorioPlanificacion(con)), con)
        finally:
            await motor.dispose()

    return asyncio.run(_correr())


def para_ana(**cambios: Any) -> e.NuevaPlanificacion:
    datos: dict[str, Any] = {
        "usua_id_destinatario": ANA.usua_id,
        "plan_actividad": "Revisión de trampas",
        "plan_fecha_inicio": date(2026, 11, 20),
        "plan_fecha_fin": date(2026, 11, 30),
        "plan_observaciones": "Revisar todas las trampas del predio",
        "lote_ids": [11, 12],
    }
    datos.update(cambios)
    return e.NuevaPlanificacion(**datos)


async def aceptada_por_ana(svc: ServicioPlanificacion, con: AsyncConnection, **cambios: Any) -> UUID:
    """Planificación de Gloria para Ana, aceptada por Ana en la BD (la respuesta es otro método)."""
    await como(con, GLORIA)
    plan = await svc.crear(GLORIA, para_ana(**cambios))
    await como(con, ANA)
    await con.execute(
        text("UPDATE sigat.planificacion SET plan_estado = 'aceptada' WHERE plan_id = :p"),
        {"p": plan.plan_id},
    )
    await como(con, GLORIA)
    return plan.plan_id


# ------------------------------------------------------------------ crear
def test_crear_para_productor_guarda_lotes_aviso_y_auditoria(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        plan = await svc.crear(GLORIA, para_ana(lote_ids=[12, 11, 12]))

        assert plan.tipo == "productor"
        assert plan.plan_estado == "pendiente"
        assert plan.usua_id_creador == GLORIA.usua_id
        assert plan.creador == "Gloria Agrónoma"
        assert plan.lotes == ["L11", "L12"]
        assert plan.lote_ids == [11, 12]
        assert [r.plal_estado for r in plan.resultados_lote] == ["pendiente", "pendiente"]
        aviso = await con.scalar(
            text("SELECT noti_titulo FROM sigat.notificacion WHERE usua_id = :u AND noti_evento_clave = :k"),
            {"u": ANA.usua_id, "k": f"plan:{plan.plan_id}:solicitud"},
        )
        assert aviso == "Planificación por aprobar"
        autor = await con.scalar(
            text(
                "SELECT usua_id FROM sigat.bitacora_auditoria WHERE bita_tabla = 'planificacion' AND bita_registro = :p"
            ),
            {"p": str(plan.plan_id)},
        )
        assert autor == GLORIA.usua_id

    correr(h, bd, prueba)


def test_crear_actividad_desde_planificacion_aceptada(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        origen = await aceptada_por_ana(svc, con)

        actividad = await svc.crear(
            GLORIA,
            e.NuevaPlanificacion(
                usua_id_destinatario=TECNICO.usua_id,
                plan_actividad="Leer trampas",
                plan_fecha_inicio=date(2026, 11, 21),
                plan_id_origen=origen,
                lote_ids=[11],
            ),
        )

        assert actividad.tipo == "tecnico"
        assert actividad.plan_id_origen == origen
        await como(con, TECNICO)
        recibidas = await svc.listar(TECNICO, "recibidas", None, None)
        assert [p.plan_id for p in recibidas.items] == [actividad.plan_id]

    correr(h, bd, prueba)


def test_crear_actividad_con_origen_pendiente_o_lote_ajeno_al_origen(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        pendiente = await svc.crear(GLORIA, para_ana())
        origen = await aceptada_por_ana(svc, con, lote_ids=[11])

        def actividad(plan_id: UUID, lote: int) -> e.NuevaPlanificacion:
            return e.NuevaPlanificacion(
                usua_id_destinatario=TECNICO.usua_id,
                plan_actividad="Leer trampas",
                plan_fecha_inicio=date(2026, 11, 21),
                plan_id_origen=plan_id,
                lote_ids=[lote],
            )

        with pytest.raises(ReglaNegocio) as no_aceptada:
            await svc.crear(GLORIA, actividad(pendiente.plan_id, 11))
        with pytest.raises(ReglaNegocio) as fuera:
            await svc.crear(GLORIA, actividad(origen, 12))

        assert no_aceptada.value.codigo == "ORIGEN_NO_ACEPTADO"
        assert fuera.value.codigo == "LOTE_NO_EN_ORIGEN"

    correr(h, bd, prueba)


def test_crear_con_lote_o_destinatario_de_otro_predio(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        with pytest.raises(ReglaNegocio) as lote:
            await svc.crear(GLORIA, para_ana(lote_ids=[21]))
        with pytest.raises(ReglaNegocio) as destinatario:
            await svc.crear(GLORIA, para_ana(usua_id_destinatario=BRUNO.usua_id, lote_ids=[]))

        assert lote.value.codigo == "LOTE_NO_ASIGNABLE"
        assert destinatario.value.codigo == "DESTINATARIO_INVALIDO"

    correr(h, bd, prueba)


# ------------------------------------------------------------------ destinatarios y lotes asignables
def test_destinatarios_y_lotes_solo_de_los_predios_del_agronomo(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        productores = await svc.destinatarios(GLORIA, "productor")
        tecnicos = await svc.destinatarios(GLORIA, "tecnico")
        lotes = await svc.lotes_asignables(GLORIA, None)
        para_bruno = await svc.lotes_asignables(GLORIA, BRUNO.usua_id)

        assert [(d.usua_id, d.subtitulo) for d in productores] == [(ANA.usua_id, "Productor · Predio Ana")]
        assert [d.usua_id for d in tecnicos] == [TECNICO.usua_id]
        assert [lote.lote_id for lote in lotes] == [11, 12]
        assert para_bruno == []

    correr(h, bd, prueba)


# ------------------------------------------------------------------ listar y obtener
def test_listar_pagina_sin_repetir_ni_saltar(
    bd: str, h: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(modulo_servicio, "TAMANO_PAGINA", 2)

    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        creadas = {(await svc.crear(GLORIA, para_ana())).plan_id for _ in range(3)}

        primera = await svc.listar(GLORIA, "creadas", None, None)
        segunda = await svc.listar(GLORIA, "creadas", None, primera.siguiente)

        assert len(primera.items) == 2
        assert primera.siguiente is not None
        assert len(segunda.items) == 1
        assert segunda.siguiente is None
        assert {p.plan_id for p in primera.items + segunda.items} == creadas

    correr(h, bd, prueba)


def test_obtener_respeta_quien_puede_verla(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        plan = await svc.crear(GLORIA, para_ana())

        assert (await svc.obtener(GLORIA, plan.plan_id)).plan_id == plan.plan_id
        assert (await svc.obtener(ANA, plan.plan_id)).plan_id == plan.plan_id
        for ajeno in (BRUNO, HUGO, TECNICO):
            with pytest.raises(NoEncontrado):
                await svc.obtener(ajeno, plan.plan_id)

    correr(h, bd, prueba)


def test_otro_agronomo_del_predio_ve_la_planificacion_si_tiene_lotes_de_ese_predio(
    bd: str, h: SimpleNamespace
) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        con_lotes = await svc.crear(GLORIA, para_ana())
        sin_lotes = await svc.crear(GLORIA, para_ana(lote_ids=[]))

        assert (await svc.obtener(IRIS, con_lotes.plan_id)).plan_id == con_lotes.plan_id
        with pytest.raises(NoEncontrado):
            await svc.obtener(IRIS, sin_lotes.plan_id)

    correr(h, bd, prueba)


# ------------------------------------------------------------------ responder e informe
def test_aceptar_y_rechazar_en_bd(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        await como(con, GLORIA)
        p1 = await svc.crear(GLORIA, para_ana())
        p2 = await svc.crear(GLORIA, para_ana())

        await como(con, ANA)
        aceptada = await svc.aceptar(ANA, p1.plan_id, e.Aceptacion(observacion="Aprobado por Ana"))
        assert aceptada.plan_estado == "aceptada"
        assert aceptada.plan_observacion_respuesta == "Aprobado por Ana"
        assert aceptada.usua_id_respuesta == ANA.usua_id

        with pytest.raises(Conflicto) as ya_resp:
            await svc.aceptar(ANA, p1.plan_id, e.Aceptacion())
        assert ya_resp.value.codigo == "PLANIFICACION_YA_RESPONDIDA"

        rechazada = await svc.rechazar(ANA, p2.plan_id, e.Rechazo(motivo="No es posible fumigar esta semana"))
        assert rechazada.plan_estado == "rechazada"
        assert rechazada.plan_observacion_respuesta == "No es posible fumigar esta semana"
        assert rechazada.usua_id_respuesta == ANA.usua_id

    correr(h, bd, prueba)


def test_actividad_tecnico_resultado_y_completar_en_bd(bd: str, h: SimpleNamespace) -> None:
    async def prueba(svc: ServicioPlanificacion, con: AsyncConnection) -> None:
        origen_id = await aceptada_por_ana(svc, con, lote_ids=[11, 12])

        await como(con, GLORIA)
        actividad = await svc.crear(
            GLORIA,
            e.NuevaPlanificacion(
                usua_id_destinatario=TECNICO.usua_id,
                plan_actividad="Inspección de lotes",
                plan_fecha_inicio=date(2026, 11, 22),
                plan_id_origen=origen_id,
                lote_ids=[11, 12],
            ),
        )

        await como(con, TECNICO)
        p1 = await svc.registrar_resultado_lote(
            TECNICO, actividad.plan_id, 11, e.EntradaResultadoLote(plal_estado="ejecutada")
        )
        assert p1.plan_estado == "pendiente"

        p2 = await svc.registrar_resultado_lote(
            TECNICO,
            actividad.plan_id,
            12,
            e.EntradaResultadoLote(plal_estado="no_ejecutada", plal_observacion="Lote no accesible"),
        )
        assert p2.plan_estado == "completada"

        await como(con, GLORIA)
        informe = await svc.actividades_tecnico(GLORIA, None, None, None)
        assert len(informe.items) >= 2
        lotes_informe = {item.lote_id for item in informe.items if item.plan_id == actividad.plan_id}
        assert lotes_informe == {11, 12}

    correr(h, bd, prueba)
