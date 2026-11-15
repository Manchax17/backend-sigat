"""Reglas del módulo concepto (emisión y reemisión del veredicto del periodo)."""

from typing import Any

import pytest

from sigat_api.compartido.errores import NoEncontrado
from sigat_api.compartido.tipos import ResultadoConcepto, ResultadoContraste
from sigat_api.modules.concepto.servicio import ServicioConcepto

FECHA = "2026-03-01"


class RepoFalso:
    def __init__(self, contraste: ResultadoContraste = ResultadoContraste.POR_DEBAJO_UMBRAL) -> None:
        self.contraste = contraste
        self.emitidos: list[dict[str, Any]] = []
        self.ultimo: dict[str, Any] | None = None

    async def indice(self, indi_id: int) -> dict[str, Any] | None:
        if self.contraste is None:  # type: ignore[comparison-overlap]
            return None
        return {
            "indi_id": indi_id,
            "peri_id": 5,
            "lote_id": 42,
            "norm_id": None if self.contraste is ResultadoContraste.SIN_UMBRAL else 3,
            "indi_resultado_contraste": str(self.contraste),
            "indi_valor": 1,
            "peri_fin": FECHA,
        }

    async def ultimo_concepto(self, lote_id: int, peri_id: int) -> dict[str, Any] | None:
        return self.ultimo

    async def insertar_concepto(self, fila: dict[str, Any]) -> int:
        self.emitidos.append(fila)
        return len(self.emitidos)


@pytest.mark.regla
async def test_concepto_01() -> None:
    """La reemisión usa como fecha de efecto la del registro que la disparó, no la de hoy."""
    repo = RepoFalso()
    servicio = ServicioConcepto(repo)  # type: ignore[arg-type]

    await servicio.reemitir(5, 9, "sincronizacion", fecha_efecto="2026-02-20")

    assert repo.emitidos[0]["fecha_efecto"] == "2026-02-20"
    assert repo.emitidos[0]["resultado"] == ResultadoConcepto.APTO.value
    assert repo.emitidos[0]["version"] == 1
    assert repo.emitidos[0]["norm_id"] == 3


@pytest.mark.regla
async def test_concepto_02() -> None:
    """Reprocesar dos veces el mismo resultado no crea una versión nueva."""
    repo = RepoFalso()
    servicio = ServicioConcepto(repo)  # type: ignore[arg-type]

    await servicio.reemitir(5, 9, "sincronizacion", fecha_efecto="2026-02-20")
    repo.ultimo = {
        **repo.emitidos[0],
        "conc_resultado": "apto",
        "conc_version": 1,
        "conc_fecha_efecto": "2026-02-20",
    }
    await servicio.reemitir(5, 9, "sincronizacion", fecha_efecto="2026-02-20")

    assert len(repo.emitidos) == 1


@pytest.mark.regla
async def test_concepto_03() -> None:
    """Sin umbral el concepto es no_emitido (UQQ-004: no se inventa el umbral)."""
    repo = RepoFalso(ResultadoContraste.SIN_UMBRAL)
    servicio = ServicioConcepto(repo)  # type: ignore[arg-type]

    await servicio.reemitir(5, 9, "sincronizacion")

    assert repo.emitidos[0]["resultado"] == ResultadoConcepto.NO_EMITIDO.value
    assert repo.emitidos[0]["norm_id"] is None
    assert repo.emitidos[0]["fecha_efecto"] == FECHA  # sin disparador, la fecha del periodo


async def test_concepto_supera_el_umbral_es_no_apto() -> None:
    repo = RepoFalso(ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL)

    await ServicioConcepto(repo).reemitir(5, 9, "sincronizacion")  # type: ignore[arg-type]

    assert repo.emitidos[0]["resultado"] == ResultadoConcepto.NO_APTO.value


async def test_concepto_de_un_indice_inexistente_falla() -> None:
    repo = RepoFalso()
    repo.contraste = None  # type: ignore[assignment]

    with pytest.raises(NoEncontrado) as error:
        await ServicioConcepto(repo).reemitir(5, 9, "sincronizacion")  # type: ignore[arg-type]

    assert error.value.codigo == "MTD_INEXISTENTE"
