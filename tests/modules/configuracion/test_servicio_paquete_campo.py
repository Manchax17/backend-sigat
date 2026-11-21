"""Pruebas del servicio de paquete de campo (ServicioPaqueteCampo), los 4 puntos de invalidación
(programa, retícula, trampas y geometría) y el consumidor de la cola TAREA_GENERAR_PAQUETE.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from sigat_api.compartido.errores import NoEncontrado
from sigat_api.compartido.esquemas import GeometriaGeoJson
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.infra.cola.cola import TAREA_GENERAR_PAQUETE, ColaProcrastinate
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.servicio import (
    ServicioLotes,
    ServicioPaqueteCampo,
    ServicioPredios,
    ServicioPrograma,
    ServicioReticula,
    ServicioTrampas,
)


def _ejecutar(coro: Any) -> Any:
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value


ACTOR_TECNICO = Actor(usua_id=2, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
ACTOR_AGRONOMO = Actor(usua_id=3, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)


class RepoFalsoPaquete:
    def __init__(self) -> None:
        self.visible = True
        self.paquete: dict[str, Any] | None = None
        self.paquetes_registrados: list[dict[str, Any]] = []
        self.invalidaciones: list[tuple[int, str]] = []

    async def predio_visible(self, usua_id: int, pred_id: int) -> bool:
        return self.visible

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return self.visible

    async def lote_predio(self, lote_id: int) -> int | None:
        return 1

    async def predio_del_lote(self, lote_id: int) -> int | None:
        return 1

    async def lote_activo_en(self, lote_id: int, fecha: date) -> bool:
        return True

    async def paquete_vigente(self, pred_id: int) -> dict[str, Any] | None:
        return self.paquete

    async def predio(self, pred_id: int) -> dict[str, Any] | None:
        return {
            "pred_id": pred_id,
            "pred_codigo": "PR-01",
            "pred_nombre": "Finca San José",
            "muni_id": 1,
            "municipio": "Pamplona",
            "departamento": "Norte de Santander",
            "pred_area": Decimal("10.5000"),
            "productor": "Carlos Productor",
            "pred_zona_horaria": "America/Bogota",
            "total_lotes": 2,
            "tiene_geometria": True,
            "responsable_tecnico": "Pedro Técnico",
        }

    async def actualizar_predio(self, pred_id: int, cambios: dict[str, Any]) -> None:
        return None

    async def actualizar_geometria_predio(self, pred_id: int, geojson: str) -> None:
        return None

    async def nueva_version_geometria(
        self,
        lote_id: int,
        geojson: str,
        desde: datetime,
        fuente: str,
        motivo: str,
        registrado_por: int | None = None,
    ) -> dict[str, Any]:
        return {
            "lotg_id": 1,
            "lotg_version": 2,
            "geometria": {
                "type": "Polygon",
                "coordinates": [[[-73.1, 7.1], [-73.1, 7.2], [-73.2, 7.2], [-73.1, 7.1]]],
            },
            "lotg_area_m2": Decimal("10000"),
            "vigente_desde": desde,
            "vigente_hasta": None,
            "fuente": fuente,
            "motivo": motivo,
            "registrado_en": datetime.now(UTC),
        }

    async def programa_vigente(self, pred_id: int, fecha: date) -> dict[str, Any] | None:
        return {
            "prog_id": 1,
            "pred_id": pred_id,
            "prog_version": 1,
            "vigente_desde": date(2026, 1, 1),
            "vigente_hasta": None,
            "ciclo_inicio": None,
            "ciclo_fin": None,
            "periodo_lectura_dias": 7,
            "distancia_min_trampas_m": 50,
            "reti_id": None,
            "resolucion_reticula_m": None,
            "proveedor_imagen": None,
            "umbral_exportacion_mtd": None,
            "norma": None,
            "escenarios": [],
            "criterios_bpa": [],
            "motivo_cambio": "Inicio",
            "creado_por": "Admin",
            "creado_en": datetime.now(UTC),
        }

    async def nueva_version_programa(
        self, pred_id: int, datos: dict[str, Any], creado_por: int
    ) -> dict[str, Any]:
        prog = await self.programa_vigente(pred_id, datos["vigente_desde"])
        assert prog is not None
        return {
            **prog,
            "prog_id": 2,
            "prog_version": 2,
            "vigente_desde": datos["vigente_desde"],
            "periodo_lectura_dias": datos["periodo_lectura_dias"],
            "distancia_min_trampas_m": datos["distancia_min_trampas_m"],
            "motivo_cambio": datos["motivo_cambio"],
        }

    async def distancia_minima_trampas(self, pred_id: int, fecha: date) -> int | None:
        return 50

    async def insertar_reticula(
        self, pred_id: int, resolucion_m: int, corte: date | None, celdas: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "reti_id": 1,
            "pred_id": pred_id,
            "resolucion_m": resolucion_m,
            "fecha_corte_imagen": corte,
            "creada_en": datetime.now(UTC),
            "celdas": {"type": "FeatureCollection", "features": celdas},
        }

    async def trampa_por_id(self, usua_id: int, tram_id: int) -> dict[str, Any] | None:
        return {
            "tram_id": tram_id,
            "tram_codigo": "54518-602-001",
            "tipo": "Jackson",
            "lote_id": 1,
            "pred_id": 1,
            "longitud": Decimal("-73.15000"),
            "latitud": Decimal("7.15000"),
            "celda": None,
            "estado": "activo",
            "atrayente": "Trimedlure",
            "ubic_id": 10,
            "dentro_del_lote": True,
            "ultima_lectura": None,
            "ultima_lectura_especimenes": None,
        }

    async def trampas_cercanas(self, *args: Any, **kwargs: Any) -> list[str]:
        return []

    async def reubicar(self, tram_id: int, nueva: dict[str, Any]) -> int:
        return 11

    async def ubicaciones(self, tram_id: int, usua_id: int) -> list[dict[str, Any]]:
        return [
            {
                "ubic_id": 11,
                "tram_id": tram_id,
                "pred_id": 1,
                "lote_id": 1,
                "ubic_fecha": date(2026, 11, 15),
                "ubic_fecha_fin": None,
                "vigi_id": 1,
                "tecn_id": 1,
                "ruta_id": None,
                "retc_id": None,
                "loca_id": None,
                "arbo_id": None,
                "ubic_long": Decimal("-73.15100"),
                "ubic_lat": Decimal("7.15100"),
                "ubic_alt": None,
                "ubic_referencia": "Reubicada",
                "ubic_origen": "operacion",
                "ubic_registrado_en": datetime.now(UTC),
            }
        ]

    async def mapa_predio(self, pred_id: int, instante: datetime) -> dict[str, Any]:
        return {
            "predio": {
                "type": "Feature",
                "id": pred_id,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-73.1, 7.1], [-73.1, 7.2], [-73.2, 7.2], [-73.1, 7.1]]],
                },
                "properties": {"pred_id": pred_id, "pred_nombre": "Finca San José", "pred_codigo": "PR-01"},
            },
            "lotes": {"type": "FeatureCollection", "features": []},
            "celdas": {"type": "FeatureCollection", "features": []},
            "trampas": {"type": "FeatureCollection", "features": []},
        }

    async def limites_predio(self, pred_id: int) -> list[float] | None:
        return [-73.2, 7.1, -73.1, 7.2]

    async def catalogos_paquete(self) -> dict[str, list[dict[str, object]]]:
        return {
            "condicion_trampa": [{"cond_id": 1, "cond_descripcion": "Operativa", "cond_activa": True}],
            "atrayente": [
                {
                    "atra_id": 1,
                    "atra_descripcion": "Feromona",
                    "atra_vida_util_min_sem": 4,
                    "atra_vida_util_max_sem": 8,
                }
            ],
            "especie_mosca": [
                {
                    "espm_id": 1,
                    "espm_descripcion": "Ceratitis capitata",
                    "espm_codigo": 1,
                    "espm_tipo": "especie",
                }
            ],
            "tipo_trampa": [{"tipt_id": 1, "tipt_descripcion": "Jackson", "tipt_paridad": "par"}],
            "producto_fitosanitario": [],
            "unidad_medida": [{"unid_id": 1, "unid_descripcion": "Hectárea", "unid_simbolo": "ha"}],
        }

    async def invalidar_paquetes(self, pred_id: int, motivo: str) -> None:
        self.invalidaciones.append((pred_id, motivo))
        self.paquete = None

    async def registrar_paquete(
        self, pred_id: int, prog_id: int, hash_hex: str, generado_en: datetime
    ) -> dict[str, Any]:
        nuevo = {
            "paqu_id": UUID("12345678-1234-5678-1234-567812345678"),
            "pred_id": pred_id,
            "paqu_version": len(self.paquetes_registrados) + 1,
            "prog_id": prog_id,
            "paqu_hash": hash_hex,
            "generado_en": generado_en,
            "invalidado_en": None,
            "tamano_bytes": None,
        }
        self.paquetes_registrados.append(nuevo)
        self.paquete = nuevo
        return nuevo


@pytest.mark.regla
def test_info_predio_no_visible_da_404() -> None:
    repo = RepoFalsoPaquete()
    repo.visible = False
    svc = ServicioPaqueteCampo(repo)  # type: ignore[arg-type]

    with pytest.raises(NoEncontrado) as exc:
        _ejecutar(svc.info(ACTOR_TECNICO, 999))
    assert exc.value.codigo == "PREDIO_NO_ENCONTRADO"


@pytest.mark.regla
def test_info_genera_paquete_bajo_demanda_si_no_existe() -> None:
    repo = RepoFalsoPaquete()
    svc = ServicioPaqueteCampo(repo)  # type: ignore[arg-type]

    info = _ejecutar(svc.info(ACTOR_TECNICO, 1))
    assert info.pred_id == 1
    assert info.paqu_version == 1
    assert len(info.paqu_hash) == 64
    assert len(repo.paquetes_registrados) == 1


@pytest.mark.regla
def test_contenido_devuelve_paquete_completo_con_geojson() -> None:
    repo = RepoFalsoPaquete()
    svc = ServicioPaqueteCampo(repo)  # type: ignore[arg-type]

    contenido = _ejecutar(svc.contenido(ACTOR_TECNICO, 1, si_no_coincide=None))
    assert contenido is not None
    assert contenido.predio.pred_nombre == "Finca San José"
    assert contenido.mapa.predio.geometry is not None
    assert contenido.mapa.predio.geometry.type == "Polygon"
    assert "condicion_trampa" in contenido.catalogos
    assert contenido.mapa_base.limites == [-73.2, 7.1, -73.1, 7.2]


@pytest.mark.regla
def test_contenido_etag_coincidente_devuelve_none_para_304() -> None:
    repo = RepoFalsoPaquete()
    svc = ServicioPaqueteCampo(repo)  # type: ignore[arg-type]

    info = _ejecutar(svc.info(ACTOR_TECNICO, 1))
    resultado = _ejecutar(svc.contenido(ACTOR_TECNICO, 1, si_no_coincide=f'"{info.paqu_hash}"'))
    assert resultado is None


@pytest.mark.regla
def test_cuatro_puntos_invalidan_paquete_y_worker_consume_cola() -> None:
    """Los 4 puntos (programa, retícula, trampas y geometría) invalidan el paquete vigente y
    encolan TAREA_GENERAR_PAQUETE; el consumidor de la cola regenera la siguiente versión."""
    repo = RepoFalsoPaquete()
    cola = ColaProcrastinate()
    svc_paquete = ServicioPaqueteCampo(repo)  # type: ignore[arg-type]
    cola.registrar(
        TAREA_GENERAR_PAQUETE,
        lambda datos: svc_paquete.generar(int(datos["pred_id"]), str(datos["motivo"])),
    )

    # Versión 1 inicial
    v1 = _ejecutar(svc_paquete.info(ACTOR_TECNICO, 1))
    assert v1.paqu_version == 1

    # Punto 1: Programa de inspección
    svc_programa = ServicioPrograma(repo, cola)  # type: ignore[arg-type]
    _ejecutar(
        svc_programa.nueva_version(
            ACTOR_AGRONOMO,
            1,
            e.NuevaVersionPrograma(
                vigente_desde=date(2026, 6, 1),
                periodo_lectura_dias=7,
                distancia_min_trampas_m=50,
                motivo_cambio="Cambio de programa",
            ),
        )
    )
    assert repo.paquete is None
    assert len(cola.pendientes) == 1
    assert _ejecutar(cola.drenar()) == 1
    assert repo.paquete is not None
    assert repo.paquete["paqu_version"] == 2

    # Punto 2: Retícula
    svc_reticula = ServicioReticula(repo, cola)  # type: ignore[arg-type]
    _ejecutar(
        svc_reticula.crear(
            ACTOR_AGRONOMO,
            1,
            e.NuevaReticula(resolucion_m=100, fecha_corte_imagen=date(2026, 6, 1)),
        )
    )
    assert repo.paquete is None
    assert _ejecutar(cola.drenar()) == 1
    assert repo.paquete is not None
    assert repo.paquete["paqu_version"] == 3

    # Punto 3: Trampas (reubicación)
    svc_trampas = ServicioTrampas(repo, cola)  # type: ignore[arg-type]
    _ejecutar(
        svc_trampas.reubicar(
            ACTOR_AGRONOMO,
            1,
            e.NuevaUbicacion(
                lote_id=1,
                ubic_fecha=date(2026, 11, 15),
                vigi_id=1,
                tecn_id=1,
                ubic_long=Decimal("-73.15100"),
                ubic_lat=Decimal("7.15100"),
            ),
        )
    )
    assert repo.paquete is None
    assert _ejecutar(cola.drenar()) == 1
    assert repo.paquete is not None
    assert repo.paquete["paqu_version"] == 4

    # Punto 4: Geometría (lote y predio)
    geom = e.EntradaGeometria(
        geometria=GeometriaGeoJson(
            type="Polygon",
            coordinates=[[[-73.1, 7.1], [-73.1, 7.2], [-73.2, 7.2], [-73.1, 7.1]]],
        ),
        fuente="digitalizacion",
        motivo="Ajuste cartográfico",
        vigente_desde=datetime(2026, 11, 15, tzinfo=UTC),
    )
    svc_lotes = ServicioLotes(repo, cola)  # type: ignore[arg-type]
    _ejecutar(svc_lotes.nueva_geometria(ACTOR_AGRONOMO, 1, geom))
    assert repo.paquete is None
    assert _ejecutar(cola.drenar()) == 1
    assert repo.paquete is not None
    assert repo.paquete["paqu_version"] == 5

    svc_predios = ServicioPredios(repo, cola)  # type: ignore[arg-type]
    _ejecutar(svc_predios.poner_geometria(ACTOR_AGRONOMO, 1, geom))
    assert repo.paquete is None
    assert _ejecutar(cola.drenar()) == 1
    assert repo.paquete is not None
    assert repo.paquete["paqu_version"] == 6
