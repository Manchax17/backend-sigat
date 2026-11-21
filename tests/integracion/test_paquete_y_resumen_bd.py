"""Pruebas de integración contra PostgreSQL/PostGIS para ``ServicioPaqueteCampo``,
los 4 puntos de invalidación, el worker de ``TAREA_GENERAR_PAQUETE`` y ``ServicioConcepto.resumen_lote``.

Ejecuta el SQL real de ``RepositorioConfiguracion`` y ``RepositorioConcepto`` contra el esquema
instalado con Alembic para garantizar que todas las columnas (``sigat.tecnico.pers_cc``,
``sigat.cosecha.cose_volumen_esperado``, ``ST_AsGeoJSON``, etc.) existen y cumplen las
restricciones de la base de datos.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sigat_api.compartido.esquemas import GeometriaGeoJson
from sigat_api.compartido.tipos import Actor, Cliente, ResultadoConcepto, RolCodigo
from sigat_api.infra.cola.cola import TAREA_GENERAR_PAQUETE, ColaProcrastinate
from sigat_api.modules.concepto.repositorio import RepositorioConcepto
from sigat_api.modules.concepto.servicio import ServicioConcepto
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.repositorio import RepositorioConfiguracion
from sigat_api.modules.configuracion.servicio import (
    ServicioLotes,
    ServicioPaqueteCampo,
    ServicioPredios,
    ServicioPrograma,
    ServicioReticula,
    ServicioTrampas,
)
from sigat_api.worker import ejecutar_generar_paquete_en_conexion

pytestmark = pytest.mark.integracion

DATOS_SEMILLA = r"""
SET search_path = sigat, public;

INSERT INTO region (regi_id, regi_descripcion) VALUES (1, 'Andina');
INSERT INTO departamento (depa_id, depa_descripcion, regi_id, depa_codigo) VALUES (1, 'Norte de Santander', 1, '54');
INSERT INTO municipio (muni_id, muni_descripcion, depa_id, muni_codigo) VALUES (1, 'Pamplona', 1, '518');
INSERT INTO centro_acopio (cent_id, cent_descripcion, depa_id) VALUES (1, 'Centro Pamplona', 1);

INSERT INTO persona (pers_cc, pers_nombre, pers_apellido) VALUES
  (1001, 'Adela', 'Admin'),
  (1002, 'Pedro', 'Técnico'),
  (2001, 'Carlos', 'Productor'),
  (3001, 'Gil', 'Agrónomo');

INSERT INTO administrador (admi_id, pers_cc, cent_id) VALUES (1, 1001, 1);
INSERT INTO tecnico (tecn_id, pers_cc, cent_id, tecn_codigo, admi_id, tecn_fecha_registro, esta_id)
VALUES (1, 1002, 1, 'TEC-001', 1, CURRENT_DATE - 60, (SELECT esta_id FROM estado_registro WHERE esta_descripcion = 'activo'));

INSERT INTO productor (pers_cc, prod_fecha_registro, prod_tecnico_registro_id, muni_id)
VALUES (2001, CURRENT_DATE - 60, 1, 1);

INSERT INTO usuario (usua_id, pers_cc, usua_usuario, usua_requiere_reinicio, rol_id, esta_id)
SELECT v.id, v.cc, v.usu, false, r.rol_id, er.esta_id
  FROM (VALUES (1, 1001::bigint, 'admin.prueba', 'ADMINISTRADOR'),
               (2, 1002::bigint, 'tecnico.prueba', 'TECNICO'),
               (3, 2001::bigint, 'productor.prueba', 'PRODUCTOR'),
               (4, 3001::bigint, 'agronomo.prueba', 'AGRONOMO_SUPERVISOR')) v(id, cc, usu, rol)
  JOIN rol r ON r.rol_codigo = v.rol
  JOIN estado_registro er ON er.esta_descripcion = 'activo';

INSERT INTO predio (
    pred_id, pred_codigo, pred_nombre, muni_id, pred_area, pers_cc,
    pred_geom, pred_fecha_registro, pred_tecnico_registro_id
) VALUES (
    1, 'PR-001', 'Finca El Duraznal', 1, 12.5000, 2001,
    ST_Multi(ST_SetSRID(ST_GeomFromText('POLYGON((-72.660 7.370, -72.640 7.370, -72.640 7.390, -72.660 7.390, -72.660 7.370))'), 4326)),
    CURRENT_DATE - 60, 1
);

