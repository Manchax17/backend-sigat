"""Acceso a datos de concepto y vistas de resumen.

Solo lectura sobre las vistas que publica la BD (ver ``__init__``). Visibilidad (RNF-015): un lote
es visible si está en ``fn_lotes_visibles(:u)``; el servicio lo exige ANTES de consultar y trata lo
no visible como inexistente (404, nunca 403). Las consultas por lote no repiten el filtro salvo
donde la BD ya publica la función filtrada (``fn_lote_resumen_app``, ``fn_mtd_serie_lote``).

Fechas: la conexión trabaja en UTC; "ese día" se interpreta en la zona horaria institucional
(``:zona``), igual que ``/yo``.
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado

# Fin del día ``:fecha`` en la zona institucional, como instante (timestamptz).
_FIN_DEL_DIA = "((CAST(:fecha AS date) + 1)::timestamp AT TIME ZONE CAST(:zona AS text))"

# Hoy en la zona institucional cuando no se indica fecha.
_FECHA_O_HOY = "COALESCE(CAST(:fecha AS date), (now() AT TIME ZONE CAST(:zona AS text))::date)"

_PERIODO = """
SELECT p.peri_id, p.lote_id, p.vigi_id, p.prog_id, p.peri_inicio, p.peri_fin, p.peri_estado
  FROM sigat.periodo_trampeo p
"""

# Concepto con el MTD que lo sustenta y la norma aplicada. ``{origen}`` = filas de concepto_aptitud.
_CONCEPTO = """
SELECT c.conc_id, c.lote_id, c.peri_id, c.conc_version, c.conc_resultado, c.conc_fecha_efecto,
       n.norm_codigo AS norma, c.conc_emitido_en, c.conc_motivo, i.indi_valor AS mtd
  FROM {origen} c
  JOIN sigat.indice_mtd i ON i.indi_id = c.indi_id
  LEFT JOIN sigat.norma n ON n.norm_id = c.norm_id
"""

# Indicadores del resumen que no publica ``v_lote_resumen_app``. Cada subconsulta usa la versión
# vigente de los registros versionados y descarta los anulados (no se inventan valores: sin datos,
# NULL o 0).
_RESUMEN = """
SELECT r.lote_id, l.lote_descripcion, r.mtd, r.escenario, r.umbral, r.contraste, r.concepto,
       r.inspecciones::int, r.aplicaciones::int, r.lecturas::int, r.especimenes::int,
       r.ultima_cosecha, r.hallazgos::int,
       (SELECT c.conc_fecha_efecto FROM sigat.v_concepto_vigente c WHERE c.lote_id = r.lote_id
         ORDER BY c.conc_fecha_efecto DESC, c.conc_id DESC LIMIT 1) AS concepto_fecha_efecto,
       (SELECT count(*) FROM sigat.visita_lote vl JOIN sigat.visita v ON v.visi_id = vl.visi_id
         WHERE vl.lote_id = r.lote_id
           AND v.visi_t_inicio_obs >= date_trunc('month', now() AT TIME ZONE CAST(:zona AS text))
                                      AT TIME ZONE CAST(:zona AS text))::int AS inspecciones_mes,
       rl.ultima_aplicacion, rl.ultima_visita, rl.trampas_vigentes::int,
       (SELECT count(DISTINCT lv.tram_id) FROM sigat.v_lectura_vigente lv
          JOIN sigat.ubicacion_trampa u ON u.ubic_id = lv.ubic_id
         WHERE u.lote_id = r.lote_id AND lv.lect_especimenes > 0)::int AS trampas_con_capturas,
       (SELECT c.cose_fecha_esperada FROM (SELECT DISTINCT ON (cose_registro_id) * FROM sigat.cosecha
                                            ORDER BY cose_registro_id, cose_version DESC) c
         WHERE c.lote_id = r.lote_id AND NOT c.cose_anulada AND c.cose_fecha_real IS NULL
         ORDER BY c.cose_fecha_esperada LIMIT 1) AS cosecha_esperada,
       (SELECT c.cose_volumen_real FROM (SELECT DISTINCT ON (cose_registro_id) * FROM sigat.cosecha
                                          ORDER BY cose_registro_id, cose_version DESC) c
         WHERE c.lote_id = r.lote_id AND NOT c.cose_anulada AND c.cose_fecha_real IS NOT NULL
         ORDER BY c.cose_fecha_real DESC LIMIT 1) AS rendimiento_real,
       (SELECT et.esti_total FROM sigat.v_estimacion_total et
          JOIN sigat.estimacion_produccion ep
            ON ep.esti_registro_id = et.esti_registro_id AND ep.esti_version = et.esti_version
         WHERE et.lote_id = r.lote_id
         ORDER BY ep.esti_t_observacion DESC LIMIT 1) AS rendimiento_estimado,
       (SELECT count(*) FROM sigat.visita_lote vl JOIN sigat.visita v ON v.visi_id = vl.visi_id
         WHERE vl.lote_id = r.lote_id
           AND v.visi_estado_revision IN ('pendiente', 'en_revision'))::int AS visitas_por_revisar
  FROM sigat.fn_lote_resumen_app(:u) r
  JOIN sigat.lote l ON l.lote_id = r.lote_id
  JOIN sigat.v_resumen_lote rl ON rl.lote_id = r.lote_id
 WHERE r.lote_id = :lote
