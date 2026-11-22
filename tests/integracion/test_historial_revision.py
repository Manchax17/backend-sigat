"""Historial y revisión del agrónomo (issue #8) contra PostgreSQL REAL.

Requiere ``SIGAT_DB_URL_PRUEBAS`` (ver ``conftest.py``). Usa una base TEMPORAL que se borra al final.
Ejecuta los servicios con el repositorio real, dentro de una transacción con
``sigat.usuario_actual`` = actor, igual que la API.

Escenario:

* Usuarios: técnico (1), productores Ana (2, predio 1) y Bruno (3, predio 2), agrónomo (4,
  asignado al predio 1). El técnico no tiene planificaciones: sus visitas salen como "lote no
  asignado ese día" (advertencia de revisión).
* Predio 1: lotes 11 y 12; trampas T1, T2 en el 11 y T3 en el 12. Predio 2: lote 21, trampa T5.
* Visita V1 (predio 1, lotes 11 y 12): L1 en T1 corregida (3 -> 4 especímenes), L2 en T2 no leída,
  L3 en T3 anulada en su versión 2.
* Visita V2 (predio 2, lote 21): L4 en T5.
* Lote 11: una aplicación vigente, un hallazgo y una etapa declarada.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from sigat_api.compartido.errores import Conflicto, NoEncontrado
from sigat_api.compartido.tipos import (
    AccionSync,
    Actor,
    Cliente,
    EstadoRevision,
    EstadoRevisionLectura,
    RolCodigo,
)
from sigat_api.infra.reloj import RelojSistema
from sigat_api.modules.registro import esquemas as e
from sigat_api.modules.registro.repositorio import RepositorioRegistro
from sigat_api.modules.registro.servicio import ServicioConsultaRegistro, ServicioRevision

pytestmark = pytest.mark.integracion

TECNICO = Actor(usua_id=1, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
BRUNO = Actor(usua_id=3, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)
AGRONOMO = Actor(usua_id=4, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)

V1 = UUID("00000000-0000-0000-0000-00000000b001")
V2 = UUID("00000000-0000-0000-0000-00000000b002")
L1, L2, L3, L4 = (UUID(f"00000000-0000-0000-0000-00000000c00{i}") for i in (1, 2, 3, 4))

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
INSERT INTO lote (lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, lote_fecha_registro, lote_tecnico_registro_id) VALUES
  (11, 'L11', 'Lote 1 · Norte', 2, 1, DATE '2026-01-01', 1),
  (12, 'L12', 'Lote 2 · Centro', 2, 1, DATE '2026-01-01', 1),
  (21, 'L21', 'Lote único', 2, 2, DATE '2026-01-01', 1);

INSERT INTO vigilancia (vigi_id, vigi_descripcion) VALUES (1, 'Vigilancia de prueba');
INSERT INTO condicion_trampa (cond_id, cond_descripcion) VALUES (1, 'Buena');
INSERT INTO trampa (tram_id, tram_codigo, tipt_id, atra_id, esta_id, tram_fecha_registro, tram_tecnico_registro_id)
SELECT v.id, v.cod, t.tipt_id, (SELECT min(atra_id) FROM atrayente), e.esta_id, DATE '2026-01-01', 1
  FROM (VALUES (1, '54125-R1-1'), (2, '54125-R1-3'), (3, '54125-R1-5'), (5, '54125-R1-9')) v(id, cod)
  JOIN tipo_trampa t ON t.tipt_descripcion = 'Jackson'
  JOIN estado_registro e ON e.esta_descripcion = 'activo';
INSERT INTO ubicacion_trampa (ubic_id, tram_id, pred_id, lote_id, ubic_fecha, ubic_fecha_fin, tecn_id, vigi_id, ubic_long, ubic_lat) VALUES
  (1, 1, 1, 11, DATE '2026-01-01', NULL, 1, 1, -72.5, 7.9),
  (2, 2, 1, 11, DATE '2026-01-01', NULL, 1, 1, -72.5, 7.9),
  (3, 3, 1, 12, DATE '2026-01-01', NULL, 1, 1, -72.5, 7.9),
  (6, 5, 2, 21, DATE '2026-01-01', NULL, 1, 1, -72.5, 7.9);

INSERT INTO dispositivo (disp_id, disp_plataforma) VALUES ('00000000-0000-0000-0000-0000000000d1', 'android');

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
   '2026-03-10 11:00-05', '2026-03-10 11:30-05', '2026-03-10 11:35-05');
INSERT INTO visita_lote (visi_id, lote_id, pred_id) VALUES
  ('00000000-0000-0000-0000-00000000b001', 11, 1), ('00000000-0000-0000-0000-00000000b001', 12, 1),
  ('00000000-0000-0000-0000-00000000b002', 21, 2);

-- Lecturas en orden: la versión 2 exige que exista la 1. La corrección de L1 la hace la agrónoma (4).
INSERT INTO lectura_trampa (lect_registro_id, lect_version, visi_id, tram_id, ubic_id, lect_leida, lect_especimenes,
                            lect_justificacion, cond_id, usua_id, disp_id, oper_id, lect_t_observacion, lect_t_captura,
                            lect_anulada, lect_motivo_anulacion, lect_motivo_correccion)
SELECT v.reg::uuid, v.ver, v.visi::uuid, v.tram, v.ubic, v.leida, v.esp, v.just, 1, v.usua,
       '00000000-0000-0000-0000-0000000000d1', pg_temp.op('lectura_trampa', v.usua, v.reg::uuid, v.ver),
       v.obs::timestamptz, v.obs::timestamptz + interval '1 minute', v.anulada, v.motivo_anul, v.motivo_corr
  FROM (VALUES
    (1, '00000000-0000-0000-0000-00000000c001', 1, '00000000-0000-0000-0000-00000000b001', 1, 1, true, 3, NULL, 1, '2026-03-10 09:10-05', false, NULL, NULL),
    (2, '00000000-0000-0000-0000-00000000c001', 2, '00000000-0000-0000-0000-00000000b001', 1, 1, true, 4, NULL, 4, '2026-03-10 09:10-05', false, NULL, 'Conteo revisado con la foto'),
    (3, '00000000-0000-0000-0000-00000000c002', 1, '00000000-0000-0000-0000-00000000b001', 2, 2, false, NULL, 'Trampa caída', 1, '2026-03-10 09:15-05', false, NULL, NULL),
    (4, '00000000-0000-0000-0000-00000000c003', 1, '00000000-0000-0000-0000-00000000b001', 3, 3, true, 1, NULL, 1, '2026-03-10 09:25-05', false, NULL, NULL),
    (5, '00000000-0000-0000-0000-00000000c003', 2, '00000000-0000-0000-0000-00000000b001', 3, 3, true, 1, NULL, 1, '2026-03-10 09:25-05', true, 'Lectura duplicada', NULL),
    (6, '00000000-0000-0000-0000-00000000c004', 1, '00000000-0000-0000-0000-00000000b002', 5, 6, true, 5, NULL, 1, '2026-03-10 11:10-05', false, NULL, NULL)
  ) v(orden, reg, ver, visi, tram, ubic, leida, esp, just, usua, obs, anulada, motivo_anul, motivo_corr)
 ORDER BY v.orden;

INSERT INTO producto_fitosanitario (prof_id, prof_nombre) VALUES (1, 'Cipermetrina');
INSERT INTO aplicacion_fitosanitaria (apli_registro_id, apli_version, lote_id, prof_id, apli_dosis, apli_dosis_uni_id,
                                      apli_area_tratada, apli_area_uni_id, pers_cc_responsable, usua_id, disp_id, oper_id,
                                      apli_t_observacion, apli_t_captura)
VALUES ('00000000-0000-0000-0000-00000000e001', 1, 11, 1, 2.0, (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'mL/L'),
        1.2, (SELECT unid_id FROM unidad_medida WHERE unid_simbolo = 'ha'), 2001, 2, '00000000-0000-0000-0000-0000000000d1',
        pg_temp.op('aplicacion_fitosanitaria', 2, '00000000-0000-0000-0000-00000000e001', 1),
        '2026-03-05 08:00-05', '2026-03-05 09:00-05');

INSERT INTO plaga_enfermedad (plag_id, plag_descripcion, plag_tipo) VALUES (1, 'Mosca de la fruta', 'plaga');
INSERT INTO severidad (seve_id, seve_descripcion, seve_orden) VALUES (1, 'Baja', 1);
INSERT INTO fenologia (feno_id, feno_descripcion) VALUES (1, 'Floración');
INSERT INTO hallazgo (hall_registro_id, lote_id, plag_id, seve_id, hall_linaje, usua_id, disp_id, oper_id,
                      hall_t_observacion, hall_t_captura)
VALUES ('00000000-0000-0000-0000-00000000a001', 11, 1, 1, 'inspeccion_oficial', 1, '00000000-0000-0000-0000-0000000000d1',
        pg_temp.op('hallazgo', 1, '00000000-0000-0000-0000-00000000a001', 1), '2026-03-10 09:40-05', '2026-03-10 09:41-05');
INSERT INTO etapa_declarada (etap_registro_id, lote_id, feno_id, etap_linaje, usua_id, disp_id, oper_id,
                             etap_t_observacion, etap_t_captura)
VALUES ('00000000-0000-0000-0000-00000000a002', 11, 1, 'autorreporte', 2, '00000000-0000-0000-0000-0000000000d1',
        pg_temp.op('etapa_declarada', 2, '00000000-0000-0000-0000-00000000a002', 1), '2026-03-08 08:00-05', '2026-03-08 08:05-05');
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


def correr[T](
    h: SimpleNamespace, bd: str, actor: Actor, uso: Callable[[RepositorioRegistro], Awaitable[T]]
) -> T:
    """Una transacción por llamada con ``sigat.usuario_actual`` = actor, como ``api.dependencias.conexion``.
    Si ``uso`` lanza, la transacción se revierte."""

    async def _correr() -> T:
        motor = create_async_engine(h.url(bd))
        try:
            async with motor.begin() as con:
                await _fijar_usuario(con, actor)
                return await uso(RepositorioRegistro(con))
        finally:
            await motor.dispose()

    return asyncio.run(_correr())


async def _fijar_usuario(con: AsyncConnection, actor: Actor) -> None:
    await con.execute(
        text("SELECT set_config('sigat.usuario_actual', CAST(:u AS text), true)"), {"u": str(actor.usua_id)}
    )


def consulta(h: SimpleNamespace, bd: str, actor: Actor, metodo: str, *args: Any) -> Any:
    return correr(h, bd, actor, lambda repo: getattr(ServicioConsultaRegistro(repo), metodo)(actor, *args))


def revision(h: SimpleNamespace, bd: str, actor: Actor, metodo: str, *args: Any) -> Any:
    return correr(
        h, bd, actor, lambda repo: getattr(ServicioRevision(repo, RelojSistema()), metodo)(actor, *args)
    )


# ------------------------------------------------------------------ visitas
def test_el_tecnico_ve_sus_visitas_y_el_agronomo_solo_las_de_su_predio(h: SimpleNamespace, bd: str) -> None:
    del_tecnico = consulta(h, bd, TECNICO, "visitas", None, None, None, None)
    del_agronomo = consulta(h, bd, AGRONOMO, "visitas", None, None, None, None)

    assert [v.visi_id for v in del_tecnico.items] == [V2, V1]  # más reciente primero
    assert [v.visi_id for v in del_agronomo.items] == [V1]
    visita = del_agronomo.items[0]
    assert visita.lote_ids == [11, 12]
    assert visita.lotes == ["L11", "L12"]
    assert visita.trampas_leidas == 3
    assert visita.tecnico == "Tomás Técnico"
    assert visita.estado_revision == EstadoRevision.PENDIENTE


def test_detalle_de_visita_incluye_la_ultima_version_de_cada_lectura(h: SimpleNamespace, bd: str) -> None:
    detalle = consulta(h, bd, AGRONOMO, "visita", V1)

    por_registro = {lect.lect_registro_id: lect for lect in detalle.lecturas}
    assert set(por_registro) == {L1, L2, L3}
    assert por_registro[L1].lect_version == 2
    assert por_registro[L1].lect_especimenes == 4
    assert por_registro[L1].especimenes_originales == 3
    assert por_registro[L1].motivo_correccion == "Conteo revisado con la foto"
    assert por_registro[L1].estado_revision == EstadoRevisionLectura.CORREGIDO
    assert por_registro[L2].estado_revision == EstadoRevisionLectura.SIN_VERIFICAR
    assert por_registro[L3].anulada is True
    assert por_registro[L3].estado_revision == EstadoRevisionLectura.RECHAZADO
    assert por_registro[L1].lote_id == 11
    assert por_registro[L1].condicion == "Buena"


def test_visita_de_otro_predio_da_404(h: SimpleNamespace, bd: str) -> None:
    with pytest.raises(NoEncontrado):
        consulta(h, bd, AGRONOMO, "visita", V2)


# ------------------------------------------------------------------ lecturas y versiones
def test_lecturas_vigentes_ocultan_las_anuladas_y_filtran(h: SimpleNamespace, bd: str) -> None:
    todas = consulta(h, bd, AGRONOMO, "lecturas", None, None, None, None)
    de_t1 = consulta(h, bd, AGRONOMO, "lecturas", None, 1, None, None)
    del_lote_12 = consulta(h, bd, AGRONOMO, "lecturas", 12, None, None, None)

    assert {lect.lect_registro_id for lect in todas.items} == {L1, L2}
    assert [lect.lect_registro_id for lect in de_t1.items] == [L1]
    assert del_lote_12.items == []  # la única lectura del lote 12 está anulada


def test_versiones_conservan_el_valor_original(h: SimpleNamespace, bd: str) -> None:
    historial = consulta(h, bd, AGRONOMO, "versiones_lectura", L1)

    assert [v.version for v in historial.versiones] == [1, 2]
    assert [v.accion for v in historial.versiones] == [AccionSync.CREAR, AccionSync.CORREGIR]
    assert historial.versiones[0].valores["lect_especimenes"] == 3
    assert historial.versiones[1].valores["lect_especimenes"] == 4
    assert historial.versiones[1].autor == "Gloria Agrónoma"
    assert historial.versiones[1].motivo == "Conteo revisado con la foto"


def test_versiones_de_una_anulacion(h: SimpleNamespace, bd: str) -> None:
    historial = consulta(h, bd, AGRONOMO, "versiones_lectura", L3)
    assert historial.versiones[1].accion == AccionSync.ANULAR
    assert historial.versiones[1].motivo == "Lectura duplicada"


def test_versiones_de_una_lectura_ajena_dan_404(h: SimpleNamespace, bd: str) -> None:
    with pytest.raises(NoEncontrado):
        consulta(h, bd, BRUNO, "versiones_lectura", L1)


# ------------------------------------------------------------------ actividad
def test_actividad_del_lote_unifica_visitas_correcciones_aplicaciones_hallazgos_y_etapas(
    h: SimpleNamespace, bd: str
) -> None:
    pagina = consulta(h, bd, AGRONOMO, "actividad", 11, None, None, None, None, None)

    tipos = [a.tipo for a in pagina.items]
    assert sorted(tipos) == ["aplicacion", "correccion", "etapa", "hallazgo", "vigilancia"]
    instantes = [a.t_observacion for a in pagina.items]
    assert instantes == sorted(instantes, reverse=True)
    correccion = next(a for a in pagina.items if a.tipo == "correccion")
    assert correccion.titulo == "Lectura corregida"
    assert correccion.rol_autor == "AGRONOMO_SUPERVISOR"


def test_actividad_filtra_por_tipo_y_linaje(h: SimpleNamespace, bd: str) -> None:
    solo_etapas = consulta(h, bd, AGRONOMO, "actividad", None, "etapa", None, None, None, None)
    autorreportes = consulta(h, bd, AGRONOMO, "actividad", None, None, "autorreporte", None, None, None)

    assert [a.tipo for a in solo_etapas.items] == ["etapa"]
    assert {a.tipo for a in autorreportes.items} == {"aplicacion", "etapa"}


def test_actividad_de_un_lote_ajeno_da_404(h: SimpleNamespace, bd: str) -> None:
    with pytest.raises(NoEncontrado):
        consulta(h, bd, AGRONOMO, "actividad", 21, None, None, None, None, None)


def test_actividad_reciente_del_lote(h: SimpleNamespace, bd: str) -> None:
    recientes = consulta(h, bd, AGRONOMO, "actividad_reciente", 11)

    assert {r.tipo for r in recientes} >= {"visita", "lectura", "aplicacion", "sincronizacion"}
    assert [r.t for r in recientes] == sorted((r.t for r in recientes), reverse=True)
    with pytest.raises(NoEncontrado):
        consulta(h, bd, AGRONOMO, "actividad_reciente", 21)


# ------------------------------------------------------------------ revisión (escribe: va al final)
def test_listado_y_detalle_de_revision(h: SimpleNamespace, bd: str) -> None:
    pagina = revision(h, bd, AGRONOMO, "listar", None, None)
    assert [r.visi_id for r in pagina.items] == [V1]
    assert any("no tenía asignado" in a for a in pagina.items[0].advertencias)

    detalle = revision(h, bd, AGRONOMO, "obtener", V1)
    assert len(detalle.lecturas) == 3
    assert {hv.registro_id for hv in detalle.versiones} == {L1, L3}

    with pytest.raises(NoEncontrado):
        revision(h, bd, AGRONOMO, "obtener", V2)


def test_observar_y_aceptar_dejan_trazabilidad_y_no_se_repiten(h: SimpleNamespace, bd: str) -> None:
    observada = revision(
        h,
        bd,
        AGRONOMO,
        "marcar_en_revision",
        V1,
        e.ObservacionRevision(motivo="Falta la foto de la trampa 3"),
    )
    assert observada.estado_revision == EstadoRevision.EN_REVISION
    with pytest.raises(Conflicto):
        revision(h, bd, AGRONOMO, "marcar_en_revision", V1, e.ObservacionRevision(motivo="Otra vez lo mismo"))

    aceptada = revision(h, bd, AGRONOMO, "aceptar", V1)
    assert aceptada.estado_revision == EstadoRevision.ACEPTADO
    assert aceptada.motivo_revision == "Falta la foto de la trampa 3"
    por_registro = {lect.lect_registro_id: lect for lect in aceptada.lecturas}
    assert por_registro[L2].estado_revision == EstadoRevisionLectura.CONFIRMADO
    assert por_registro[L1].estado_revision == EstadoRevisionLectura.CORREGIDO  # la corrección manda

    with pytest.raises(Conflicto) as exc:
        revision(h, bd, AGRONOMO, "aceptar", V1)
    assert exc.value.codigo == "REVISION_YA_RESUELTA"

    [(estado, por, en)] = h.consulta(
        bd,
        "SELECT visi_estado_revision, visi_revisada_por, visi_revisada_en IS NOT NULL "
        "FROM sigat.visita WHERE visi_id = '00000000-0000-0000-0000-00000000b001'",
    )
    assert (estado, por, en) == ("aceptado", AGRONOMO.usua_id, True)
    bitacora = h.consulta(
        bd,
        "SELECT usua_id FROM sigat.bitacora_auditoria WHERE bita_tabla = 'visita' "
        "AND bita_registro = '00000000-0000-0000-0000-00000000b001' AND bita_accion = 'UPDATE'",
    )
    assert [fila[0] for fila in bitacora] == [AGRONOMO.usua_id, AGRONOMO.usua_id]

    # Aceptada, ya no aparece entre lo que falta por revisar, pero sí al filtrar por estado.
    assert revision(h, bd, AGRONOMO, "listar", None, None).items == []
    assert [r.visi_id for r in revision(h, bd, AGRONOMO, "listar", EstadoRevision.ACEPTADO, None).items] == [
        V1
    ]
