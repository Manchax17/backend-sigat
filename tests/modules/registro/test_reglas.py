from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from sigat_api.compartido.contratos import OperacionCampo, PertenenciaTrampa
from sigat_api.compartido.errores import Conflicto, NoImplementado
from sigat_api.compartido.tipos import (
    AccionSync,
    Actor,
    ClasificacionOperacion,
    Cliente,
    EntidadSync,
    Linaje,
    ResultadoContraste,
    RolCodigo,
)
from sigat_api.modules.registro.dominio import (
    MENSAJES,
    EstadoPrevio,
    MotivoRechazo,
    clasificar,
    decidir,
    orden_canonico,
)
from sigat_api.modules.registro.mtd import EntradaMtd, calcular
from sigat_api.modules.registro.servicio import ServicioRegistro, ServicioRevision
from sigat_api.modules.registro.validador import ValidadorInvariantes

AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
DISP = UUID("0190f5b0-0000-7000-8000-0000000000aa")
USUA = 7
PREDIO = 3
LOTE = 42
TRAMPA = 11
VISITA = UUID("0190f5b0-0000-7000-8000-0000000000b1")


class RepoFalso:
    def __init__(self) -> None:
        self.lecturas: list[dict[str, Any]] = []
        self.visitas: list[tuple[dict[str, Any], list[int]]] = []
        self.previo = EstadoPrevio(None, None, None, False)
        self.anterior: dict[str, Any] | None = None
        self.periodo: dict[str, Any] | None = {"peri_id": 5, "peri_estado": "abierto"}

    async def estado_previo(
        self,
        entidad: str,
        registro_id: UUID,
        lote_id: int | None,
        tram_id: int | None,
        t_observacion: datetime,
    ) -> EstadoPrevio:
        return self.previo

    async def insertar_version(self, entidad: str, fila: dict[str, Any]) -> UUID:
        self.lecturas.append(fila)
        return uuid4()

    async def insertar_visita(self, fila: dict[str, Any], lote_ids: list[int]) -> None:
        self.visitas.append((fila, lote_ids))

    async def lectura_vigente(self, registro_id: UUID) -> dict[str, Any] | None:
        return self.anterior

    async def visita_existe(self, visi_id: UUID) -> bool:
        return True

    async def visita_lotes(self, visi_id: UUID) -> list[int]:
        return [LOTE]

    async def periodo_de(self, lote_id: int, fecha: date) -> dict[str, Any] | None:
        return self.periodo


class ConfiguracionFalsa:
    def __init__(self) -> None:
        self.pertenencia = PertenenciaTrampa(
            tram_id=TRAMPA, ubic_id=9, lote_id=LOTE, pred_id=PREDIO, lote_activo=True
        )
        self.visible = True
        self.lote_activo = True

    async def pertenencia_trampa(self, tram_id: int, instante: datetime) -> PertenenciaTrampa | None:
        return self.pertenencia

    async def lote_activo_en(self, lote_id: int, instante: datetime) -> bool:
        return self.lote_activo

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return self.visible

    async def lote_predio(self, lote_id: int) -> int | None:
        return PREDIO


class RelojFalso:
    def ahora(self) -> datetime:
        return AHORA


def _actor(rol: RolCodigo = RolCodigo.TECNICO) -> Actor:
    return Actor(usua_id=USUA, rol=rol, disp_id=DISP, cliente=Cliente.MOVIL)


def _operacion(
    *,
    entidad: EntidadSync = EntidadSync.LECTURA_TRAMPA,
    accion: AccionSync = AccionSync.CREAR,
    version: int = 1,
    datos: dict[str, Any] | None = None,
    t_observacion: datetime = AHORA,
    linaje: Linaje | None = Linaje.INSPECCION_OFICIAL,
) -> OperacionCampo:
    return OperacionCampo(
        oper_id=1,
        cliente_id=uuid4(),
        disp_id=DISP,
        entidad=entidad,
        accion=accion,
        registro_id=uuid4(),
        version=version,
        linaje=linaje,
        t_observacion=t_observacion,
        t_captura=t_observacion,
        deriva_reloj_seg=0,
        datos=datos or _datos_lectura(),
    )


def _datos_lectura(**cambios: Any) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "visi_id": str(VISITA),
        "tram_id": TRAMPA,
        "cond_id": 1,
        "lect_leida": True,
        "lect_especimenes": 4,
        "lect_justificacion": None,
    }
    datos.update(cambios)
    return datos