"""


class RepositorioConcepto:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    async def _filas(self, sql: str, parametros: dict[str, Any]) -> list[dict[str, Any]]:
        resultado = await self._con.execute(text(sql), parametros)
        return [dict(f) for f in resultado.mappings()]

    async def _fila(self, sql: str, parametros: dict[str, Any]) -> dict[str, Any] | None:
        filas = await self._filas(sql, parametros)
        return filas[0] if filas else None

    # ------------------------------------------------------------------ existencia y visibilidad
    async def lote_visible(self, usua_id: int | None, lote_id: int) -> bool:
        if usua_id is None:
            resultado = await self._con.scalar(
                text("SELECT EXISTS (SELECT 1 FROM sigat.lote WHERE lote_id = :lote)"),
                {"lote": lote_id},
            )
            return bool(resultado)
        resultado = await self._con.scalar(
            text("SELECT EXISTS (SELECT 1 FROM sigat.fn_lotes_visibles(:u) WHERE lote_id = :lote)"),
            {"u": usua_id, "lote": lote_id},
        )
        return bool(resultado)

    async def periodo_de_lote(self, lote_id: int, peri_id: int) -> bool:
        resultado = await self._con.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM sigat.periodo_trampeo WHERE peri_id = :peri AND lote_id = :lote)"
            ),
            {"peri": peri_id, "lote": lote_id},
        )
        return bool(resultado)

    # ------------------------------------------------------------------ consultas
    async def resumen(self, usua_id: int | None, lote_id: int, zona: str) -> dict[str, Any] | None:
        """fn_lote_resumen_app(:usua) WHERE lote_id = :lote + indicadores complementarios."""
        return await self._fila(_RESUMEN, {"u": usua_id, "lote": lote_id, "zona": zona})

    async def serie(
        self, usua_id: int | None, lote_id: int, desde: date | None, hasta: date | None
    ) -> list[dict[str, Any]]:
        """fn_mtd_serie_lote(:usua). Un periodo entra si se SOLAPA con [desde, hasta]."""
        return await self._filas(
            """
SELECT s.peri_id, s.peri_inicio, s.peri_fin, s.mtd, s.indi_moscas AS moscas, s.indi_trampas AS trampas,
       s.indi_dias_exposicion AS dias_exposicion, s.umbral, s.contraste, e.esce_descripcion AS escenario
  FROM sigat.fn_mtd_serie_lote(:u) s
  LEFT JOIN sigat.escenario_trampeo e ON e.esce_id = s.esce_id
 WHERE s.lote_id = :lote
   AND (CAST(:desde AS date) IS NULL OR s.peri_fin >= CAST(:desde AS date))
   AND (CAST(:hasta AS date) IS NULL OR s.peri_inicio <= CAST(:hasta AS date))
 ORDER BY s.peri_inicio, s.peri_id
