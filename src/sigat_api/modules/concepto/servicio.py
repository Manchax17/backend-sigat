"""Casos de uso de concepto y resúmenes. ``ServicioConcepto.reemitir`` implementa
``sincronizacion.puertos.PuertoConcepto``."""

from datetime import UTC, date, datetime

from sigat_api.compartido.errores import NoEncontrado, NoImplementado
from sigat_api.compartido.tipos import Actor, ResultadoConcepto, ResultadoContraste
from sigat_api.modules.concepto import esquemas as e
from sigat_api.modules.concepto.repositorio import RepositorioConcepto

_MAPA_CONCEPTO: dict[str, ResultadoConcepto] = {
    ResultadoContraste.POR_DEBAJO_UMBRAL.value: ResultadoConcepto.APTO,
    ResultadoContraste.IGUAL_O_SUPERIOR_UMBRAL.value: ResultadoConcepto.NO_APTO,
    ResultadoContraste.SIN_UMBRAL.value: ResultadoConcepto.NO_EMITIDO,
}


def derivar_resultado_concepto(contraste: ResultadoContraste | str | None) -> ResultadoConcepto:
    """Deriva el concepto de aptitud a partir del contraste del MTD (RF-024 / fn_concepto_valida)."""
    if contraste is None:
        return ResultadoConcepto.NO_EMITIDO
    clave = contraste.value if isinstance(contraste, ResultadoContraste) else str(contraste)
    return _MAPA_CONCEPTO.get(clave, ResultadoConcepto.NO_EMITIDO)


class ServicioConcepto:
    def __init__(self, repo: RepositorioConcepto) -> None:
        self._repo = repo

    async def reemitir(self, peri_id: int, indi_id: int, motivo: str) -> None:
        """Emite versión n+1 del concepto del periodo: ``apto`` (por_debajo_umbral), ``no_apto``
        (igual_o_superior_umbral) o ``no_emitido`` (sin_umbral). ``conc_fecha_efecto`` = fecha de
        observación del registro que lo disparó. Si el resultado no cambia, no crea versión."""
        datos = await self._repo.datos_reemision(peri_id, indi_id)
        if datos is None:
            raise NoEncontrado("El periodo o índice MTD no existe.", codigo="PERIODO_NO_ENCONTRADO")

        resultado = derivar_resultado_concepto(datos.get("contraste"))
        vigente = datos.get("resultado_vigente")
        if vigente is not None and str(vigente) == resultado.value:
            return

        fecha_efecto = datos.get("fecha_efecto")
        if isinstance(fecha_efecto, datetime):
            fecha_efecto = fecha_efecto.date()
        elif not isinstance(fecha_efecto, date):
            fecha_efecto = datos["peri_fin"]

        await self._repo.insertar_concepto(
            {
                "lote_id": datos["lote_id"],
                "peri_id": peri_id,
                "indi_id": indi_id,
                "conc_resultado": resultado.value,
                "conc_fecha_efecto": fecha_efecto,
                "norm_id": datos.get("norm_id"),
                "conc_motivo": motivo,
            }
        )

    async def resumen_lote(self, actor: Actor, lote_id: int) -> e.ResumenLote:
        datos = await self._repo.resumen(lote_id, actor.usua_id)
        if datos is None:
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        serie_filas = await self._repo.serie(lote_id, None, None, actor.usua_id)
        datos["serie"] = [e.PuntoMtd(**sf) for sf in serie_filas]
        return e.ResumenLote(**datos)

    async def serie_mtd(
        self, actor: Actor, lote_id: int, desde: date | None, hasta: date | None
    ) -> list[e.PuntoMtd]:
        filas = await self._repo.serie(lote_id, desde, hasta, actor.usua_id)
        return [e.PuntoMtd(**f) for f in filas]

    async def periodos(self, actor: Actor, lote_id: int) -> list[e.PeriodoTrampeo]:
        if not await self._repo.lote_visible(lote_id, actor.usua_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        filas = await self._repo.periodos(lote_id)
        return [e.PeriodoTrampeo(**f) for f in filas]

    async def versiones_mtd(self, actor: Actor, lote_id: int, peri_id: int) -> list[e.VersionMtd]:
        if not await self._repo.lote_visible(lote_id, actor.usua_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        filas = await self._repo.versiones_mtd(peri_id)
        return [e.VersionMtd(**f) for f in filas]

    async def conceptos(self, actor: Actor, lote_id: int) -> list[e.Concepto]:
        if not await self._repo.lote_visible(lote_id, actor.usua_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        filas = await self._repo.conceptos(lote_id)
        return [e.Concepto(**f) for f in filas]

    async def concepto_a_fecha(self, actor: Actor, lote_id: int, fecha: date) -> e.ConceptoAFecha:
        if not await self._repo.lote_visible(lote_id, actor.usua_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        fila = await self._repo.concepto_a_fecha(lote_id, fecha)
        if fila is None:
            return e.ConceptoAFecha(
                fecha_consulta=fecha,
                concepto=None,
                explicacion="No había concepto emitido con fecha de efecto igual o anterior a la fecha consultada.",
            )
        conc = e.Concepto(**fila)
        if conc.conc_resultado == ResultadoConcepto.NO_EMITIDO:
            explicacion = "No emitido: no había umbral configurado para esa fecha."
        else:
            explicacion = (
                f"Concepto {conc.conc_resultado.value} vigente al {fecha.isoformat()} "
                f"(efecto desde {conc.conc_fecha_efecto.isoformat()})."
            )
        return e.ConceptoAFecha(fecha_consulta=fecha, concepto=conc, explicacion=explicacion)

    async def etapa(self, actor: Actor, lote_id: int, fecha: date | None) -> e.EtapaLote:
        if not await self._repo.lote_visible(lote_id, actor.usua_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        datos = await self._repo.etapa(lote_id, fecha or datetime.now(UTC).date())
        return e.EtapaLote(**datos)

    async def cultivo_productor(self, actor: Actor) -> e.CultivoProductor:
        """Solo PRODUCTOR: sus lotes (predios de los que es dueño)."""
        raise NoImplementado("Cultivo del productor")
