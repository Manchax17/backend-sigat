"""Casos de uso de concepto y resúmenes. ``ServicioConcepto.reemitir`` implementa
``sincronizacion.puertos.PuertoConcepto``."""

from datetime import date, datetime
from typing import Any

from sigat_api.compartido.errores import NoEncontrado, NoImplementado, ReglaNegocio
from sigat_api.compartido.tipos import Actor, ResultadoConcepto, ResultadoContraste
from sigat_api.modules.concepto import esquemas as e
from sigat_api.modules.concepto.repositorio import RepositorioConcepto

ZONA_PREDETERMINADA = "America/Bogota"

_EXPLICACION = {
    ResultadoConcepto.APTO: "Apto: el MTD del periodo estaba por debajo del umbral.",
    ResultadoConcepto.NO_APTO: "No apto: el MTD del periodo igualaba o superaba el umbral.",
    ResultadoConcepto.NO_EMITIDO: "No emitido: no había umbral configurado para esa fecha.",
}
SIN_CONCEPTO = "Sin concepto: a esa fecha aún no se había emitido ningún concepto para el lote."

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


def explicar_concepto(concepto: e.Concepto | None) -> str:
    """Texto de RF-024 para quien consulta: emitido (apto / no apto), no emitido (sin umbral) o
    ausencia de concepto. Distingue "no había umbral" de "no se había emitido nada"."""
    if concepto is None:
        return SIN_CONCEPTO
    return _EXPLICACION[ResultadoConcepto(concepto.conc_resultado)]


def etapa_lote(fila: dict[str, Any]) -> e.EtapaLote:
    """RN-012: la etapa se DERIVA; la declarada convive. Solo hay discrepancia si existen ambas y
    difieren (sin regla o sin declaración no se afirma nada)."""
    derivada, declarada = fila.get("derivada"), fila.get("declarada")
    return e.EtapaLote(
        derivada=derivada,
        declarada=declarada,
        declarada_el=fila.get("declarada_el"),
        discrepancia=derivada is not None and declarada is not None and derivada != declarada,
    )


def periodo_actual(periodos: list[e.PeriodoTrampeo]) -> e.PeriodoTrampeo | None:
    """El periodo abierto más reciente; si no hay abierto, ninguno (no se inventa uno)."""
    abiertos = [p for p in periodos if p.peri_estado == "abierto"]
    return max(abiertos, key=lambda p: (p.peri_inicio, p.peri_id)) if abiertos else None


def revision_pendiente(visitas_por_revisar: int) -> str | None:
    if visitas_por_revisar <= 0:
        return None
    if visitas_por_revisar == 1:
        return "1 visita por revisar"
    return f"{visitas_por_revisar} visitas por revisar"


class ServicioConcepto:
    def __init__(self, repo: RepositorioConcepto, zona_horaria: str = ZONA_PREDETERMINADA) -> None:
        self._repo = repo
        self._zona = zona_horaria

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

    async def _exigir_lote_visible(self, actor: Actor, lote_id: int) -> None:
        if not await self._repo.lote_visible(actor.usua_id, lote_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")

    async def resumen_lote(self, actor: Actor, lote_id: int) -> e.ResumenLote:
        await self._exigir_lote_visible(actor, lote_id)
        fila = await self._repo.resumen(actor.usua_id, lote_id, self._zona)

        if fila is None:  # el lote no existe o no es visible
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")

        periodos = [e.PeriodoTrampeo(**p) for p in await self._repo.periodos(lote_id)]
        serie = [e.PuntoMtd(**p) for p in await self._repo.serie(actor.usua_id, lote_id, None, None)]
        etapa_dict = await self._repo.etapa(lote_id, None, self._zona)
        etapa = etapa_lote(etapa_dict)
        # Excluir los campos que el servicio construye explícitamente para evitar colisión de kwargs.
        _construidos = {"mtd_es_estimacion", "periodo_actual", "etapa", "revision_pendiente", "serie"}
        datos = {k: v for k, v in fila.items() if k in e.ResumenLote.model_fields and k not in _construidos}
        return e.ResumenLote(
            **datos,
            mtd_es_estimacion=False,  # ADR-15: la cifra del servidor es la autorizada
            periodo_actual=periodo_actual(periodos),
            etapa=etapa,
            revision_pendiente=revision_pendiente(int(fila.get("visitas_por_revisar") or 0)),
            serie=serie,
        )

    async def serie_mtd(
        self, actor: Actor, lote_id: int, desde: date | None, hasta: date | None
    ) -> list[e.PuntoMtd]:
        if desde is not None and hasta is not None and desde > hasta:
            raise ReglaNegocio(
                "La fecha inicial no puede ser posterior a la final.", codigo="RANGO_FECHAS_INVALIDO"
            )
        await self._exigir_lote_visible(actor, lote_id)
        filas = await self._repo.serie(actor.usua_id, lote_id, desde, hasta)
        return [e.PuntoMtd(**f) for f in filas]

    async def periodos(self, actor: Actor, lote_id: int) -> list[e.PeriodoTrampeo]:
        await self._exigir_lote_visible(actor, lote_id)
        return [e.PeriodoTrampeo(**f) for f in await self._repo.periodos(lote_id)]

    async def versiones_mtd(self, actor: Actor, lote_id: int, peri_id: int) -> list[e.VersionMtd]:
        await self._exigir_lote_visible(actor, lote_id)
        if not await self._repo.periodo_de_lote(lote_id, peri_id):
            raise NoEncontrado("El periodo no existe en este lote.", codigo="PERIODO_NO_ENCONTRADO")
        return [e.VersionMtd(**f) for f in await self._repo.versiones_mtd(peri_id)]

    async def conceptos(self, actor: Actor, lote_id: int) -> list[e.Concepto]:
        await self._exigir_lote_visible(actor, lote_id)
        return [e.Concepto(**f) for f in await self._repo.conceptos(lote_id)]

    async def concepto_a_fecha(self, actor: Actor, lote_id: int, fecha: date) -> e.ConceptoAFecha:
        """RF-024: el estado que se habría obtenido ESE día, no el de hoy."""
        await self._exigir_lote_visible(actor, lote_id)
        fila = await self._repo.concepto_a_fecha(lote_id, fecha, self._zona)
        concepto = e.Concepto(**fila) if fila is not None else None
        return e.ConceptoAFecha(
            fecha_consulta=fecha, concepto=concepto, explicacion=explicar_concepto(concepto)
        )

    async def etapa(self, actor: Actor, lote_id: int, fecha: date | None) -> e.EtapaLote:
        await self._exigir_lote_visible(actor, lote_id)
        return etapa_lote(await self._repo.etapa(lote_id, fecha, self._zona))

    async def cultivo_productor(self, actor: Actor) -> e.CultivoProductor:
        """Solo PRODUCTOR: sus lotes (predios de los que es dueño)."""
        raise NoImplementado("Cultivo del productor")
