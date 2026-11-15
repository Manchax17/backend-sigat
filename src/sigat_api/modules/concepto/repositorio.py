"""Acceso a datos de concepto y vistas de resumen."""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado


class RepositorioConcepto:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    async def resumen(self, lote_id: int) -> dict[str, Any] | None:
        """fn_lote_resumen_app(:usua) WHERE lote_id = :lote."""
        raise NoImplementado("Resumen del lote")

    async def serie(self, lote_id: int, desde: date | None, hasta: date | None) -> list[dict[str, Any]]:
        """fn_mtd_serie_lote(:usua)."""
        raise NoImplementado("Serie del MTD")

    async def periodos(self, lote_id: int) -> list[dict[str, Any]]:
        raise NoImplementado("Periodos")

    async def versiones_mtd(self, peri_id: int) -> list[dict[str, Any]]:
        raise NoImplementado("Versiones del MTD")

    async def conceptos(self, lote_id: int) -> list[dict[str, Any]]:
        raise NoImplementado("Conceptos")

    async def concepto_a_fecha(self, lote_id: int, fecha: date) -> dict[str, Any] | None:
        """Versión vigente con conc_fecha_efecto <= fecha y conc_emitido_en <= fin de ese día."""
        raise NoImplementado("Concepto a fecha")

    async def insertar_concepto(self, fila: dict[str, Any]) -> int:
        """INSERT concepto_aptitud versión n+1 (fn_concepto_valida)."""
        conc_id = await self._con.scalar(
            text(
                """
INSERT INTO sigat.concepto_aptitud (
    lote_id, peri_id, indi_id, conc_version, conc_resultado,
    conc_fecha_efecto, norm_id, conc_motivo
) VALUES (
    :lote_id, :peri_id, :indi_id, :version, :resultado,
    :fecha_efecto, :norm_id, :motivo
)
RETURNING conc_id
"""
            ),
            fila,
        )
        return int(conc_id)

    async def indice(self, indi_id: int) -> dict[str, Any] | None:
        """Datos del MTD que el concepto necesita (contraste, norma y lote del periodo)."""
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT m.indi_id, m.peri_id, m.indi_resultado_contraste, m.norm_id, m.indi_valor,
       p.lote_id, p.peri_fin
  FROM sigat.indice_mtd m
  JOIN sigat.periodo_trampeo p ON p.peri_id = m.peri_id
 WHERE m.indi_id = :indi
"""
                    ),
                    {"indi": indi_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila else None

    async def ultimo_concepto(self, lote_id: int, peri_id: int) -> dict[str, Any] | None:
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT conc_id, conc_version, conc_resultado, indi_id, norm_id, conc_fecha_efecto
  FROM sigat.concepto_aptitud
 WHERE lote_id = :lote AND peri_id = :peri
 ORDER BY conc_version DESC
 LIMIT 1
"""
                    ),
                    {"lote": lote_id, "peri": peri_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila else None

    async def cultivo_productor(self, usua_id: int) -> dict[str, Any]:
        raise NoImplementado("Cultivo del productor")
