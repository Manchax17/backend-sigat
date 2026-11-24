"""Servicio de planificación: consultas, creación, destinatarios y lotes asignables (sin BD)."""

from copy import deepcopy
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

import pytest

from sigat_api.compartido.errores import (
    Conflicto,
    NoEncontrado,
    Prohibido,
    ReglaNegocio,
)
from sigat_api.compartido.tipos import Actor, Cliente, EstadoPlanificacion, RolCodigo
from sigat_api.modules.planificacion import esquemas as e
from sigat_api.modules.planificacion.cursor import TAMANO_PAGINA, CursorInvalido, codificar, decodificar
from sigat_api.modules.planificacion.servicio import ServicioPlanificacion

AGRONOMO = Actor(usua_id=2, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
OTRO_AGRONOMO = Actor(usua_id=5, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
TECNICO = Actor(usua_id=3, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
PRODUCTOR = Actor(usua_id=4, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)

PLAN = UUID("00000000-0000-0000-0000-0000000000a1")
ORIGEN = UUID("00000000-0000-0000-0000-0000000000a2")

FILA = {
    "plan_id": PLAN,
    "tipo": "productor",
    "plan_actividad": "Revisión de trampas",
    "plan_fecha_inicio": date(2026, 11, 20),
    "plan_fecha_fin": date(2026, 11, 30),
    "plan_hora_desde": None,
    "plan_hora_hasta": None,
    "plan_estado": "pendiente",
    "plan_observaciones": "Revisar todas las trampas",
    "plan_observacion_respuesta": None,
    "plan_creada_en": datetime(2026, 11, 16, 15, 0, tzinfo=UTC),
    "plan_respondida_en": None,
    "usua_id_destinatario": 4,
    "destinatario": "Productor Prueba",
    "rol_destinatario": "Productor",
    "usua_id_creador": 2,
    "creador": "Agro Prueba",
    "arch_id_documento": None,
    "documento": None,
    "lotes": ["L01", "L02"],
    "lote_ids": [1, 2],
    "plan_id_origen": None,
    "usua_id_respuesta": None,
    "respondio": None,
    "lotes_pendientes": 2,
    "lotes_ejecutados": 0,
    "lotes_no_ejecutados": 0,
}

ORIGEN_ACEPTADO = {**FILA, "plan_id": ORIGEN, "plan_estado": "aceptada"}

RESULTADO = {
    "lote_id": 1,
    "lote_codigo": "L01",
    "plal_sync_id": UUID("00000000-0000-0000-0000-0000000000b1"),
    "plal_estado": "pendiente",
    "plal_observacion": None,
    "plal_resuelta_en": None,
    "visi_id": None,
    "evidencias": 0,
}

LOTES = [
    {"lote_id": 1, "lote_codigo": "L01", "lote_descripcion": "Lote 1", "pred_id": 1, "pred_nombre": "Finca"},
    {"lote_id": 2, "lote_codigo": "L02", "lote_descripcion": "Lote 2", "pred_id": 1, "pred_nombre": "Finca"},
]


ACTIVIDAD = {
    "plan_id": PLAN,
    "plan_id_origen": ORIGEN,
    "planificacion_aprobada": "Revisión de trampas",
    "aprobada_el": datetime(2026, 11, 16, 16, 0, tzinfo=UTC),
    "plan_actividad": "Leer trampas",
    "plan_fecha_inicio": date(2026, 11, 20),
    "plan_fecha_fin": None,
    "actividad_estado": "pendiente",
    "usua_id": 3,
    "tecnico": "Tomás Técnico",
    "lote_id": 1,
    "lote_codigo": "L01",
    "pred_id": 1,
    "resultado": "pendiente",
    "justificacion": None,
    "resuelta_en": None,
    "visi_id": None,
    "evidencias": 0,
}


class RepoFalso:
    def __init__(self) -> None:
        self.filas: dict[UUID, dict[str, Any]] = {PLAN: deepcopy(FILA), ORIGEN: deepcopy(ORIGEN_ACEPTADO)}
        self.listado: list[dict[str, Any]] = [deepcopy(FILA)]
        self.resultados: dict[UUID, list[dict[str, Any]]] = {PLAN: [deepcopy(RESULTADO)]}
        self.listado_actividades: list[dict[str, Any]] = [deepcopy(ACTIVIDAD)]
        self.agronomo_predio = False
        self.destinatario_tipo: str | None = "productor"
        self.asignables = deepcopy(LOTES)
        self.insertadas: list[tuple[dict[str, Any], list[int]]] = []
        self.llamadas_listar: list[tuple[Any, ...]] = []
        self.llamadas_actividades: list[tuple[Any, ...]] = []
        self.respondidas: list[tuple[UUID, str, str | None]] = []
        self.resultados_registrados: list[tuple[Any, ...]] = []

    async def listar(
        self, usua_id: int, bandeja: str, estado: str | None, despues_de: Any, limite: int
    ) -> list[dict[str, Any]]:
        self.llamadas_listar.append((usua_id, bandeja, estado, despues_de, limite))
        return self.listado

    async def obtener(self, plan_id: UUID) -> dict[str, Any] | None:
        return self.filas.get(plan_id)

    async def resultados_lote(self, plan_id: UUID) -> list[dict[str, Any]]:
        return deepcopy(self.resultados.get(plan_id, [deepcopy(RESULTADO)]))

    async def responder(self, plan_id: UUID, estado: str, observacion: str | None) -> int:
        fila = self.filas.get(plan_id)
        if fila is None or fila["plan_estado"] != "pendiente":
            return 0
        fila["plan_estado"] = estado
        fila["plan_observacion_respuesta"] = observacion
        fila["plan_respondida_en"] = datetime.now(UTC)
        fila["usua_id_respuesta"] = fila["usua_id_destinatario"]
        self.respondidas.append((plan_id, estado, observacion))
        return 1

    async def registrar_resultado_lote(
        self,
        plan_id: UUID,
        lote_id: int,
        estado: str,
        observacion: str | None,
        visi_id: UUID | None,
        oper_id: int | None,
    ) -> bool:
        lista = self.resultados.setdefault(plan_id, [deepcopy(RESULTADO)])
        res = next((r for r in lista if r["lote_id"] == lote_id), None)
        if res is None or res["plal_estado"] != "pendiente":
            return False
        res["plal_estado"] = estado
        res["plal_observacion"] = observacion
        res["visi_id"] = visi_id
        res["plal_resuelta_en"] = datetime.now(UTC)
        self.resultados_registrados.append((plan_id, lote_id, estado, observacion, visi_id))
        if all(r["plal_estado"] != "pendiente" for r in lista):
            if plan_id in self.filas:
                self.filas[plan_id]["plan_estado"] = "completada"
            return True
        return False

    async def actividades_tecnico(
        self,
        usua_id_actor: int,
        usua_id: int | None,
        pred_id: int | None,
        despues_de: Any,
        limite: int,
    ) -> list[dict[str, Any]]:
        self.llamadas_actividades.append((usua_id_actor, usua_id, pred_id, despues_de, limite))
        return self.listado_actividades

    async def agronomo_del_predio(self, usua_id: int, plan_id: UUID) -> bool:
        return self.agronomo_predio

    async def destinatarios(
        self, tipo: str | None, usua_id_agronomo: int, usua_id: int | None = None
    ) -> list[dict[str, Any]]:
        if self.destinatario_tipo is None:
            return []
        return [
            {
                "usua_id": 4,
                "nombre": "Productor Prueba",
                "subtitulo": "Productor · Finca",
                "tipo": self.destinatario_tipo,
            }
        ]

    async def lotes_asignables(self, usua_id_agronomo: int, destinatario: int | None) -> list[dict[str, Any]]:
        return self.asignables

    async def insertar(self, fila: dict[str, Any], lote_ids: list[int]) -> UUID:
        self.insertadas.append((fila, lote_ids))
        return PLAN


def servicio(repo: RepoFalso) -> ServicioPlanificacion:
    return ServicioPlanificacion(repo)  # type: ignore[arg-type]


def nueva(**cambios: Any) -> e.NuevaPlanificacion:
    datos: dict[str, Any] = {
        "usua_id_destinatario": 4,
        "plan_actividad": "Revisión de trampas",
        "plan_fecha_inicio": date(2026, 11, 20),
        "plan_fecha_fin": date(2026, 11, 30),
        "plan_observaciones": "Revisar todas las trampas",
        "lote_ids": [1, 2],
    }
    datos.update(cambios)
    return e.NuevaPlanificacion(**datos)


def actividad(**cambios: Any) -> e.NuevaPlanificacion:
    datos: dict[str, Any] = {
        "usua_id_destinatario": 3,
        "plan_actividad": "Leer trampas",
        "plan_fecha_inicio": date(2026, 11, 21),
        "plan_fecha_fin": date(2026, 11, 25),
        "plan_id_origen": ORIGEN,
        "lote_ids": [1],
    }
    datos.update(cambios)
    return e.NuevaPlanificacion(**datos)


def repo_para_tecnico() -> RepoFalso:
    repo = RepoFalso()
    repo.destinatario_tipo = "tecnico"
    return repo


# ------------------------------------------------------------------ listar
@pytest.mark.regla
async def test_listar_pasa_bandeja_estado_y_pide_una_fila_de_mas() -> None:
    repo = RepoFalso()

    pagina = await servicio(repo).listar(AGRONOMO, "creadas", EstadoPlanificacion.PENDIENTE, None)

    assert [p.plan_id for p in pagina.items] == [PLAN]
    assert pagina.siguiente is None
    assert repo.llamadas_listar == [(2, "creadas", "pendiente", None, TAMANO_PAGINA + 1)]


@pytest.mark.regla
async def test_listar_con_pagina_llena_devuelve_cursor_de_la_ultima_fila() -> None:
    repo = RepoFalso()
    repo.listado = [deepcopy(FILA) for _ in range(TAMANO_PAGINA + 1)]

    pagina = await servicio(repo).listar(AGRONOMO, "todas", None, None)

    assert len(pagina.items) == TAMANO_PAGINA
    assert pagina.siguiente == codificar(FILA["plan_creada_en"].isoformat(), str(PLAN))


@pytest.mark.regla
async def test_listar_con_cursor_pide_lo_que_sigue_a_esa_clave() -> None:
    repo = RepoFalso()
    cursor = codificar(FILA["plan_creada_en"].isoformat(), str(PLAN))

    await servicio(repo).listar(AGRONOMO, "todas", None, cursor)

    assert repo.llamadas_listar[0][3] == (FILA["plan_creada_en"], PLAN)


@pytest.mark.regla
@pytest.mark.parametrize("cursor", ["basura", codificar("no-es-fecha", "x"), codificar(1, 2, 3)])
async def test_listar_con_cursor_manipulado_da_400(cursor: str) -> None:
    with pytest.raises(CursorInvalido):
        await servicio(RepoFalso()).listar(AGRONOMO, "todas", None, cursor)


def test_cursor_ida_y_vuelta() -> None:
    assert decodificar(codificar("2026-11-16", "abc", 7), 3) == ["2026-11-16", "abc", 7]
    assert decodificar(None, 2) is None


# ------------------------------------------------------------------ obtener
@pytest.mark.regla
@pytest.mark.parametrize("actor", [AGRONOMO, PRODUCTOR])
async def test_obtener_visible_para_creador_y_destinatario(actor: Actor) -> None:
    detalle = await servicio(RepoFalso()).obtener(actor, PLAN)

    assert detalle.plan_id == PLAN
    assert detalle.resultados_lote[0].lote_codigo == "L01"


@pytest.mark.regla
async def test_obtener_visible_para_el_agronomo_del_predio() -> None:
    repo = RepoFalso()
    repo.agronomo_predio = True

    detalle = await servicio(repo).obtener(OTRO_AGRONOMO, PLAN)

    assert detalle.plan_id == PLAN


@pytest.mark.regla
@pytest.mark.parametrize("actor", [TECNICO, OTRO_AGRONOMO])
async def test_obtener_ajena_da_404(actor: Actor) -> None:
    with pytest.raises(NoEncontrado) as error:
        await servicio(RepoFalso()).obtener(actor, PLAN)

    assert error.value.codigo == "PLANIFICACION_NO_ENCONTRADA"


@pytest.mark.regla
async def test_el_acceso_al_predio_no_basta_si_no_es_agronomo() -> None:
    repo = RepoFalso()
    repo.agronomo_predio = True

    with pytest.raises(NoEncontrado):
        await servicio(repo).obtener(TECNICO, PLAN)


@pytest.mark.regla
async def test_obtener_inexistente_da_404() -> None:
    with pytest.raises(NoEncontrado):
        await servicio(RepoFalso()).obtener(AGRONOMO, UUID(int=9))


# ------------------------------------------------------------------ crear: comunes
@pytest.mark.regla
async def test_crear_para_productor_guarda_al_actor_como_creador() -> None:
    repo = RepoFalso()

    detalle = await servicio(repo).crear(AGRONOMO, nueva(plan_observaciones="  Revisar todas las trampas  "))

    fila, lote_ids = repo.insertadas[0]
    assert fila["usua_id_creador"] == AGRONOMO.usua_id
    assert fila["plan_observaciones"] == "Revisar todas las trampas"
    assert fila["plan_documento_clase"] is None
    assert lote_ids == [1, 2]
    assert detalle.plan_id == PLAN


@pytest.mark.regla
async def test_crear_quita_lotes_repetidos() -> None:
    repo = RepoFalso()

    await servicio(repo).crear(AGRONOMO, nueva(lote_ids=[2, 1, 2]))

    assert repo.insertadas[0][1] == [2, 1]


@pytest.mark.regla
@pytest.mark.parametrize("actor", [TECNICO, PRODUCTOR])
async def test_solo_el_agronomo_crea(actor: Actor) -> None:
    repo = RepoFalso()

    with pytest.raises(Prohibido) as error:
        await servicio(repo).crear(actor, nueva())

    assert error.value.codigo == "SOLO_AGRONOMO_CREA"
    assert repo.insertadas == []


@pytest.mark.regla
@pytest.mark.parametrize(
    ("cambios", "codigo"),
    [
        ({"plan_fecha_fin": date(2026, 11, 19)}, "FECHAS_INVALIDAS"),
        ({"plan_hora_desde": time(8)}, "HORARIO_INCOMPLETO"),
        ({"plan_hora_hasta": time(8)}, "HORARIO_INCOMPLETO"),
        ({"plan_hora_desde": time(10), "plan_hora_hasta": time(8)}, "HORARIO_INVALIDO"),
        ({"plan_hora_desde": time(8), "plan_hora_hasta": time(8)}, "HORARIO_INVALIDO"),
    ],
)
async def test_crear_valida_fechas_y_horario(cambios: dict[str, Any], codigo: str) -> None:
    repo = RepoFalso()

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva(**cambios))

    assert error.value.codigo == codigo
    assert repo.insertadas == []


@pytest.mark.regla
async def test_crear_acepta_fecha_fin_igual_a_inicio_y_horario_completo() -> None:
    repo = RepoFalso()

    await servicio(repo).crear(
        AGRONOMO,
        nueva(plan_fecha_fin=date(2026, 11, 20), plan_hora_desde=time(8), plan_hora_hasta=time(12)),
    )

    assert len(repo.insertadas) == 1


@pytest.mark.regla
async def test_crear_con_destinatario_que_no_es_de_sus_predios_da_422() -> None:
    repo = RepoFalso()
    repo.destinatario_tipo = None

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva())

    assert error.value.codigo == "DESTINATARIO_INVALIDO"


@pytest.mark.regla
async def test_crear_con_lote_no_asignable_da_422() -> None:
    repo = RepoFalso()
    repo.asignables = [LOTES[0]]

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva(lote_ids=[1, 2]))

    assert error.value.codigo == "LOTE_NO_ASIGNABLE"
    assert "[2]" in error.value.detalle