""",
            {"u": usua_id, "lote": lote_id, "desde": desde, "hasta": hasta},
        )

    async def periodos(self, lote_id: int) -> list[dict[str, Any]]:
        return await self._filas(
            _PERIODO + " WHERE p.lote_id = :lote ORDER BY p.peri_inicio, p.peri_id", {"lote": lote_id}
        )

    async def versiones_mtd(self, peri_id: int) -> list[dict[str, Any]]:
        """Todas las versiones del MTD general del periodo (sin especie), de la original a la vigente."""
        return await self._filas(
            """
SELECT i.indi_id, i.indi_version, i.indi_valor, i.indi_moscas, i.indi_trampas, i.indi_dias_exposicion,
       i.indi_umbral_aplicado, i.indi_resultado_contraste, e.esce_descripcion AS escenario,
       i.indi_calculado_en, i.indi_motivo_recalculo
  FROM sigat.indice_mtd i
  LEFT JOIN sigat.escenario_trampeo e ON e.esce_id = i.esce_id
 WHERE i.peri_id = :peri AND i.espm_id IS NULL
 ORDER BY i.indi_version
""",
            {"peri": peri_id},
        )

    async def conceptos(self, lote_id: int) -> list[dict[str, Any]]:
        """Todas las versiones emitidas, por periodo y versión."""
        return await self._filas(
            _CONCEPTO.format(origen="sigat.concepto_aptitud")
            + " WHERE c.lote_id = :lote ORDER BY c.peri_id, c.conc_version",
            {"lote": lote_id},
        )

    async def concepto_a_fecha(self, lote_id: int, fecha: date, zona: str) -> dict[str, Any] | None:
        """RF-024. Lo que se sabía al final de ese día: de cada periodo, la última versión EMITIDA
        hasta entonces (``conc_emitido_en`` <= fin del día); de ellas, la que ya surtía efecto
        (``conc_fecha_efecto`` <= fecha) más reciente. Una reemisión posterior NO cambia la respuesta."""
        # Solo se interpolan constantes del módulo (sin datos del usuario): de ahí el noqa.
        origen = f"""(SELECT * FROM (  -- RF-024
          SELECT DISTINCT ON (peri_id) * FROM sigat.concepto_aptitud
           WHERE lote_id = :lote AND conc_emitido_en < {_FIN_DEL_DIA}
           ORDER BY peri_id, conc_version DESC) conocidos
         WHERE conc_fecha_efecto <= CAST(:fecha AS date))"""  # noqa: S608
        return await self._fila(
            _CONCEPTO.format(origen=origen)
            + " ORDER BY c.conc_fecha_efecto DESC, c.conc_emitido_en DESC, c.conc_id DESC LIMIT 1",
            {"lote": lote_id, "fecha": fecha, "zona": zona},
        )

    async def etapa(self, lote_id: int, fecha: date | None, zona: str) -> dict[str, Any]:
        """RN-012. ``derivada`` = fn_etapa_derivada(lote, fecha); ``declarada`` = la última etapa
        declarada vigente (no anulada) observada hasta el final de ese día."""
        fila = await self._fila(
            f"""
WITH f AS (SELECT {_FECHA_O_HOY} AS fecha)
SELECT (SELECT fe.feno_descripcion FROM sigat.fenologia fe
         WHERE fe.feno_id = sigat.fn_etapa_derivada(CAST(:lote AS integer), f.fecha)) AS derivada,
       d.feno_descripcion AS declarada, d.etap_t_observacion AS declarada_el
  FROM f
  LEFT JOIN LATERAL (
       SELECT fe.feno_descripcion, e.etap_t_observacion
         FROM (SELECT DISTINCT ON (etap_registro_id) * FROM sigat.etapa_declarada
                WHERE lote_id = :lote ORDER BY etap_registro_id, etap_version DESC) e
         JOIN sigat.fenologia fe ON fe.feno_id = e.feno_id
        WHERE NOT e.etap_anulada
          AND e.etap_t_observacion < ((f.fecha + 1)::timestamp AT TIME ZONE CAST(:zona AS text))
        ORDER BY e.etap_t_observacion DESC LIMIT 1
  ) d ON true
