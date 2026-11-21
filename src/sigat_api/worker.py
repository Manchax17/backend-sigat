"""Proceso de la cola de trabajo: ``python -m sigat_api.worker``.

Mismo código e imagen que la API (ADR-14). Registra las tareas de ``infra/cola``
y compone sus dependencias igual que ``api/fabricas.py``, pero con una
transacción por tarea y un actor de sistema para ``sigat.usuario_actual``.
"""

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.config import obtener_configuracion
from sigat_api.infra.cola.cola import TAREA_GENERAR_PAQUETE, ColaProcrastinate
from sigat_api.infra.db.motor import cerrar_motor, crear_motor, obtener_motor
from sigat_api.infra.observabilidad.logs import configurar_logs
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.repositorio import RepositorioConfiguracion
from sigat_api.modules.configuracion.servicio import ServicioPaqueteCampo


async def ejecutar_generar_paquete_en_conexion(
    con: AsyncConnection, pred_id: int, motivo: str
) -> e.PaqueteCampoInfo:
    """Ejecuta la generación de paquete de campo sobre una conexión activa."""
    svc = ServicioPaqueteCampo(RepositorioConfiguracion(con))
    return await svc.generar(pred_id=pred_id, motivo=motivo)


async def manejar_generar_paquete_campo(datos: dict[str, Any]) -> e.PaqueteCampoInfo:
    """Consumidor de ``TAREA_GENERAR_PAQUETE``: abre transacción y regenera el paquete del predio."""
    pred_id = int(datos["pred_id"])
    motivo = str(datos.get("motivo") or "Regeneración asíncrona de paquete de campo")
    async with obtener_motor().begin() as con:
        usua_sistema = await con.scalar(
            text(
                """
                SELECT u.usua_id
                  FROM sigat.usuario u
                  JOIN sigat.rol r ON r.rol_id = u.rol_id
                 WHERE u.usua_eliminado_en IS NULL
                 ORDER BY (r.rol_codigo = 'ADMINISTRADOR') DESC, u.usua_id ASC
                 LIMIT 1
                """
            )
        )
        if usua_sistema is not None:
            await con.execute(
                text("SELECT set_config('sigat.usuario_actual', CAST(:u AS text), true)"),
                {"u": str(usua_sistema)},
            )
        return await ejecutar_generar_paquete_en_conexion(con, pred_id, motivo)


def configurar_worker(cola: ColaProcrastinate) -> ColaProcrastinate:
    """Registra los manejadores oficiales de la cola en la instancia dada."""
    cola.registrar(TAREA_GENERAR_PAQUETE, manejar_generar_paquete_campo)
    return cola


async def ciclo_worker(cola: ColaProcrastinate, *, intervalo_seg: float = 1.0) -> None:
    """Bucle de consumo continuo del worker."""
    configurar_worker(cola)
    while True:
        procesada = await cola.consumir_siguiente()
        if not procesada:
            await asyncio.sleep(intervalo_seg)


async def principal() -> None:
    cfg = obtener_configuracion()
    configurar_logs(cfg)
    crear_motor(cfg)
    cola = configurar_worker(ColaProcrastinate())
    try:
        await ciclo_worker(cola)
    finally:
        await cerrar_motor()


if __name__ == "__main__":
    asyncio.run(principal())