# ------------------------------------------------------------------ crear: productor
@pytest.mark.regla
@pytest.mark.parametrize("observacion", [None, "", "          ", "123456789", "   123456789   "])
async def test_productor_sin_documento_exige_observacion_de_10(observacion: str | None) -> None:
    repo = RepoFalso()

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva(plan_observaciones=observacion))

    assert error.value.codigo == "DOCUMENTO_U_OBSERVACION_REQUERIDO"


@pytest.mark.regla
async def test_productor_con_documento_no_necesita_observacion() -> None:
    repo = RepoFalso()
    documento = UUID(int=77)

    await servicio(repo).crear(AGRONOMO, nueva(plan_observaciones=None, arch_id_documento=documento))

    fila = repo.insertadas[0][0]
    assert fila["arch_id_documento"] == documento
    assert fila["plan_documento_clase"] == "documento_instruccion"


@pytest.mark.regla
async def test_productor_no_lleva_plan_id_origen() -> None:
    with pytest.raises(ReglaNegocio) as error:
        await servicio(RepoFalso()).crear(AGRONOMO, nueva(plan_id_origen=ORIGEN))

    assert error.value.codigo == "ORIGEN_NO_PERMITIDO"


@pytest.mark.regla
async def test_productor_admite_planificacion_sin_lotes() -> None:
    repo = RepoFalso()

    await servicio(repo).crear(AGRONOMO, nueva(lote_ids=[]))

    assert repo.insertadas[0][1] == []


