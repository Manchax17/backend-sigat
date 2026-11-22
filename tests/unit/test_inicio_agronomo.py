"""Pruebas unitarias para el endpoint GET /api/v1/yo/inicio con rol AGRONOMO_SUPERVISOR."""

import os
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from pydantic import SecretStr

from sigat_api.api.fabricas import (
    servicio_lotes,
    servicio_notificaciones,
    servicio_perfil,
    servicio_planificacion,
    servicio_revision,
)
from sigat_api.compartido.tipos import Cliente, RolCodigo
from sigat_api.config import Configuracion
from sigat_api.infra.seguridad.tokens import ServicioTokens
from sigat_api.modules.identidad import esquemas as ei


def _generar_token(rol: RolCodigo, usua_id: int = 1) -> str:
    """Genera un token JWT firmado válido para las pruebas unitarias."""
    cfg = Configuracion(jwt_secreto=SecretStr(os.environ["SIGAT_JWT_SECRETO"]))
    token, _ = ServicioTokens(cfg).emitir_acceso(usua_id, rol, None, Cliente.WEB)
    return token


# --- 1. Resumen de inicio ---
def test_inicio_agronomo_devuelve_indicadores(cliente: TestClient) -> None:
    mock_notis = AsyncMock()
    mock_notis.conteo.return_value.no_leidas = 2

    mock_revision = AsyncMock()
    mock_revision.contar_por_revisar.return_value = 5

    mock_planif = AsyncMock()
    mock_planif.contar_pendientes.return_value = 3
    mock_planif.contar_en_curso.return_value = 1

    mock_lotes = AsyncMock()
    mock_lotes.contar_sobre_umbral.return_value = 4

    cliente.app.dependency_overrides[servicio_notificaciones] = lambda: mock_notis
    cliente.app.dependency_overrides[servicio_revision] = lambda: mock_revision
    cliente.app.dependency_overrides[servicio_planificacion] = lambda: mock_planif
    cliente.app.dependency_overrides[servicio_lotes] = lambda: mock_lotes

    token = _generar_token(RolCodigo.AGRONOMO_SUPERVISOR, usua_id=10)

    respuesta = cliente.get("/api/v1/yo/inicio", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200
    datos = respuesta.json()

    assert datos["rol"] == "AGRONOMO_SUPERVISOR"
    assert datos["notificaciones_no_leidas"] == 2
    assert datos["agronomo"] == {
        "registros_por_revisar": 5,
        "planificaciones_pendientes": 3,
        "actividades_en_curso": 1,
        "lotes_sobre_umbral": 4,
    }
    assert datos["tecnico"] is None
    assert datos["productor"] is None


def test_inicio_otro_rol_no_incluye_bloque_agronomo(cliente: TestClient) -> None:
    mock_notis = AsyncMock()
    mock_notis.conteo.return_value.no_leidas = 0

    mock_revision = AsyncMock()
    mock_planif = AsyncMock()
    mock_lotes = AsyncMock()

    cliente.app.dependency_overrides[servicio_notificaciones] = lambda: mock_notis
    cliente.app.dependency_overrides[servicio_revision] = lambda: mock_revision
    cliente.app.dependency_overrides[servicio_planificacion] = lambda: mock_planif
    cliente.app.dependency_overrides[servicio_lotes] = lambda: mock_lotes

    token = _generar_token(RolCodigo.ADMINISTRADOR, usua_id=1)

    respuesta = cliente.get("/api/v1/yo/inicio", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["rol"] == "ADMINISTRADOR"
    assert datos["agronomo"] is None

    mock_revision.contar_por_revisar.assert_not_called()
    mock_planif.contar_pendientes.assert_not_called()
    mock_planif.contar_en_curso.assert_not_called()
    mock_lotes.contar_sobre_umbral.assert_not_called()


def test_inicio_sin_token_da_401(cliente: TestClient) -> None:
    respuesta = cliente.get("/api/v1/yo/inicio")
    assert respuesta.status_code == 401
    assert respuesta.headers["content-type"].startswith("application/problem+json")


# --- 2. Contexto del usuario (Perfil y Predios) ---
def test_perfil_corresponde_al_usuario_autenticado(cliente: TestClient) -> None:
    mock_perfil = AsyncMock()
    mock_perfil.perfil.return_value = ei.PerfilPropio(
        usua_id=10,
        nombre="Agrónomo Supervisor",
        usuario="agronomo.test",
        correo="agronomo@sigat.gov.co",
        telefono="3101234567",
        rol=RolCodigo.AGRONOMO_SUPERVISOR,
        rol_descripcion="Agrónomo supervisor fitosanitario",
        foto_arch_id=None,
        foto_url=None,
        actualizado_en=None,
    )
    cliente.app.dependency_overrides[servicio_perfil] = lambda: mock_perfil

    token = _generar_token(RolCodigo.AGRONOMO_SUPERVISOR, usua_id=10)
    respuesta = cliente.get("/api/v1/yo/perfil", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["usua_id"] == 10
    assert datos["rol"] == "AGRONOMO_SUPERVISOR"
    assert datos["usuario"] == "agronomo.test"


def test_predios_aplica_reglas_de_acceso(cliente: TestClient) -> None:
    mock_perfil = AsyncMock()
    mock_perfil.predios.return_value = [
        ei.PredioAccesible(pred_id=1, pred_nombre="Finca El Durazno", origen="asignacion")
    ]
    cliente.app.dependency_overrides[servicio_perfil] = lambda: mock_perfil

    token = _generar_token(RolCodigo.AGRONOMO_SUPERVISOR, usua_id=10)
    respuesta = cliente.get("/api/v1/yo/predios", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert len(datos) == 1
    assert datos[0]["pred_id"] == 1
    assert datos[0]["pred_nombre"] == "Finca El Durazno"
