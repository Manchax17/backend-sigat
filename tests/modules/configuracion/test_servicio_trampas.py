"""Servicio de trampas: alta, actualización e historial de ubicaciones.

No toca PostgreSQL: el repositorio se sustituye por un doble que devuelve lo que devolverían las
consultas. Así se prueban las decisiones del servicio (404, 409, 422, RN-004, RN-005).
"""

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from sigat_api.compartido.errores import Conflicto, NoEncontrado, ReglaNegocio
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.servicio import ServicioTrampas

AGRONOMO = Actor(usua_id=2, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)

TRAMPA = {
    "tram_id": 7,
    "tram_codigo": "15001-602-001",
    "tipo": "Jackson",
    "lote_id": 1,
    "pred_id": 1,
    "longitud": Decimal("-73.35950"),
    "latitud": Decimal("5.53050"),
    "celda": None,
    "estado": "activo",
    "atrayente": "Trimedlure",
    "ubic_id": 3,
    "dentro_del_lote": True,
    "ultima_lectura": None,
    "ultima_lectura_especimenes": None,
    "lote_sin_geometria": False,
    "con_ubicacion_vigente": True,
}


UBICACION_VIGENTE = {
    "ubic_id": 4,
    "tram_id": 7,
    "pred_id": 1,
    "lote_id": 2,
    "ubic_fecha": date(2026, 11, 20),
    "ubic_fecha_fin": None,
    "tecn_id": 1,
    "vigi_id": 1,
    "ruta_id": None,
    "retc_id": None,
    "loca_id": None,
    "arbo_id": None,
    "ubic_long": Decimal("-73.35850"),
    "ubic_lat": Decimal("5.53050"),
    "ubic_alt": None,
    "ubic_referencia": None,
    "ubic_origen": "operacion",
    "ubic_registrado_en": datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
}

UBICACION_ANTERIOR = {
    **UBICACION_VIGENTE,
    "ubic_id": 3,
    "lote_id": 1,
    "ubic_fecha": date(2026, 11, 13),
    "ubic_fecha_fin": date(2026, 11, 20),
    "ubic_long": Decimal("-73.35950"),
    "ubic_registrado_en": datetime(2026, 11, 13, 15, 0, tzinfo=UTC),
}


def nueva_trampa(**cambios: Any) -> e.NuevaTrampa:
    datos: dict[str, Any] = {
        "tram_codigo": "15001-602-001",
        "tipt_id": 1,
        "atra_id": 1,
        "ubicacion": {
            "lote_id": 1,
            "ubic_fecha": date(2026, 11, 13),
            "vigi_id": 1,
            "tecn_id": 1,
            "ubic_long": Decimal("-73.35950"),
            "ubic_lat": Decimal("5.53050"),
        },
    }
    datos.update(cambios)
    return e.NuevaTrampa(**datos)


def nueva_ubicacion(**cambios: Any) -> e.NuevaUbicacion:
    datos: dict[str, Any] = {
        "lote_id": 2,
        "ubic_fecha": date(2026, 11, 21),
        "vigi_id": 1,
        "tecn_id": 1,
        "ubic_long": Decimal("-73.35820"),
        "ubic_lat": Decimal("5.53090"),
    }
    datos.update(cambios)
    return e.NuevaUbicacion(**datos)