def _servicio(
    *,
    repo: RepoFalso | None = None,
    configuracion: ConfiguracionFalsa | None = None,
) -> tuple[ServicioRegistro, RepoFalso, ConfiguracionFalsa]:
    repo = repo or RepoFalso()
    configuracion = configuracion or ConfiguracionFalsa()
    validador = ValidadorInvariantes(configuracion, repo)  # type: ignore[arg-type]
    return (
        ServicioRegistro(
            repo,  # type: ignore[arg-type]
            validador,
            operaciones=None,  # type: ignore[arg-type]
            configuracion=configuracion,  # type: ignore[arg-type]
            reloj=RelojFalso(),
        ),
        repo,
        configuracion,
    )


def _previo(**cambios: Any) -> EstadoPrevio:
    valores: dict[str, Any] = {
        "version_vigente": None,
        "t_observacion_original": None,
        "ultima_t_observacion_serie": None,
        "hay_solapamiento": False,
    }
    valores.update(cambios)
    return EstadoPrevio(**valores)


def _reemplazar(previo: EstadoPrevio, **cambios: Any) -> EstadoPrevio:
    valores = {
        "version_vigente": previo.version_vigente,
        "t_observacion_original": previo.t_observacion_original,
        "ultima_t_observacion_serie": previo.ultima_t_observacion_serie,
        "hay_solapamiento": previo.hay_solapamiento,
    }
    valores.update(cambios)
    return EstadoPrevio(**valores)


@pytest.mark.regla
def test_registro_01() -> None:
    antes = AHORA - timedelta(hours=1)
    despues = AHORA + timedelta(minutes=1)
    tardia = orden_canonico(despues, AHORA, "a")
    temprana_con_captura_tardia = orden_canonico(antes, despues, "b")
    temprana_mismo_instante_a = orden_canonico(antes, AHORA, "a")
    temprana_mismo_instante_b = orden_canonico(antes, AHORA, "b")

    assert sorted(
        [tardia, temprana_con_captura_tardia, temprana_mismo_instante_b, temprana_mismo_instante_a]
    ) == [temprana_mismo_instante_a, temprana_mismo_instante_b, temprana_con_captura_tardia, tardia]
    assert len(tardia) == 3


@pytest.mark.regla
def test_registro_02() -> None:
    ultima = AHORA
    previo = _reemplazar(_previo(), ultima_t_observacion_serie=ultima)

    assert (
        clasificar(AccionSync.CREAR, 1, AHORA - timedelta(days=2), previo)
        is ClasificacionOperacion.ALTA_RETROACTIVA
    )
    assert (
        clasificar(AccionSync.CREAR, 1, AHORA + timedelta(minutes=1), previo)
        is ClasificacionOperacion.ALTA_NUEVA
    )


@pytest.mark.regla
def test_registro_03() -> None:
    previo = _reemplazar(_previo(), version_vigente=1, t_observacion_original=AHORA)

    assert clasificar(AccionSync.CORREGIR, 2, AHORA, previo) is ClasificacionOperacion.CORRECCION
    assert clasificar(AccionSync.CORREGIR, 3, AHORA, previo) is ClasificacionOperacion.RECHAZADA
    assert clasificar(AccionSync.CORREGIR, 2, AHORA, _previo()) is ClasificacionOperacion.RECHAZADA
    assert clasificar(AccionSync.ANULAR, 2, AHORA, previo) is ClasificacionOperacion.CORRECCION


@pytest.mark.regla
def test_registro_04() -> None:
    previo = _reemplazar(_previo(), version_vigente=1, t_observacion_original=AHORA)

    clasificacion, motivo = decidir(AccionSync.CORREGIR, 2, AHORA - timedelta(minutes=5), previo)

    assert clasificacion is ClasificacionOperacion.RECHAZADA
    assert motivo is MotivoRechazo.T_OBSERVACION_CAMBIADA
    assert MENSAJES[motivo] == ("La fecha de observación no se puede cambiar; anule y cree un registro nuevo")


@pytest.mark.regla
def test_registro_05() -> None:
    previo = _reemplazar(_previo(), hay_solapamiento=True)

    assert clasificar(AccionSync.CREAR, 1, AHORA, previo) is ClasificacionOperacion.REVISAR
    con_version = _reemplazar(previo, version_vigente=1, t_observacion_original=AHORA)
    assert clasificar(AccionSync.CORREGIR, 2, AHORA, con_version) is ClasificacionOperacion.REVISAR


