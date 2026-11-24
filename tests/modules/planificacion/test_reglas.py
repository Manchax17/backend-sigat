"""Reglas del módulo planificacion (los disparadores fn_planificacion_* las refuerzan en la BD)."""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from sigat_api.compartido.errores import (
    Conflicto,
    NoEncontrado,
    Prohibido,
    ReglaNegocio,
)
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.modules.planificacion import esquemas as e
from sigat_api.modules.planificacion.servicio import ServicioPlanificacion

AGRONOMO = Actor(usua_id=2, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
ORIGEN = UUID(int=1)


class _Repo:
    """Destinatario del ``tipo`` dado, lotes 1 y 2 asignables y un origen en ``estado_origen``."""

    def __init__(self, tipo: str, estado_origen: str = "aceptada") -> None:
        self.tipo = tipo
        self.estado_origen = estado_origen
        self.insertadas: list[dict[str, Any]] = []

    async def destinatarios(
        self, tipo: str | None, agronomo: int, usua_id: int | None = None
    ) -> list[dict[str, Any]]:
        return [{"usua_id": usua_id, "nombre": "X", "subtitulo": "X", "tipo": self.tipo}]

    async def lotes_asignables(self, agronomo: int, destinatario: int | None) -> list[dict[str, Any]]:
        return [{"lote_id": 1}, {"lote_id": 2}]

    async def obtener(self, plan_id: UUID) -> dict[str, Any] | None:
        return {
            "plan_id": ORIGEN,
            "tipo": "productor",
            "plan_estado": self.estado_origen,
            "plan_fecha_inicio": date(2026, 11, 1),
            "plan_fecha_fin": None,
            "lote_ids": [1, 2],
            "usua_id_creador": AGRONOMO.usua_id,
            "usua_id_destinatario": 4,
        }

    async def insertar(self, fila: dict[str, Any], lote_ids: list[int]) -> UUID:
        self.insertadas.append(fila)
        raise _Insertada


class _Insertada(Exception):
    """Corta el flujo tras insertar: estas reglas no dependen de la respuesta."""


def _nueva(destinatario: int, **cambios: Any) -> e.NuevaPlanificacion:
    return e.NuevaPlanificacion(
        usua_id_destinatario=destinatario,
        plan_actividad="Actividad",
        plan_fecha_inicio=date(2026, 11, 20),
        **cambios,
    )


async def _crear(repo: _Repo, actor: Actor, s: e.NuevaPlanificacion) -> None:
    with pytest.raises(_Insertada):
        await ServicioPlanificacion(repo).crear(actor, s)  # type: ignore[arg-type]


@pytest.mark.regla
async def test_planificacion_01() -> None:
    """solo el agrónomo crea (C01: creador = actor)."""
    for rol in (RolCodigo.TECNICO, RolCodigo.PRODUCTOR, RolCodigo.ADMINISTRADOR):
        otro = Actor(usua_id=9, rol=rol, disp_id=None, cliente=Cliente.WEB)
        svc = ServicioPlanificacion(_Repo("productor"))  # type: ignore[arg-type]
        with pytest.raises(Prohibido):
            await svc.crear(otro, _nueva(4, plan_observaciones="Observación válida"))

    repo = _Repo("productor")
    await _crear(repo, AGRONOMO, _nueva(4, plan_observaciones="Observación válida"))
    assert repo.insertadas[0]["usua_id_creador"] == AGRONOMO.usua_id


@pytest.mark.regla
async def test_planificacion_02() -> None:
    """planificación a productor exige documento u observación >= 10."""
    svc = ServicioPlanificacion(_Repo("productor"))  # type: ignore[arg-type]
    with pytest.raises(ReglaNegocio) as error:
        await svc.crear(AGRONOMO, _nueva(4, plan_observaciones="123456789"))
    assert error.value.codigo == "DOCUMENTO_U_OBSERVACION_REQUERIDO"

    await _crear(_Repo("productor"), AGRONOMO, _nueva(4, plan_observaciones="1234567890"))
    await _crear(_Repo("productor"), AGRONOMO, _nueva(4, arch_id_documento=UUID(int=7)))


@pytest.mark.regla
async def test_planificacion_03() -> None:
    """actividad de técnico exige plan_id_origen ACEPTADA y al menos un lote."""
    casos = [
        (_Repo("tecnico"), _nueva(3, plan_id_origen=ORIGEN), "LOTES_REQUERIDOS"),
        (_Repo("tecnico"), _nueva(3, lote_ids=[1]), "ORIGEN_REQUERIDO"),
        (_Repo("tecnico", "pendiente"), _nueva(3, plan_id_origen=ORIGEN, lote_ids=[1]), "ORIGEN_NO_ACEPTADO"),
    ]
    for repo, s, codigo in casos:
        with pytest.raises(ReglaNegocio) as error:
            await ServicioPlanificacion(repo).crear(AGRONOMO, s)  # type: ignore[arg-type]
        assert error.value.codigo == codigo

    await _crear(_Repo("tecnico"), AGRONOMO, _nueva(3, plan_id_origen=ORIGEN, lote_ids=[1]))


class _RepoRespuesta:
    def __init__(self, estado: str = "pendiente", tipo: str = "productor", destinatario: int = 4) -> None:
        self.estado = estado
        self.tipo = tipo
        self.destinatario = destinatario
        self.observacion: str | None = None

    async def obtener(self, plan_id: UUID) -> dict[str, Any] | None:
        return {
            "plan_id": plan_id,
            "tipo": self.tipo,
            "plan_actividad": "Actividad",
            "plan_fecha_inicio": date(2026, 11, 20),
            "plan_fecha_fin": None,
            "plan_hora_desde": None,
            "plan_hora_hasta": None,
            "plan_estado": self.estado,
            "plan_observaciones": None,
            "plan_observacion_respuesta": self.observacion,
            "plan_creada_en": datetime(2026, 11, 16, 15, 0, tzinfo=UTC),
            "plan_respondida_en": None,
            "usua_id_destinatario": self.destinatario,
            "destinatario": "Productor",
            "rol_destinatario": "Productor" if self.tipo == "productor" else "Técnico",
            "usua_id_creador": AGRONOMO.usua_id,
            "creador": "Agrónomo",
            "arch_id_documento": None,
            "documento": None,
            "lotes": ["L01"],
            "lote_ids": [1],
            "plan_id_origen": None,
            "usua_id_respuesta": None,
            "respondio": None,
            "lotes_pendientes": 1,
            "lotes_ejecutados": 0,
            "lotes_no_ejecutados": 0,
        }

    async def resultados_lote(self, plan_id: UUID) -> list[dict[str, Any]]:
        return []

    async def responder(self, plan_id: UUID, estado: str, observacion: str | None) -> int:
        if self.estado != "pendiente":
            return 0
        self.estado = estado
        self.observacion = observacion
        return 1


class _RepoLotes:
    def __init__(self, estado_plan: str = "pendiente", estados_lotes: dict[int, str] | None = None) -> None:
        self.estado_plan = estado_plan
        self.estados_lotes = estados_lotes or {1: "pendiente", 2: "pendiente"}
        self.observaciones: dict[int, str | None] = {}
        self.visitas: dict[int, UUID | None] = {}

    async def obtener(self, plan_id: UUID) -> dict[str, Any] | None:
        return {
            "plan_id": plan_id,
            "tipo": "tecnico",
            "plan_actividad": "Actividad",
            "plan_fecha_inicio": date(2026, 11, 20),
            "plan_fecha_fin": None,
            "plan_hora_desde": None,
            "plan_hora_hasta": None,
            "plan_estado": self.estado_plan,
            "plan_observaciones": None,
            "plan_observacion_respuesta": None,
            "plan_creada_en": datetime(2026, 11, 16, 15, 0, tzinfo=UTC),
            "plan_respondida_en": None,
            "usua_id_destinatario": 3,
            "destinatario": "Técnico",
            "rol_destinatario": "Técnico",
            "usua_id_creador": AGRONOMO.usua_id,
            "creador": "Agrónomo",
            "arch_id_documento": None,
            "documento": None,
            "lotes": [f"L0{k}" for k in self.estados_lotes],
            "lote_ids": list(self.estados_lotes.keys()),
            "plan_id_origen": ORIGEN,
            "usua_id_respuesta": None,
            "respondio": None,
            "lotes_pendientes": sum(1 for v in self.estados_lotes.values() if v == "pendiente"),
            "lotes_ejecutados": sum(1 for v in self.estados_lotes.values() if v == "ejecutada"),
            "lotes_no_ejecutados": sum(1 for v in self.estados_lotes.values() if v == "no_ejecutada"),
        }

    async def resultados_lote(self, plan_id: UUID) -> list[dict[str, Any]]:
        return [
            {
                "lote_id": lid,
                "lote_codigo": f"L0{lid}",
                "plal_sync_id": UUID(int=lid),
                "plal_estado": est,
                "plal_observacion": self.observaciones.get(lid),
                "plal_resuelta_en": None,
                "visi_id": self.visitas.get(lid),
                "evidencias": 0,
            }
            for lid, est in self.estados_lotes.items()
        ]

    async def registrar_resultado_lote(
        self,
        plan_id: UUID,
        lote_id: int,
        estado: str,
        observacion: str | None,
        visi_id: UUID | None,
        oper_id: int | None,
    ) -> bool:
        if self.estados_lotes.get(lote_id) != "pendiente":
            return False
        self.estados_lotes[lote_id] = estado
        self.observaciones[lote_id] = observacion
        self.visitas[lote_id] = visi_id
        if all(v != "pendiente" for v in self.estados_lotes.values()):
            self.estado_plan = "completada"
            return True
        return False


@pytest.mark.regla
async def test_planificacion_04() -> None:
    """solo el productor destinatario responde y solo si pendiente."""
    productor = Actor(usua_id=4, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)
    otro = Actor(usua_id=9, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)
    plan_id = UUID(int=10)

    svc_otro = ServicioPlanificacion(_RepoRespuesta())  # type: ignore[arg-type]
    with pytest.raises(NoEncontrado) as err_dest:
        await svc_otro.aceptar(otro, plan_id, e.Aceptacion())
    assert err_dest.value.codigo == "PLANIFICACION_NO_ENCONTRADA"

    svc_tec = ServicioPlanificacion(_RepoRespuesta(tipo="tecnico", destinatario=3))  # type: ignore[arg-type]
    tec = Actor(usua_id=3, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
    with pytest.raises(ReglaNegocio) as err_tec:
        await svc_tec.aceptar(tec, plan_id, e.Aceptacion())
    assert err_tec.value.codigo == "TECNICO_NO_RESPONDE"

    for estado_previo in ("aceptada", "rechazada"):
        svc_resp = ServicioPlanificacion(_RepoRespuesta(estado=estado_previo))  # type: ignore[arg-type]
        with pytest.raises(Conflicto) as err_resp:
            await svc_resp.aceptar(productor, plan_id, e.Aceptacion())
        assert err_resp.value.codigo == "PLANIFICACION_YA_RESPONDIDA"

    repo_ok = _RepoRespuesta()
    detalle_ok = await ServicioPlanificacion(repo_ok).aceptar(  # type: ignore[arg-type]
        productor, plan_id, e.Aceptacion(observacion="De acuerdo")
    )
    assert detalle_ok.plan_estado == "aceptada"

    repo_rech = _RepoRespuesta()
    detalle_rech = await ServicioPlanificacion(repo_rech).rechazar(  # type: ignore[arg-type]
        productor, plan_id, e.Rechazo(motivo="No es posible fumigar esta semana")
    )
    assert detalle_rech.plan_estado == "rechazada"


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
async def test_planificacion_06() -> None:
    """no_ejecutada exige justificación; un lote se resuelve una vez."""
    tecnico = Actor(usua_id=3, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
    plan_id = UUID(int=20)

    svc = ServicioPlanificacion(_RepoLotes())  # type: ignore[arg-type]
    with pytest.raises(ReglaNegocio) as err_just:
        await svc.registrar_resultado_lote(
            tecnico, plan_id, 1, e.EntradaResultadoLote(plal_estado="no_ejecutada", plal_observacion="  ")
        )
    assert err_just.value.codigo == "JUSTIFICACION_REQUERIDA"

    with pytest.raises(ReglaNegocio) as err_vis:
        await svc.registrar_resultado_lote(
            tecnico,
            plan_id,
            1,
            e.EntradaResultadoLote(
                plal_estado="no_ejecutada",
                plal_observacion="Lluvia intensa",
                visi_id=UUID(int=99),
            ),
        )
    assert err_vis.value.codigo == "VISITA_NO_PERMITIDA"

    svc_resuelto = ServicioPlanificacion(_RepoLotes(estados_lotes={1: "ejecutada", 2: "pendiente"}))  # type: ignore[arg-type]
    with pytest.raises(Conflicto) as err_repetido:
        await svc_resuelto.registrar_resultado_lote(
            tecnico, plan_id, 1, e.EntradaResultadoLote(plal_estado="ejecutada")
        )
    assert err_repetido.value.codigo == "LOTE_YA_RESUELTO"


@pytest.mark.regla
async def test_planificacion_07() -> None:
    """al resolver todos los lotes la actividad pasa a completada."""
    tecnico = Actor(usua_id=3, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
    plan_id = UUID(int=30)
    repo = _RepoLotes(estados_lotes={1: "pendiente", 2: "pendiente"})
    svc = ServicioPlanificacion(repo)  # type: ignore[arg-type]

    r1 = await svc.registrar_resultado_lote(
        tecnico, plan_id, 1, e.EntradaResultadoLote(plal_estado="ejecutada")
    )
    assert r1.plan_estado == "pendiente"
    assert repo.estados_lotes[1] == "ejecutada"
    assert repo.estado_plan == "pendiente"

    r2 = await svc.registrar_resultado_lote(
        tecnico,
        plan_id,
        2,
        e.EntradaResultadoLote(plal_estado="no_ejecutada", plal_observacion="Predio inundado"),
    )
    assert r2.plan_estado == "completada"
    assert repo.estados_lotes[2] == "no_ejecutada"
    assert repo.estado_plan == "completada"
