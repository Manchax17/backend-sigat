"""Reglas del módulo configuracion. Cada regla cuenta para la cobertura de RNF-017."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from sigat_api.api.fabricas import servicio_paquete
from sigat_api.compartido.errores import Conflicto
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.config import Configuracion
from sigat_api.infra.cola.cola import TAREA_GENERAR_PAQUETE, ColaProcrastinate
from sigat_api.infra.seguridad.tokens import ServicioTokens
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.dominio import reticula_valida
from sigat_api.modules.configuracion.servicio import ServicioPrograma, ServicioTrampas


class _RepoCodigoExistente:
    """Lote visible, tipo Jackson y el código ya registrado en el catálogo."""

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return True

    async def predio_del_lote(self, lote_id: int) -> int:
        return 1

    async def lote_activo_en(self, lote_id: int, fecha: date) -> bool:
        return True

    async def tipo_trampa(self, tipt_id: int) -> dict[str, Any]:
        return {"tipt_id": 1, "tipt_descripcion": "Jackson", "tipt_paridad": "impar"}

    async def existe_codigo_trampa(self, tram_codigo: str) -> bool:
        return True


@pytest.mark.regla
async def test_configuracion_01() -> None:
    """código de trampa duplicado da 409 'Duplicidad de código de trampa'."""
    actor = Actor(usua_id=2, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
    nueva = e.NuevaTrampa(
        tram_codigo="15001-602-001",
        tipt_id=1,
        atra_id=1,
        ubicacion=e.NuevaUbicacion(
            lote_id=1,
            ubic_fecha=date(2026, 11, 13),
            vigi_id=1,
            tecn_id=1,
            ubic_long=Decimal("-73.35950"),
            ubic_lat=Decimal("5.53050"),
        ),
    )

    with pytest.raises(Conflicto) as error:
        await ServicioTrampas(_RepoCodigoExistente(), None).crear(actor, nueva)  # type: ignore[arg-type]

    assert error.value.estado_http == 409
    assert error.value.codigo == "CODIGO_TRAMPA_DUPLICADO"
    assert "Duplicidad de código de trampa" in error.value.detalle


class _RepoProgramaVersiones:
    """Simula versiones de programa e histórico de periodos calculados (ADR-05)."""

    def __init__(self) -> None:
        self.versiones: list[dict[str, Any]] = [
            {
                "prog_id": 1,
                "pred_id": 1,
                "prog_version": 1,
                "vigente_desde": date(2026, 1, 1),
                "vigente_hasta": None,
                "ciclo_inicio": None,
                "ciclo_fin": None,
                "periodo_lectura_dias": 7,
                "distancia_min_trampas_m": 50,
                "reti_id": None,
                "resolucion_reticula_m": None,
                "proveedor_imagen": None,
                "umbral_exportacion_mtd": Decimal("0.1000"),
                "norma": None,
                "escenarios": [],
                "criterios_bpa": [],
                "motivo_cambio": "Inicial",
                "creado_por": "Admin",
                "creado_en": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ]
        self.periodos_calculados: list[dict[str, Any]] = [
            {
                "peri_id": 100,
                "prog_id": 1,
                "umbral_aplicado": Decimal("0.1000"),
            }
        ]
        self.invalidaciones: list[tuple[int, str]] = []

    async def predio_visible(self, usua_id: int, pred_id: int) -> bool:
        return pred_id == 1

    async def programa_vigente(self, pred_id: int, fecha: date) -> dict[str, Any] | None:
        for v in reversed(self.versiones):
            desde = v["vigente_desde"]
            hasta = v["vigente_hasta"]
            if v["pred_id"] == pred_id and desde <= fecha and (hasta is None or hasta > fecha):
                return dict(v)
        return None

    async def nueva_version_programa(
        self, pred_id: int, datos: dict[str, Any], creado_por: int
    ) -> dict[str, Any]:
        self.versiones[-1]["vigente_hasta"] = datos["vigente_desde"]
        nueva = {
            "prog_id": len(self.versiones) + 1,
            "pred_id": pred_id,
            "prog_version": len(self.versiones) + 1,
            "vigente_desde": datos["vigente_desde"],
            "vigente_hasta": None,
            "ciclo_inicio": datos.get("ciclo_inicio"),
            "ciclo_fin": datos.get("ciclo_fin"),
            "periodo_lectura_dias": datos["periodo_lectura_dias"],
            "distancia_min_trampas_m": datos["distancia_min_trampas_m"],
            "reti_id": datos.get("reti_id"),
            "resolucion_reticula_m": None,
            "proveedor_imagen": datos.get("proveedor_imagen"),
            "umbral_exportacion_mtd": datos.get("umbral_exportacion_mtd"),
            "norma": None,
            "escenarios": [],
            "criterios_bpa": list(datos.get("criterios_bpa", [])),
            "motivo_cambio": datos["motivo_cambio"],
            "creado_por": f"Usuario {creado_por}",
            "creado_en": datetime(2026, 6, 1, tzinfo=UTC),
        }
        self.versiones.append(nueva)
        return dict(nueva)

    async def invalidar_paquetes(self, pred_id: int, motivo: str) -> None:
        self.invalidaciones.append((pred_id, motivo))


def _ejecutar(coro: Any) -> Any:
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value


@pytest.mark.regla
def test_configuracion_02() -> None:
    """nueva versión del programa no altera periodos ya calculados (ADR-05)."""
    actor = Actor(usua_id=2, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
    repo = _RepoProgramaVersiones()
    cola = ColaProcrastinate()
    svc = ServicioPrograma(repo, cola)  # type: ignore[arg-type]

    nueva = _ejecutar(
        svc.nueva_version(
            actor,
            1,
            e.NuevaVersionPrograma(
                vigente_desde=date(2026, 6, 1),
                periodo_lectura_dias=10,
                distancia_min_trampas_m=45,
                umbral_exportacion_mtd=Decimal("0.0500"),
                motivo_cambio="Ajuste normativo semestral",
            ),
        )
    )

    assert nueva.prog_version == 2
    assert nueva.umbral_exportacion_mtd == Decimal("0.0500")
    # El programa anterior quedó cerrado en la fecha de entrada en vigor del nuevo
    prog_en_mayo = _ejecutar(svc.vigente(actor, 1, date(2026, 5, 15)))
    assert prog_en_mayo.prog_version == 1
    assert prog_en_mayo.umbral_exportacion_mtd == Decimal("0.1000")
    # Los periodos ya calculados conservan su umbral original (ADR-05)
    assert repo.periodos_calculados[0]["umbral_aplicado"] == Decimal("0.1000")
    # Y el paquete de campo del predio fue invalidado y encolado
    assert len(repo.invalidaciones) == 1
    assert repo.invalidaciones[0][0] == 1
    assert len(cola.pendientes) == 1
    assert cola.pendientes[0].tarea == TAREA_GENERAR_PAQUETE


@pytest.mark.regla
def test_configuracion_03() -> None:
    """retícula menor o igual a la distancia mínima se rechaza (§5.3)."""
    assert not reticula_valida(60, 60)  # igual a la distancia mínima -> rechazada
    assert not reticula_valida(40, 60)  # menor que la distancia mínima -> rechazada
    assert reticula_valida(80, 60)  # mayor que la distancia mínima -> aceptada


class _RepoTrampaInexistente:
    async def trampa_por_codigo(self, tram_codigo: str) -> dict[str, Any] | None:
        return None


class _RepoTrampaFueraDelLote:
    async def trampa_por_codigo(self, tram_codigo: str) -> dict[str, Any] | None:
        return {
            "tram_id": 1,
            "tram_codigo": "68001-A-001",
            "tipo": "Jackson",
            "lote_id": 1,
            "pred_id": 1,
            "longitud": Decimal("-73.120"),
            "latitud": Decimal("7.120"),
            "celda": None,
            "estado": "activo",
            "atrayente": None,
            "ubic_id": 1,
            "dentro_del_lote": False,
            "lote_sin_geometria": False,
            "con_ubicacion_vigente": True,
            "ultima_lectura": None,
            "ultima_lectura_especimenes": None,
        }


@pytest.mark.regla
async def test_configuracion_04() -> None:
    """búsqueda de trampa inexistente responde en_catalogo=false (no 404)."""
    actor = Actor(usua_id=2, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
    resultado = await ServicioTrampas(_RepoTrampaInexistente(), None).buscar_por_codigo(  # type: ignore[arg-type]
        actor, "99999-Z-999", None, None, None
    )
    assert resultado.en_catalogo is False
    assert resultado.trampa is None
    assert "no está en el catálogo" in resultado.advertencias[0]


@pytest.mark.regla
async def test_configuracion_05() -> None:
    """trampa fuera del lote ADVIERTE y no bloquea (RF-012)."""
    actor = Actor(usua_id=2, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
    resultado = await ServicioTrampas(_RepoTrampaFueraDelLote(), None).buscar_por_codigo(  # type: ignore[arg-type]
        actor, "68001-A-001", 1, None, None
    )
    assert resultado.en_catalogo is True
    assert resultado.trampa is not None
    assert any("fuera del lote" in a for a in resultado.advertencias)


class _ServicioPaqueteVigente:
    """Simula el contrato de ``ServicioPaqueteCampo.contenido``: devuelve ``None`` cuando
    ``If-None-Match`` trae el hash del paquete vigente, y la ruta lo traduce en 304."""

    def __init__(self, hash_vigente: str) -> None:
        self.hash_vigente = hash_vigente
        self.recibido: str | None = None

    async def contenido(self, actor: Actor, pred_id: int, si_no_coincide: str | None) -> Any:
        self.recibido = si_no_coincide
        assert si_no_coincide == f'"{self.hash_vigente}"', "este test cubre solo el hash vigente"
        return None


def _token_acceso(rol: RolCodigo, usua_id: int = 2) -> str:
    """Token JWT firmado válido para autenticar al actor en las rutas."""
    cfg = Configuracion(jwt_secreto=SecretStr(os.environ["SIGAT_JWT_SECRETO"]))
    token, _ = ServicioTokens(cfg).emitir_acceso(usua_id, rol, None, Cliente.WEB)
    return token


@pytest.mark.regla
def test_configuracion_06(cliente: TestClient) -> None:
    """paquete de campo responde 304 con If-None-Match vigente (ADR-09)."""
    hash_vigente = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # pragma: allowlist secret (SHA-256 de cadena vacía, constante pública)
    servicio = _ServicioPaqueteVigente(hash_vigente)
    cliente.app.dependency_overrides[servicio_paquete] = lambda: servicio

    respuesta = cliente.get(
        "/api/v1/predios/1/paquete-campo/contenido",
        headers={
            "Authorization": f"Bearer {_token_acceso(RolCodigo.TECNICO)}",
            "If-None-Match": f'"{hash_vigente}"',
        },
    )

    assert respuesta.status_code == 304  # no modificado: el dispositivo ya tiene el vigente
    assert servicio.recibido == f'"{hash_vigente}"'  # la cabecera llega al servicio sin tocar
