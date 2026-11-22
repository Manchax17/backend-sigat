"""Reglas del módulo planificacion.

Estado real del módulo: la capa Python es un esqueleto. ``ServicioPlanificacion`` y
``RepositorioPlanificacion`` lanzan ``NoImplementado`` y no llegan nunca a tocar a la BD; las
reglas se aplican hoy en PostgreSQL (disparadores ``fn_planificacion_valida``,
``fn_planificacion_lote_valida``, ``fn_planificacion_lote_resultado`` y las restricciones CHECK;
``bd/sql/04_funciones.sql`` y ``bd/sql/01_esquema_completo.sql``).

Solo la regla del motivo de rechazo tiene hoy un punto de aplicación en Python
(``esquemas.Rechazo``, ``min_length=10``) y es la única testeable unitariamente aquí. Los demás
stubs conservan ``@PENDIENTE``: una prueba con dobles de repositorio solo vería ``NoImplementado``
y no demostraría nada sobre la regla; la honradez es no forzarla (ver informe del trabajo).
"""

import pytest
from pydantic import ValidationError

from sigat_api.modules.planificacion import esquemas as e

PENDIENTE = pytest.mark.skip(reason="Por implementar")


@pytest.mark.regla
@PENDIENTE
def test_planificacion_01() -> None:
    """solo el agrónomo crea (C01: creador = actor)."""


@pytest.mark.regla
@PENDIENTE
def test_planificacion_02() -> None:
    """planificación a productor exige documento u observación >= 10."""


@pytest.mark.regla
@PENDIENTE
def test_planificacion_03() -> None:
    """actividad de técnico exige plan_id_origen ACEPTADA y al menos un lote."""


@pytest.mark.regla
@PENDIENTE
def test_planificacion_04() -> None:
    """solo el productor destinatario responde y solo si pendiente."""


@pytest.mark.regla
def test_planificacion_05() -> None:
    """rechazo exige motivo >= 10 (punto de aplicación hoy en Python: ``esquemas.Rechazo``)."""
    with pytest.raises(ValidationError) as error:
        e.Rechazo(motivo="corto")

    fallos = error.value.errors()
    assert len(fallos) == 1
    assert fallos[0]["loc"] == ("motivo",)
    assert fallos[0]["type"] == "string_too_short"

    # El límite es 10: nueve se rechazan, diez se aceptan.
    with pytest.raises(ValidationError) as error_nueve:
        e.Rechazo(motivo="123456789")
    assert error_nueve.value.errors()[0]["loc"] == ("motivo",)

    justo = e.Rechazo(motivo="1234567890")
    assert justo.motivo == "1234567890"


@pytest.mark.regla
@PENDIENTE
def test_planificacion_06() -> None:
    """no_ejecutada exige justificación; un lote se resuelve una vez."""


@pytest.mark.regla
@PENDIENTE
def test_planificacion_07() -> None:
    """al resolver todos los lotes la actividad pasa a completada."""