# ------------------------------------------------------------------ crear: técnico
@pytest.mark.regla
async def test_actividad_valida_desde_origen_aceptado() -> None:
    repo = repo_para_tecnico()

    await servicio(repo).crear(AGRONOMO, actividad())

    fila, lote_ids = repo.insertadas[0]
    assert fila["plan_id_origen"] == ORIGEN
    assert lote_ids == [1]


@pytest.mark.regla
async def test_actividad_sin_lotes_da_422() -> None:
    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo_para_tecnico()).crear(AGRONOMO, actividad(lote_ids=[]))

    assert error.value.codigo == "LOTES_REQUERIDOS"


@pytest.mark.regla
async def test_actividad_sin_origen_da_422() -> None:
    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo_para_tecnico()).crear(AGRONOMO, actividad(plan_id_origen=None))

    assert error.value.codigo == "ORIGEN_REQUERIDO"


@pytest.mark.regla
async def test_actividad_con_origen_inexistente_o_ajeno_da_422() -> None:
    repo = repo_para_tecnico()
    repo.filas[ORIGEN]["usua_id_creador"] = 99
    repo.filas[ORIGEN]["usua_id_destinatario"] = 98

    with pytest.raises(ReglaNegocio) as ajeno:
        await servicio(repo).crear(AGRONOMO, actividad())
    with pytest.raises(ReglaNegocio) as inexistente:
        await servicio(repo).crear(AGRONOMO, actividad(plan_id_origen=UUID(int=9)))

    assert ajeno.value.codigo == "ORIGEN_INVALIDO"
    assert inexistente.value.codigo == "ORIGEN_INVALIDO"