class RepoFalso:
    """Respuestas de ``RepositorioConfiguracion``; cada prueba ajusta las que necesita."""

    def __init__(self) -> None:
        self.visible = True
        self.pred_id: int | None = 1
        self.lote_activo = True
        self.tipo: dict[str, Any] | None = {
            "tipt_id": 1,
            "tipt_descripcion": "Jackson",
            "tipt_paridad": "impar",
        }
        self.codigo_existe = False
        self.distancia: int | None = 50
        self.cercanas: list[str] = []
        self.trampa: dict[str, Any] | None = deepcopy(TRAMPA)
        self.insertadas: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.consultas_cercania: list[tuple[Any, ...]] = []
        self.actualizadas: list[tuple[int, dict[str, Any]]] = []
        self.historial: list[dict[str, Any]] = [deepcopy(UBICACION_VIGENTE), deepcopy(UBICACION_ANTERIOR)]
        self.reubicadas: list[tuple[int, dict[str, Any]]] = []

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return self.visible

    async def predio_del_lote(self, lote_id: int) -> int | None:
        return self.pred_id

    async def lote_activo_en(self, lote_id: int, fecha: date) -> bool:
        return self.lote_activo

    async def tipo_trampa(self, tipt_id: int) -> dict[str, Any] | None:
        return self.tipo

    async def existe_codigo_trampa(self, tram_codigo: str) -> bool:
        return self.codigo_existe

    async def distancia_minima_trampas(self, pred_id: int, fecha: date) -> int | None:
        return self.distancia

    async def trampas_cercanas(
        self,
        pred_id: int,
        lon: Decimal,
        lat: Decimal,
        fecha: date,
        distancia_m: int,
        excluir_tram_id: int | None = None,
    ) -> list[str]:
        self.consultas_cercania.append((pred_id, lon, lat, fecha, distancia_m, excluir_tram_id))
        return self.cercanas

    async def insertar_trampa(self, datos: dict[str, Any], ubicacion: dict[str, Any]) -> int:
        self.insertadas.append((datos, ubicacion))
        return 7

    async def trampa_por_id(self, usua_id: int, tram_id: int) -> dict[str, Any] | None:
        return self.trampa

    async def actualizar_trampa(self, tram_id: int, cambios: dict[str, Any]) -> None:
        self.actualizadas.append((tram_id, cambios))

    async def ubicaciones(self, tram_id: int, usua_id: int) -> list[dict[str, Any]]:
        """El repositorio real ya filtra por ``fn_lotes_visibles``: no visible = sin filas."""
        return self.historial if self.visible else []

    async def reubicar(self, tram_id: int, nueva: dict[str, Any]) -> int:
        self.reubicadas.append((tram_id, nueva))
        nueva_fila = {
            "ubic_id": 5,
            "tram_id": tram_id,
            "pred_id": nueva["pred_id"],
            "lote_id": nueva["lote_id"],
            "ubic_fecha": nueva["ubic_fecha"],
            "ubic_fecha_fin": None,
            "tecn_id": nueva["tecn_id"],
            "vigi_id": nueva["vigi_id"],
            "ruta_id": nueva.get("ruta_id"),
            "retc_id": nueva.get("retc_id"),
            "loca_id": nueva.get("loca_id"),
            "arbo_id": nueva.get("arbo_id"),
            "ubic_long": nueva["ubic_long"],
            "ubic_lat": nueva["ubic_lat"],
            "ubic_alt": nueva.get("ubic_alt"),
            "ubic_referencia": nueva.get("ubic_referencia"),
            "ubic_origen": "operacion",
            "ubic_registrado_en": datetime(2026, 11, 21, 10, 0, tzinfo=UTC),
        }
        self.historial.insert(0, nueva_fila)
        return 5


def servicio(repo: RepoFalso) -> ServicioTrampas:
    return ServicioTrampas(repo, None)  # type: ignore[arg-type]


# ------------------------------------------------------------------ crear
@pytest.mark.regla
async def test_crear_registra_trampa_y_ubicacion_y_devuelve_el_esquema() -> None:
    repo = RepoFalso()

    trampa = await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert trampa.tram_id == 7
    assert trampa.dentro_del_lote is True
    datos, ubicacion = repo.insertadas[0]
    assert datos["tram_tecnico_registro_id"] == 1
    assert ubicacion["pred_id"] == 1
    assert ubicacion["lote_id"] == 1


@pytest.mark.regla
async def test_crear_en_lote_no_visible_da_404_y_no_inserta() -> None:
    repo = RepoFalso()
    repo.visible = False

    with pytest.raises(NoEncontrado) as error:
        await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert error.value.codigo == "LOTE_NO_ENCONTRADO"
    assert repo.insertadas == []


@pytest.mark.regla
async def test_crear_en_lote_inactivo_en_la_fecha_de_instalacion_da_422() -> None:
    """Tabla ``lote``: activo entre ``lote_fecha_registro`` y ``lote_fecha_baja``."""
    repo = RepoFalso()
    repo.lote_activo = False

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert error.value.codigo == "LOTE_INACTIVO"
    assert repo.insertadas == []