@pytest.mark.regla
async def test_registro_06() -> None:
    validador = ValidadorInvariantes(ConfiguracionFalsa(), RepoFalso())  # type: ignore[arg-type]
    futura = _operacion(t_observacion=AHORA + timedelta(hours=1))

    assert await validador.validar(futura, AHORA, _actor()) is MotivoRechazo.FECHA_FUTURA
    casi_ahora = _operacion(t_observacion=AHORA + timedelta(minutes=4))
    assert await validador.validar(casi_ahora, AHORA, _actor()) is None


@pytest.mark.regla
async def test_registro_07() -> None:
    repo = RepoFalso()
    configuracion = ConfiguracionFalsa()
    validador = ValidadorInvariantes(configuracion, repo)  # type: ignore[arg-type]
    op = _operacion()

    configuracion.pertenencia = PertenenciaTrampa(
        tram_id=TRAMPA, ubic_id=None, lote_id=None, pred_id=PREDIO, lote_activo=True
    )
    assert await validador.validar(op, AHORA, _actor()) is MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE

    configuracion.pertenencia = PertenenciaTrampa(
        tram_id=TRAMPA, ubic_id=9, lote_id=LOTE + 1, pred_id=PREDIO, lote_activo=True
    )
    assert await validador.validar(op, AHORA, _actor()) is MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE


@pytest.mark.regla
async def test_registro_08() -> None:
    repo = RepoFalso()
    validador = ValidadorInvariantes(ConfiguracionFalsa(), repo)  # type: ignore[arg-type]

    sin_justificar = _operacion(
        datos=_datos_lectura(lect_leida=False, lect_especimenes=None, lect_justificacion="   ")
    )
    con_especimenes = _operacion(
        datos=_datos_lectura(lect_leida=False, lect_especimenes=0, lect_justificacion="Trampa caída")
    )
    correcta = _operacion(
        datos=_datos_lectura(lect_leida=False, lect_especimenes=None, lect_justificacion="Trampa caída")
    )

    assert await validador.validar(sin_justificar, AHORA, _actor()) is MotivoRechazo.VALOR_NO_VALIDO
    assert await validador.validar(con_especimenes, AHORA, _actor()) is MotivoRechazo.VALOR_NO_VALIDO
    assert await validador.validar(correcta, AHORA, _actor()) is None


async def test_visita_y_lectura_se_persisten_con_el_actor_del_token() -> None:
    servicio, repo, _ = _servicio()
    visita = _operacion(
        entidad=EntidadSync.VISITA,
        datos={
            "pred_id": PREDIO,
            "lote_ids": [LOTE],
            "visi_t_inicio_obs": (AHORA - timedelta(hours=2)).isoformat(),
            "visi_t_fin_obs": AHORA.isoformat(),
            "ruta_id": None,
        },
        linaje=None,
    )

    resultado_visita = await servicio.aplicar(_actor(), visita)
    resultado_lectura = await servicio.aplicar(_actor(), _operacion())

    assert resultado_visita.clasificacion is ClasificacionOperacion.ALTA_NUEVA
    fila_visita, lotes = repo.visitas[0]
    assert fila_visita["usua_id"] == USUA
    assert fila_visita["disp_id"] == DISP
    assert fila_visita["oper_id"] == visita.oper_id
    assert lotes == [LOTE]

    assert resultado_lectura.clasificacion is ClasificacionOperacion.ALTA_NUEVA
    fila_lectura = repo.lecturas[0]
    assert fila_lectura["lect_linaje"] is Linaje.INSPECCION_OFICIAL
    assert fila_lectura["lect_anulada"] is False
    assert fila_lectura["ubic_id"] == 9
    assert fila_lectura["lect_t_observacion"] == AHORA


async def test_correccion_parcial_conserva_los_datos_de_la_version_anterior() -> None:
    repo = RepoFalso()
    repo.previo = EstadoPrevio(1, AHORA, AHORA, False)
    repo.anterior = {
        "visi_id": VISITA,
        "tram_id": TRAMPA,
        "ubic_id": 9,
        "lect_leida": True,
        "lect_especimenes": 4,
        "lect_justificacion": None,
        "cond_id": 1,
        "lect_observaciones": "Primera visita",
    }
    servicio, _, _ = _servicio(repo=repo)

    resultado = await servicio.aplicar(
        _actor(),
        _operacion(
            accion=AccionSync.CORREGIR,
            version=2,
            datos=_datos_lectura(lect_especimenes=6, lect_motivo_correccion="Recuento"),
        ),
    )

    assert resultado.clasificacion is ClasificacionOperacion.CORRECCION
    fila = repo.lecturas[0]
    assert fila["lect_version"] == 2
    assert fila["lect_especimenes"] == 6
    assert fila["lect_observaciones"] == "Primera visita"