@pytest.mark.regla
@pytest.mark.parametrize(
    "cambio_origen",
    [
        {"plan_estado": "pendiente"},
        {"plan_estado": "rechazada"},
        {"plan_estado": "completada"},
        {"tipo": "tecnico"},
    ],
)
async def test_actividad_exige_origen_de_productor_aceptado(cambio_origen: dict[str, Any]) -> None:
    repo = repo_para_tecnico()
    repo.filas[ORIGEN].update(cambio_origen)

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, actividad())

    assert error.value.codigo == "ORIGEN_NO_ACEPTADO"


@pytest.mark.regla
@pytest.mark.parametrize(
    "cambios",
    [
        {"plan_fecha_inicio": date(2026, 11, 19)},
        {"plan_fecha_fin": date(2026, 12, 1)},
        {"plan_fecha_inicio": date(2026, 12, 1), "plan_fecha_fin": None},
    ],
)
async def test_actividad_fuera_de_las_fechas_del_origen_da_422(cambios: dict[str, Any]) -> None:
    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo_para_tecnico()).crear(AGRONOMO, actividad(**cambios))

    assert error.value.codigo == "FECHAS_FUERA_DEL_ORIGEN"


@pytest.mark.regla
async def test_actividad_con_origen_sin_fecha_fin_admite_cualquier_fin_posterior() -> None:
    repo = repo_para_tecnico()
    repo.filas[ORIGEN]["plan_fecha_fin"] = None

    await servicio(repo).crear(AGRONOMO, actividad(plan_fecha_fin=date(2027, 1, 1)))

    assert len(repo.insertadas) == 1


