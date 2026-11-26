"""Consulta de conceptos agronómicos (issue #9): resumen, serie MTD, periodos, versiones, conceptos,
concepto a una fecha (RF-024) y etapa fenológica (RN-012).

No toca PostgreSQL: el repositorio se sustituye por un doble que devuelve filas con la forma de las
consultas reales. Se prueban las decisiones del servicio (404 sin revelar datos ajenos, explicación
de RF-024, discrepancia de RN-012, casos sin información). La semántica SQL se prueba contra la base
real en ``tests/integracion/test_conceptos_bd.py``.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from sigat_api.compartido.errores import NoEncontrado, ReglaNegocio
from sigat_api.compartido.tipos import Actor, Cliente, ResultadoConcepto, ResultadoContraste, RolCodigo
from sigat_api.modules.concepto import esquemas as e
from sigat_api.modules.concepto.servicio import (
    SIN_CONCEPTO,
    ServicioConcepto,
    etapa_lote,
    explicar_concepto,
    periodo_actual,
    revision_pendiente,
)

AGRONOMO = Actor(usua_id=3, rol=RolCodigo.AGRONOMO_SUPERVISOR, disp_id=None, cliente=Cliente.WEB)
LOTE = 11
AJENO = 99

PERIODO: dict[str, Any] = {
    "peri_id": 1,
    "lote_id": LOTE,
    "vigi_id": 1,
    "prog_id": 1,
    "peri_inicio": date(2026, 11, 1),
    "peri_fin": date(2026, 11, 7),
    "peri_estado": "cerrado",
}
PERIODO_ABIERTO = {
    **PERIODO,
    "peri_id": 2,
    "peri_inicio": date(2026, 11, 8),
    "peri_fin": date(2026, 11, 14),
    "peri_estado": "abierto",
}

PUNTO: dict[str, Any] = {
    "peri_id": 1,
    "peri_inicio": date(2026, 11, 1),
    "peri_fin": date(2026, 11, 7),
    "mtd": Decimal("0.142857"),
    "moscas": 3,
    "trampas": 3,
    "dias_exposicion": Decimal("7.000"),
    "umbral": Decimal("0.5000"),
    "contraste": "por_debajo_umbral",
    "escenario": "Bajo",
}

VERSION: dict[str, Any] = {
    "indi_id": 5,
    "indi_version": 1,
    "indi_valor": Decimal("2.1"),
    "indi_moscas": 21,
    "indi_trampas": 1,
    "indi_dias_exposicion": Decimal("10.000"),
    "indi_umbral_aplicado": None,
    "indi_resultado_contraste": "sin_umbral",
    "escenario": None,
    "indi_calculado_en": datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
    "indi_motivo_recalculo": None,
}

CONCEPTO: dict[str, Any] = {
    "conc_id": 7,
    "lote_id": LOTE,
    "peri_id": 1,
    "conc_version": 1,
    "conc_resultado": "apto",
    "conc_fecha_efecto": date(2026, 11, 5),
    "norma": "RES-448",
    "conc_emitido_en": datetime(2026, 11, 5, 15, 0, tzinfo=UTC),
    "conc_motivo": None,
    "mtd": Decimal("0.142857"),
}

RESUMEN: dict[str, Any] = {
    "lote_id": LOTE,
    "lote_descripcion": "Lote norte",
    "mtd": Decimal("0.142857"),
    "escenario": "Bajo",
    "umbral": Decimal("0.5000"),
    "contraste": "por_debajo_umbral",
    "concepto": "apto",
    "concepto_fecha_efecto": date(2026, 11, 5),
    "inspecciones": 4,
    "inspecciones_mes": 2,
    "aplicaciones": 1,
    "ultima_aplicacion": datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
    "lecturas": 6,
    "especimenes": 3,
    "trampas_vigentes": 2,
    "trampas_con_capturas": 1,
    "ultima_visita": datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
    "ultima_cosecha": None,
    "cosecha_esperada": date(2027, 1, 15),
    "rendimiento_estimado": Decimal("1200.000"),
    "rendimiento_real": None,
    "hallazgos": 0,
    "visitas_por_revisar": 2,
}


class RepoFalso:
    """Doble del repositorio: responde con filas fijas y registra los argumentos recibidos."""

    def __init__(self, **sobre: Any) -> None:
        self.visibles = {LOTE}
        self.periodos_lote = {(LOTE, 1), (LOTE, 2)}
        self.filas: dict[str, Any] = {
            "resumen": dict(RESUMEN),
            "serie": [dict(PUNTO)],
            "periodos": [dict(PERIODO), dict(PERIODO_ABIERTO)],
            "versiones_mtd": [dict(VERSION)],
            "conceptos": [dict(CONCEPTO)],
            "concepto_a_fecha": dict(CONCEPTO),
            "etapa": {"derivada": "Floración", "declarada": "Floración", "declarada_el": None},
        }
        self.filas.update(sobre)
        self.llamadas: list[tuple[str, tuple[Any, ...]]] = []

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        return lote_id in self.visibles

    async def periodo_de_lote(self, lote_id: int, peri_id: int) -> bool:
        return (lote_id, peri_id) in self.periodos_lote

    def _dar(self, nombre: str, *args: Any) -> Any:
        self.llamadas.append((nombre, args))
        return self.filas[nombre]

    async def resumen(self, *args: Any) -> Any:
        return self._dar("resumen", *args)

    async def serie(self, *args: Any) -> Any:
        return self._dar("serie", *args)

    async def periodos(self, *args: Any) -> Any:
        return self._dar("periodos", *args)

    async def versiones_mtd(self, *args: Any) -> Any:
        return self._dar("versiones_mtd", *args)

    async def conceptos(self, *args: Any) -> Any:
        return self._dar("conceptos", *args)

    async def concepto_a_fecha(self, *args: Any) -> Any:
        return self._dar("concepto_a_fecha", *args)

    async def etapa(self, *args: Any) -> Any:
        return self._dar("etapa", *args)


def servicio(repo: RepoFalso) -> ServicioConcepto:
    return ServicioConcepto(repo, "America/Bogota")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- visibilidad
@pytest.mark.parametrize(
    "llamada",
    [
        lambda s: s.resumen_lote(AGRONOMO, AJENO),
        lambda s: s.serie_mtd(AGRONOMO, AJENO, None, None),
        lambda s: s.periodos(AGRONOMO, AJENO),
        lambda s: s.versiones_mtd(AGRONOMO, AJENO, 1),
        lambda s: s.conceptos(AGRONOMO, AJENO),
        lambda s: s.concepto_a_fecha(AGRONOMO, AJENO, date(2026, 11, 5)),
        lambda s: s.etapa(AGRONOMO, AJENO, None),
    ],
)
async def test_lote_no_visible_o_inexistente_responde_404_sin_consultar(llamada: Any) -> None:
    repo = RepoFalso()
    with pytest.raises(NoEncontrado) as err:
        await llamada(servicio(repo))
    assert err.value.codigo == "LOTE_NO_ENCONTRADO"
    assert repo.llamadas == []  # no se lee nada del lote ajeno


async def test_versiones_de_un_periodo_de_otro_lote_responde_404() -> None:
    repo = RepoFalso()
    with pytest.raises(NoEncontrado) as err:
        await servicio(repo).versiones_mtd(AGRONOMO, LOTE, 77)
    assert err.value.codigo == "PERIODO_NO_ENCONTRADO"
    assert repo.llamadas == []


# ---------------------------------------------------------------------------- resumen
async def test_resumen_integra_indicadores_periodo_etapa_y_serie() -> None:
    repo = RepoFalso()
    r = await servicio(repo).resumen_lote(AGRONOMO, LOTE)

    assert r.lote_descripcion == "Lote norte"
    assert r.mtd == Decimal("0.142857")
    assert r.mtd_es_estimacion is False
    assert r.contraste == ResultadoContraste.POR_DEBAJO_UMBRAL
    assert r.concepto == ResultadoConcepto.APTO
    assert r.periodo_actual is not None
    assert r.periodo_actual.peri_id == 2
    assert r.etapa.derivada == "Floración"
    assert r.etapa.discrepancia is False
    assert [p.peri_id for p in r.serie] == [1]
    assert r.revision_pendiente == "2 visitas por revisar"
    assert ("resumen", (AGRONOMO.usua_id, LOTE, "America/Bogota")) in repo.llamadas


async def test_resumen_sin_informacion_no_inventa_valores() -> None:
    vacio = {
        **RESUMEN,
        "mtd": None,
        "escenario": None,
        "umbral": None,
        "contraste": None,
        "concepto": None,
        "concepto_fecha_efecto": None,
        "inspecciones": 0,
        "inspecciones_mes": 0,
        "aplicaciones": 0,
        "ultima_aplicacion": None,
        "lecturas": 0,
        "especimenes": 0,
        "trampas_vigentes": 0,
        "trampas_con_capturas": 0,
        "ultima_visita": None,
        "cosecha_esperada": None,
        "rendimiento_estimado": None,
        "visitas_por_revisar": 0,
    }
    repo = RepoFalso(
        resumen=vacio,
        serie=[],
        periodos=[],
        etapa={"derivada": None, "declarada": None, "declarada_el": None},
    )
    r = await servicio(repo).resumen_lote(AGRONOMO, LOTE)

    assert r.mtd is None
    assert r.umbral is None
    assert r.concepto is None
    assert r.periodo_actual is None
    assert r.serie == []
    assert r.revision_pendiente is None
    assert r.etapa.discrepancia is False


async def test_resumen_de_lote_dado_de_baja_responde_404() -> None:
    with pytest.raises(NoEncontrado):
        await servicio(RepoFalso(resumen=None)).resumen_lote(AGRONOMO, LOTE)


# ---------------------------------------------------------------------------- serie, periodos, versiones
async def test_serie_pasa_los_filtros_de_fecha() -> None:
    repo = RepoFalso()
    serie = await servicio(repo).serie_mtd(AGRONOMO, LOTE, date(2026, 11, 1), date(2026, 11, 30))
    assert serie[0].contraste == ResultadoContraste.POR_DEBAJO_UMBRAL
    assert repo.llamadas == [("serie", (AGRONOMO.usua_id, LOTE, date(2026, 11, 1), date(2026, 11, 30)))]


async def test_serie_con_rango_invertido_es_regla_de_negocio() -> None:
    with pytest.raises(ReglaNegocio) as err:
        await servicio(RepoFalso()).serie_mtd(AGRONOMO, LOTE, date(2026, 11, 30), date(2026, 11, 1))
    assert err.value.codigo == "RANGO_FECHAS_INVALIDO"


async def test_periodos_del_lote() -> None:
    periodos = await servicio(RepoFalso()).periodos(AGRONOMO, LOTE)
    assert [(p.peri_id, p.peri_estado) for p in periodos] == [(1, "cerrado"), (2, "abierto")]


async def test_versiones_conservan_valor_anterior_y_motivo_de_recalculo() -> None:
    recalculo = {
        **VERSION,
        "indi_id": 6,
        "indi_version": 2,
        "indi_valor": Decimal("1.4"),
        "indi_moscas": 14,
        "indi_motivo_recalculo": "Corrección de lectura",
    }
    versiones = await servicio(RepoFalso(versiones_mtd=[dict(VERSION), recalculo])).versiones_mtd(
        AGRONOMO, LOTE, 1
    )
    assert [(v.indi_version, v.indi_valor, v.indi_motivo_recalculo) for v in versiones] == [
        (1, Decimal("2.1"), None),
        (2, Decimal("1.4"), "Corrección de lectura"),
    ]


async def test_conceptos_devuelve_todas_las_versiones() -> None:
    v2 = {
        **CONCEPTO,
        "conc_id": 8,
        "conc_version": 2,
        "conc_resultado": "no_apto",
        "conc_motivo": "Recálculo",
    }
    conceptos = await servicio(RepoFalso(conceptos=[dict(CONCEPTO), v2])).conceptos(AGRONOMO, LOTE)
    assert [(c.conc_version, c.conc_resultado) for c in conceptos] == [
        (1, ResultadoConcepto.APTO),
        (2, ResultadoConcepto.NO_APTO),
    ]


# ---------------------------------------------------------------------------- RF-024
async def test_concepto_a_fecha_consulta_ese_dia_en_la_zona_institucional() -> None:
    repo = RepoFalso()
    r = await servicio(repo).concepto_a_fecha(AGRONOMO, LOTE, date(2026, 11, 6))
    assert r.fecha_consulta == date(2026, 11, 6)
    assert r.concepto is not None
    assert r.concepto.conc_id == 7
    assert r.explicacion.startswith("Apto")
    assert repo.llamadas == [("concepto_a_fecha", (LOTE, date(2026, 11, 6), "America/Bogota"))]


async def test_concepto_a_fecha_sin_concepto_lo_explica() -> None:
    r = await servicio(RepoFalso(concepto_a_fecha=None)).concepto_a_fecha(AGRONOMO, LOTE, date(2026, 1, 1))
    assert r.concepto is None
    assert r.explicacion == SIN_CONCEPTO


@pytest.mark.parametrize(
    ("resultado", "inicio"),
    [("apto", "Apto"), ("no_apto", "No apto"), ("no_emitido", "No emitido: no había umbral")],
)
def test_explicacion_distingue_emitido_no_emitido_y_sin_umbral(resultado: str, inicio: str) -> None:
    assert explicar_concepto(e.Concepto(**{**CONCEPTO, "conc_resultado": resultado})).startswith(inicio)


# ---------------------------------------------------------------------------- RN-012
@pytest.mark.parametrize(
    ("derivada", "declarada", "discrepancia"),
    [
        ("Floración", "Floración", False),
        ("Floración", "Cuajado", True),
        (None, "Cuajado", False),  # sin regla o sin fecha de plantación: no se afirma discrepancia
        ("Floración", None, False),  # sin declaración
        (None, None, False),
    ],
)
def test_etapa_marca_discrepancia_solo_si_existen_ambas_y_difieren(
    derivada: str | None, declarada: str | None, discrepancia: bool
) -> None:
    etapa = etapa_lote({"derivada": derivada, "declarada": declarada, "declarada_el": None})
    assert etapa.discrepancia is discrepancia


async def test_etapa_pasa_la_fecha_consultada() -> None:
    repo = RepoFalso(
        etapa={
            "derivada": "Floración",
            "declarada": "Cuajado",
            "declarada_el": datetime(2026, 11, 2, tzinfo=UTC),
        }
    )
    etapa = await servicio(repo).etapa(AGRONOMO, LOTE, date(2026, 11, 3))
    assert etapa.discrepancia is True
    assert repo.llamadas == [("etapa", (LOTE, date(2026, 11, 3), "America/Bogota"))]


# ---------------------------------------------------------------------------- auxiliares
def test_periodo_actual_es_el_abierto_mas_reciente() -> None:
    otro_abierto = {**PERIODO_ABIERTO, "peri_id": 3, "peri_inicio": date(2026, 11, 15)}
    periodos = [e.PeriodoTrampeo(**p) for p in (PERIODO, PERIODO_ABIERTO, otro_abierto)]
    actual = periodo_actual(periodos)
    assert actual is not None
    assert actual.peri_id == 3
    assert periodo_actual([e.PeriodoTrampeo(**PERIODO)]) is None


@pytest.mark.parametrize(
    ("n", "texto"), [(0, None), (1, "1 visita por revisar"), (3, "3 visitas por revisar")]
)
def test_revision_pendiente(n: int, texto: str | None) -> None:
    assert revision_pendiente(n) == texto