INSERT INTO usuario_predio (usua_id, pred_id, usup_desde, usup_hasta) VALUES
  (2, 1, CURRENT_DATE - 60, NULL),
  (4, 1, CURRENT_DATE - 60, NULL);

INSERT INTO lote (
    lote_id, lote_codigo, lote_descripcion, lote_area, pred_id, espc_id,
    lote_fecha_plantacion, lote_fecha_registro, lote_tecnico_registro_id
) VALUES (
    1, '01', 'Lote 01 Durazno', 3.2000, 1,
    (SELECT espc_id FROM especie_cultivo WHERE espc_descripcion = 'Durazno' LIMIT 1),
    CURRENT_DATE - 120, CURRENT_DATE - 60, 1
);

INSERT INTO lote_geometria (
    lote_id, lotg_version, lotg_geom, lotg_vigente_desde, lotg_fuente, lotg_motivo, lotg_registrado_por
) VALUES (
    1, 1,
    ST_Multi(ST_SetSRID(ST_GeomFromText('POLYGON((-72.655 7.375, -72.645 7.375, -72.645 7.385, -72.655 7.385, -72.655 7.375))'), 4326)),
    now() - interval '30 days', 'digitalizacion', 'Geometría inicial', 1
);

INSERT INTO reticula (reti_id, pred_id, reti_resolucion_m, reti_fecha_corte_imagen)
VALUES (1, 1, 100, CURRENT_DATE - 30);

INSERT INTO reticula_celda (reti_id, retc_codigo, retc_geom)
VALUES (
    1, 'C001',
    ST_SetSRID(ST_GeomFromText('POLYGON((-72.655 7.375, -72.650 7.375, -72.650 7.380, -72.655 7.380, -72.655 7.375))'), 4326)
);

INSERT INTO programa_inspeccion (
    prog_id, pred_id, prog_version, prog_vigente_desde, prog_periodo_lectura_dias,
    prog_distancia_min_trampas_m, reti_id, prog_umbral_exportacion_mtd, prog_motivo_cambio, prog_creado_por
) VALUES (
    1, 1, 1, CURRENT_DATE - 30, 7, 50, 1, 0.1000, 'Programa inicial', 1
);

INSERT INTO trampa (
    tram_id, tram_codigo, tipt_id, atra_id, esta_id, tram_fecha_registro, tram_tecnico_registro_id
) VALUES (
    1, '54518-602-001',
    (SELECT tipt_id FROM tipo_trampa WHERE tipt_paridad = 'impar' LIMIT 1),
    (SELECT atra_id FROM atrayente LIMIT 1),
    (SELECT esta_id FROM estado_registro WHERE esta_descripcion = 'activo'),
    CURRENT_DATE - 30, 1
);

INSERT INTO ubicacion_trampa (
    ubic_id, tram_id, pred_id, lote_id, ubic_fecha, tecn_id, vigi_id, ubic_long, ubic_lat
) VALUES (
    1, 1, 1, 1, CURRENT_DATE - 30, 1,
    (SELECT vigi_id FROM tipo_vigilancia LIMIT 1),
    -72.65000, 7.38000
);

INSERT INTO periodo_trampeo (
    peri_id, lote_id, vigi_id, prog_id, peri_inicio, peri_fin, peri_estado
) VALUES (
    1, 1,
    (SELECT vigi_id FROM tipo_vigilancia LIMIT 1),
    1, CURRENT_DATE - 7, CURRENT_DATE + 7, 'abierto'
);

INSERT INTO indice_mtd (
    indi_id, peri_id, indi_version, indi_moscas, indi_trampas, indi_dias_exposicion,
    indi_umbral_aplicado, indi_resultado_contraste, esce_id
) VALUES (
    1, 1, 1, 0, 1, 7.0, 0.1000, 'por_debajo_umbral',
    (SELECT esce_id FROM escenario_trampeo ORDER BY esce_orden LIMIT 1)
);

INSERT INTO concepto_aptitud (
    conc_id, lote_id, peri_id, indi_id, conc_version, conc_resultado, conc_fecha_efecto, conc_motivo
) VALUES (
    1, 1, 1, 1, 1, 'apto', CURRENT_DATE - 1, 'Concepto inicial'
);