@pytest.mark.regla
async def test_actividad_con_lote_fuera_del_origen_da_422() -> None:
    repo = repo_para_tecnico()
    repo.filas[ORIGEN]["lote_ids"] = [1]

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, actividad(lote_ids=[1, 2]))

    assert error.value.codigo == "LOTE_NO_EN_ORIGEN"


# ------------------------------------------------------------------ destinatarios y lotes asignables
@pytest.mark.regla
async def test_destinatarios_devuelve_el_esquema() -> None:
    destinatarios = await servicio(RepoFalso()).destinatarios(AGRONOMO, "productor")

    assert destinatarios[0].subtitulo == "Productor · Finca"
    assert destinatarios[0].tipo == "productor"


@pytest.mark.regla
async def test_lotes_asignables_devuelve_el_esquema() -> None:
    lotes = await servicio(RepoFalso()).lotes_asignables(AGRONOMO, 3)

    assert [lote.lote_codigo for lote in lotes] == ["L01", "L02"]


@pytest.mark.regla
@pytest.mark.parametrize("actividad_texto", ["   ", "  a ", " ab  "])
async def test_crear_no_cuenta_los_espacios_de_la_actividad(actividad_texto: str) -> None:
    repo = RepoFalso()

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).crear(AGRONOMO, nueva(plan_actividad=actividad_texto))

    assert error.value.codigo == "ACTIVIDAD_INVALIDA"
    assert repo.insertadas == []


