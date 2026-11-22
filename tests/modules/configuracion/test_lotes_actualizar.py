"""Pruebas unitarias de ``ServicioLotes.actualizar`` con repositorio en memoria (sin BD)."""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sigat_api.compartido.errores import NoEncontrado, ReglaNegocio
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.servicio import ServicioLotes

ACTOR = SimpleNamespace(usua_id=4)


def _fila(**extra) -> dict:
    """Fila de detalle como la devuelve ``repo.lote`` (v_lote_app + columnas de sigat.lote)."""
    base = {
        "lote_id": 11,
        "lote_codigo": "L11",
        "lote_descripcion": "Lote 1 · Norte",
        "lote_area": Decimal("2"),
        "pred_id": 1,
        "pred_nombre": "Predio Ana",
        "productor": "Ana Productora",
        "municipio": "Muni",
        "cultivo": None,
        "variedad": None,
        "vigilancia_activa": False,
        "trampas_vigentes": 0,
        "espc_id": None,
        "vari_id": None,
        "lote_fecha_plantacion": None,
        "lote_fecha_registro": date(2026, 1, 1),
        "lote_fecha_baja": None,
        "geometria": None,
        "geometria_version": None,
    }
    return {**base, **extra}


class RepoFalso:
    """Imita al repositorio real: ``lote`` devuelve None si el lote está de baja (como v_lote_app)."""

    def __init__(self, fila: dict, visible: bool = True) -> None:
        self.fila = dict(fila)
        self.visible = visible
        self.actualizaciones: list[dict] = []
        self.lecturas = 0

    async def lote_visible(self, usua_id, lote_id):
        return self.visible

    async def lote(self, lote_id):
        self.lecturas += 1
        if self.fila.get("lote_fecha_baja") is not None:
            return None
        return dict(self.fila)

    async def actualizar_lote(self, lote_id, cambios):
        self.actualizaciones.append(dict(cambios))
        # Solo las columnas que existen en la fila de detalle (la auditoría no vuelve en la lectura).
        self.fila.update({k: v for k, v in cambios.items() if k in self.fila})


def _servicio(fila=None, visible=True):
    repo = RepoFalso(fila or _fila(), visible)
    return ServicioLotes(repo, cola=None), repo  # type: ignore[arg-type]


def _actualizar(svc, **campos):
    return asyncio.run(svc.actualizar(ACTOR, 11, e.ActualizacionLote(**campos)))


# ------------------------------------------------------------------ camino feliz
def test_actualiza_solo_los_campos_enviados_y_la_fecha_de_auditoria():
    svc, repo = _servicio()
    detalle = _actualizar(svc, lote_descripcion="Nueva descripción")

    assert detalle.lote_descripcion == "Nueva descripción"
    [cambios] = repo.actualizaciones
    assert set(cambios) == {"lote_descripcion", "lote_fecha_actualizacion"}
    # la columna es ``date`` (no datetime) y no se toca la FK a tecnico
    assert type(cambios["lote_fecha_actualizacion"]) is date
    assert not isinstance(cambios["lote_fecha_actualizacion"], datetime)
    assert "lote_tecnico_actualizacion_id" not in cambios


def test_actualizacion_comun_hace_una_sola_lectura():
    """Sin baja ni cambio de especie/variedad no hay lectura previa (evita consultas redundantes)."""
    svc, repo = _servicio()
    _actualizar(svc, lote_area=Decimal("3.5"))
    assert repo.lecturas == 1


# ------------------------------------------------------------------ acceso y validaciones
def test_lote_no_visible_devuelve_404_y_no_escribe():
    svc, repo = _servicio(visible=False)
    with pytest.raises(NoEncontrado) as exc:
        _actualizar(svc, lote_descripcion="X")
    assert exc.value.codigo == "LOTE_NO_ENCONTRADO"
    assert repo.actualizaciones == []


def test_body_vacio_es_422_y_no_escribe():
    svc, repo = _servicio()
    with pytest.raises(ReglaNegocio) as exc:
        _actualizar(svc)
    assert exc.value.codigo == "ACTUALIZACION_VACIA"
    assert repo.actualizaciones == []


@pytest.mark.parametrize("campo", ["lote_descripcion", "lote_area"])
def test_null_explicito_en_columna_obligatoria_es_422(campo):
    svc, repo = _servicio()
    with pytest.raises(ReglaNegocio) as exc:
        _actualizar(svc, **{campo: None})
    assert exc.value.codigo == "CAMPO_OBLIGATORIO"
    assert repo.actualizaciones == []


@pytest.mark.parametrize("area", [Decimal("0"), Decimal("-1")])
def test_area_no_positiva_la_rechaza_el_esquema(area):
    with pytest.raises(ValidationError):
        e.ActualizacionLote(lote_area=area)


def test_el_productor_no_es_editable():
    """``ActualizacionLote`` no tiene campo de productor: o se rechaza o se ignora, nunca llega al UPDATE."""
    try:
        s = e.ActualizacionLote(productor="Otro")  # type: ignore[call-arg]
    except ValidationError:
        return
    assert s.model_dump(exclude_unset=True) == {}


# ------------------------------------------------------------------ especie / variedad
def test_variedad_sin_especie_es_422():
    svc, repo = _servicio(_fila(espc_id=None, vari_id=None))
    with pytest.raises(ReglaNegocio) as exc:
        _actualizar(svc, vari_id=1)
    assert exc.value.codigo == "VARIEDAD_SIN_ESPECIE"
    assert repo.actualizaciones == []


def test_cambiar_especie_sin_vari_id_cuando_ya_tenia_variedad_es_422():
    svc, repo = _servicio(_fila(espc_id=1, vari_id=1))
    with pytest.raises(ReglaNegocio) as exc:
        _actualizar(svc, espc_id=2)
    assert exc.value.codigo == "VARIEDAD_INCOHERENTE"
    assert repo.actualizaciones == []


def test_cambiar_especie_limpiando_variedad_es_valido():
    svc, _ = _servicio(_fila(espc_id=1, vari_id=1))
    detalle = _actualizar(svc, espc_id=2, vari_id=None)
    assert (detalle.espc_id, detalle.vari_id) == (2, None)


def test_asignar_especie_a_lote_sin_variedad_es_valido():
    svc, _ = _servicio(_fila(espc_id=None, vari_id=None))
    assert _actualizar(svc, espc_id=1).espc_id == 1


# ------------------------------------------------------------------ baja
def test_dar_de_baja_responde_200_con_el_detalle_aunque_la_vista_ya_lo_oculte():
    svc, repo = _servicio()
    detalle = _actualizar(svc, lote_fecha_baja=date(2026, 6, 1))

    assert detalle.lote_id == 11
    assert detalle.lote_fecha_baja == date(2026, 6, 1)
    assert len(repo.actualizaciones) == 1
    assert repo.lecturas == 2  # una previa (para poder responder) y una posterior (devuelve None)