async def test_anulacion_marca_la_lectura_sin_borrar_la_anterior() -> None:
    repo = RepoFalso()
    repo.previo = EstadoPrevio(1, AHORA, AHORA, False)
    repo.anterior = {
        "visi_id": VISITA,
        "tram_id": TRAMPA,
        "ubic_id": 9,
        "lect_leida": True,
        "lect_especimenes": 4,
        "cond_id": 1,
    }
    servicio, _, _ = _servicio(repo=repo)

    await servicio.aplicar(
        _actor(),
        _operacion(
            accion=AccionSync.ANULAR,
            version=2,
            datos=_datos_lectura(lect_motivo_anulacion="Trampa mal identificada"),
        ),
    )

    fila = repo.lecturas[0]
    assert fila["lect_version"] == 2
    assert fila["lect_anulada"] is True
    assert fila["lect_motivo_anulacion"] == "Trampa mal identificada"
    assert len(repo.lecturas) == 1


async def test_visita_en_lote_no_visible_se_rechaza_sin_tocar_el_historico() -> None:
    configuracion = ConfiguracionFalsa()
    configuracion.visible = False
    servicio, repo, _ = _servicio(configuracion=configuracion)

    resultado = await servicio.aplicar(
        _actor(),
        _operacion(
            entidad=EntidadSync.VISITA,
            datos={
                "pred_id": PREDIO,
                "lote_ids": [LOTE],
                "visi_t_inicio_obs": AHORA.isoformat(),
                "visi_t_fin_obs": AHORA.isoformat(),
            },
            linaje=None,
        ),
    )

    assert resultado.clasificacion is ClasificacionOperacion.RECHAZADA
    assert resultado.codigo == MotivoRechazo.SIN_PERMISO_SOBRE_LOTE.value
    assert repo.visitas == []


async def test_lectura_sin_periodo_es_fuera_de_periodo() -> None:
    repo = RepoFalso()
    repo.periodo = None
    servicio, _, _ = _servicio(repo=repo)

    resultado = await servicio.aplicar(_actor(), _operacion())

    assert resultado.clasificacion is ClasificacionOperacion.RECHAZADA
    assert resultado.codigo == MotivoRechazo.FUERA_DE_PERIODO.value
    assert repo.lecturas == []


async def test_lectura_de_productor_es_autorreporte() -> None:
    servicio, repo, _ = _servicio()

    resultado = await servicio.aplicar(_actor(RolCodigo.PRODUCTOR), _operacion(linaje=None))

    assert resultado.clasificacion is ClasificacionOperacion.ALTA_NUEVA
    assert repo.lecturas[0]["lect_linaje"] is Linaje.AUTORREPORTE