@pytest.mark.regla
async def test_crear_guarda_la_actividad_sin_espacios_sobrantes() -> None:
    repo = RepoFalso()

    await servicio(repo).crear(AGRONOMO, nueva(plan_actividad="  Revisión  "))

    assert repo.insertadas[0][0]["plan_actividad"] == "Revisión"


# ------------------------------------------------------------------ responder (aceptar / rechazar)
@pytest.mark.regla
async def test_aceptar_actualiza_estado_y_guarda_observacion() -> None:
    repo = RepoFalso()
    detalle = await servicio(repo).aceptar(PRODUCTOR, PLAN, e.Aceptacion(observacion="Aprobado"))

    assert detalle.plan_estado == "aceptada"
    assert detalle.plan_observacion_respuesta == "Aprobado"
    assert repo.filas[PLAN]["plan_estado"] == "aceptada"


@pytest.mark.regla
async def test_aceptar_observacion_vacia_guarda_none() -> None:
    repo = RepoFalso()
    detalle = await servicio(repo).aceptar(PRODUCTOR, PLAN, e.Aceptacion(observacion="   "))

    assert detalle.plan_observacion_respuesta is None
    assert repo.filas[PLAN]["plan_observacion_respuesta"] is None


@pytest.mark.regla
async def test_aceptar_por_otro_usuario_da_404() -> None:
    with pytest.raises(NoEncontrado) as error:
        await servicio(RepoFalso()).aceptar(AGRONOMO, PLAN, e.Aceptacion())

    assert error.value.codigo == "PLANIFICACION_NO_ENCONTRADA"


@pytest.mark.regla
async def test_aceptar_actividad_de_tecnico_da_422() -> None:
    repo = RepoFalso()
    repo.filas[PLAN]["tipo"] = "tecnico"
    repo.filas[PLAN]["usua_id_destinatario"] = TECNICO.usua_id

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).aceptar(TECNICO, PLAN, e.Aceptacion())

    assert error.value.codigo == "TECNICO_NO_RESPONDE"


@pytest.mark.regla
async def test_aceptar_planificacion_ya_respondida_da_409() -> None:
    repo = RepoFalso()
    repo.filas[PLAN]["plan_estado"] = "aceptada"

    with pytest.raises(Conflicto) as error:
        await servicio(repo).aceptar(PRODUCTOR, PLAN, e.Aceptacion())

    assert error.value.codigo == "PLANIFICACION_YA_RESPONDIDA"


