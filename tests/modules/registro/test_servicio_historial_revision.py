"""Historial y revisión del agrónomo (issue #8): visitas, lecturas, versiones, revisiones y actividad.

No toca PostgreSQL: el repositorio se sustituye por un doble que devuelve filas con la forma de
las consultas reales. Se prueban las decisiones del servicio (404 sin revelar datos ajenos,
transiciones de revisión, trazabilidad, paginación, advertencias) sin levantar la base.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from sigat_api.compartido.errores import Conflicto, NoEncontrado, ReglaNegocio
from sigat_api.compartido.tipos import AccionSync, Actor, Cliente, EstadoRevision, Linaje, RolCodigo
from sigat_api.modules.registro import esquemas as e
from sigat_api.modules.registro.cursor import TAMANO_PAGINA, CursorInvalido, codificar, decodificar
from sigat_api.modules.registro.servicio import (
    ServicioConsultaRegistro,
    ServicioRevision,
    advertencias_revision,
    validar_transicion,
)

AGRONOMO = Actor(usua_id=3, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
TECNICO = Actor(usua_id=2, rol=RolCodigo.TECNICO, disp_id=None, cliente=Cliente.MOVIL)
AHORA = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
VISI = UUID("0190f0d0-0000-7000-8000-000000000001")
REGISTRO = UUID("0190f0d0-0000-7000-8000-0000000000aa")

VISITA: dict[str, Any] = {
    "visi_id": VISI,
    "usua_id": 2,
    "tecnico": "Técnico Prueba",
    "pred_id": 1,
    "pred_nombre": "Finca de demostración",
    "ruta_id": None,
    "t_inicio_obs": datetime(2026, 11, 10, 8, 0, tzinfo=UTC),
    "t_fin_obs": datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
    "t_captura": datetime(2026, 11, 10, 9, 5, tzinfo=UTC),
    "t_sincronizacion": datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
    "deriva_reloj_seg": 4,
    "estado_revision": "pendiente",
    "motivo_revision": None,
    "revisada_por": None,
    "revisada_en": None,
    "lote_ids": [1],
    "lotes": ["01"],
    "trampas_leidas": 1,
    "lote_sin_asignacion": False,
    "trampa_fuera_del_lote": False,
}

LECTURA: dict[str, Any] = {
    "lect_id": UUID("0190f0d0-0000-7000-8000-0000000000b2"),
    "lect_registro_id": REGISTRO,
    "lect_version": 2,
    "visi_id": VISI,
    "tram_id": 1,
    "tram_codigo": "68001-A-001",
    "lote_id": 1,
    "ubic_id": 1,
    "lect_leida": True,
    "lect_especimenes": 7,
    "lect_justificacion": None,
    "cond_id": 1,
    "condicion": "Buena",
    "lect_fecha_cambio_cuerpo": None,
    "lect_fecha_cambio_atrayente": None,
    "atra_id": None,
    "lect_observaciones": None,
    "latitud": Decimal("7.118"),
    "longitud": Decimal("-73.122"),
    "lect_precision_m": 4.0,
    "lect_coincide_lote": True,
    "linaje": "inspeccion_oficial",
    "usua_id": 3,
    "autor": "Agrónomo Prueba",
    "t_observacion": datetime(2026, 11, 10, 8, 10, tzinfo=UTC),
    "t_captura": datetime(2026, 11, 10, 8, 11, tzinfo=UTC),
    "t_sincronizacion": datetime(2026, 11, 11, 10, 0, tzinfo=UTC),
    "deriva_reloj_seg": None,
    "anulada": False,
    "motivo_anulacion": None,
    "motivo_correccion": "Se contaron dos veces los mismos especímenes",
    "especimenes_originales": 9,
    "estado_revision": "corregido",
    "evidencias": 1,
}

VERSIONES: list[dict[str, Any]] = [
    {
        "version": 1,
        "anulada": False,
        "motivo": None,
        "autor": "Técnico Prueba",
        "usua_id": 2,
        "t_sincronizacion": datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
        "t_observacion": LECTURA["t_observacion"],
        "valores": {"lect_especimenes": 9},
    },
    {
        "version": 2,
        "anulada": False,
        "motivo": "Se contaron dos veces los mismos especímenes",
        "autor": "Agrónomo Prueba",
        "usua_id": 3,
        "t_sincronizacion": datetime(2026, 11, 11, 10, 0, tzinfo=UTC),
        "t_observacion": LECTURA["t_observacion"],
        "valores": {"lect_especimenes": 7},
    },
]

ACTIVIDAD: dict[str, Any] = {
    "lote_id": 1,
    "lote_descripcion": "Lote de demostración",
    "tipo": "vigilancia",
    "registro_id": VISI,
    "t_observacion": VISITA["t_inicio_obs"],
    "usua_id": 2,
    "autor": "Técnico Prueba",
    "rol_autor": "TECNICO",
    "linaje": "inspeccion_oficial",
    "titulo": "Visita de vigilancia",
    "detalle": "1 de 2 trampas revisadas",
}


class RelojFijo:
    def ahora(self) -> datetime:
        return AHORA


class RepoFalso:
    """Simula ``RepositorioRegistro`` con una sola visita y una lectura corregida."""

    def __init__(self) -> None:
        self.fila_visita: dict[str, Any] | None = deepcopy(VISITA)
        self.filas_lecturas: list[dict[str, Any]] = [deepcopy(LECTURA)]
        self.versiones_lectura: list[dict[str, Any]] = deepcopy(VERSIONES)
        self.lotes_visibles = {1}
        self.filas_paginadas: list[dict[str, Any]] = [deepcopy(VISITA)]
        self.actividad_filas: list[dict[str, Any]] = [deepcopy(ACTIVIDAD)]
        self.llamadas: list[tuple[Any, ...]] = []

    # visitas y lecturas
    async def visitas(
        self, usua_id: int, solo_propias: bool, filtros: dict[str, Any], despues: Any, limite: int
    ):
        self.llamadas.append(("visitas", usua_id, solo_propias, filtros, despues, limite))
        return deepcopy(self.filas_paginadas)

    async def visita(self, usua_id: int, solo_propias: bool, visi_id: UUID):
        self.llamadas.append(("visita", usua_id, solo_propias, visi_id))
        return deepcopy(self.fila_visita) if self.fila_visita and visi_id == VISI else None

    async def lecturas_de_visita(self, visi_id: UUID):
        return deepcopy(self.filas_lecturas)

    async def lecturas(
        self, usua_id: int, solo_propias: bool, filtros: dict[str, Any], despues: Any, limite: int
    ):
        self.llamadas.append(("lecturas", usua_id, solo_propias, filtros, despues, limite))
        return deepcopy(self.filas_lecturas)

    async def lectura_visible(self, usua_id: int, solo_propias: bool, registro_id: UUID) -> bool:
        return registro_id == REGISTRO

    async def versiones(self, entidad: str, registro_id: UUID):
        self.llamadas.append(("versiones", entidad, registro_id))
        return deepcopy(self.versiones_lectura)

    # revisión
    async def registros_por_revisar(self, usua_id: int, estado: str | None, despues: Any, limite: int):
        self.llamadas.append(("por_revisar", usua_id, estado, despues, limite))
        return deepcopy(self.filas_paginadas)

    async def registro_revision(self, usua_id: int, visi_id: UUID, bloquear: bool = False):
        self.llamadas.append(("registro_revision", usua_id, visi_id, bloquear))
        return deepcopy(self.fila_visita) if self.fila_visita and visi_id == VISI else None

    async def actualizar_revision_visita(
        self, visi_id: UUID, estado: str, motivo: str | None, usua_id: int, en: datetime
    ) -> None:
        self.llamadas.append(("actualizar", visi_id, estado, motivo, usua_id, en))
        assert self.fila_visita is not None
        self.fila_visita.update(
            estado_revision=estado, motivo_revision=motivo, revisada_por=usua_id, revisada_en=en
        )

    async def contar_por_revisar(self, usua_id: int) -> int:
        return 1

    # actividad
    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return lote_id in self.lotes_visibles

    async def actividad(self, usua_id: int, filtros: dict[str, Any], despues: Any, limite: int):
        self.llamadas.append(("actividad", usua_id, filtros, despues, limite))
        return deepcopy(self.actividad_filas)

    async def actividad_reciente(self, usua_id: int, lote_id: int, limite: int):
        self.llamadas.append(("reciente", usua_id, lote_id, limite))
        return [{"tipo": "visita", "texto": "Visita de Técnico Prueba", "t": VISITA["t_inicio_obs"]}]


def _revision(repo: RepoFalso) -> ServicioRevision:
    return ServicioRevision(repo, RelojFijo(), umbral_deriva_seg=300)  # type: ignore[arg-type]


def _consulta(repo: RepoFalso) -> ServicioConsultaRegistro:
    return ServicioConsultaRegistro(repo)  # type: ignore[arg-type]


def _ultima(repo: RepoFalso, nombre: str) -> tuple[Any, ...]:
    return [c for c in repo.llamadas if c[0] == nombre][-1]


# ------------------------------------------------------------------ transiciones de revisión
@pytest.mark.regla
@pytest.mark.parametrize(
    ("actual", "destino"),
    [
        (EstadoRevision.PENDIENTE, EstadoRevision.EN_REVISION),
        (EstadoRevision.PENDIENTE, EstadoRevision.ACEPTADO),
        (EstadoRevision.EN_REVISION, EstadoRevision.ACEPTADO),
    ],
)
def test_transiciones_permitidas(actual: EstadoRevision, destino: EstadoRevision) -> None:
    validar_transicion(actual, destino)


@pytest.mark.regla
@pytest.mark.parametrize(
    ("actual", "destino", "codigo"),
    [
        (EstadoRevision.ACEPTADO, EstadoRevision.ACEPTADO, "REVISION_YA_RESUELTA"),
        (EstadoRevision.ACEPTADO, EstadoRevision.EN_REVISION, "REVISION_YA_RESUELTA"),
        (EstadoRevision.EN_REVISION, EstadoRevision.EN_REVISION, "REVISION_YA_EN_REVISION"),
        (EstadoRevision.EN_REVISION, EstadoRevision.PENDIENTE, "TRANSICION_REVISION_NO_PERMITIDA"),
    ],
)
def test_transiciones_no_permitidas_dan_409(
    actual: EstadoRevision, destino: EstadoRevision, codigo: str
) -> None:
    with pytest.raises(Conflicto) as exc:
        validar_transicion(actual, destino)
    assert exc.value.codigo == codigo
    assert exc.value.estado_http == 409


# ------------------------------------------------------------------ aceptar / observar
@pytest.mark.regla
async def test_aceptar_registra_quien_cuando_y_conserva_el_motivo_previo() -> None:
    repo = RepoFalso()
    assert repo.fila_visita is not None
    repo.fila_visita.update(estado_revision="en_revision", motivo_revision="Revisar la trampa 68001-A-001")

    detalle = await _revision(repo).aceptar(AGRONOMO, VISI)

    _, visi, estado, motivo, usua, en = _ultima(repo, "actualizar")
    assert (visi, estado, usua, en) == (VISI, "aceptado", AGRONOMO.usua_id, AHORA)
    assert motivo == "Revisar la trampa 68001-A-001"
    assert detalle.estado_revision == EstadoRevision.ACEPTADO
    assert _ultima(repo, "registro_revision")[3] is False  # el detalle final se relee sin bloqueo
    assert ("registro_revision", AGRONOMO.usua_id, VISI, True) in repo.llamadas  # se bloqueó la fila


@pytest.mark.regla
async def test_aceptar_una_visita_ya_aceptada_da_409_y_no_escribe() -> None:
    repo = RepoFalso()
    assert repo.fila_visita is not None
    repo.fila_visita["estado_revision"] = "aceptado"

    with pytest.raises(Conflicto) as exc:
        await _revision(repo).aceptar(AGRONOMO, VISI)

    assert exc.value.codigo == "REVISION_YA_RESUELTA"
    assert not [c for c in repo.llamadas if c[0] == "actualizar"]


@pytest.mark.regla
async def test_marcar_en_revision_guarda_motivo_autor_y_hora() -> None:
    repo = RepoFalso()

    detalle = await _revision(repo).marcar_en_revision(
        AGRONOMO, VISI, e.ObservacionRevision(motivo="  La trampa 3 no tiene foto de evidencia  ")
    )

    _, _, estado, motivo, usua, en = _ultima(repo, "actualizar")
    assert (estado, motivo, usua, en) == ("en_revision", "La trampa 3 no tiene foto de evidencia", 3, AHORA)
    assert detalle.estado_revision == EstadoRevision.EN_REVISION
    assert detalle.motivo_revision == "La trampa 3 no tiene foto de evidencia"


@pytest.mark.regla
async def test_marcar_en_revision_dos_veces_da_409() -> None:
    repo = RepoFalso()
    assert repo.fila_visita is not None
    repo.fila_visita["estado_revision"] = "en_revision"

    with pytest.raises(Conflicto) as exc:
        await _revision(repo).marcar_en_revision(
            AGRONOMO, VISI, e.ObservacionRevision(motivo="Otra observación más")
        )
    assert exc.value.codigo == "REVISION_YA_EN_REVISION"


@pytest.mark.regla
@pytest.mark.parametrize("operacion", ["obtener", "aceptar", "observar"])
async def test_revision_de_visita_ajena_o_inexistente_da_404(operacion: str) -> None:
    repo = RepoFalso()
    repo.fila_visita = None
    svc = _revision(repo)
    llamadas = {
        "obtener": lambda: svc.obtener(AGRONOMO, VISI),
        "aceptar": lambda: svc.aceptar(AGRONOMO, VISI),
        "observar": lambda: svc.marcar_en_revision(
            AGRONOMO, VISI, e.ObservacionRevision(motivo="Motivo suficiente")
        ),
    }

    with pytest.raises(NoEncontrado) as exc:
        await llamadas[operacion]()
    assert exc.value.codigo == "VISITA_NO_ENCONTRADA"
    assert not [c for c in repo.llamadas if c[0] == "actualizar"]


# ------------------------------------------------------------------ detalle y listado de revisiones
@pytest.mark.regla
async def test_detalle_de_revision_trae_lecturas_y_versiones_de_las_corregidas() -> None:
    repo = RepoFalso()
    original = deepcopy(LECTURA) | {
        "lect_registro_id": UUID(int=5),
        "lect_version": 1,
        "motivo_correccion": None,
    }
    repo.filas_lecturas.append(original)

    detalle = await _revision(repo).obtener(AGRONOMO, VISI)

    assert [lect.lect_version for lect in detalle.lecturas] == [2, 1]
    assert len(detalle.versiones) == 1  # solo la lectura con más de una versión
    historial = detalle.versiones[0]
    assert historial.registro_id == REGISTRO
    assert [v.accion for v in historial.versiones] == [AccionSync.CREAR, AccionSync.CORREGIR]


@pytest.mark.regla
async def test_listar_revisiones_pasa_el_estado_y_calcula_advertencias() -> None:
    repo = RepoFalso()
    repo.filas_paginadas[0].update(lote_sin_asignacion=True, deriva_reloj_seg=-900)

    pagina = await _revision(repo).listar(AGRONOMO, EstadoRevision.EN_REVISION, None)

    assert _ultima(repo, "por_revisar")[2] == "en_revision"
    assert pagina.siguiente is None
    assert len(pagina.items[0].advertencias) == 2
    assert pagina.items[0].observada_el == VISITA["t_inicio_obs"]


@pytest.mark.regla
def test_advertencias_no_se_inventan_si_todo_esta_en_orden() -> None:
    assert advertencias_revision(deepcopy(VISITA), 300) == []
    fuera = deepcopy(VISITA) | {"trampa_fuera_del_lote": True, "deriva_reloj_seg": 301}
    assert len(advertencias_revision(fuera, 300)) == 2


# ------------------------------------------------------------------ visitas y lecturas
@pytest.mark.regla
async def test_el_tecnico_solo_consulta_sus_visitas_y_el_agronomo_las_de_sus_lotes() -> None:
    repo = RepoFalso()
    svc = _consulta(repo)

    await svc.visitas(TECNICO, None, None, None, None)
    assert _ultima(repo, "visitas")[2] is True
    await svc.visitas(AGRONOMO, 1, None, None, None)
    assert _ultima(repo, "visitas")[2] is False
    assert _ultima(repo, "visitas")[3]["pred_id"] == 1


@pytest.mark.regla
async def test_visitas_con_rango_invertido_da_422() -> None:
    with pytest.raises(ReglaNegocio):
        await _consulta(RepoFalso()).visitas(AGRONOMO, None, AHORA, AHORA - timedelta(days=1), None)


@pytest.mark.regla
async def test_detalle_de_visita_no_visible_da_404() -> None:
    repo = RepoFalso()
    repo.fila_visita = None
    with pytest.raises(NoEncontrado):
        await _consulta(repo).visita(TECNICO, VISI)


@pytest.mark.regla
async def test_detalle_de_visita_trae_sus_lecturas() -> None:
    detalle = await _consulta(RepoFalso()).visita(AGRONOMO, VISI)
    assert detalle.visi_id == VISI
    assert detalle.lecturas[0].especimenes_originales == 9


@pytest.mark.regla
async def test_lecturas_pasa_filtros_y_pagina_por_cursor() -> None:
    repo = RepoFalso()
    repo.filas_lecturas = [
        deepcopy(LECTURA) | {"lect_registro_id": UUID(int=i), "t_observacion": AHORA - timedelta(minutes=i)}
        for i in range(TAMANO_PAGINA + 1)
    ]
    svc = _consulta(repo)

    pagina = await svc.lecturas(AGRONOMO, 1, 1, None, None)

    assert len(pagina.items) == TAMANO_PAGINA
    assert pagina.siguiente is not None
    ultima = pagina.items[-1]
    assert decodificar(pagina.siguiente) == (ultima.t_observacion, ultima.lect_registro_id, None)

    await svc.lecturas(AGRONOMO, 1, 1, None, pagina.siguiente)
    assert _ultima(repo, "lecturas")[4] == decodificar(pagina.siguiente)


@pytest.mark.regla
async def test_versiones_conservan_la_original_y_la_correccion() -> None:
    historial = await _consulta(RepoFalso()).versiones_lectura(AGRONOMO, REGISTRO)

    assert historial.entidad == "lectura_trampa"
    assert [v.version for v in historial.versiones] == [1, 2]
    assert historial.versiones[0].valores == {"lect_especimenes": 9}
    assert historial.versiones[1].motivo == "Se contaron dos veces los mismos especímenes"


@pytest.mark.regla
async def test_versiones_de_una_anulacion_se_marcan_como_anular() -> None:
    repo = RepoFalso()
    repo.versiones_lectura[1].update(anulada=True, motivo="Lectura de una trampa que no existe")

    historial = await _consulta(repo).versiones_lectura(AGRONOMO, REGISTRO)
    assert historial.versiones[1].accion == AccionSync.ANULAR


@pytest.mark.regla
async def test_versiones_de_lectura_no_visible_da_404_sin_consultar_versiones() -> None:
    repo = RepoFalso()
    with pytest.raises(NoEncontrado) as exc:
        await _consulta(repo).versiones_lectura(TECNICO, UUID(int=99))
    assert exc.value.codigo == "LECTURA_NO_ENCONTRADA"
    assert not [c for c in repo.llamadas if c[0] == "versiones"]


# ------------------------------------------------------------------ actividad
@pytest.mark.regla
async def test_actividad_pasa_filtros_y_respeta_el_contrato() -> None:
    repo = RepoFalso()

    pagina = await _consulta(repo).actividad(
        AGRONOMO, 1, "vigilancia", Linaje.INSPECCION_OFICIAL, None, None, None
    )

    filtros = _ultima(repo, "actividad")[2]
    assert filtros["lote_id"] == 1
    assert filtros["tipo"] == "vigilancia"
    assert filtros["linaje"] == "inspeccion_oficial"
    assert pagina.items[0].titulo == "Visita de vigilancia"


@pytest.mark.regla
async def test_actividad_de_un_lote_no_visible_da_404() -> None:
    repo = RepoFalso()
    with pytest.raises(NoEncontrado) as exc:
        await _consulta(repo).actividad(TECNICO, 77, None, None, None, None, None)
    assert exc.value.codigo == "LOTE_NO_ENCONTRADO"
    assert not [c for c in repo.llamadas if c[0] == "actividad"]


@pytest.mark.regla
async def test_actividad_cursor_incluye_el_lote_para_desempatar() -> None:
    repo = RepoFalso()
    repo.actividad_filas = [deepcopy(ACTIVIDAD) | {"lote_id": i} for i in range(TAMANO_PAGINA + 1)]

    pagina = await _consulta(repo).actividad(AGRONOMO, None, None, None, None, None, None)

    assert pagina.siguiente is not None
    assert decodificar(pagina.siguiente) == (ACTIVIDAD["t_observacion"], VISI, TAMANO_PAGINA - 1)


@pytest.mark.regla
async def test_actividad_reciente_exige_lote_visible() -> None:
    repo = RepoFalso()
    recientes = await _consulta(repo).actividad_reciente(AGRONOMO, 1)
    assert recientes[0].tipo == "visita"
    with pytest.raises(NoEncontrado):
        await _consulta(repo).actividad_reciente(AGRONOMO, 2)


# ------------------------------------------------------------------ cursor
@pytest.mark.regla
def test_cursor_ida_y_vuelta() -> None:
    assert decodificar(None) is None
    assert decodificar(codificar(AHORA, VISI)) == (AHORA, VISI, None)
    assert decodificar(codificar(AHORA, VISI, 4)) == (AHORA, VISI, 4)


@pytest.mark.regla
@pytest.mark.parametrize("cursor", ["no-es-base64!!", codificar(AHORA, VISI)[:-6] + "zzzz", "e30"])
def test_cursor_manipulado_da_400(cursor: str) -> None:
    with pytest.raises(CursorInvalido) as exc:
        decodificar(cursor)
    assert exc.value.estado_http == 400
