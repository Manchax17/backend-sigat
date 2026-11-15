"""Contrato de la huella de contenido (R-T4, RN-010).

Los vectores son compartidos con el móvil y deben ser idénticos byte a byte.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from sigat_api.modules.sincronizacion import esquemas as e
from sigat_api.modules.sincronizacion import huella

VECTORES = Path(__file__).resolve().parents[2] / "vectores_huella.json"


def _documento() -> dict[str, Any]:
    return json.loads(VECTORES.read_text(encoding="utf-8"))


@pytest.mark.regla
@pytest.mark.parametrize("vector", _documento()["vectores"], ids=lambda v: v["nombre"])
def test_vector_de_huella(vector: dict[str, Any]) -> None:
    """Mismo contenido canónico -> mismo hash, con el orden de claves de entrada irrelevante."""
    assert huella.calcular(vector["contenido"]) == vector["hash"]


@pytest.mark.regla
@pytest.mark.parametrize("vector", _documento()["normalizacion_instante"], ids=lambda v: v["entrada"])
def test_normalizacion_del_instante(vector: dict[str, str]) -> None:
    """El instante se canoniza a UTC con milisegundos y sufijo ``Z`` (Python y Dart coinciden)."""
    entrada = datetime.fromisoformat(vector["entrada"].replace("Z", "+00:00"))
    assert huella.instante_canonico(entrada) == vector["canonica"]


def test_instante_sin_zona_se_asume_utc() -> None:
    sin_zona = datetime.fromisoformat("2026-03-02T09:00:00")  # naive: el contrato asume UTC
    assert huella.instante_canonico(sin_zona) == "2026-03-02T09:00:00.000Z"


def test_la_zona_no_cambia_la_huella() -> None:
    """El mismo instante con distinta zona produce el mismo hash (decisión del Sprint 2)."""
    bogota = timezone(timedelta(hours=-5))
    visita = {
        "entidad": "lectura_trampa",
        "accion": "crear",
        "registro_id": str(UUID("0190f5b0-0000-7000-8000-0000000000b1")),
        "version": 1,
        "linaje": "inspeccion_oficial",
        "t_observacion": huella.instante_canonico(datetime(2026, 3, 1, 8, 15, 30, 250000, tzinfo=bogota)),
        "datos": {"tram_id": 11},
    }
    utc = {**visita, "t_observacion": "2026-03-01T13:15:30.250Z"}

    assert huella.calcular(visita) == huella.calcular(utc)


def test_contenido_canonico_excluye_los_campos_de_reenvio() -> None:
    """``cliente_id``, ``t_captura`` y la deriva no entran en la huella."""
    base: dict[str, Any] = {
        "cliente_id": UUID("0190f5b0-0000-7000-8000-0000000000c1"),
        "entidad": "lectura_trampa",
        "accion": "crear",
        "registro_id": UUID("0190f5b0-0000-7000-8000-0000000000b1"),
        "version": 1,
        "linaje": "inspeccion_oficial",
        "t_observacion": datetime(2026, 3, 1, 13, 15, 30, 250000, tzinfo=UTC),
        "t_captura": datetime(2026, 3, 1, 13, 16, 0, tzinfo=UTC),
        "deriva_reloj_seg": 0,
        "confirma_deriva_reloj": False,
        "hash_contenido": "0" * 64,
        "datos": {"tram_id": 11},
    }
    primera = e.OperacionSync(**base)
    reenvio = e.OperacionSync(
        **{
            **base,
            "cliente_id": UUID("0190f5b0-0000-7000-8000-0000000000c2"),
            "t_captura": datetime(2026, 3, 1, 14, 0, 0, tzinfo=UTC),
            "deriva_reloj_seg": 600,
            "confirma_deriva_reloj": True,
        }
    )

    canonico = huella.contenido_canonico(primera)
    assert set(canonico) == {
        "entidad",
        "accion",
        "registro_id",
        "version",
        "linaje",
        "t_observacion",
        "datos",
    }
    assert huella.calcular(canonico) == huella.calcular(huella.contenido_canonico(reenvio))
