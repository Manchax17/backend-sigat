from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from sigat_api.modules.sincronizacion import esquemas as e
from sigat_api.modules.sincronizacion import huella

VECTORES: list[dict[str, Any]] = [
    {
        "nombre": "lectura simple",
        "contenido": {
            "entidad": "lectura_trampa",
            "accion": "crear",
            "registro_id": "0190f5b0-0000-7000-8000-0000000000b1",
            "version": 1,
            "linaje": "inspeccion_oficial",
            "t_observacion": "2026-03-01T13:15:30.250Z",
            "datos": {
                "visi_id": "0190f5b0-0000-7000-8000-0000000000a0",
                "tram_id": 11,
                "cond_id": 1,
                "lect_leida": True,
                "lect_especimenes": 4,
                "lect_justificacion": None,
            },
        },
        "hash": "43a8396af58e26b1e8f5bd6bbad24f830ab04566cb03da4d7221d28a26849731",  # pragma: allowlist secret
    },
    {
        "nombre": "visita con varios lotes y nulos",
        "contenido": {
            "entidad": "visita",
            "accion": "crear",
            "registro_id": "0190f5b0-0000-7000-8000-0000000000a0",
            "version": 1,
            "linaje": None,
            "t_observacion": "2026-03-02T09:00:00.000Z",
            "datos": {
                "pred_id": 3,
                "lote_ids": [42, 43],
                "ruta_id": None,
                "visi_t_inicio_obs": "2026-03-02T08:40:00.000Z",
                "visi_t_fin_obs": "2026-03-02T09:00:00.000Z",
            },
        },
        "hash": "6f5dcb27b553cba96ebb9e737cbb97920f0aa407565fc55dc64d9a26ca70bb79",  # pragma: allowlist secret
    },
    {
        "nombre": "decimales, unicode y orden de claves",
        "contenido": {
            "t_observacion": "2026-03-03T08:30:15.500Z",
            "datos": {
                "zona": "Trampa «A» ñ",
                "lect_precision_m": 1.0,
                "lect_observaciones": "Borde del lote — revisar",
                "atra_id": 2,
                "lect_leida": False,
                "lect_especimenes": None,
                "lect_justificacion": "Cebo seco",
                "smalls": 1e-06,
                "tiny": 1e-07,
            },
            "linaje": "inspeccion_oficial",
            "version": 1,
            "registro_id": "0190f5b0-0000-7000-8000-0000000000b2",
            "accion": "crear",
            "entidad": "lectura_trampa",
        },
        "hash": "ed5e8f45ca91fa5fda6a0b75bfa5b45e95994101d4652f049b0f6d0d9366fcab",  # pragma: allowlist secret
    },
    {
        "nombre": "corrección de versión 2",
        "contenido": {
            "entidad": "lectura_trampa",
            "accion": "corregir",
            "registro_id": "0190f5b0-0000-7000-8000-0000000000b3",
            "version": 2,
            "linaje": "inspeccion_oficial",
            "t_observacion": "2026-03-04T17:00:00.000Z",
            "datos": {
                "visi_id": "0190f5b0-0000-7000-8000-0000000000a0",
                "tram_id": 12,
                "cond_id": 2,
                "lect_leida": True,
                "lect_especimenes": 0,
                "lect_motivo_correccion": "Se contaron de nuevo",
            },
        },
        "hash": "21321e345e5614768d7e4c48c07eba2cbb1b3f186cf7bc112ac49c6bdf45b49a",  # pragma: allowlist secret
    },
    {
        "nombre": "anulación",
        "contenido": {
            "entidad": "lectura_trampa",
            "accion": "anular",
            "registro_id": "0190f5b0-0000-7000-8000-0000000000b4",
            "version": 3,
            "linaje": "inspeccion_oficial",
            "t_observacion": "2026-03-05T06:45:00.000Z",
            "datos": {
                "visi_id": "0190f5b0-0000-7000-8000-0000000000a0",
                "tram_id": 13,
                "cond_id": 1,
                "lect_leida": True,
                "lect_especimenes": 7,
                "lect_motivo_anulacion": "Trampa mal identificada",
            },
        },
        "hash": "afada6776cce04197e4749d3a58b329ce223370bc3791ef4e332ccce9db7e40f",  # pragma: allowlist secret
    },
]

NORMALIZACION: list[dict[str, str]] = [
    {"entrada": "2026-03-01T08:15:30.250-05:00", "canonica": "2026-03-01T13:15:30.250Z"},
    {"entrada": "2026-03-03T10:30:15.500+02:00", "canonica": "2026-03-03T08:30:15.500Z"},
    {"entrada": "2026-03-02T09:00:00.000Z", "canonica": "2026-03-02T09:00:00.000Z"},
    {"entrada": "2026-03-02T04:00:00.123456Z", "canonica": "2026-03-02T04:00:00.123Z"},
]


@pytest.mark.regla
@pytest.mark.parametrize("vector", VECTORES, ids=lambda v: v["nombre"])
def test_vector_de_huella(vector: dict[str, Any]) -> None:
    assert huella.calcular(vector["contenido"]) == vector["hash"]


@pytest.mark.regla
@pytest.mark.parametrize("vector", NORMALIZACION, ids=lambda v: v["entrada"])
def test_normalizacion_del_instante(vector: dict[str, str]) -> None:
    entrada = datetime.fromisoformat(vector["entrada"].replace("Z", "+00:00"))
    assert huella.instante_canonico(entrada) == vector["canonica"]


def test_instante_sin_zona_se_asume_utc() -> None:
    sin_zona = datetime.fromisoformat("2026-03-02T09:00:00")
    assert huella.instante_canonico(sin_zona) == "2026-03-02T09:00:00.000Z"


def test_la_zona_no_cambia_la_huella() -> None:
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
