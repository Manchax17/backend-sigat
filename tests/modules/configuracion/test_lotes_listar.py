"""Pruebas unitarias de ``ServicioLotes.listar`` con repositorio en memoria.

No requiere base de datos ni ``SIGAT_DB_URL_PRUEBAS`` (usa ``RepoFalso`` y ``cola=None``).

Escenario cubierto:

* Listado completo cuando ``pred_id`` es ``None`` y verificación de los argumentos enviados al repositorio.
* Filtrado por predio (``pred_id``) retornando únicamente los lotes correspondientes.
* Respuesta vacía (``[]``) cuando el actor no tiene lotes visibles.
* Campo ``indicadores`` en ``None`` aunque ``indicadores=True``, mientras no exista fuente definida.
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from sigat_api.modules.configuracion.servicio import ServicioLotes


def _fila(lote_id: int, pred_id: int) -> dict:
    """Construye una fila mínima válida como la que devuelve el repositorio."""
    return {
        "lote_id": lote_id,
        "lote_codigo": f"L{lote_id}",
        "lote_descripcion": "Lote de prueba",
        "lote_area": Decimal("1.5"),
        "pred_id": pred_id,
        "pred_nombre": "Predio",
        "productor": "Productor",
        "municipio": "Municipio",
        "cultivo": None,
        "variedad": None,
        "vigilancia_activa": True,
        "trampas_vigentes": 0,
    }


class RepoFalso:
    """Doble de prueba en memoria para ``RepositorioConfiguracion``."""

    def __init__(self, filas):
        self.filas = filas
        self.llamadas = []

    async def lotes_visibles(self, usua_id, pred_id):
        self.llamadas.append((usua_id, pred_id))
        return [f for f in self.filas if pred_id is None or f["pred_id"] == pred_id]


def _servicio(filas):
    """Instancia ``ServicioLotes`` inyectando un ``RepoFalso`` con las filas dadas."""
    repo = RepoFalso(filas)
    return ServicioLotes(repo, cola=None), repo


ACTOR = SimpleNamespace(usua_id=7)


# ------------------------------------------------------------------ listado y filtros
def test_listar_sin_filtro_devuelve_todos_los_visibles():
    svc, repo = _servicio([_fila(1, 10), _fila(2, 20)])
    lotes = asyncio.run(svc.listar(ACTOR, None, False))
    assert [lote.lote_id for lote in lotes] == [1, 2]
    assert repo.llamadas == [(7, None)]


def test_listar_filtra_por_predio():
    svc, repo = _servicio([_fila(1, 10), _fila(2, 20)])
    lotes = asyncio.run(svc.listar(ACTOR, 20, False))
    assert [lote.lote_id for lote in lotes] == [2]
    assert repo.llamadas == [(7, 20)]


def test_listar_sin_lotes_visibles_devuelve_lista_vacia():
    svc, _ = _servicio([])
    assert asyncio.run(svc.listar(ACTOR, None, False)) == []


# ------------------------------------------------------------------ indicadores
def test_listar_indicadores_quedan_en_null_mientras_no_esten_definidos():
    """PENDIENTE DE REVISIÓN: ``IndicadoresLote`` no tiene fuente en la BD.
    Si se define cómo calcularlos, esta prueba debe cambiar."""
    svc, _ = _servicio([_fila(1, 10)])
    lotes = asyncio.run(svc.listar(ACTOR, None, True))
    assert lotes[0].indicadores is None
