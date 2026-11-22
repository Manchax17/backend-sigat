"""Reglas del módulo catálogo (RNF-017).

Este backend aún no implementa ``ServicioCatalogo.reemplazar`` («Guardar catálogo»):
lanza ``NoImplementado``, y ``RepositorioCatalogo.en_uso``/``RepositorioCatalogo.guardar``
también. Las reglas 01-03 viven exactamente en ese camino (o en SQL), así que no hay
lógica Python que probar sin inventarla: quedan marcadas con skip y el motivo completo.
Del módulo solo existe en Python el listado/lectura (``listar``, ``items``,
``departamentos``, ``municipios``), que no cubre estas reglas.
"""

import pytest


@pytest.mark.regla
@pytest.mark.skip(
    reason="ServicioCatalogo.reemplazar lanza NoImplementado: ningún camino Python rechaza "
    "repetidos sin distinguir mayúsculas (la regla vive en el reemplazo, hoy sin implementar)."
)
def test_catalogo_01() -> None:
    """valores repetidos sin distinguir mayúsculas se rechazan."""


@pytest.mark.regla
@pytest.mark.skip(
    reason="Depende de ServicioCatalogo.reemplazar + RepositorioCatalogo.en_uso, ambos NoImplementado: "
    "no existe hoy la lógica que, ante un valor en uso ausente del reemplazo, lo marca activo=false "
    "(retirado) en vez de borrarlo; cuando se implemente, este test debe ejercitar ese camino con un repo doble."
)
def test_catalogo_02() -> None:
    """un valor en uso ausente del reemplazo se retira, no se borra."""


@pytest.mark.regla
@pytest.mark.skip(
    reason="La invalidación de los paquetes de campo sería un efecto de SQL sobre paquete_campo "
    "(Nueva versión vía la tarea de la cola ``generar_paquete_campo``) disparada por reemplazar(), "
    "que lanza NoImplementado. En Python solo existe la constante TAREA_GENERAR_PAQUETE en "
    "infra/cola/cola.py; ningún código la invoca. Efecto de BD, sin lógica Python testeable: requiere "
    "integración (PostgreSQL) o la implementación de reemplazo()."
)
def test_catalogo_03() -> None:
    """guardar un catálogo invalida los paquetes de campo."""