@pytest.mark.regla
async def test_crear_con_tipo_inexistente_da_422() -> None:
    repo = RepoFalso()
    repo.tipo = None

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert error.value.codigo == "TIPO_TRAMPA_INVALIDO"


@pytest.mark.regla
async def test_crear_jackson_con_numero_par_incumple_rn005() -> None:
    repo = RepoFalso()

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva_trampa(tram_codigo="15001-602-002"))

    assert error.value.codigo == "CODIGO_TRAMPA_INVALIDO"
    assert "RN-005" in error.value.detalle
    assert repo.insertadas == []


@pytest.mark.regla
async def test_crear_con_codigo_existente_da_409() -> None:
    repo = RepoFalso()
    repo.codigo_existe = True

    with pytest.raises(Conflicto) as error:
        await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert error.value.codigo == "CODIGO_TRAMPA_DUPLICADO"
    assert repo.insertadas == []


@pytest.mark.regla
async def test_crear_cerca_de_otra_trampa_incumple_rn004() -> None:
    repo = RepoFalso()
    repo.cercanas = ["15001-602-003"]

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert error.value.codigo == "DISTANCIA_MINIMA_TRAMPAS"
    assert "15001-602-003" in error.value.detalle
    assert "50 m" in error.value.detalle
    assert repo.insertadas == []


@pytest.mark.regla
async def test_crear_sin_programa_vigente_no_exige_distancia() -> None:
    repo = RepoFalso()
    repo.distancia = None

    await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert repo.consultas_cercania == []
    assert len(repo.insertadas) == 1


@pytest.mark.regla
async def test_crear_fuera_del_lote_advierte_y_no_bloquea() -> None:
    """RF-012: el punto fuera del lote se informa en ``dentro_del_lote``, no impide el alta."""
    repo = RepoFalso()
    assert repo.trampa is not None
    repo.trampa["dentro_del_lote"] = False

    trampa = await servicio(repo).crear(AGRONOMO, nueva_trampa())

    assert trampa.dentro_del_lote is False
    assert len(repo.insertadas) == 1


# ------------------------------------------------------------------ actualizar
@pytest.mark.regla
async def test_actualizar_trampa_no_visible_da_404_y_no_cambia_nada() -> None:
    repo = RepoFalso()
    repo.trampa = None

    with pytest.raises(NoEncontrado) as error:
        await servicio(repo).actualizar(AGRONOMO, 99, e.ActualizacionTrampa(atra_id=2))

    assert error.value.codigo == "TRAMPA_NO_ENCONTRADA"
    assert repo.actualizadas == []


@pytest.mark.regla
async def test_actualizar_atrayente_aplica_solo_ese_campo() -> None:
    repo = RepoFalso()

    trampa = await servicio(repo).actualizar(AGRONOMO, 7, e.ActualizacionTrampa(atra_id=2))

    assert trampa.tram_id == 7
    assert repo.actualizadas == [(7, {"atra_id": 2})]


@pytest.mark.regla
async def test_cambiar_estado_sin_tecnico_responsable_da_422() -> None:
    repo = RepoFalso()

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).actualizar(AGRONOMO, 7, e.ActualizacionTrampa(esta_id=2))

    assert error.value.codigo == "TECNICO_ESTADO_REQUERIDO"
    assert repo.actualizadas == []


@pytest.mark.regla
async def test_cambiar_estado_con_tecnico_responsable_se_aplica() -> None:
    repo = RepoFalso()

    await servicio(repo).actualizar(AGRONOMO, 7, e.ActualizacionTrampa(esta_id=2, tecn_id_estado=1))

    assert repo.actualizadas == [(7, {"esta_id": 2, "tecn_id_estado": 1})]


@pytest.mark.regla
async def test_atrayente_nulo_explicito_da_422() -> None:
    repo = RepoFalso()

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).actualizar(AGRONOMO, 7, e.ActualizacionTrampa(atra_id=None))

    assert error.value.codigo == "ATRAYENTE_INVALIDO"
    assert repo.actualizadas == []


@pytest.mark.regla
async def test_estado_nulo_explicito_da_422() -> None:
    repo = RepoFalso()

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).actualizar(AGRONOMO, 7, e.ActualizacionTrampa(esta_id=None, tecn_id_estado=1))

    assert error.value.codigo == "ESTADO_INVALIDO"
    assert repo.actualizadas == []


