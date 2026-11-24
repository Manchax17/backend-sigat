from datetime import datetime
from typing import Protocol

from sigat_api.compartido.contratos import PertenenciaTrampa
from sigat_api.compartido.tipos import Actor


class PuertoConfiguracion(Protocol):
    async def pertenencia_trampa(self, tram_id: int, instante: datetime) -> PertenenciaTrampa | None:
        """Ubicación vigente de la trampa en ``instante`` (no hoy): la asignación que regía al observar."""
        ...

    async def lote_activo_en(self, lote_id: int, instante: datetime) -> bool: ...

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool: ...

    async def lote_predio(self, lote_id: int) -> int | None: ...


class PuertoOperaciones(Protocol):
    """Implementa: sincronización. Para escrituras EN LÍNEA de la web, que también necesitan
    una ``operacion_sync`` (la BD exige ``oper_id``)."""

    async def registrar_operacion_en_linea(
        self,
        actor: Actor,
        entidad: str,
        accion: str,
        registro_id: str,
        version: int,
        datos: dict[str, object],
        t_captura: datetime,
    ) -> int:
        """Inserta operacion_sync con el disp_id de la sesión web y devuelve oper_id."""
        ...
