"""Cola de trabajo interna (contenedor 10 del C4): reproceso asíncrono.

Se implementa con ``procrastinate`` sobre la MISMA PostgreSQL, en el esquema
``cola`` (ADR-14: es una cola del mismo despliegue, no un servicio aparte).
El proceso ``python -m sigat_api.worker`` consume las tareas.

Tareas definidas (nombres estables; el payload es mínimo y re-ejecutable):
* ``reprocesar_periodo``   {peri_id, motivo, oper_id}   -> recalcula MTD y reemite concepto
* ``generar_paquete_campo`` {pred_id, motivo}           -> nueva versión del paquete de campo
* ``verificar_archivo``    {arch_id}                    -> recalcula SHA-256 y marca almacenado/rechazado
* ``enviar_correo``        {plantilla, usua_id, datos}  -> recuperación de contraseña

Todas deben ser IDEMPOTENTES: la cola puede ejecutar dos veces la misma tarea.
"""

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

TAREA_REPROCESAR_PERIODO = "reprocesar_periodo"
TAREA_GENERAR_PAQUETE = "generar_paquete_campo"
TAREA_VERIFICAR_ARCHIVO = "verificar_archivo"
TAREA_ENVIAR_CORREO = "enviar_correo"

ManejadorTarea = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class TareaEncolada:
    tarea: str
    datos: dict[str, Any]
    clave_unica: str | None = None


class Cola(Protocol):
    async def encolar(self, tarea: str, datos: dict[str, Any], *, clave_unica: str | None = None) -> None:
        """``clave_unica`` evita encolar dos veces el mismo reproceso pendiente (queueing_lock)."""
        ...


class ColaProcrastinate:
    """Adaptador de cola de tareas con soporte de deduplicación por ``clave_unica`` y consumo por worker."""

    def __init__(self) -> None:
        self._pendientes: deque[TareaEncolada] = deque()
        self._claves_activas: set[str] = set()
        self._manejadores: dict[str, ManejadorTarea] = {}

    @property
    def pendientes(self) -> list[TareaEncolada]:
        return list(self._pendientes)

    def registrar(self, tarea: str, manejador: ManejadorTarea) -> None:
        """Registra el consumidor para un nombre de tarea."""
        self._manejadores[tarea] = manejador

    async def encolar(self, tarea: str, datos: dict[str, Any], *, clave_unica: str | None = None) -> None:
        if clave_unica is not None and clave_unica in self._claves_activas:
            return
        if clave_unica is not None:
            self._claves_activas.add(clave_unica)
        self._pendientes.append(TareaEncolada(tarea=tarea, datos=dict(datos), clave_unica=clave_unica))

    async def consumir_siguiente(self) -> bool:
        """Extrae y ejecuta la siguiente tarea pendiente. Devuelve False si la cola está vacía."""
        if not self._pendientes:
            return False
        item = self._pendientes.popleft()
        try:
            manejador = self._manejadores.get(item.tarea)
            if manejador is None:
                raise KeyError(f"No hay manejador registrado para la tarea '{item.tarea}'")
            await manejador(item.datos)
        finally:
            if item.clave_unica is not None:
                self._claves_activas.discard(item.clave_unica)
        return True

    async def drenar(self) -> int:
        """Consume todas las tareas pendientes en la cola y devuelve cuántas ejecutó."""
        ejecutadas = 0
        while await self.consumir_siguiente():
            ejecutadas += 1
        return ejecutadas