-- Planificación aprobada y derivada al técnico (usua_id = 2) para visibilidad en fn_lotes_visibles
DO $$
DECLARE
  v_plan_prod uuid;
  v_plan_tec uuid;
  v_disp uuid := gen_random_uuid();
  v_oper bigint;
BEGIN
  PERFORM set_config('sigat.usuario_actual', '4', true);
  INSERT INTO planificacion (usua_id_destinatario, usua_id_creador, plan_actividad,
                             plan_fecha_inicio, plan_fecha_fin, plan_estado)
  VALUES (3, 4, 'Plan predio 1', CURRENT_DATE - 10, CURRENT_DATE + 20, 'pendiente')
  RETURNING plan_id INTO v_plan_prod;
  INSERT INTO planificacion_lote (plan_id, lote_id) VALUES (v_plan_prod, 1);

  PERFORM set_config('sigat.usuario_actual', '3', true);
  UPDATE planificacion SET plan_estado = 'aceptada' WHERE plan_id = v_plan_prod;

  PERFORM set_config('sigat.usuario_actual', '4', true);
  INSERT INTO planificacion (usua_id_destinatario, usua_id_creador, plan_actividad,
                             plan_fecha_inicio, plan_fecha_fin, plan_estado, plan_id_origen)
  VALUES (2, 4, 'Inspección semanal', CURRENT_DATE - 5, CURRENT_DATE + 15, 'pendiente', v_plan_prod)
  RETURNING plan_id INTO v_plan_tec;
  INSERT INTO planificacion_lote (plan_id, lote_id) VALUES (v_plan_tec, 1);

  -- Dispositivo y cosecha vigente para validar cosecha_esperada, rendimiento_estimado y rendimiento_real
  INSERT INTO dispositivo (disp_id, usua_id, disp_identificador, disp_plataforma, disp_version_app)
  VALUES (v_disp, 3, 'dev-prod-01', 'android', '1.0.0');

  INSERT INTO operacion_sync (
      oper_cliente_id, disp_id, usua_id, oper_entidad, oper_accion,
      oper_t_captura, oper_payload_hash, oper_clasificacion
  ) VALUES (
      gen_random_uuid(), v_disp, 3, 'cosecha', 'crear',
      now(), repeat('a', 64), 'aceptada'
  ) RETURNING oper_id INTO v_oper;

  INSERT INTO cosecha (
      cose_registro_id, cose_version, lote_id, cose_fecha_esperada, cose_volumen_esperado,
      cose_fecha_real, cose_volumen_real, cose_volumen_uni_id, usua_id, disp_id, oper_id,
      cose_t_observacion, cose_t_captura
  ) VALUES (
      gen_random_uuid(), 1, 1, CURRENT_DATE + 15, 500.000,
      NULL, NULL, (SELECT unid_id FROM unidad_medida LIMIT 1), 3, v_disp, v_oper,
      now() - interval '1 hour', now()
  );
END $$;
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
        ejecutar(h, nombre, DATOS_SEMILLA)
        yield nombre
    finally:
        h.borrar_bd(nombre)


