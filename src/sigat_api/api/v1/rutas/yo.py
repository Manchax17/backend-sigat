"""Recursos del usuario autenticado: perfil, inicio, predios y notificaciones."""

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, status

from sigat_api.api.dependencias import ActorDep
from sigat_api.api.fabricas import (
    servicio_lotes,
    servicio_notificaciones,
    servicio_perfil,
    servicio_planificacion,
    servicio_revision,
)
from sigat_api.api.v1 import esquemas_compuestos as c
from sigat_api.api.v1.comunes import ERRORES, Cursor
from sigat_api.compartido.esquemas import Pagina
from sigat_api.compartido.tipos import RolCodigo
from sigat_api.config import obtener_configuracion
from sigat_api.modules.configuracion.servicio import ServicioLotes
from sigat_api.modules.identidad import esquemas as ei
from sigat_api.modules.identidad.servicio import ServicioPerfil
from sigat_api.modules.notificaciones import esquemas as en
from sigat_api.modules.notificaciones.servicio import ServicioNotificaciones
from sigat_api.modules.planificacion.servicio import ServicioPlanificacion
from sigat_api.modules.registro.servicio import ServicioRevision

router = APIRouter(prefix="/yo", tags=["Mi cuenta"], responses=ERRORES)
Perfil = Annotated[ServicioPerfil, Depends(servicio_perfil)]
Notis = Annotated[ServicioNotificaciones, Depends(servicio_notificaciones)]
Revision = Annotated[ServicioRevision, Depends(servicio_revision)]
Planificacion = Annotated[ServicioPlanificacion, Depends(servicio_planificacion)]
Lotes = Annotated[ServicioLotes, Depends(servicio_lotes)]


@router.get("/inicio", response_model=c.InicioResumen, summary="Resumen de la pantalla de inicio de mi rol")
async def inicio(
    actor: ActorDep, notis: Notis, revision: Revision, planificacion: Planificacion, lotes: Lotes
) -> c.InicioResumen:
    """Fecha, «datos al» y avisos sin leer.

    Los bloques ``tecnico`` / ``productor`` / ``agronomo`` van en ``null`` (nunca valores
    inventados) hasta que sus módulos estén implementados: ver CHANGELOG.md, «Pendientes
    técnicos».
    """
    cfg = obtener_configuracion()
    ahora = datetime.now(UTC)

    # Solo si el usuario creado es Agronomo calculamos sus 4 indicadores:
    agronomo: c.InicioAgronomo | None = None
    if actor.rol == RolCodigo.AGRONOMO_SUPERVISOR:
        agronomo = c.InicioAgronomo(
            registros_por_revisar=await revision.contar_por_revisar(actor),
            planificaciones_pendientes=await planificacion.contar_pendientes(actor),
            actividades_en_curso=await planificacion.contar_en_curso(actor),
            lotes_sobre_umbral=await lotes.contar_sobre_umbral(actor),
        )

    return c.InicioResumen(
        rol=actor.rol,
        fecha=ahora.astimezone(ZoneInfo(cfg.zona_horaria_predeterminada)).date(),
        datos_al=ahora,
        notificaciones_no_leidas=(await notis.conteo(actor)).no_leidas,
        agronomo=agronomo,
    )


@router.get("/perfil", response_model=ei.PerfilPropio, summary="Mi perfil")
async def perfil(actor: ActorDep, svc: Perfil) -> ei.PerfilPropio:
    return await svc.perfil(actor)


@router.put(
    "/perfil/foto", response_model=ei.PerfilPropio, summary="Poner foto de perfil (archivo ya subido)"
)
async def poner_foto(s: ei.FotoPerfilEntrada, actor: ActorDep, svc: Perfil) -> ei.PerfilPropio:
    return await svc.poner_foto(actor, s)


@router.delete("/perfil/foto", response_model=ei.PerfilPropio, summary="Quitar foto de perfil")
async def quitar_foto(actor: ActorDep, svc: Perfil) -> ei.PerfilPropio:
    return await svc.quitar_foto(actor)


@router.get("/predios", response_model=list[ei.PredioAccesible], summary="Predios a los que tengo acceso hoy")
async def predios(actor: ActorDep, svc: Perfil) -> list[ei.PredioAccesible]:
    return await svc.predios(actor)


@router.get(
    "/preferencias-notificacion",
    response_model=en.PreferenciasNotificacion,
    summary="Mis preferencias de aviso",
)
async def preferencias(actor: ActorDep, svc: Notis) -> en.PreferenciasNotificacion:
    return await svc.preferencias(actor)


@router.put(
    "/preferencias-notificacion",
    response_model=en.PreferenciasNotificacion,
    summary="Guardar preferencias de aviso",
)
async def guardar_preferencias(
    s: en.PreferenciasNotificacion, actor: ActorDep, svc: Notis
) -> en.PreferenciasNotificacion:
    return await svc.guardar_preferencias(actor, s)


@router.get(
    "/notificaciones", response_model=Pagina[en.Notificacion], summary="Mis avisos (más reciente primero)"
)
async def notificaciones(
    actor: ActorDep, svc: Notis, cursor: Cursor = None, no_leidas: Annotated[bool, Query()] = False
) -> Pagina[en.Notificacion]:
    return await svc.listar(actor, no_leidas, cursor)


@router.get("/notificaciones/conteo", response_model=en.ConteoNoLeidas, summary="Número de avisos sin leer")
async def conteo(actor: ActorDep, svc: Notis) -> en.ConteoNoLeidas:
    return await svc.conteo(actor)


@router.post(
    "/notificaciones/{noti_id}/leida",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Marcar un aviso como leído",
)
async def marcar_leida(noti_id: int, actor: ActorDep, svc: Notis) -> None:
    await svc.marcar_leida(actor, noti_id)


@router.post(
    "/notificaciones/leidas", status_code=status.HTTP_204_NO_CONTENT, summary="Marcar todos como leídos"
)
async def marcar_todas(actor: ActorDep, svc: Notis) -> None:
    await svc.marcar_todas(actor)
