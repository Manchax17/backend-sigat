"""Reglas del módulo concepto (RNF-017)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from sigat_api.compartido.tipos import Actor, Cliente, ResultadoConcepto, ResultadoContraste, RolCodigo
from sigat_api.modules.concepto.servicio import ServicioConcepto


class _RepoConceptoFalso:
    def __init__(
        self,
        *,
        contraste: str = ResultadoContraste.POR_DEBAJO_UMBRAL.value,
        fecha_efecto: date = date(2026, 11, 10),
        resultado_vigente: str | None = None,
        historial: list[dict[str, Any]] | None = None,
    ) -> None:
        self.contraste = contraste
        self.fecha_efecto = fecha_efecto
        self.resultado_vigente = resultado_vigente
        self.insertados: list[dict[str, Any]] = []
        self.historial = list(historial or [])

    async def lote_visible(self, usua_id: int | None, lote_id: int | None = None) -> bool:
        # Permite invocarse como (usua_id, lote_id) o como (lote_id, usua_id)
        return True

    async def datos_reemision(self, peri_id: int, indi_id: int) -> dict[str, Any] | None:
        return {
            "peri_id": peri_id,
            "lote_id": 1,
            "peri_fin": date(2026, 11, 24),
            "norm_id": 1,
            "indi_id": indi_id,
            "contraste": self.contraste,
            "fecha_efecto": self.fecha_efecto,
            "resultado_vigente": self.resultado_vigente,
        }

    async def insertar_concepto(self, fila: dict[str, Any]) -> int:
        self.insertados.append(dict(fila))
        return len(self.insertados)

    async def concepto_a_fecha(
        self, lote_id: int, fecha: date, zona: str = "America/Bogota"
    ) -> dict[str, Any] | None:
        candidatos = [
            c
            for c in self.historial
            if c["lote_id"] == lote_id
            and c["conc_fecha_efecto"] <= fecha
            and c["conc_emitido_en"].date() <= fecha
        ]
        if not candidatos:
            return None
        candidatos.sort(key=lambda c: (c["conc_fecha_efecto"], c["conc_version"], c["conc_id"]), reverse=True)
        return dict(candidatos[0])


def _ejecutar(coro: Any) -> Any:
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value


@pytest.mark.regla
def test_concepto_01() -> None:
    """reemisión usa fecha de efecto = t_observacion del registro disparador."""
    fecha_obs = date(2026, 11, 12)
    repo = _RepoConceptoFalso(
        contraste=ResultadoContraste.POR_DEBAJO_UMBRAL.value,
        fecha_efecto=fecha_obs,
    )
    svc = ServicioConcepto(repo)  # type: ignore[arg-type]

    _ejecutar(svc.reemitir(peri_id=10, indi_id=25, motivo="Lectura tardía sincronizada"))

    assert len(repo.insertados) == 1
    insertado = repo.insertados[0]
    assert insertado["conc_fecha_efecto"] == fecha_obs
    assert insertado["conc_resultado"] == ResultadoConcepto.APTO.value
    assert insertado["peri_id"] == 10
    assert insertado["indi_id"] == 25


@pytest.mark.regla
def test_concepto_02() -> None:
    """concepto a una fecha devuelve el que regía ese día (RF-024)."""
    actor = Actor(usua_id=2, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
    historial = [
        {
            "conc_id": 1,
            "lote_id": 1,
            "peri_id": 1,
            "conc_version": 1,
            "conc_resultado": ResultadoConcepto.APTO.value,
            "conc_fecha_efecto": date(2026, 11, 1),
            "norma": "ICA-001",
            "conc_emitido_en": datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
            "conc_motivo": "Cálculo inicial",
            "mtd": Decimal("0.0200"),
        },
        {
            "conc_id": 2,
            "lote_id": 1,
            "peri_id": 1,
            "conc_version": 2,
            "conc_resultado": ResultadoConcepto.NO_APTO.value,
            "conc_fecha_efecto": date(2026, 11, 15),
            "norma": "ICA-001",
            "conc_emitido_en": datetime(2026, 11, 15, 18, 0, tzinfo=UTC),
            "conc_motivo": "Captura sobre umbral",
            "mtd": Decimal("0.1500"),
        },
    ]
    repo = _RepoConceptoFalso(historial=historial)
    svc = ServicioConcepto(repo)  # type: ignore[arg-type]

    en_dia_10 = _ejecutar(svc.concepto_a_fecha(actor, 1, date(2026, 11, 10)))
    assert en_dia_10.concepto is not None
    assert en_dia_10.concepto.conc_resultado == ResultadoConcepto.APTO
    assert en_dia_10.concepto.conc_version == 1

    en_dia_20 = _ejecutar(svc.concepto_a_fecha(actor, 1, date(2026, 11, 20)))
    assert en_dia_20.concepto is not None
    assert en_dia_20.concepto.conc_resultado == ResultadoConcepto.NO_APTO
    assert en_dia_20.concepto.conc_version == 2


@pytest.mark.regla
def test_concepto_03() -> None:
    """sin umbral el concepto es no_emitido."""
    repo = _RepoConceptoFalso(
        contraste=ResultadoContraste.SIN_UMBRAL.value,
        fecha_efecto=date(2026, 11, 10),
    )
    svc = ServicioConcepto(repo)  # type: ignore[arg-type]

    _ejecutar(svc.reemitir(peri_id=10, indi_id=30, motivo="Sin umbral de exportación configurado"))

    assert len(repo.insertados) == 1
    assert repo.insertados[0]["conc_resultado"] == ResultadoConcepto.NO_EMITIDO.value
