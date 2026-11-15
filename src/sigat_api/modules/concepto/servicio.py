"""Casos de uso de concepto y resúmenes. ``ServicioConcepto.reemitir`` implementa
``sincronizacion.puertos.PuertoConcepto``."""

from datetime import date

from sigat_api.compartido.errores import NoEncontrado, NoImplementado, ReglaNegocio
from sigat_api.compartido.tipos import Actor, ResultadoConcepto, ResultadoContraste
from sigat_api.modules.concepto import esquemas as e
from sigat_api.modules.concepto.repositorio import RepositorioConcepto

_RESULTADO_POR_CONTRASTE = {
    ResultadoContraste.POR_DEBAJO_UMBRAL: ResultadoConcepto.APTO,
    ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL: ResultadoConcepto.NO_APTO,
    ResultadoContraste.SIN_UMBRAL: ResultadoConcepto.NO_EMITIDO,
}


class ServicioConcepto:
    def __init__(self, repo: RepositorioConcepto) -> None:
        self._repo = repo

    async def reemitir(
        self,
        peri_id: int,
        indi_id: int,
        motivo: str,
        fecha_efecto: date | None = None,
    ) -> None:
        """Emite la versión n+1 del concepto del periodo (``apto``, ``no_apto`` o
        ``no_emitido``). ``conc_fecha_efecto`` es la fecha del registro que lo
        disparó; si el resultado no cambia, no crea versión."""
        indice = await self._repo.indice(indi_id)
        if indice is None:
            raise NoEncontrado("El índice MTD no existe.", codigo="MTD_INEXISTENTE")

        contraste = ResultadoContraste(str(indice["indi_resultado_contraste"]))
        resultado = _RESULTADO_POR_CONTRASTE[contraste]
        norm_id = None if resultado == ResultadoConcepto.NO_EMITIDO else indice["norm_id"]
        if resultado != ResultadoConcepto.NO_EMITIDO and norm_id is None:
            raise ReglaNegocio(
                "El MTD tiene umbral pero su programa no trae la norma asociada (UQQ-004).",
                codigo="NORMA_UMBRAL_FALTANTE",
            )

        fecha = fecha_efecto or indice["peri_fin"]
        anterior = await self._repo.ultimo_concepto(int(indice["lote_id"]), peri_id)
        if anterior is not None and self._sin_cambios(anterior, resultado, indi_id, norm_id, fecha):
            return

        version = int(anterior["conc_version"]) + 1 if anterior else 1
        await self._repo.insertar_concepto(
            {
                "lote_id": int(indice["lote_id"]),
                "peri_id": peri_id,
                "indi_id": indi_id,
                "version": version,
                "resultado": resultado.value,
                "fecha_efecto": fecha,
                "norm_id": norm_id,
                "motivo": motivo if version > 1 else None,
            }
        )

    @staticmethod
    def _sin_cambios(
        anterior: dict[str, object],
        resultado: ResultadoConcepto,
        indi_id: int,
        norm_id: object,
        fecha: date,
    ) -> bool:
        return (
            str(anterior["conc_resultado"]) == resultado.value
            and int(str(anterior["indi_id"])) == indi_id
            and anterior["norm_id"] == norm_id
            and anterior["conc_fecha_efecto"] == fecha
        )

    async def resumen_lote(self, actor: Actor, lote_id: int) -> e.ResumenLote:
        raise NoImplementado("Resumen del lote")

    async def serie_mtd(
        self, actor: Actor, lote_id: int, desde: date | None, hasta: date | None
    ) -> list[e.PuntoMtd]:
        raise NoImplementado("Serie del MTD")

    async def periodos(self, actor: Actor, lote_id: int) -> list[e.PeriodoTrampeo]:
        raise NoImplementado("Periodos")

    async def versiones_mtd(self, actor: Actor, lote_id: int, peri_id: int) -> list[e.VersionMtd]:
        raise NoImplementado("Versiones del MTD")

    async def conceptos(self, actor: Actor, lote_id: int) -> list[e.Concepto]:
        raise NoImplementado("Conceptos del lote")

    async def concepto_a_fecha(self, actor: Actor, lote_id: int, fecha: date) -> e.ConceptoAFecha:
        raise NoImplementado("Concepto a una fecha")

    async def etapa(self, actor: Actor, lote_id: int, fecha: date | None) -> e.EtapaLote:
        raise NoImplementado("Etapa fenológica")

    async def cultivo_productor(self, actor: Actor) -> e.CultivoProductor:
        """Solo PRODUCTOR: sus lotes (predios de los que es dueño)."""
        raise NoImplementado("Cultivo del productor")
