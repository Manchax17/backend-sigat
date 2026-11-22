"""Genera ``contracts/openapi.json`` desde el código (Arquitectura §9.1: el contrato lo publica el servidor).

python scripts/exportar_openapi.py              # escribe el archivo
python scripts/exportar_openapi.py --verificar  # falla si el archivo versionado no coincide (CI)
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from sigat_api.main import crear_app  # noqa: E402

DESTINO = RAIZ / "contracts" / "openapi.json"

# CPython 3.13 renombró frases de razón a RFC 9110 (422 pasó de «Unprocessable
# Entity» a «Unprocessable Content», entre otras). FastAPI las toma de
# http.client.responses, así que el contrato generado dependía de la versión del
# intérprete: el CI usa Python 3.12 y el desarrollo puede usar 3.13+. Se escribe
# y se verifica siempre la forma RFC 9110 para que el contrato no cambie por
# cambiar el intérprete (ver CHANGELOG.md, cierre Sprint 2).
_FRASES_RFC9110 = {
    "Request Entity Too Large": "Content Too Large",
    "Request-URI Too Long": "URI Too Long",
    "Requested Range Not Satisfiable": "Range Not Satisfiable",
    "Unprocessable Entity": "Unprocessable Content",
}


def _normalizar_frases(esquema: object) -> object:
    if isinstance(esquema, dict):
        return {clave: _normalizar_frases(valor) for clave, valor in esquema.items()}
    if isinstance(esquema, list):
        return [_normalizar_frases(valor) for valor in esquema]
    if isinstance(esquema, str):
        return _FRASES_RFC9110.get(esquema, esquema)
    return esquema


def generar() -> str:
    return (
        json.dumps(_normalizar_frases(crear_app().openapi()), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def main() -> int:
    contenido = generar()
    if "--verificar" in sys.argv:
        actual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        if actual != contenido:
            print("contracts/openapi.json no coincide con el código. Ejecute: make contrato", file=sys.stderr)
            return 1
        print("Contrato sincronizado.")
        return 0
    DESTINO.write_text(contenido, encoding="utf-8")
    print(f"Escrito {DESTINO.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
