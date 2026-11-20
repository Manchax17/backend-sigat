"""Reglas del módulo concepto (RNF-017).

``concepto_a_fecha`` (RF-024) ya está implementado (issue #9): su regla se prueba aquí sobre el
servicio y, la semántica SQL «lo que se sabía al final de ese día», contra la base real en
``tests/integracion/test_conceptos_bd.py``.

``reemitir`` sigue siendo esqueleto (la reemisión disparada por sincronización está fuera del
alcance del issue #9): las reglas 01 y 03 viven en ese código que aún no existe, así que se dejan
con skip y motivo; NO se inventa la lógica.
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from sigat_api.compartido.tipos import Actor, Cliente, ResultadoConcepto, RolCodigo
from sigat_api.modules.concepto.servicio import ServicioConcepto


class _RepoUnConcepto:
    def __init__(self, fila: dict[str, Any]) -> None:
        self._fila = fila
        self.fecha: date | None = None

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return True

    async def concepto_a_fecha(self, lote_id: int, fecha: date, zona: str) -> dict[str, Any]:
        self.fecha = fecha
        return self._fila


@pytest.mark.regla
@pytest.mark.skip(
    reason="ServicioConcepto.reemitir lanza NoImplementado: ningún código Python asigna hoy "
    "conc_fecha_efecto = t_observacion del registro disparador (la regla está solo en el docstring)."
)
def test_concepto_01() -> None:
    """reemisión usa fecha de efecto = t_observacion del registro disparador."""


@pytest.mark.regla
async def test_concepto_02() -> None:
    """concepto a una fecha devuelve el que regía ese día (RF-024)."""
    regia = {
        "conc_id": 1,
        "lote_id": 11,
        "peri_id": 1,
        "conc_version": 1,
        "conc_resultado": "apto",
        "conc_fecha_efecto": date(2026, 11, 5),
        "norma": None,
        "conc_emitido_en": datetime(2026, 11, 5, 15, 0, tzinfo=UTC),
        "conc_motivo": None,
        "mtd": None,
    }
    repo = _RepoUnConcepto(regia)
    r = await ServicioConcepto(repo, "America/Bogota").concepto_a_fecha(  # type: ignore[arg-type]
        Actor(usua_id=1, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB),
        11,
        date(2026, 11, 6),
    )
    # Se pregunta por ESE día (no por hoy) y se responde con lo que regía entonces.
    assert repo.fecha == date(2026, 11, 6)
    assert r.fecha_consulta == date(2026, 11, 6)
    assert r.concepto is not None
    assert r.concepto.conc_resultado == ResultadoConcepto.APTO
    assert r.explicacion.startswith("Apto")


@pytest.mark.regla
@pytest.mark.skip(
    reason="La derivación «sin umbral -> no_emitido» vive en reemitir(), hoy NoImplementado. Solo existen "
    "los vocabularios ResultadoContraste.SIN_UMBRAL / ResultadoConcepto.NO_EMITIDO; probarlos no probaría "
    "la regla."
)
def test_concepto_03() -> None:
    """sin umbral el concepto es no_emitido."""