AGRONOMO = Actor(usua_id=3, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
T_OBS = datetime(2026, 11, 10, 8, 10, tzinfo=UTC)
VISI = UUID("0190f0d0-0000-7000-8000-000000000001")
REGISTRO = UUID("0190f0d0-0000-7000-8000-0000000000aa")
CLIENTE_ID = UUID("0190f0d0-0000-7000-8000-0000000000c1")
DISP_ID = UUID("0190f0d0-0000-7000-8000-0000000000d2")


def _el(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 11, dia, hora, minuto, tzinfo=UTC)


VERSIONES: list[dict[str, Any]] = [
    {
        "version": 1,
        "anulada": False,
        "motivo": None,
        "autor": "Técnico Prueba",
        "usua_id": 2,
        "t_sincronizacion": _el(10, 12),
        "t_observacion": T_OBS,
        "valores": {"lect_especimenes": 9},
    },
    {
        "version": 2,
        "anulada": False,
        "motivo": "Se contaron dos veces los mismos especímenes",
        "autor": "Agrónomo Prueba",
        "usua_id": 3,
        "t_sincronizacion": _el(11, 10),
        "t_observacion": T_OBS,
        "valores": {"lect_especimenes": 7},
    },
]

VISITA_ACEPTADA: dict[str, Any] = {
    "visi_id": VISI,
    "usua_id": 2,
    "tecnico": "Técnico Prueba",
    "pred_id": 1,
    "pred_nombre": "Finca de demostración",
    "ruta_id": None,
    "t_inicio_obs": _el(10, 8),
    "t_fin_obs": _el(10, 9),
    "t_captura": _el(10, 9, 5),
    "t_sincronizacion": _el(10, 12),
    "deriva_reloj_seg": 4,
    "estado_revision": "aceptado",
    "motivo_revision": "Duplicada con la visita del martes",
    "revisada_por": 3,
    "revisada_en": AHORA,
    "lote_ids": [1],
    "lotes": ["01"],
    "trampas_leidas": 1,
    "lote_sin_asignacion": False,
    "trampa_fuera_del_lote": False,
}


class _RepoVersiones:
    """Solo lo que ``versiones_lectura`` necesita: visibilidad + versiones append-only."""

    def __init__(self, versiones: list[dict[str, Any]]) -> None:
        self.filas = deepcopy(versiones)
        self.consultadas: list[tuple[str, UUID]] = []

    async def lectura_visible(self, usua_id: int, solo_propias: bool, registro_id: UUID) -> bool:
        return registro_id == REGISTRO

    async def versiones(self, entidad: str, registro_id: UUID) -> list[dict[str, Any]]:
        self.consultadas.append((entidad, registro_id))
        return deepcopy(self.filas)


class _RepoRevision:
    """Una visita ya aceptada: ``aceptar`` debe dar 409 sin escribir."""

    def __init__(self) -> None:
        self.fila = deepcopy(VISITA_ACEPTADA)
        self.llamadas: list[tuple[Any, ...]] = []

    async def registro_revision(self, usua_id: int, visi_id: UUID, bloquear: bool = False):
        self.llamadas.append(("registro_revision", visi_id, bloquear))
        return deepcopy(self.fila) if visi_id == VISI else None

    async def actualizar_revision_visita(
        self, visi_id: UUID, estado: str, motivo: str | None, usua_id: int, en: datetime
    ) -> None:
        self.llamadas.append(("actualizar", visi_id, estado))

    async def lecturas_de_visita(self, visi_id: UUID):
        return []

    async def versiones(self, entidad: str, registro_id: UUID):
        return []


class _ConfigFalsa:
    """Puerto de configuración: registra si el validador llegó a consultarlo."""

    def __init__(self, pertenencia: PertenenciaTrampa | None) -> None:
        self.pertenencia = pertenencia
        self.llamadas: list[tuple[int, datetime]] = []

    async def pertenencia_trampa(self, tram_id: int, instante: datetime) -> PertenenciaTrampa | None:
        self.llamadas.append((tram_id, instante))
        return self.pertenencia

    async def lote_activo_en(self, lote_id: int, instante: datetime) -> bool:
        return True


class _RelojFijo:
    def ahora(self) -> datetime:
        return AHORA


def test_registro_09() -> None:
    """MTD = M/(T×D) y T×D=0 da nulo.

    La fórmula no está programada en ``mtd.calcular`` (esqueleto): tripwire. Cuando se
    implemente, este test debe afirmar 12/(3×4)=1.0 y valor None con T×D=0.
    """
    entrada = EntradaMtd(moscas=12, trampas=3, dias_exposicion=Decimal("4"), umbral=Decimal("2.5"))

    with pytest.raises(NoImplementado) as exc:
        calcular(entrada)

    assert exc.value.estado_http == 501
    assert exc.value.codigo == "NO_IMPLEMENTADO"
    assert "Cálculo del MTD" in exc.value.detalle


@pytest.mark.regla
def test_registro_10() -> None:
    """sin umbral vigente a la fecha del periodo, el contraste es sin_umbral (concepto no_emitido).

    El cálculo vive en ``mtd.calcular`` (esqueleto): tripwire + tokens del contrato de contraste.
    """
    sin_umbral = EntradaMtd(moscas=12, trampas=3, dias_exposicion=Decimal("4"), umbral=None)

    with pytest.raises(NoImplementado) as exc:
        calcular(sin_umbral)

    assert "Cálculo del MTD" in exc.value.detalle
    # tokens que la regla debe devolver (d_resultado_contraste, RF-024)
    assert ResultadoContraste.SIN_UMBRAL.value == "sin_umbral"
    assert ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL.value == "igual_o_superior_umbral"


# ------------------------------------------------------------------ revisión
@pytest.mark.regla
async def test_registro_11() -> None:
    """aceptar una revisión ya aceptada da 409 REVISION_YA_RESUELTA y no vuelve a escribir."""
    repo = _RepoRevision()

    with pytest.raises(Conflicto) as exc:
        await ServicioRevision(repo, _RelojFijo()).aceptar(AGRONOMO, VISI)  # type: ignore[arg-type]

    assert exc.value.estado_http == 409
    assert exc.value.codigo == "REVISION_YA_RESUELTA"
    assert "ya fue aceptada" in exc.value.detalle
    assert ("registro_revision", VISI, True) in repo.llamadas  # bloqueó la fila antes de decidir
    assert not [c for c in repo.llamadas if c[0] == "actualizar"]  # no sobrescribió la decisión
