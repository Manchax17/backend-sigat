"""Procesador del reproceso: recalcula el MTD y reemite el concepto en ese orden."""

from typing import Any

from sigat_api.modules.sincronizacion.procesador import ProcesadorReproceso


class RecalculoFalso:
    def __init__(self) -> None:
        self.llamadas: list[tuple[int, str]] = []

    async def recalcular_periodo(self, peri_id: int, motivo: str) -> int:
        self.llamadas.append((peri_id, motivo))
        return 77


class ConceptoFalso:
    def __init__(self) -> None:
        self.llamadas: list[dict[str, Any]] = []

    async def reemitir(self, peri_id: int, indi_id: int, motivo: str, fecha_efecto: Any = None) -> None:
        self.llamadas.append(
            {
                "peri_id": peri_id,
                "indi_id": indi_id,
                "motivo": motivo,
                "fecha_efecto": fecha_efecto,
            }
        )


async def test_reproceso_recalcula_y_luego_reemite_con_la_fecha_del_registro() -> None:
    recalculo = RecalculoFalso()
    concepto = ConceptoFalso()

    await ProcesadorReproceso(recalculo, concepto).ejecutar(
        peri_id=5,
        motivo="sincronizacion",
        oper_id=12,
        fecha_efecto="2026-03-01T13:15:30.250Z",
    )

    assert recalculo.llamadas == [(5, "sincronizacion")]
    assert concepto.llamadas == [
        {
            "peri_id": 5,
            "indi_id": 77,
            "motivo": "sincronizacion",
            "fecha_efecto": concepto.llamadas[0]["fecha_efecto"],
        }
    ]
    assert str(concepto.llamadas[0]["fecha_efecto"]) == "2026-03-01"


async def test_reproceso_sin_fecha_usa_la_del_periodo() -> None:
    concepto = ConceptoFalso()

    await ProcesadorReproceso(RecalculoFalso(), concepto).ejecutar(5, "sincronizacion", None)

    assert concepto.llamadas[0]["fecha_efecto"] is None
