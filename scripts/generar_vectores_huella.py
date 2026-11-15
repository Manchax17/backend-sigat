"""Genera ``tests/vectores_huella.json`` y su copia idéntica en el móvil.

Uso::

    .venv/bin/python scripts/generar_vectores_huella.py

Si cambia la canonización del backend, este script regenera el archivo y la
prueba del móvil falla hasta actualizar el algoritmo en Dart. La copia del móvil
no se edita a mano.
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from sigat_api.modules.sincronizacion import huella  # noqa: E402

DESTINO_BACKEND = RAIZ / "tests" / "vectores_huella.json"
DESTINO_MOVIL = RAIZ.parent / "movil-frontend-sigat" / "test" / "vectores_huella.json"

VECTORES: list[dict[str, object]] = [
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
                "smalls": 0.000001,
                "tiny": 1e-7,
            },
            "linaje": "inspeccion_oficial",
            "version": 1,
            "registro_id": "0190f5b0-0000-7000-8000-0000000000b2",
            "accion": "crear",
            "entidad": "lectura_trampa",
        },
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
    },
]

NORMALIZACION: list[dict[str, str]] = [
    {"entrada": "2026-03-01T08:15:30.250-05:00", "canonica": "2026-03-01T13:15:30.250Z"},
    {"entrada": "2026-03-03T10:30:15.500+02:00", "canonica": "2026-03-03T08:30:15.500Z"},
    {"entrada": "2026-03-02T09:00:00.000Z", "canonica": "2026-03-02T09:00:00.000Z"},
    {"entrada": "2026-03-02T04:00:00.123456Z", "canonica": "2026-03-02T04:00:00.123Z"},
]


def documento() -> dict[str, object]:
    vectores = [{**vector, "hash": huella.calcular(vector["contenido"])} for vector in VECTORES]
    return {
        "descripcion": (
            "Vectores compartidos de la huella de contenido (R-T4, RN-010). El backend y el móvil "
            "deben producir EXACTAMENTE estos hashes. No editar a mano: se regeneran con "
            "backend-sigat/scripts/generar_vectores_huella.py."
        ),
        "algoritmo": {
            "canonicalizacion": "RFC 8785 (JCS)",
            "hash": "sha256",
            "codificacion": "hex",
        },
        "normalizacion_instante": NORMALIZACION,
        "vectores": vectores,
    }


def main() -> None:
    texto = json.dumps(documento(), ensure_ascii=False, indent=2) + "\n"
    DESTINO_BACKEND.write_text(texto, encoding="utf-8")
    if DESTINO_MOVIL.parent.exists():
        DESTINO_MOVIL.write_text(texto, encoding="utf-8")
    else:  # pragma: no cover - el móvil puede no estar en este árbol de trabajo
        print(f"Aviso: no existe {DESTINO_MOVIL.parent}; solo se escribió el backend.")
    print(f"Vectores escritos en {DESTINO_BACKEND}")


if __name__ == "__main__":
    main()
