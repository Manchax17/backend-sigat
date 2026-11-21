"""Pruebas de ServicioConcepto (resumen del lote con MTD, umbral y serie)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from sigat_api.compartido.errores import NoEncontrado
from sigat_api.compartido.tipos import Actor, Cliente, ResultadoConcepto, ResultadoContraste, RolCodigo
from sigat_api.modules.concepto.servicio import ServicioConcepto


def _ejecutar(coro: Any) -> Any:
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


ACTOR_TECNICO = Actor(usua_id=2, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)


class RepoFalsoConcepto:
    def __init__(self) -> None:
        self.datos_resumen: dict[str, Any] | None = {
            "lote_id": 1,
            "lote_descripcion": "Lote 01 Durazno",
            "mtd": Decimal("0.0500"),
            "mtd_es_estimacion": False,
            "escenario": "Baja prevalencia",
            "umbral": Decimal("0.1000"),
            "contraste": ResultadoContraste.POR_DEBAJO_UMBRAL,
            "concepto": ResultadoConcepto.APTO,
            "concepto_fecha_efecto": date(2026, 11, 10),
            "inspecciones": 3,
            "inspecciones_mes": 1,
            "aplicaciones": 0,
            "ultima_aplicacion": None,
            "lecturas": 6,
            "especimenes": 1,
            "trampas_vigentes": 6,
            "trampas_con_capturas": 1,
            "ultima_visita": datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
            "ultima_cosecha": None,
            "cosecha_esperada": date(2026, 12, 1),
            "rendimiento_estimado": Decimal("500.00"),
            "rendimiento_real": None,
            "hallazgos": 0,
            "revision_pendiente": None,
            "etapa": {
                "derivada": "Floración",
                "declarada": "Vegetativo",
                "declarada_el": None,
                "discrepancia": True,
            },
            "periodo_actual": {
                "peri_id": 1,
                "lote_id": 1,
                "vigi_id": 1,
                "prog_id": 1,
                "peri_inicio": date(2026, 11, 1),
                "peri_fin": date(2026, 11, 15),
                "peri_estado": "abierto",
            },
        }
        self.serie_data: list[dict[str, Any]] = [
            {
                "peri_id": 1,
                "peri_inicio": date(2026, 11, 1),
                "peri_fin": date(2026, 11, 15),
                "mtd": Decimal("0.0500"),
                "moscas": 1,
                "trampas": 6,
                "dias_exposicion": Decimal("7.0"),
                "umbral": Decimal("0.1000"),
                "contraste": ResultadoContraste.POR_DEBAJO_UMBRAL,
                "escenario": "Baja prevalencia",
            }
        ]

    async def resumen(self, lote_id: int, usua_id: int | None = None) -> dict[str, Any] | None:
        if self.datos_resumen and self.datos_resumen["lote_id"] == lote_id:
            return dict(self.datos_resumen)
        return None

    async def serie(
        self, lote_id: int, desde: date | None = None, hasta: date | None = None, usua_id: int | None = None
    ) -> list[dict[str, Any]]:
        return list(self.serie_data)


@pytest.mark.regla
def test_resumen_lote_completo_con_serie() -> None:
    repo = RepoFalsoConcepto()
    svc = ServicioConcepto(repo)  # type: ignore[arg-type]

    resumen = _ejecutar(svc.resumen_lote(ACTOR_TECNICO, 1))
    assert resumen.lote_id == 1
    assert resumen.mtd == Decimal("0.0500")
    assert resumen.umbral == Decimal("0.1000")
    assert resumen.contraste == ResultadoContraste.POR_DEBAJO_UMBRAL
    assert resumen.concepto == ResultadoConcepto.APTO
    assert resumen.etapa.discrepancia is True
    assert len(resumen.serie) == 1
    assert resumen.serie[0].mtd == Decimal("0.0500")


@pytest.mark.regla
def test_resumen_lote_no_visible_da_404() -> None:
    repo = RepoFalsoConcepto()
    repo.datos_resumen = None
    svc = ServicioConcepto(repo)  # type: ignore[arg-type]

    with pytest.raises(NoEncontrado) as exc:
        _ejecutar(svc.resumen_lote(ACTOR_TECNICO, 999))
    assert exc.value.codigo == "LOTE_NO_ENCONTRADO"
