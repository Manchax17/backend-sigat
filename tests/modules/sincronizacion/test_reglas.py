"""Reglas del módulo sincronización: recepción idempotente y respuesta por operación."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from sigat_api.compartido.contratos import ResultadoAplicacion
from sigat_api.compartido.errores import Prohibido
from sigat_api.compartido.tipos import (
    AccionSync,
    Actor,
    ClasificacionOperacion,
    Cliente,
    EntidadSync,
    ResultadoSync,
    RolCodigo,
)
from sigat_api.config import Configuracion
from sigat_api.infra.cola.cola import TAREA_REPROCESAR_PERIODO
from sigat_api.modules.sincronizacion import esquemas as e
from sigat_api.modules.sincronizacion import huella
from sigat_api.modules.sincronizacion.servicio import ServicioSincronizacion

AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
DISP = UUID("0190f5b0-0000-7000-8000-0000000000aa")
USUA = 7


class RepoFalso:
    """Repositorio en memoria con SAVEPOINT real (snapshot) para probar la reclasificación."""

    def __init__(self, *, activo: bool = True) -> None:
        self.activo = activo
        self.ops: dict[int, dict[str, Any]] = {}
        self.reenvios: list[tuple[int, str, str]] = []
        self._siguiente = 1

    async def dispositivo(self, disp_id: UUID) -> dict[str, Any] | None:
        if not self.activo:
            return {"disp_id": disp_id, "disp_activo": False, "disp_plataforma": "android"}
        return {"disp_id": disp_id, "disp_activo": True, "disp_plataforma": "android"}

    async def operacion_existente(self, disp_id: UUID, cliente_id: UUID) -> dict[str, Any] | None:
        for fila in self.ops.values():
            if fila["disp_id"] == disp_id and fila["cliente_id"] == cliente_id:
                return {
                    "oper_id": fila["oper_id"],
                    "oper_registro_id": fila["registro_id"],
                    "oper_version": fila["version"],
                    "oper_hash_contenido": fila["hash"],
                    "oper_clasificacion": str(fila["clasificacion"]),
                    "oper_motivo": fila["motivo"],
                }
        return None

    async def registrar_reenvio(self, oper_id: int, hash_hex: str, resultado: str) -> None:
        self.reenvios.append((oper_id, hash_hex, resultado))

    async def insertar_operacion(self, fila: dict[str, Any]) -> int:
        oper_id = self._siguiente
        self._siguiente += 1
        self.ops[oper_id] = {**fila, "oper_id": oper_id, "t_recepcion": AHORA}
        return oper_id

    @asynccontextmanager
    async def punto_de_guarda(self) -> AsyncIterator[None]:
        ops = {clave: dict(fila) for clave, fila in self.ops.items()}
        reenvios = list(self.reenvios)
        siguiente = self._siguiente
        try:
            yield
        except Exception:
            self.ops = ops
            self.reenvios = reenvios
            self._siguiente = siguiente
            raise

    async def pendientes_de_usuario(self, usua_id: int, clasificaciones: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "oper_id": fila["oper_id"],
                "cliente_id": fila["cliente_id"],
                "entidad": fila["entidad"],
                "accion": fila["accion"],
                "registro_id": fila["registro_id"],
                "version": fila["version"],
                "clasificacion": str(fila["clasificacion"]),
                "motivo": fila["motivo"],
                "t_captura": fila["t_captura"],
                "t_recepcion": fila["t_recepcion"],
                "datos": fila["payload"] or {},
            }
            for fila in self.ops.values()
            if fila["usua_id"] == usua_id and str(fila["clasificacion"]) in clasificaciones
        ]


class RepoQueFallaUnaVez(RepoFalso):
    def __init__(self) -> None:
        super().__init__()
        self.fallo = False

    async def insertar_operacion(self, fila: dict[str, Any]) -> int:
        if not self.fallo:
            self.fallo = True
            raise RuntimeError("fallo técnico simulado")
        return await super().insertar_operacion(fila)


class RegistroFalso:
    def __init__(self, resultados: list[ResultadoAplicacion] | None = None) -> None:
        self.resultados = resultados or [ResultadoAplicacion(ClasificacionOperacion.ALTA_NUEVA)]
        self.llamadas: list[Any] = []

    async def aplicar(self, actor: Actor, op: Any) -> ResultadoAplicacion:
        self.llamadas.append(op)
        return self.resultados[min(len(self.llamadas) - 1, len(self.resultados) - 1)]


class PlanificacionFalsa:
    async def aplicar_resultado_lote(self, actor: Actor, op: Any) -> ResultadoAplicacion:
        raise AssertionError("La prueba no debería delegar en planificación")


class ColaFalsa:
    def __init__(self) -> None:
        self.tareas: list[tuple[str, dict[str, Any], str | None]] = []

    async def encolar(self, tarea: str, datos: dict[str, Any], *, clave_unica: str | None = None) -> None:
        self.tareas.append((tarea, datos, clave_unica))


class RelojFalso:
    def __init__(self, ahora: datetime = AHORA) -> None:
        self._ahora = ahora

    def ahora(self) -> datetime:
        return self._ahora


def _actor(disp_id: UUID = DISP) -> Actor:
    return Actor(usua_id=USUA, rol=RolCodigo.TECNICO, disp_id=disp_id, cliente=Cliente.MOVIL)


def _op(
    *,
    entidad: EntidadSync = EntidadSync.VISITA,
    accion: AccionSync = AccionSync.CREAR,
    cliente_id: UUID | None = None,
    version: int = 1,
    datos: dict[str, Any] | None = None,
    deriva: int | None = None,
    confirma: bool = False,
) -> e.OperacionSync:
    borrador = e.OperacionSync(
        cliente_id=cliente_id or uuid4(),
        entidad=entidad,
        accion=accion,
        registro_id=uuid4(),
        version=version,
        linaje=None,
        t_observacion=AHORA,
        t_captura=AHORA,
        deriva_reloj_seg=deriva,
        confirma_deriva_reloj=confirma,
        hash_contenido="0" * 64,
        datos=datos or {"pred_id": 1},
    )
    return borrador.model_copy(
        update={"hash_contenido": huella.calcular(huella.contenido_canonico(borrador))}
    )


def _lote(*ops: e.OperacionSync, enviado_en: datetime = AHORA) -> e.LoteSincronizacion:
    return e.LoteSincronizacion(disp_id=DISP, enviado_en=enviado_en, operaciones=list(ops))


def _servicio(
    *,
    repo: RepoFalso | None = None,
    registro: RegistroFalso | None = None,
    cola: ColaFalsa | None = None,
) -> tuple[ServicioSincronizacion, RepoFalso, RegistroFalso, ColaFalsa]:
    repo = repo or RepoFalso()
    registro = registro or RegistroFalso()
    cola = cola or ColaFalsa()
    servicio = ServicioSincronizacion(
        repo=repo,  # type: ignore[arg-type]
        registro=registro,
        planificacion=PlanificacionFalsa(),
        cola=cola,
        reloj=RelojFalso(),
        cfg=Configuracion(sync_umbral_deriva_reloj_seg=300),
    )
    return servicio, repo, registro, cola


@pytest.mark.regla
async def test_sincronizacion_01() -> None:
    """R-T4 mismo cliente_id y mismo hash es idempotente_repetida sin duplicar."""
    svc, repo, registro, _ = _servicio()
    op = _op()

    primera = await svc.recibir(_actor(), _lote(op))
    segunda = await svc.recibir(_actor(), _lote(op))

    assert primera.resultados[0].resultado is ResultadoSync.ALTA_NUEVA
    assert segunda.resultados[0].resultado is ResultadoSync.IDEMPOTENTE_REPETIDA
    assert segunda.resultados[0].oper_id == primera.resultados[0].oper_id
    assert len(repo.ops) == 1
    assert len(registro.llamadas) == 1
    assert repo.reenvios == [(primera.resultados[0].oper_id, op.hash_contenido, "idempotente_repetida")]


@pytest.mark.regla
async def test_sincronizacion_02() -> None:
    """mismo cliente_id con hash distinto es conflicto_contenido."""
    svc, repo, _, _ = _servicio()
    cliente_id = uuid4()
    op = _op(cliente_id=cliente_id, datos={"pred_id": 1})
    otra = _op(cliente_id=cliente_id, datos={"pred_id": 2})

    await svc.recibir(_actor(), _lote(op))
    respuesta = await svc.recibir(_actor(), _lote(otra))

    assert respuesta.resultados[0].resultado is ResultadoSync.CONFLICTO_CONTENIDO
    assert respuesta.resultados[0].reintentable is False
    assert respuesta.resultados[0].codigo == "CONFLICTO_CONTENIDO"
    assert len(repo.ops) == 1
    assert repo.reenvios[-1][1:] == (otra.hash_contenido, "conflicto_contenido")


@pytest.mark.regla
async def test_sincronizacion_03() -> None:
    """lote parcialmente aceptado responde por operación."""
    registro = RegistroFalso(
        [
            ResultadoAplicacion(ClasificacionOperacion.RECHAZADA, motivo="sin permiso", codigo="SIN_PERMISO"),
            ResultadoAplicacion(ClasificacionOperacion.ALTA_NUEVA),
        ]
    )
    svc, repo, _, _ = _servicio(registro=registro)

    respuesta = await svc.recibir(_actor(), _lote(_op(), _op()))

    assert [r.resultado for r in respuesta.resultados] == [
        ResultadoSync.RECHAZADA,
        ResultadoSync.ALTA_NUEVA,
    ]
    assert all(not r.reintentable for r in respuesta.resultados)
    assert [str(f["clasificacion"]) for f in repo.ops.values()] == ["rechazada", "alta_nueva"]


@pytest.mark.regla
async def test_sincronizacion_04() -> None:
    """revisar/rechazada tienen reintentable=false."""
    svc, _, _, _ = _servicio()
    con_deriva = _op(deriva=600)
    no_leida_sin_justificar = _op(
        entidad=EntidadSync.LECTURA_TRAMPA,
        datos={"lect_leida": False, "lect_especimenes": None},
    )

    respuesta = await svc.recibir(_actor(), _lote(con_deriva, no_leida_sin_justificar))

    assert respuesta.resultados[0].resultado is ResultadoSync.REVISAR
    assert respuesta.resultados[0].reintentable is False
    assert respuesta.resultados[1].resultado is ResultadoSync.RECHAZADA
    assert respuesta.resultados[1].reintentable is False
    assert respuesta.resultados[1].codigo == "VALOR_NO_VALIDO"


@pytest.mark.regla
async def test_sincronizacion_05() -> None:
    """R-T6 deriva de reloj sin confirmación queda en revisar."""
    svc, repo, registro, _ = _servicio()
    op = _op()
    lote = _lote(op, enviado_en=AHORA + timedelta(minutes=10))

    respuesta = await svc.recibir(_actor(), lote)
    confirmada = await svc.recibir(_actor(), _lote(_op(deriva=600, confirma=True, cliente_id=uuid4())))

    assert respuesta.resultados[0].resultado is ResultadoSync.REVISAR
    assert respuesta.resultados[0].codigo == "DERIVA_RELOJ"
    assert next(iter(repo.ops.values()))["clasificacion"] is ClasificacionOperacion.REVISAR
    assert next(iter(repo.ops.values()))["payload"] == op.datos
    assert len(registro.llamadas) == 1
    assert confirmada.resultados[0].resultado is ResultadoSync.ALTA_NUEVA


@pytest.mark.regla
async def test_sincronizacion_06() -> None:
    """disp_id distinto al del token rechaza el lote con 403."""
    svc, _, _, _ = _servicio()

    with pytest.raises(Prohibido) as distinto:
        await svc.recibir(_actor(disp_id=uuid4()), _lote(_op()))
    assert distinto.value.estado_http == 403

    inactivo, _, _, _ = _servicio(repo=RepoFalso(activo=False))
    with pytest.raises(Prohibido) as dado_de_baja:
        await inactivo.recibir(_actor(), _lote(_op()))
    assert dado_de_baja.value.codigo == "DISPOSITIVO_INACTIVO"


@pytest.mark.regla
async def test_sincronizacion_07() -> None:
    """alta retroactiva encola reproceso con clave única por periodo."""
    fecha_efecto = AHORA - timedelta(days=3)
    registro = RegistroFalso(
        [
            ResultadoAplicacion(
                ClasificacionOperacion.ALTA_RETROACTIVA,
                periodos_afectados=[10, 10, 11],
                fecha_efecto=fecha_efecto,
            )
        ]
    )
    svc, repo, _, cola = _servicio(registro=registro)

    respuesta = await svc.recibir(_actor(), _lote(_op()))

    assert respuesta.resultados[0].resultado is ResultadoSync.ALTA_RETROACTIVA
    assert respuesta.resultados[0].reproceso == "encolado"
    assert respuesta.resultados[0].fecha_efecto == fecha_efecto
    assert len(repo.ops) == 1
    assert next(iter(repo.ops.values()))["clasificacion"] is ClasificacionOperacion.ALTA_RETROACTIVA
    assert len(registro.llamadas) == 2  # intento provisional + reaplicación con la clasificación final
    assert [t[0] for t in cola.tareas] == [TAREA_REPROCESAR_PERIODO, TAREA_REPROCESAR_PERIODO]
    assert [t[2] for t in cola.tareas] == ["reprocesar_periodo:10", "reprocesar_periodo:11"]


async def test_huella_invalida_no_persiste() -> None:
    svc, repo, registro, _ = _servicio()
    op = _op().model_copy(update={"hash_contenido": "f" * 64})

    respuesta = await svc.recibir(_actor(), _lote(op))

    assert respuesta.resultados[0].resultado is ResultadoSync.RECHAZADA
    assert respuesta.resultados[0].codigo == "HUELLA_INVALIDA"
    assert respuesta.resultados[0].oper_id is None
    assert repo.ops == {}
    assert registro.llamadas == []


async def test_no_leida_y_cero_moscas_son_distintas() -> None:
    svc, _, registro, _ = _servicio()
    no_leida = _op(
        entidad=EntidadSync.LECTURA_TRAMPA,
        datos={
            "lect_leida": False,
            "lect_especimenes": None,
            "lect_justificacion": "Trampa caída por el viento",
        },
    )
    cero_moscas = _op(
        entidad=EntidadSync.LECTURA_TRAMPA,
        datos={"lect_leida": True, "lect_especimenes": 0, "lect_justificacion": None},
    )

    respuesta = await svc.recibir(_actor(), _lote(no_leida, cero_moscas))

    assert [r.resultado for r in respuesta.resultados] == [
        ResultadoSync.ALTA_NUEVA,
        ResultadoSync.ALTA_NUEVA,
    ]
    assert len(registro.llamadas) == 2


async def test_correccion_crea_version_sin_sobrescribir() -> None:
    registro = RegistroFalso([ResultadoAplicacion(ClasificacionOperacion.CORRECCION)])
    svc, repo, _, _ = _servicio(registro=registro)

    respuesta = await svc.recibir(
        _actor(),
        _lote(_op(accion=AccionSync.CORREGIR, version=2, datos={"pred_id": 1, "visi_id": str(uuid4())})),
    )

    assert respuesta.resultados[0].resultado is ResultadoSync.CORRECCION
    assert respuesta.resultados[0].version == 2
    assert next(iter(repo.ops.values()))["clasificacion"] is ClasificacionOperacion.CORRECCION
    assert len(registro.llamadas) == 1


async def test_error_tecnico_no_tumba_el_resto_del_lote() -> None:
    repo = RepoQueFallaUnaVez()
    svc, _, _, _ = _servicio(repo=repo)

    respuesta = await svc.recibir(_actor(), _lote(_op(), _op()))

    assert respuesta.resultados[0].reintentable is True
    assert respuesta.resultados[0].codigo == "ERROR_TECNICO"
    assert respuesta.resultados[0].oper_id is None
    assert respuesta.resultados[1].resultado is ResultadoSync.ALTA_NUEVA
    assert len(repo.ops) == 1


async def test_pendientes_devuelve_datos_guardados() -> None:
    repo = RepoFalso()
    await repo.insertar_operacion(
        {
            "disp_id": DISP,
            "cliente_id": uuid4(),
            "usua_id": USUA,
            "entidad": EntidadSync.LECTURA_TRAMPA,
            "accion": AccionSync.CREAR,
            "registro_id": uuid4(),
            "version": 1,
            "hash": "0" * 64,
            "clasificacion": ClasificacionOperacion.REVISAR,
            "motivo": "solapamiento",
            "t_captura": AHORA,
            "deriva": 0,
            "payload": {"lect_leida": True, "lect_especimenes": 3},
        }
    )
    await repo.insertar_operacion(
        {
            "disp_id": DISP,
            "cliente_id": uuid4(),
            "usua_id": USUA,
            "entidad": EntidadSync.VISITA,
            "accion": AccionSync.CREAR,
            "registro_id": uuid4(),
            "version": 1,
            "hash": "0" * 64,
            "clasificacion": ClasificacionOperacion.ALTA_NUEVA,
            "motivo": None,
            "t_captura": AHORA,
            "deriva": 0,
            "payload": None,
        }
    )
    svc, _, _, _ = _servicio(repo=repo)

    pendientes = await svc.pendientes(_actor(), [ClasificacionOperacion.REVISAR])

    assert len(pendientes) == 1
    assert pendientes[0].motivo == "solapamiento"
    assert pendientes[0].datos == {"lect_leida": True, "lect_especimenes": 3}
