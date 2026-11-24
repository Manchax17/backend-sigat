"""Pruebas de ServicioConsultaRegistro (visitas e historial del técnico)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from sigat_api.compartido.errores import NoEncontrado
from sigat_api.compartido.tipos import (
    Actor,
    Cliente,
    EstadoRevision,
    EstadoRevisionLectura,
    Linaje,
    RolCodigo,
)
from sigat_api.modules.registro.servicio import ServicioConsultaRegistro


def _ejecutar(coro: Any) -> Any:
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


ACTOR_TECNICO_1 = Actor(usua_id=10, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
ACTOR_TECNICO_2 = Actor(usua_id=20, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)


class RepoFalsoRegistro:
    def __init__(self) -> None:
        self.visitas_data: list[dict[str, Any]] = [
            {
                "visi_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "usua_id": 10,
                "tecnico": "Técnico 10",
                "pred_id": 1,
                "pred_nombre": "Finca San José",
                "ruta_id": None,
                "t_inicio_obs": datetime(2026, 11, 10, 8, 0, tzinfo=UTC),
                "t_fin_obs": datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
                "t_captura": datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
                "t_sincronizacion": datetime(2026, 11, 10, 10, 0, tzinfo=UTC),
                "deriva_reloj_seg": 0,
                "estado_revision": EstadoRevision.PENDIENTE,
                "motivo_revision": None,
                "revisada_por": None,
                "revisada_en": None,
                "lote_ids": [1],
                "lotes": ["01"],
                "trampas_leidas": 2,
            }
        ]
        self.lecturas_data: list[dict[str, Any]] = [
            {
                "lect_id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "lect_registro_id": UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                "lect_version": 1,
                "visi_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "tram_id": 1,
                "tram_codigo": "54518-A-001",
                "lote_id": 1,
                "ubic_id": 1,
                "lect_leida": True,
                "lect_especimenes": 0,
                "lect_justificacion": None,
                "cond_id": 1,
                "condicion": "Operativa",
                "lect_fecha_cambio_cuerpo": None,
                "lect_fecha_cambio_atrayente": None,
                "atra_id": 1,
                "lect_observaciones": None,
                "latitud": None,
                "longitud": None,
                "lect_precision_m": None,
                "lect_coincide_lote": True,
                "linaje": Linaje.INSPECCION_OFICIAL,
                "usua_id": 10,
                "autor": "Técnico 10",
                "t_observacion": datetime(2026, 11, 10, 8, 30, tzinfo=UTC),
                "t_captura": datetime(2026, 11, 10, 8, 30, tzinfo=UTC),
                "t_sincronizacion": datetime(2026, 11, 10, 10, 0, tzinfo=UTC),
                "deriva_reloj_seg": 0,
                "anulada": False,
                "motivo_anulacion": None,
                "motivo_correccion": None,
                "especimenes_originales": 0,
                "estado_revision": EstadoRevisionLectura.SIN_VERIFICAR,
                "evidencias": 0,
            }
        ]

    async def visitas(
        self,
        usua_id: int,
        solo_propias: bool,
        filtros: dict[str, Any],
        cursor: Any,
        limite: int,
    ) -> list[dict[str, Any]]:
        if solo_propias:
            items = [v for v in self.visitas_data if v["usua_id"] == usua_id]
        else:
            items = list(self.visitas_data)
        return items

    async def visita(
        self,
        usua_id: int,
        solo_propias: bool,
        visi_id: UUID,
    ) -> dict[str, Any] | None:
        for v in self.visitas_data:
            if v["visi_id"] == visi_id:
                if solo_propias and v["usua_id"] != usua_id:
                    return None
                return v
        return None

    async def lecturas_de_visita(self, visi_id: UUID) -> list[dict[str, Any]]:
        return [row for row in self.lecturas_data if row["visi_id"] == visi_id]


@pytest.mark.regla
def test_visitas_tecnico_solo_ve_sus_visitas() -> None:
    repo = RepoFalsoRegistro()
    svc = ServicioConsultaRegistro(repo)  # type: ignore[arg-type]

    # Técnico 10 ve su visita
    pagina10 = _ejecutar(svc.visitas(ACTOR_TECNICO_1, pred_id=None, desde=None, hasta=None, cursor=None))
    assert len(pagina10.items) == 1
    assert pagina10.items[0].tecnico == "Técnico 10"

    # Técnico 20 no ve visitas ajenas
    pagina20 = _ejecutar(svc.visitas(ACTOR_TECNICO_2, pred_id=None, desde=None, hasta=None, cursor=None))
    assert len(pagina20.items) == 0


@pytest.mark.regla
def test_visita_detalle_con_lecturas() -> None:
    repo = RepoFalsoRegistro()
    svc = ServicioConsultaRegistro(repo)  # type: ignore[arg-type]
    visi_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    detalle = _ejecutar(svc.visita(ACTOR_TECNICO_1, visi_id))
    assert detalle.visi_id == visi_id
    assert len(detalle.lecturas) == 1
    assert detalle.lecturas[0].tram_codigo == "54518-A-001"


@pytest.mark.regla
def test_visita_detalle_otro_tecnico_da_404() -> None:
    repo = RepoFalsoRegistro()
    svc = ServicioConsultaRegistro(repo)  # type: ignore[arg-type]
    visi_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    with pytest.raises(NoEncontrado) as exc:
        _ejecutar(svc.visita(ACTOR_TECNICO_2, visi_id))
    assert exc.value.codigo == "VISITA_NO_ENCONTRADA"