@pytest.mark.regla
async def test_rechazar_actualiza_estado_y_motivo() -> None:
    repo = RepoFalso()
    detalle = await servicio(repo).rechazar(
        PRODUCTOR, PLAN, e.Rechazo(motivo="No es posible fumigar esta semana")
    )

    assert detalle.plan_estado == "rechazada"
    assert detalle.plan_observacion_respuesta == "No es posible fumigar esta semana"
    assert repo.filas[PLAN]["plan_estado"] == "rechazada"


@pytest.mark.regla
async def test_rechazar_por_otro_usuario_da_404() -> None:
    with pytest.raises(NoEncontrado) as error:
        await servicio(RepoFalso()).rechazar(
            AGRONOMO, PLAN, e.Rechazo(motivo="No es posible fumigar esta semana")
        )

    assert error.value.codigo == "PLANIFICACION_NO_ENCONTRADA"


@pytest.mark.regla
async def test_rechazar_actividad_de_tecnico_da_422() -> None:
    repo = RepoFalso()
    repo.filas[PLAN]["tipo"] = "tecnico"
    repo.filas[PLAN]["usua_id_destinatario"] = TECNICO.usua_id

    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).rechazar(TECNICO, PLAN, e.Rechazo(motivo="No es posible fumigar esta semana"))

    assert error.value.codigo == "TECNICO_NO_RESPONDE"


@pytest.mark.regla
async def test_rechazar_planificacion_ya_respondida_da_409() -> None:
    repo = RepoFalso()
    repo.filas[PLAN]["plan_estado"] = "rechazada"

    with pytest.raises(Conflicto) as error:
        await servicio(repo).rechazar(PRODUCTOR, PLAN, e.Rechazo(motivo="No es posible fumigar esta semana"))

    assert error.value.codigo == "PLANIFICACION_YA_RESPONDIDA"


# ------------------------------------------------------------------ resultado en lote
def repo_actividad_tecnico() -> RepoFalso:
    repo = RepoFalso()
    repo.filas[PLAN] = {
        **deepcopy(FILA),
        "tipo": "tecnico",
        "usua_id_destinatario": TECNICO.usua_id,
        "rol_destinatario": "Técnico",
        "plan_id_origen": ORIGEN,
        "lote_ids": [1, 2],
        "lotes": ["L01", "L02"],
    }
    repo.resultados[PLAN] = [
        deepcopy(RESULTADO),
        {**deepcopy(RESULTADO), "lote_id": 2, "lote_codigo": "L02"},
    ]
    return repo


@pytest.mark.regla
async def test_resultado_lote_ejecutada_con_visita() -> None:
    repo = repo_actividad_tecnico()
    visita = UUID("00000000-0000-0000-0000-0000000000c1")
    detalle = await servicio(repo).registrar_resultado_lote(
        TECNICO, PLAN, 1, e.EntradaResultadoLote(plal_estado="ejecutada", visi_id=visita)
    )

    assert detalle.plan_estado == "pendiente"
    res1 = next(r for r in detalle.resultados_lote if r.lote_id == 1)
    assert res1.plal_estado == "ejecutada"
    assert res1.visi_id == visita


@pytest.mark.regla
async def test_resultado_lote_no_ejecutada_sin_justificacion_da_422() -> None:
    repo = repo_actividad_tecnico()
    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).registrar_resultado_lote(
            TECNICO, PLAN, 1, e.EntradaResultadoLote(plal_estado="no_ejecutada", plal_observacion="  ")
        )

    assert error.value.codigo == "JUSTIFICACION_REQUERIDA"


@pytest.mark.regla
async def test_resultado_lote_no_ejecutada_con_visita_da_422() -> None:
    repo = repo_actividad_tecnico()
    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).registrar_resultado_lote(
            TECNICO,
            PLAN,
            1,
            e.EntradaResultadoLote(
                plal_estado="no_ejecutada",
                plal_observacion="Lluvia",
                visi_id=UUID("00000000-0000-0000-0000-0000000000c1"),
            ),
        )

    assert error.value.codigo == "VISITA_NO_PERMITIDA"


