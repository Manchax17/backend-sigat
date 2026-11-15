"""Lo que SINCRONIZACIÓN necesita de otros módulos."""

from datetime import date
from typing import Protocol

from sigat_api.compartido.contratos import OperacionCampo, ResultadoAplicacion
from sigat_api.compartido.tipos import Actor


class PuertoRegistro(Protocol):
    """Implementa: registro.ServicioRegistro."""

    async def aplicar(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion: ...


class PuertoResultadoPlanificacion(Protocol):
    """Implementa: planificacion. Para la entidad ``planificacion_lote`` (C02/C03 v8)."""

    async def aplicar_resultado_lote(self, actor: Actor, op: OperacionCampo) -> ResultadoAplicacion: ...


class PuertoRecalculo(Protocol):
    """Implementa: registro.mtd.MotorMtd (lo usa el procesador de reproceso)."""

    async def recalcular_periodo(self, peri_id: int, motivo: str) -> int: ...


class PuertoConcepto(Protocol):
    """Implementa: concepto.ServicioConcepto."""

    async def reemitir(
        self,
        peri_id: int,
        indi_id: int,
        motivo: str,
        fecha_efecto: date | None = None,
    ) -> None: ...
