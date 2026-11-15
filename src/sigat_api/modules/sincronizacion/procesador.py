"""Procesador de reproceso (Arquitectura §6.3). Lo ejecuta el worker, no la petición.

Recalcula el índice del periodo (nueva versión solo si cambió) y reemite el
concepto con ``conc_fecha_efecto`` = t_observacion del registro que lo disparó.
Es idempotente: repetir la tarea no crea versiones duplicadas. La notificación a
agrónomo/productor queda fuera del Sprint 2.
"""

from datetime import date, datetime

from sigat_api.modules.sincronizacion.puertos import PuertoConcepto, PuertoRecalculo


class ProcesadorReproceso:
    def __init__(self, recalculo: PuertoRecalculo, concepto: PuertoConcepto) -> None:
        self._recalculo = recalculo
        self._concepto = concepto

    async def ejecutar(
        self,
        peri_id: int,
        motivo: str,
        oper_id: int | None,  # el payload lo conserva la cola para auditoría
        fecha_efecto: str | None = None,
    ) -> None:
        indi_id = await self._recalculo.recalcular_periodo(peri_id, motivo)
        await self._concepto.reemitir(peri_id, indi_id, motivo, fecha_efecto=_fecha(fecha_efecto))


def _fecha(valor: str | None) -> date | None:
    if not valor:
        return None
    if isinstance(valor, datetime):  # tolera que la cola entregue un datetime
        return valor.date()
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).date()
