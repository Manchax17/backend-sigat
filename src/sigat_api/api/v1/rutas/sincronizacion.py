from typing import Annotated

from fastapi import APIRouter, Depends

from sigat_api.api.dependencias import requiere
from sigat_api.api.fabricas import servicio_sincronizacion
from sigat_api.api.v1.comunes import ERRORES
from sigat_api.compartido.tipos import Actor
from sigat_api.modules.sincronizacion import esquemas as e
from sigat_api.modules.sincronizacion.servicio import ServicioSincronizacion

router = APIRouter(prefix="/sincronizacion", tags=["Sincronización"], responses=ERRORES)
Svc = Annotated[ServicioSincronizacion, Depends(servicio_sincronizacion)]
Emisor = Annotated[Actor, Depends(requiere("sincronizacion.enviar"))]


@router.post("/lotes", response_model=e.RespuestaLote, summary="Enviar un lote de operaciones idempotentes")
async def enviar_lote(s: e.LoteSincronizacion, actor: Emisor, svc: Svc) -> e.RespuestaLote:
    """Siempre 200 si el lote se pudo leer: el resultado de CADA operación va en ``resultados``.
    403 solo si el disp_id no es el del token o el dispositivo está dado de baja."""
    return await svc.recibir(actor, s)


@router.get(
    "/operaciones",
    response_model=list[e.OperacionPendiente],
    summary="Consultar las operaciones recibidas y su resultado",
)
async def operaciones(actor: Emisor, svc: Svc) -> list[e.OperacionPendiente]:
    return await svc.pendientes(actor)