@pytest.mark.regla
async def test_resultado_lote_ya_resuelto_da_409() -> None:
    repo = repo_actividad_tecnico()
    repo.resultados[PLAN][0]["plal_estado"] = "ejecutada"

    with pytest.raises(Conflicto) as error:
        await servicio(repo).registrar_resultado_lote(
            TECNICO, PLAN, 1, e.EntradaResultadoLote(plal_estado="ejecutada")
        )

    assert error.value.codigo == "LOTE_YA_RESUELTO"


@pytest.mark.regla
async def test_resultado_lote_no_en_actividad_da_404() -> None:
    repo = repo_actividad_tecnico()
    with pytest.raises(NoEncontrado) as error:
        await servicio(repo).registrar_resultado_lote(
            TECNICO, PLAN, 99, e.EntradaResultadoLote(plal_estado="ejecutada")
        )

    assert error.value.codigo == "LOTE_NO_EN_ACTIVIDAD"


@pytest.mark.regla
async def test_resultado_lote_por_otro_usuario_da_404() -> None:
    repo = repo_actividad_tecnico()
    with pytest.raises(NoEncontrado) as error:
        await servicio(repo).registrar_resultado_lote(
            AGRONOMO, PLAN, 1, e.EntradaResultadoLote(plal_estado="ejecutada")
        )

    assert error.value.codigo == "PLANIFICACION_NO_ENCONTRADA"


@pytest.mark.regla
async def test_resultado_lote_planificacion_de_productor_da_422() -> None:
    repo = RepoFalso()
    with pytest.raises(ReglaNegocio) as error:
        await servicio(repo).registrar_resultado_lote(
            PRODUCTOR, PLAN, 1, e.EntradaResultadoLote(plal_estado="ejecutada")
        )

    assert error.value.codigo == "SOLO_ACTIVIDAD_TECNICO"


@pytest.mark.regla
async def test_resultado_lote_actividad_no_pendiente_da_409() -> None:
    repo = repo_actividad_tecnico()
    repo.filas[PLAN]["plan_estado"] = "completada"

    with pytest.raises(Conflicto) as error:
        await servicio(repo).registrar_resultado_lote(
            TECNICO, PLAN, 1, e.EntradaResultadoLote(plal_estado="ejecutada")
        )

    assert error.value.codigo == "ACTIVIDAD_NO_PENDIENTE"


@pytest.mark.regla
async def test_ultimo_lote_resuelto_completa_la_actividad() -> None:
    repo = repo_actividad_tecnico()
    await servicio(repo).registrar_resultado_lote(
        TECNICO, PLAN, 1, e.EntradaResultadoLote(plal_estado="ejecutada")
    )
    detalle = await servicio(repo).registrar_resultado_lote(
        TECNICO,
        PLAN,
        2,
        e.EntradaResultadoLote(plal_estado="no_ejecutada", plal_observacion="Acceso cerrado"),
    )

    assert detalle.plan_estado == "completada"
    assert repo.filas[PLAN]["plan_estado"] == "completada"


# ------------------------------------------------------------------ informe de actividades técnico
@pytest.mark.regla
async def test_actividades_tecnico_devuelve_pagina() -> None:
    repo = RepoFalso()
    pagina = await servicio(repo).actividades_tecnico(AGRONOMO, None, None, None)

    assert len(pagina.items) == 1
    assert pagina.items[0].plan_id == PLAN
    assert pagina.siguiente is None


@pytest.mark.regla
async def test_actividades_tecnico_paginacion() -> None:
    repo = RepoFalso()
    repo.listado_actividades = [deepcopy(ACTIVIDAD) for _ in range(TAMANO_PAGINA + 1)]
    pagina = await servicio(repo).actividades_tecnico(AGRONOMO, None, None, None)

    assert len(pagina.items) == TAMANO_PAGINA
    assert pagina.siguiente is not None


@pytest.mark.regla
async def test_actividades_tecnico_cursor_invalido_da_400() -> None:
    repo = RepoFalso()
    with pytest.raises(CursorInvalido):
        await servicio(repo).actividades_tecnico(AGRONOMO, None, None, "invalido!!")
