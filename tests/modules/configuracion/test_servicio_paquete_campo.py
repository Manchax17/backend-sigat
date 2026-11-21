"""Pruebas del servicio de paquete de campo (ServicioPaqueteCampo).

Valida generación bajo demanda, 404 por visibilidad y respuesta 304 (None) por ETag.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from sigat_api.compartido.errores import NoEncontrado
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.modules.configuracion.servicio import ServicioPaqueteCampo


def _ejecutar(coro: Any) -> Any:
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


ACTOR_TECNICO = Actor(usua_id=2, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)


class RepoFalsoPaquete:
    def __init__(self) -> None:
        self.visible = True
        self.paquete: dict[str, Any] | None = None
        self.paquetes_registrados: list[dict[str, Any]] = []
        self.invalidaciones: list[tuple[int, str]] = []

    async def predio_visible(self, usua_id: int, pred_id: int) -> bool:
        return self.visible

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