@pytest.mark.regla
async def test_actualizar_sin_cambios_devuelve_la_trampa_sin_escribir() -> None:
    repo = RepoFalso()

    trampa = await servicio(repo).actualizar(AGRONOMO, 7, e.ActualizacionTrampa())

    assert trampa.tram_id == 7
    assert repo.actualizadas == []


# ------------------------------------------------------------------ historial de ubicaciones
@pytest.mark.regla
async def test_historial_de_trampa_no_visible_da_404() -> None:
    repo = RepoFalso()
    repo.visible = False

    with pytest.raises(NoEncontrado) as error:
        await servicio(repo).ubicaciones(AGRONOMO, 99)

    assert error.value.codigo == "TRAMPA_NO_ENCONTRADA"


@pytest.mark.regla
async def test_historial_conserva_la_ubicacion_anterior_cerrada() -> None:
    repo = RepoFalso()

    historial = await servicio(repo).ubicaciones(AGRONOMO, 7)

    assert [u.ubic_id for u in historial] == [4, 3]
    assert historial[0].ubic_fecha_fin is None
    assert historial[1].ubic_fecha_fin == date(2026, 11, 20)
    assert historial[1].lote_id == 1


# ------------------------------------------------------------------ reubicar
@pytest.mark.regla
async def test_reubicar_trampa_no_visible_da_404_sin_escribir() -> None:
    repo = RepoFalso()
    repo.trampa = None

    with pytest.raises(NoEncontrado) as error:
        await servicio(repo).reubicar(AGRONOMO, 99, nueva_ubicacion())

    assert error.value.codigo == "TRAMPA_NO_ENCONTRADA"
    assert repo.reubicadas == []


@pytest.mark.regla
async def test_reubicar_en_lote_no_visible_da_404() -> None:
    repo = RepoFalso()
    repo.visible = False

    with pytest.raises(NoEncontrado) as error:
        await servicio(repo).reubicar(AGRONOMO, 7, nueva_ubicacion())

    assert error.value.codigo == "LOTE_NO_ENCONTRADO"
    assert repo.reubicadas == []


@pytest.mark.regla
async def test_reubicar_en_lote_inactivo_da_422() -> None:
    repo = RepoFalso()
    repo.lote_activo = False

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).reubicar(AGRONOMO, 7, nueva_ubicacion())

    assert error.value.codigo == "LOTE_INACTIVO"
    assert repo.reubicadas == []


@pytest.mark.regla
async def test_reubicar_cerca_de_otra_trampa_da_422_y_excluye_la_propia() -> None:
    repo = RepoFalso()
    repo.cercanas = ["15001-602-003"]

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).reubicar(AGRONOMO, 7, nueva_ubicacion())

    assert error.value.codigo == "DISTANCIA_MINIMA_TRAMPAS"
    assert "15001-602-003" in error.value.detalle
    assert repo.consultas_cercania[0][5] == 7
    assert repo.reubicadas == []


@pytest.mark.regla
async def test_reubicar_valido_devuelve_nueva_ubicacion_y_guarda_pred_id() -> None:
    repo = RepoFalso()

    res = await servicio(repo).reubicar(AGRONOMO, 7, nueva_ubicacion())

    assert res.ubic_id == 5
    assert res.tram_id == 7
    assert res.lote_id == 2
    assert res.pred_id == 1
    assert res.ubic_fecha == date(2026, 11, 21)
    assert res.ubic_fecha_fin is None
    assert len(repo.reubicadas) == 1
    tram_id_recibido, datos_recibidos = repo.reubicadas[0]
    assert tram_id_recibido == 7
    assert datos_recibidos["pred_id"] == 1
    assert datos_recibidos["lote_id"] == 2


@pytest.mark.regla
async def test_reubicar_sin_programa_vigente_no_exige_distancia() -> None:
    repo = RepoFalso()
    repo.distancia = None

    res = await servicio(repo).reubicar(AGRONOMO, 7, nueva_ubicacion())

    assert res.ubic_id == 5
    assert repo.consultas_cercania == []
    assert len(repo.reubicadas) == 1
