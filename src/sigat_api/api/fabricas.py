"""RAÍZ DE COMPOSICIÓN por petición.

Único lugar donde se conectan los módulos entre sí: cada servicio recibe sus
repositorios (con la conexión de la petición) y los PUERTOS que necesita, que
aquí se satisfacen con servicios de otros módulos. Los módulos nunca se
importan entre ellos (import-linter lo verifica).
"""

from datetime import datetime
from functools import lru_cache
from typing import Any

import structlog
from fastapi import Depends

from sigat_api.api.dependencias import ConexionDep, ConexionPublicaDep, configuracion
from sigat_api.compartido.contratos import PertenenciaTrampa
from sigat_api.config import Configuracion
from sigat_api.infra.almacenamiento.objetos import AlmacenS3
from sigat_api.infra.cola.cola import TAREA_REPROCESAR_PERIODO, ColaProcrastinate
from sigat_api.infra.reloj import RelojSistema
from sigat_api.infra.seguridad.tokens import ServicioTokens
from sigat_api.modules.auditoria.repositorio import RepositorioAuditoria
from sigat_api.modules.auditoria.servicio import ServicioAuditoria
from sigat_api.modules.catalogo.repositorio import RepositorioCatalogo
from sigat_api.modules.catalogo.servicio import ServicioCatalogo
from sigat_api.modules.concepto.repositorio import RepositorioConcepto
from sigat_api.modules.concepto.servicio import ServicioConcepto
from sigat_api.modules.configuracion.repositorio import RepositorioConfiguracion
from sigat_api.modules.configuracion.servicio import (
    ServicioLotes,
    ServicioPaqueteCampo,
    ServicioPredios,
    ServicioPrograma,
    ServicioReticula,
    ServicioTrampas,
)
from sigat_api.modules.identidad.repositorio import RepositorioIdentidad
from sigat_api.modules.identidad.servicio import (
    ServicioAutenticacion,
    ServicioAutorizacion,
    ServicioPerfil,
    ServicioUsuarios,
)
from sigat_api.modules.imagenes.repositorio import RepositorioArchivos
from sigat_api.modules.imagenes.servicio import ServicioArchivos
from sigat_api.modules.notificaciones.repositorio import RepositorioNotificaciones
from sigat_api.modules.notificaciones.servicio import ServicioNotificaciones
from sigat_api.modules.planificacion.repositorio import RepositorioPlanificacion
from sigat_api.modules.planificacion.servicio import ServicioPlanificacion
from sigat_api.modules.registro.mtd import MotorMtd
from sigat_api.modules.registro.repositorio import RepositorioRegistro
from sigat_api.modules.registro.servicio import ServicioConsultaRegistro, ServicioRegistro, ServicioRevision
from sigat_api.modules.registro.validador import ValidadorInvariantes
from sigat_api.modules.sincronizacion.procesador import ProcesadorReproceso
from sigat_api.modules.sincronizacion.repositorio import RepositorioSincronizacion
from sigat_api.modules.sincronizacion.servicio import (
    RegistradorOperacionesEnLinea,
    ServicioDispositivos,
    ServicioSincronizacion,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------- recursos de proceso (singleton)
@lru_cache
def _cola() -> ColaProcrastinate:
    return ColaProcrastinate()


@lru_cache
def _reloj() -> RelojSistema:
    return RelojSistema()


def _almacen(cfg: Configuracion = Depends(configuracion)) -> AlmacenS3:
    return AlmacenS3(cfg)


# ---------------------------------------------------------------- identidad
def servicio_autenticacion(
    con: ConexionPublicaDep, cfg: Configuracion = Depends(configuracion)
) -> ServicioAutenticacion:
    return ServicioAutenticacion(
        RepositorioIdentidad(con),
        ServicioDispositivos(RepositorioSincronizacion(con)),
        ServicioTokens(cfg),
        cfg,
        avisos=ServicioNotificaciones(RepositorioNotificaciones(con)),
    )


def servicio_autenticacion_actor(
    con: ConexionDep, cfg: Configuracion = Depends(configuracion)
) -> ServicioAutenticacion:
    return ServicioAutenticacion(
        RepositorioIdentidad(con),
        ServicioDispositivos(RepositorioSincronizacion(con)),
        ServicioTokens(cfg),
        cfg,
    )


def servicio_archivos(con: ConexionDep, almacen: AlmacenS3 = Depends(_almacen)) -> ServicioArchivos:
    return ServicioArchivos(RepositorioArchivos(con), almacen, _cola())


def servicio_perfil(
    con: ConexionDep, archivos: ServicioArchivos = Depends(servicio_archivos)
) -> ServicioPerfil:
    return ServicioPerfil(RepositorioIdentidad(con), archivos)


def servicio_usuarios(con: ConexionDep) -> ServicioUsuarios:
    return ServicioUsuarios(RepositorioIdentidad(con))


def servicio_autorizacion(con: ConexionDep) -> ServicioAutorizacion:
    return ServicioAutorizacion(RepositorioIdentidad(con))


# ---------------------------------------------------------------- catálogo y configuración
def servicio_catalogo(con: ConexionDep) -> ServicioCatalogo:
    return ServicioCatalogo(RepositorioCatalogo(con), _cola())


def servicio_predios(con: ConexionDep) -> ServicioPredios:
    return ServicioPredios(RepositorioConfiguracion(con), _cola())


def servicio_lotes(con: ConexionDep) -> ServicioLotes:
    return ServicioLotes(RepositorioConfiguracion(con), _cola())


def servicio_trampas(con: ConexionDep) -> ServicioTrampas:
    return ServicioTrampas(RepositorioConfiguracion(con), _cola())


def servicio_programa(con: ConexionDep) -> ServicioPrograma:
    return ServicioPrograma(RepositorioConfiguracion(con), _cola())


def servicio_reticula(con: ConexionDep) -> ServicioReticula:
    return ServicioReticula(RepositorioConfiguracion(con), _cola())


def servicio_paquete(con: ConexionDep) -> ServicioPaqueteCampo:
    return ServicioPaqueteCampo(RepositorioConfiguracion(con))


# ---------------------------------------------------------------- registro, sincronización, planificación
class _AdaptadorConfiguracionParaRegistro:
    """Satisface ``registro.puertos.PuertoConfiguracion`` con el repositorio de configuración."""

    def __init__(self, repo: RepositorioConfiguracion) -> None:
        self._repo = repo

    async def pertenencia_trampa(self, tram_id: int, instante: datetime) -> PertenenciaTrampa | None:
        """Ubicación de la trampa vigente en ``instante`` (ubicacion_trampa [ubic_fecha, ubic_fecha_fin))."""
        fila = await self._repo.pertenencia_trampa(tram_id, instante)
        if fila is None:
            return None
        return PertenenciaTrampa(
            tram_id=tram_id,
            ubic_id=int(fila["ubic_id"]),
            lote_id=int(fila["lote_id"]) if fila.get("lote_id") is not None else None,
            pred_id=int(fila["pred_id"]),
            lote_activo=await self._repo.lote_activo_en(int(fila["lote_id"]), instante)
            if fila.get("lote_id") is not None
            else False,
        )

    async def lote_activo_en(self, lote_id: int, instante: datetime) -> bool:
        """lote_fecha_registro <= fecha < lote_fecha_baja (o sin baja)."""
        return await self._repo.lote_activo_en(lote_id, instante)

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return await self._repo.lote_visible(usua_id, lote_id)

    async def lote_predio(self, lote_id: int) -> int | None:
        return await self._repo.lote_predio(lote_id)


class _ColaEnProceso:
    """Sustituto temporal de ``procrastinate`` mientras no se autorice el esquema ``cola``.

    Ejecuta el reproceso en la misma transacción, en un SAVEPOINT: si el recálculo
    falla, el dato de campo ya aplicado se conserva (ADR-12) y el periodo se
    recalcula en la siguiente lectura. Cuando exista la cola real, se cambia esta
    clase sin tocar ``ServicioSincronizacion``.
    """

    def __init__(self, con: Any) -> None:
        self._con = con
        self._hechas: set[str] = set()

    async def encolar(self, tarea: str, datos: dict[str, Any], *, clave_unica: str | None = None) -> None:
        if tarea != TAREA_REPROCESAR_PERIODO:
            log.warning("cola_en_proceso.tarea_no_soportada", tarea=tarea)
            return
        if clave_unica is not None:
            if clave_unica in self._hechas:
                return
            self._hechas.add(clave_unica)
        procesador = ProcesadorReproceso(
            MotorMtd(RepositorioRegistro(self._con)),
            ServicioConcepto(RepositorioConcepto(self._con)),
        )
        try:
            async with self._con.begin_nested():
                await procesador.ejecutar(
                    peri_id=int(datos["peri_id"]),
                    motivo=str(datos.get("motivo", "sincronizacion")),
                    oper_id=int(datos["oper_id"]) if datos.get("oper_id") is not None else None,
                    fecha_efecto=datos.get("fecha_efecto"),
                )
        except Exception:  # pragma: no cover - se observa en los logs, no tumba el sync
            log.exception("cola_en_proceso.reproceso_fallido", tarea=tarea, datos=datos)


def servicio_planificacion(con: ConexionDep) -> ServicioPlanificacion:
    return ServicioPlanificacion(RepositorioPlanificacion(con))


def servicio_registro(con: ConexionDep) -> ServicioRegistro:
    repo = RepositorioRegistro(con)
    configuracion_registro = _AdaptadorConfiguracionParaRegistro(RepositorioConfiguracion(con))
    validador = ValidadorInvariantes(configuracion_registro, repo)
    operaciones = RegistradorOperacionesEnLinea(RepositorioSincronizacion(con))
    return ServicioRegistro(
        repo,
        validador,
        operaciones=operaciones,
        configuracion=configuracion_registro,
        reloj=_reloj(),
    )


def servicio_sincronizacion(
    con: ConexionDep,
    registro: ServicioRegistro = Depends(servicio_registro),
    planificacion: ServicioPlanificacion = Depends(servicio_planificacion),
    cfg: Configuracion = Depends(configuracion),
) -> ServicioSincronizacion:
    return ServicioSincronizacion(
        RepositorioSincronizacion(con),
        registro=registro,
        planificacion=planificacion,
        cola=_ColaEnProceso(con),
        reloj=_reloj(),
        cfg=cfg,
    )


def servicio_revision(con: ConexionDep) -> ServicioRevision:
    return ServicioRevision(RepositorioRegistro(con), _reloj())


def servicio_consulta_registro(con: ConexionDep) -> ServicioConsultaRegistro:
    return ServicioConsultaRegistro(RepositorioRegistro(con))


# ---------------------------------------------------------------- concepto, notificaciones, auditoría
def servicio_concepto(con: ConexionDep) -> ServicioConcepto:
    return ServicioConcepto(RepositorioConcepto(con))


def servicio_notificaciones(con: ConexionDep) -> ServicioNotificaciones:
    return ServicioNotificaciones(RepositorioNotificaciones(con))


def servicio_auditoria(con: ConexionDep) -> ServicioAuditoria:
    return ServicioAuditoria(RepositorioAuditoria(con))
