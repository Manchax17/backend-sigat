"""Reglas del motor MTD: agregación del periodo y días de exposición (RF-022, ADR-13)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from sigat_api.compartido.tipos import ResultadoContraste
from sigat_api.modules.registro.mtd import EntradaMtd, MotorMtd, calcular, redondear_dias


class RepoFalso:
    def __init__(self) -> None:
        self.lecturas: list[dict[str, Any]] = []
        self.umbral: dict[str, Any] | None = None
        self.insertado: dict[str, Any] | None = None

    async def lecturas_mtd(self, peri_id: int) -> list[dict[str, Any]]:
        return self.lecturas

    async def umbral_vigente(self, peri_id: int) -> dict[str, Any] | None:
        return self.umbral

    async def insertar_mtd(self, **kwargs: Any) -> int:
        self.insertado = kwargs
        return 77


def _lectura(tram_id: int, fecha: date, inicio: date, especimenes: int = 3) -> dict[str, Any]:
    return {
        "lect_id": f"0190f5b0-0000-7000-8000-0000000000{tram_id:02d}",
        "tram_id": tram_id,
        "lect_especimenes": especimenes,
        "lect_fecha": fecha,
        "inicio_exposicion": inicio,
    }


def test_dias_de_exposicion_promedia_por_trampa_atendida() -> None:
    """D = promedio de días desde el inicio de exposición hasta la última lectura, inclusive."""
    por_trampa = {
        # empezó el 1 y su última lectura fue el 4: 1..4 inclusive = 4 días
        11: [_lectura(11, date(2026, 3, 4), date(2026, 3, 1))],
        # empezó el día 6: 6..10 = 5 días
        12: [
            _lectura(12, date(2026, 3, 8), date(2026, 3, 6)),
            _lectura(12, date(2026, 3, 10), date(2026, 3, 6)),
        ],
    }

    assert MotorMtd._dias_exposicion(por_trampa) == Decimal("4.500")


def test_trampa_sin_lecturas_no_cuenta_como_atendida() -> None:
    assert MotorMtd._dias_exposicion({}) == Decimal("0")


async def test_recalcular_periodo_inserta_el_indice_del_periodo() -> None:
    repo = RepoFalso()
    repo.lecturas = [
        _lectura(11, date(2026, 3, 5), date(2026, 3, 1), especimenes=10),
        _lectura(12, date(2026, 3, 5), date(2026, 3, 1), especimenes=20),
    ]
    repo.umbral = {"prog_id": 2, "umbral": Decimal("1.5000"), "norm_id": 3}
    motor = MotorMtd(repo)  # type: ignore[arg-type]

    indi_id = await motor.recalcular_periodo(5, "sincronizacion")

    assert indi_id == 77
    assert repo.insertado is not None
    assert repo.insertado["moscas"] == 30
    assert repo.insertado["trampas"] == 2
    assert repo.insertado["dias_exposicion"] == Decimal("5.000")
    # 30 / (2 × 5) = 3.0 moscas/trampa/día, por encima del umbral 1,5
    assert repo.insertado["valor"] == Decimal("3.000000")
    assert repo.insertado["contraste"] is ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL
    assert repo.insertado["umbral"] == repo.umbral
    assert sorted(repo.insertado["lect_ids"]) == sorted(
        [
            _lectura(11, date(2026, 3, 5), date(2026, 3, 1))["lect_id"],
            _lectura(12, date(2026, 3, 5), date(2026, 3, 1))["lect_id"],
        ]
    )
    assert repo.insertado["motivo"] == "sincronizacion"


async def test_recalcular_periodo_sin_lecturas_deja_el_valor_nulo() -> None:
    repo = RepoFalso()
    motor = MotorMtd(repo)  # type: ignore[arg-type]

    await motor.recalcular_periodo(5, "sincronizacion")

    assert repo.insertado is not None
    assert repo.insertado["valor"] is None
    assert repo.insertado["contraste"] is ResultadoContraste.SIN_UMBRAL
    assert repo.insertado["lect_ids"] == []


@pytest.mark.parametrize(
    ("moscas", "trampas", "dias", "umbral", "valor", "contraste"),
    [
        (30, 3, "10", "0.5", Decimal("1.000000"), ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL),
        (14, 4, "7", "1", Decimal("0.500000"), ResultadoContraste.POR_DEBAJO_UMBRAL),
        (1, 3, "1", None, Decimal("0.333333"), ResultadoContraste.SIN_UMBRAL),
    ],
)
def test_calcular_redondea_a_seis_decimales(
    moscas: int,
    trampas: int,
    dias: str,
    umbral: str | None,
    valor: Decimal,
    contraste: ResultadoContraste,
) -> None:
    """``indice_mtd.indi_valor`` es numeric(14,6) y el contraste se decide con el valor redondeado."""
    salida = calcular(
        EntradaMtd(
            moscas=moscas,
            trampas=trampas,
            dias_exposicion=Decimal(dias),
            umbral=Decimal(umbral) if umbral is not None else None,
        )
    )

    assert salida.valor == valor
    assert salida.contraste is contraste


def test_redondear_dias_usa_la_precision_de_la_columna() -> None:
    assert redondear_dias(Decimal("7.5")) == Decimal("7.500")
    assert redondear_dias(Decimal("10") / Decimal("6")) == Decimal("1.667")