def test_paquete_campo_resumen_lote_e_invalidacion_contra_postgres(h: SimpleNamespace, bd: str) -> None:
    actor_tec = Actor(usua_id=2, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
    actor_agr = Actor(usua_id=4, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)

    async def _correr() -> None:
        motor = create_async_engine(h.url(bd))
        try:
            async with motor.begin() as con:
                await con.execute(text("SELECT set_config('sigat.usuario_actual', '2', true)"))
                repo_cfg = RepositorioConfiguracion(con)
                cola = ColaProcrastinate()
                cola.registrar(
                    TAREA_GENERAR_PAQUETE,
                    lambda datos: ejecutar_generar_paquete_en_conexion(
                        con, int(datos["pred_id"]), str(datos["motivo"])
                    ),
                )

                # 1. Predio y responsable_tecnico (verifica JOIN sigat.tecnico -> sigat.persona por pers_cc)
                svc_predios = ServicioPredios(repo_cfg, cola)
                predios = await svc_predios.listar(actor_tec)
                assert len(predios) == 1
                assert predios[0].responsable_tecnico == "Pedro Técnico"

                # 2. Paquete de campo info + contenido + ETag 304
                svc_paquete = ServicioPaqueteCampo(repo_cfg)
                info_v1 = await svc_paquete.info(actor_tec, 1)
                assert info_v1.paqu_version == 1

                contenido = await svc_paquete.contenido(actor_tec, 1, si_no_coincide=None)
                assert contenido is not None
                assert contenido.predio.responsable_tecnico == "Pedro Técnico"
                assert contenido.mapa.predio.geometry is not None
                assert len(contenido.mapa.lotes.features) == 1
                assert len(contenido.mapa.celdas.features) == 1
                assert len(contenido.mapa.trampas.features) == 1

                no_modificado = await svc_paquete.contenido(
                    actor_tec, 1, si_no_coincide=f'"{info_v1.paqu_hash}"'
                )
                assert no_modificado is None

                # 3. Resumen del lote (verifica cose_volumen_esperado, cose_fecha_esperada, concepto_fecha_efecto)
                repo_conc = RepositorioConcepto(con)
                svc_conc = ServicioConcepto(repo_conc)
                resumen = await svc_conc.resumen_lote(actor_tec, 1)
                assert resumen.lote_id == 1
                assert resumen.concepto == ResultadoConcepto.APTO
                assert resumen.concepto_fecha_efecto is not None
                assert resumen.rendimiento_estimado == Decimal("500.000")
                assert resumen.cosecha_esperada is not None
                assert len(resumen.serie) == 1

                # 4. Concepto a una fecha (RF-024)
                hoy_utc = datetime.now(UTC).date()
                c_fecha = await svc_conc.concepto_a_fecha(actor_tec, 1, hoy_utc)
                assert c_fecha.concepto is not None
                assert c_fecha.concepto.conc_resultado == ResultadoConcepto.APTO

                # 5. Los 4 puntos de invalidación + consumo en worker
                await con.execute(text("SELECT set_config('sigat.usuario_actual', '4', true)"))
                # 5a. Geometría del lote
                svc_lotes = ServicioLotes(repo_cfg, cola)
                await svc_lotes.nueva_geometria(
                    actor_agr,
                    1,
                    e.EntradaGeometria(
                        geometria=GeometriaGeoJson(
                            type="Polygon",
                            coordinates=[
                                [
                                    [-72.656, 7.375],
                                    [-72.645, 7.375],
                                    [-72.645, 7.385],
                                    [-72.656, 7.385],
                                    [-72.656, 7.375],
                                ]
                            ],
                        ),
                        fuente="digitalizacion",
                        motivo="Ampliación occidental",
                        vigente_desde=datetime.now(UTC),
                    ),
                )
                assert await repo_cfg.paquete_vigente(1) is None
                assert await cola.drenar() == 1
                v2 = await repo_cfg.paquete_vigente(1)
                assert v2 is not None
                assert v2["paqu_version"] == 2

                # 5b. Trampa (actualización de atrayente)
                svc_trampas = ServicioTrampas(repo_cfg, cola)
                await svc_trampas.actualizar(actor_agr, 1, e.ActualizacionTrampa(atra_id=1))
                assert await repo_cfg.paquete_vigente(1) is None
                assert await cola.drenar() == 1
                v3 = await repo_cfg.paquete_vigente(1)
                assert v3 is not None
                assert v3["paqu_version"] == 3

                # 5c. Retícula nueva
                svc_reticula = ServicioReticula(repo_cfg, cola)
                await svc_reticula.crear(actor_agr, 1, e.NuevaReticula(resolucion_m=150))
                assert await repo_cfg.paquete_vigente(1) is None
                assert await cola.drenar() == 1
                v4 = await repo_cfg.paquete_vigente(1)
                assert v4 is not None
                assert v4["paqu_version"] == 4

                # 5d. Programa de inspección nueva versión
                svc_programa = ServicioPrograma(repo_cfg, cola)
                await svc_programa.nueva_version(
                    actor_agr,
                    1,
                    e.NuevaVersionPrograma(
                        vigente_desde=hoy_utc,
                        periodo_lectura_dias=10,
                        distancia_min_trampas_m=50,
                        reti_id=1,
                        umbral_exportacion_mtd=Decimal("0.0800"),
                        motivo_cambio="Nuevo ciclo semestral",
                    ),
                )
                assert await repo_cfg.paquete_vigente(1) is None
                assert await cola.drenar() == 1
                v5 = await repo_cfg.paquete_vigente(1)
                assert v5 is not None
                assert v5["paqu_version"] == 5
        finally:
            await motor.dispose()

    asyncio.run(_correr())
