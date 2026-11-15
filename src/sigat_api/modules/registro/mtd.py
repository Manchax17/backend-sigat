"""Motor de umbrales MTD (Arquitectura §5.4, ADR-13, RN-001).

El MTD con autoridad se calcula solo aquí; el móvil muestra una estimación y no
la envía como dato (ADR-15).

    MTD = M / (T × D)        [moscas / trampa / día]

M son las moscas de las lecturas oficiales vigentes en las trampas atendidas, T
esas trampas y D el promedio de días de exposición. Si T × D = 0, ``indi_valor``
queda nulo y el contraste es ``sin_umbral``.

Días de exposición (RF-022, decisión del Sprint 2, ver
``DOCUMENTOS/Sprint_2_Sincronizacion_SIGAT.md``): por trampa atendida, desde
``max(peri_inicio, ubicacion_trampa.ubic_fecha)`` hasta su última lectura del
periodo, inclusive. El umbral es el de ``programa_inspeccion`` vigente en la
fecha del periodo; si no existe, el concepto queda ``no_emitido`` (UQQ-004).
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sigat_api.compartido.tipos import ResultadoContraste
from sigat_api.modules.registro.repositorio import RepositorioRegistro

_SEIS_DECIMALES = Decimal("0.000001")
_TRES_DECIMALES = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class EntradaMtd:
    moscas: int
    trampas: int
    dias_exposicion: Decimal
    umbral: Decimal | None


@dataclass(frozen=True, slots=True)
class SalidaMtd:
    valor: Decimal | None
    contraste: ResultadoContraste


def calcular(entrada: EntradaMtd) -> SalidaMtd:
    """Calcula el MTD (6 decimales) y lo contrasta con el umbral vigente.

    ``igual`` al umbral cuenta como ``igual_o_superior_umbral`` (RF-024). El redondeo es
    ``ROUND_HALF_UP`` para coincidir con la columna generada de PostgreSQL (``round(...)``).
    """
    dias = Decimal(entrada.dias_exposicion)
    denominador = Decimal(entrada.trampas) * dias
    if entrada.trampas <= 0 or dias <= 0 or denominador == 0:
        return SalidaMtd(valor=None, contraste=ResultadoContraste.SIN_UMBRAL)

    valor = (Decimal(entrada.moscas) / denominador).quantize(_SEIS_DECIMALES, rounding=ROUND_HALF_UP)
    if entrada.umbral is None:
        return SalidaMtd(valor=valor, contraste=ResultadoContraste.SIN_UMBRAL)
    contraste = (
        ResultadoContraste.POR_DEBAJO_UMBRAL
        if valor < entrada.umbral
        else ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL
    )
    return SalidaMtd(valor=valor, contraste=contraste)


def redondear_dias(valor: Decimal) -> Decimal:
    """``indi_dias_exposicion`` es ``numeric(8,3)``."""
    return Decimal(valor).quantize(_TRES_DECIMALES, rounding=ROUND_HALF_UP)


class MotorMtd:
    """Orquesta el recálculo de un periodo: lee lecturas vigentes, inserta ``indice_mtd``
    versión n+1 con ``indi_motivo_recalculo`` y ``indice_mtd_lectura``. Append-only."""

    def __init__(self, repo: RepositorioRegistro) -> None:
        self._repo = repo

    async def recalcular_periodo(self, peri_id: int, motivo: str) -> int:
        """Devuelve el nuevo ``indi_id``. Idempotente: si el resultado no cambia, no crea versión."""
        lecturas = await self._repo.lecturas_mtd(peri_id)
        umbral = await self._repo.umbral_vigente(peri_id)

        por_trampa: dict[int, list[dict[str, Any]]] = {}
        for fila in lecturas:
            por_trampa.setdefault(int(fila["tram_id"]), []).append(fila)

        moscas = sum(int(f["lect_especimenes"] or 0) for f in lecturas)
        trampas = len(por_trampa)
        dias = self._dias_exposicion(por_trampa)
        salida = calcular(
            EntradaMtd(
                moscas=moscas,
                trampas=trampas,
                dias_exposicion=dias,
                umbral=umbral["umbral"] if umbral else None,
            )
        )
        return await self._repo.insertar_mtd(
            peri_id=peri_id,
            moscas=moscas,
            trampas=trampas,
            dias_exposicion=dias,
            valor=salida.valor,
            contraste=salida.contraste,
            umbral=umbral,
            lect_ids=[f["lect_id"] for f in lecturas],
            motivo=motivo,
        )

    @staticmethod
    def _dias_exposicion(por_trampa: dict[int, list[dict[str, Any]]]) -> Decimal:
        if not por_trampa:
            return Decimal("0")
        total = Decimal("0")
        for filas in por_trampa.values():
            inicio = min(f["inicio_exposicion"] for f in filas)
            fin = max(f["lect_fecha"] for f in filas)
            dias = (fin - inicio).days + 1
            total += Decimal(max(dias, 0))
        return redondear_dias(total / Decimal(len(por_trampa)))