""",  # noqa: S608 (solo constantes del módulo)
            {"lote": lote_id, "fecha": fecha, "zona": zona},
        )
        # La CTE siempre devuelve una fila; el respaldo solo satisface al verificador de tipos.
        return fila or {"derivada": None, "declarada": None, "declarada_el": None}

    async def datos_reemision(self, peri_id: int, indi_id: int) -> dict[str, Any] | None:
        """Obtiene el contexto del periodo, índice MTD, fecha de observación del disparador y concepto actual."""
        sql = """
        SELECT pt.peri_id,
               pt.lote_id,
               pt.peri_fin,
               pi.norm_id,
               im.indi_id,
               im.indi_resultado_contraste AS contraste,
               COALESCE(
                   (SELECT (lt.lect_t_observacion AT TIME ZONE pr.pred_zona_horaria)::date
                      FROM sigat.lectura_trampa lt
                     WHERE lt.oper_id = im.oper_id_disparador
                     ORDER BY lt.lect_t_observacion DESC
                     LIMIT 1),
                   (SELECT max((lt.lect_t_observacion AT TIME ZONE pr.pred_zona_horaria)::date)
                      FROM sigat.v_lectura_vigente lt
                      JOIN sigat.ubicacion_trampa u ON u.ubic_id = lt.ubic_id
                     WHERE u.lote_id = pt.lote_id
                       AND (lt.lect_t_observacion AT TIME ZONE pr.pred_zona_horaria)::date
                           BETWEEN pt.peri_inicio AND pt.peri_fin),
                   pt.peri_fin
               ) AS fecha_efecto,
               (SELECT cv.conc_resultado
                  FROM sigat.v_concepto_vigente cv
                 WHERE cv.lote_id = pt.lote_id AND cv.peri_id = pt.peri_id
                 LIMIT 1) AS resultado_vigente
          FROM sigat.periodo_trampeo pt
          JOIN sigat.lote l ON l.lote_id = pt.lote_id
          JOIN sigat.predio pr ON pr.pred_id = l.pred_id
          JOIN sigat.programa_inspeccion pi ON pi.prog_id = pt.prog_id
          JOIN sigat.indice_mtd im ON im.indi_id = :indi_id AND im.peri_id = pt.peri_id
         WHERE pt.peri_id = :peri_id
        """
        fila = (
            (await self._con.execute(text(sql), {"peri_id": peri_id, "indi_id": indi_id})).mappings().first()
        )
        return dict(fila) if fila else None

    async def insertar_concepto(self, fila: dict[str, Any]) -> int:
        """INSERT concepto_aptitud versión n+1 (fn_concepto_valida)."""
        sql = """
        INSERT INTO sigat.concepto_aptitud (
            lote_id, peri_id, indi_id, conc_version, conc_resultado,
            conc_fecha_efecto, norm_id, conc_motivo
        ) VALUES (
            :lote_id,
            :peri_id,
            :indi_id,
            COALESCE(
                (SELECT max(conc_version)
                   FROM sigat.concepto_aptitud
                  WHERE lote_id = :lote_id AND peri_id = :peri_id),
                0
            ) + 1,
            :conc_resultado,
            :conc_fecha_efecto,
            :norm_id,
            :conc_motivo
        )
        RETURNING conc_id
        """
        conc_id = await self._con.scalar(
            text(sql),
            {
                "lote_id": fila["lote_id"],
                "peri_id": fila["peri_id"],
                "indi_id": fila["indi_id"],
                "conc_resultado": fila["conc_resultado"],
                "conc_fecha_efecto": fila["conc_fecha_efecto"],
                "norm_id": fila.get("norm_id"),
                "conc_motivo": fila.get("conc_motivo"),
            },
        )
        if conc_id is None:
            raise RuntimeError("No se pudo insertar el concepto de aptitud")
        return int(conc_id)

    async def cultivo_productor(self, usua_id: int) -> dict[str, Any]:
        raise NoImplementado("Cultivo del productor")
