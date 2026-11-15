"""Reglas del núcleo de registro (motor temporal, invariantes y MTD).

Cada prueba cubre una regla de negocio y cuenta para la cobertura de RNF-017.
Sin base de datos: repositorio y configuración se sustituyen por dobles.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from sigat_api.compartido.contratos import (
    OperacionCampo,
    PertenenciaTrampa,
    ResultadoAplicacion,
)
from sigat_api.compartido.errores import Conflicto
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
from sigat_api.modules.registro.mtd import EntradaMtd, calcular, redondear_dias
from sigat_api.modules.registro.servicio import ServicioRegistro
from sigat_api.modules.registro.validador import ValidadorInvariantes
from sigat_api.modules.sincronizacion import esquemas as sync_e
from sigat_api.modules.sincronizacion.servicio import ServicioSincronizacion

AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
DISP = UUID("0190f5b0-0000-7000-8000-0000000000aa")
USUA = 7
PREDIO = 3
LOTE = 42
TRAMPA = 11
VISITA = UUID("0190f5b0-0000-7000-8000-0000000000b1")


# --------------------------------------------------------------------- dobles
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


# --------------------------------------------------------------------- R-T1…R-T5
@pytest.mark.regla
def test_registro_01() -> None:
    """R-T1 orden canónico por t_observacion y desempate (t_captura, id)."""
    antes = AHORA - timedelta(hours=1)
    despues = AHORA + timedelta(minutes=1)
    tardia = orden_canonico(despues, AHORA, "a")
    temprana_con_captura_tardia = orden_canonico(antes, despues, "b")
    temprana_mismo_instante_a = orden_canonico(antes, AHORA, "a")
    temprana_mismo_instante_b = orden_canonico(antes, AHORA, "b")

    assert sorted(
        [tardia, temprana_con_captura_tardia, temprana_mismo_instante_b, temprana_mismo_instante_a]
    ) == [temprana_mismo_instante_a, temprana_mismo_instante_b, temprana_con_captura_tardia, tardia]
    # La hora de llegada (t_sincronizacion) no participa: no aparece en la tupla canónica.
    assert len(tardia) == 3


@pytest.mark.regla
def test_registro_02() -> None:
    """R-T2 alta con t_observacion anterior a la última es alta_retroactiva."""
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
    """R-T3 corrección crea versión n+1 y conserva la anterior."""
    previo = _reemplazar(_previo(), version_vigente=1, t_observacion_original=AHORA)

    assert clasificar(AccionSync.CORREGIR, 2, AHORA, previo) is ClasificacionOperacion.CORRECCION
    # Saltarse una versión o corregir algo que no existe no se aplica.
    assert clasificar(AccionSync.CORREGIR, 3, AHORA, previo) is ClasificacionOperacion.RECHAZADA
    assert clasificar(AccionSync.CORREGIR, 2, AHORA, _previo()) is ClasificacionOperacion.RECHAZADA
    assert clasificar(AccionSync.ANULAR, 2, AHORA, previo) is ClasificacionOperacion.CORRECCION


@pytest.mark.regla
def test_registro_04() -> None:
    """ADR-02 corrección que cambia t_observacion se rechaza."""
    previo = _reemplazar(_previo(), version_vigente=1, t_observacion_original=AHORA)

    clasificacion, motivo = decidir(AccionSync.CORREGIR, 2, AHORA - timedelta(minutes=5), previo)

    assert clasificacion is ClasificacionOperacion.RECHAZADA
    assert motivo is MotivoRechazo.T_OBSERVACION_CAMBIADA
    assert MENSAJES[motivo] == ("La fecha de observación no se puede cambiar; anule y cree un registro nuevo")


@pytest.mark.regla
def test_registro_05() -> None:
    """R-T5 solapamiento queda en revisar y no elige ganador."""
    previo = _reemplazar(_previo(), hay_solapamiento=True)

    assert clasificar(AccionSync.CREAR, 1, AHORA, previo) is ClasificacionOperacion.REVISAR
    con_version = _reemplazar(previo, version_vigente=1, t_observacion_original=AHORA)
    assert clasificar(AccionSync.CORREGIR, 2, AHORA, con_version) is ClasificacionOperacion.REVISAR


# --------------------------------------------------------------------- validador
@pytest.mark.regla
async def test_registro_06() -> None:
    """Fecha de observación futura (más allá de la tolerancia) se rechaza."""
    validador = ValidadorInvariantes(ConfiguracionFalsa(), RepoFalso())  # type: ignore[arg-type]
    futura = _operacion(t_observacion=AHORA + timedelta(hours=1))

    assert await validador.validar(futura, AHORA, _actor()) is MotivoRechazo.FECHA_FUTURA
    # Cinco minutos de adelanto del teléfono siguen siendo aceptables.
    casi_ahora = _operacion(t_observacion=AHORA + timedelta(minutes=4))
    assert await validador.validar(casi_ahora, AHORA, _actor()) is None


@pytest.mark.regla
async def test_registro_07() -> None:
    """Trampa que no pertenecía al lote en t_observacion se rechaza."""
    repo = RepoFalso()
    configuracion = ConfiguracionFalsa()
    validador = ValidadorInvariantes(configuracion, repo)  # type: ignore[arg-type]
    op = _operacion()

    configuracion.pertenencia = PertenenciaTrampa(
        tram_id=TRAMPA, ubic_id=None, lote_id=None, pred_id=PREDIO, lote_activo=True
    )
    assert await validador.validar(op, AHORA, _actor()) is MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE

    # Ubicada en un lote activo, pero la visita no cubrió ese lote.
    configuracion.pertenencia = PertenenciaTrampa(
        tram_id=TRAMPA, ubic_id=9, lote_id=LOTE + 1, pred_id=PREDIO, lote_activo=True
    )
    assert await validador.validar(op, AHORA, _actor()) is MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE


@pytest.mark.regla
async def test_registro_08() -> None:
    """Lectura no leída exige justificación y especímenes nulos."""
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


@pytest.mark.regla
def test_registro_09() -> None:
    """MTD = M/(T×D) y T×D=0 da nulo."""
    con_umbral = calcular(
        EntradaMtd(moscas=30, trampas=3, dias_exposicion=Decimal("10"), umbral=Decimal("0.5000"))
    )

    assert con_umbral.valor == Decimal("1.000000")
    assert con_umbral.contraste is ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL

    bajo = calcular(EntradaMtd(moscas=14, trampas=4, dias_exposicion=Decimal("7"), umbral=Decimal("1")))
    assert bajo.valor == Decimal("0.500000")
    assert bajo.contraste is ResultadoContraste.POR_DEBAJO_UMBRAL

    # Sin trampas o sin días de exposición no hay índice que contrastar.
    for entrada in (
        EntradaMtd(moscas=5, trampas=0, dias_exposicion=Decimal("3"), umbral=None),
        EntradaMtd(moscas=5, trampas=2, dias_exposicion=Decimal("0"), umbral=None),
    ):
        salida = calcular(entrada)
        assert salida.valor is None
        assert salida.contraste is ResultadoContraste.SIN_UMBRAL

    # indi_dias_exposicion es numeric(8,3): el promedio se redondea a 3 decimales.
    assert redondear_dias(Decimal("10") / Decimal("3")) == Decimal("3.333")


@pytest.mark.regla
def test_registro_10() -> None:
    """Sin umbral vigente a la fecha del periodo el contraste es sin_umbral."""
    salida = calcular(EntradaMtd(moscas=30, trampas=3, dias_exposicion=Decimal("10"), umbral=None))

    assert salida.valor == Decimal("1.000000")
    assert salida.contraste is ResultadoContraste.SIN_UMBRAL


# --------------------------------------------------------------------- servicio
@pytest.mark.regla
async def test_registro_11() -> None:
    """Resolver una operación ya resuelta da 409 (revisión no se aplica dos veces)."""
    revisada = {
        "oper_id": 9,
        "disp_id": DISP,
        "oper_cliente_id": uuid4(),
        "usua_id": USUA,
        "oper_entidad": "lectura_trampa",
        "oper_accion": "crear",
        "oper_registro_id": uuid4(),
        "oper_version": 1,
        "oper_hash_contenido": "0" * 64,
        "oper_clasificacion": "revisar",
        "oper_motivo": "solapamiento",
        "oper_t_captura": AHORA,
        "oper_deriva_reloj_seg": 0,
        "oper_payload": {"lect_leida": True},
    }

    class RepoResolucion(RepoFalso):
        async def operacion(self, oper_id: int) -> dict[str, Any] | None:
            return revisada

        async def ya_resuelta(self, oper_id: int) -> bool:
            return True

        @asynccontextmanager
        async def punto_de_guarda(self) -> AsyncIterator[None]:
            yield

        async def insertar_operacion(self, fila: dict[str, Any]) -> int:
            raise AssertionError("No se debe insertar una resolución duplicada")

    class RegistroFalso:
        async def aplicar(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion:
            raise AssertionError("No se debe aplicar una resolución duplicada")

    class ColaFalsa:
        async def encolar(self, tarea: str, datos: dict[str, Any], *, clave_unica: str | None = None) -> None:
            raise AssertionError("No se debe encolar")

    servicio = ServicioSincronizacion(
        RepoResolucion(),  # type: ignore[arg-type]
        registro=RegistroFalso(),
        planificacion=None,  # type: ignore[arg-type]
        cola=ColaFalsa(),
        reloj=RelojFalso(),
        cfg=None,  # type: ignore[arg-type]
    )

    with pytest.raises(Conflicto) as error:
        await servicio.resolver(
            _actor(), 9, sync_e.ResolucionOperacion(decision="confirmar", motivo="Se verificó en campo")
        )

    assert error.value.estado_http == 409
    assert error.value.codigo == "REVISION_YA_RESUELTA"


# --------------------------------------------------------------------- vertical
async def test_visita_y_lectura_se_persisten_con_el_actor_del_token() -> None:
    """El vertical del sprint: una visita y su lectura se insertan en la misma transacción."""
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
    assert fila_lectura["ubic_id"] == 9  # la ubicación vigente la pone el servidor
    assert fila_lectura["lect_t_observacion"] == AHORA
    assert resultado_lectura.periodos_afectados == [5]
    assert resultado_lectura.fecha_efecto == AHORA


async def test_correccion_parcial_conserva_los_datos_de_la_version_anterior() -> None:
    """Una corrección solo trae lo que cambió; el resto se hereda de la versión vigente."""
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
    assert fila["lect_observaciones"] == "Primera visita"  # heredado, no se pierde


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
    assert len(repo.lecturas) == 1  # la versión 1 nunca se toca: tabla append-only


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


async def test_lectura_de_productor_es_autorreporte_y_no_alimenta_el_mtd() -> None:
    servicio, repo, _ = _servicio()

    resultado = await servicio.aplicar(_actor(RolCodigo.PRODUCTOR), _operacion(linaje=None))

    assert resultado.clasificacion is ClasificacionOperacion.ALTA_NUEVA
    assert repo.lecturas[0]["lect_linaje"] is Linaje.AUTORREPORTE
    assert resultado.periodos_afectados == []
