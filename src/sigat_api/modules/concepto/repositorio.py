"""Acceso a datos de concepto y vistas de resumen."""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado


class RepositorioConcepto:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    async def lote_visible(self, lote_id: int, usua_id: int | None = None) -> bool:
        sql = """
        SELECT EXISTS (
            SELECT 1
              FROM sigat.lote l
             WHERE l.lote_id = :lote_id
               AND (
                   CAST(:u AS integer) IS NULL
                   OR EXISTS (
                       SELECT 1 FROM sigat.usuario u
                       JOIN sigat.rol r ON r.rol_id = u.rol_id
                      WHERE u.usua_id = :u AND r.rol_codigo = 'ADMINISTRADOR'
                   )
                   OR l.lote_id IN (SELECT lote_id FROM sigat.fn_lotes_visibles(:u))
               )
        )
        """
        return bool(await self._con.scalar(text(sql), {"lote_id": lote_id, "u": usua_id}))

    async def resumen(self, lote_id: int, usua_id: int | None = None) -> dict[str, Any] | None:
        """fn_lote_resumen_app(:usua) WHERE lote_id = :lote."""
        sql = """
        SELECT l.lote_id,
               l.lote_descripcion,
               vra.mtd,
               false AS mtd_es_estimacion,
               vra.escenario,
               vra.umbral,
               vra.contraste,
               vra.concepto,
               (SELECT c.conc_fecha_efecto
                  FROM sigat.v_concepto_vigente c
                 WHERE c.lote_id = l.lote_id
                 ORDER BY c.conc_fecha_efecto DESC, c.conc_id DESC LIMIT 1) AS concepto_fecha_efecto,
               COALESCE(vra.inspecciones, 0) AS inspecciones,
               (SELECT count(*)::int
                  FROM sigat.visita_lote vl
                  JOIN sigat.visita v USING (visi_id)
                 WHERE vl.lote_id = l.lote_id
                   AND v.visi_t_inicio_obs >= date_trunc('month', CURRENT_DATE)) AS inspecciones_mes,
               COALESCE(vra.aplicaciones, 0) AS aplicaciones,
               rl.ultima_aplicacion,
               COALESCE(vra.lecturas, 0) AS lecturas,
               COALESCE(vra.especimenes, 0) AS especimenes,
               COALESCE(rl.trampas_vigentes, 0) AS trampas_vigentes,
               (SELECT count(DISTINCT t.tram_id)::int
                  FROM sigat.ubicacion_trampa u
                  JOIN sigat.trampa t ON t.tram_id = u.tram_id
                  JOIN sigat.v_lectura_vigente lv ON lv.ubic_id = u.ubic_id
                 WHERE u.lote_id = l.lote_id
                   AND u.ubic_fecha_fin IS NULL
                   AND lv.lect_especimenes > 0) AS trampas_con_capturas,
               rl.ultima_visita,
               vra.ultima_cosecha,
               cos_vig.cose_fecha_esperada AS cosecha_esperada,
               cos_vig.cose_volumen_esperado AS rendimiento_estimado,
               cos_vig.cose_volumen_real AS rendimiento_real,
               COALESCE(vra.hallazgos, 0) AS hallazgos,
               (SELECT v.visi_estado_revision
                  FROM sigat.visita v
                  JOIN sigat.visita_lote vl USING (visi_id)
                 WHERE vl.lote_id = l.lote_id AND v.visi_estado_revision IN ('pendiente', 'en_revision')
                 ORDER BY v.visi_t_inicio_obs DESC LIMIT 1) AS revision_pendiente,
               f.feno_descripcion AS etapa_derivada,
               vra.etapa_declarada,
               (SELECT e.etap_t_observacion
                  FROM (SELECT DISTINCT ON (etap_registro_id) *
                          FROM sigat.etapa_declarada
                         ORDER BY etap_registro_id, etap_version DESC) e
                 WHERE e.lote_id = l.lote_id AND NOT e.etap_anulada
                 ORDER BY e.etap_t_observacion DESC LIMIT 1) AS etapa_declarada_el
          FROM sigat.lote l
          LEFT JOIN sigat.v_lote_resumen_app vra ON vra.lote_id = l.lote_id
          LEFT JOIN sigat.v_resumen_lote rl ON rl.lote_id = l.lote_id
          LEFT JOIN sigat.fenologia f ON f.feno_id = sigat.fn_etapa_derivada(l.lote_id, CURRENT_DATE)
          LEFT JOIN LATERAL (
              SELECT c.cose_fecha_esperada,
                     c.cose_volumen_esperado,
                     c.cose_volumen_real
                FROM sigat.cosecha c
               WHERE c.lote_id = l.lote_id
                 AND NOT c.cose_anulada
                 AND NOT EXISTS (
                     SELECT 1 FROM sigat.cosecha n
                      WHERE n.cose_registro_id = c.cose_registro_id
                        AND n.cose_version > c.cose_version
                 )
               ORDER BY (c.cose_fecha_real IS NULL) DESC,
                        c.cose_t_observacion DESC,
                        c.cose_t_captura DESC,
                        c.cose_registro_id DESC
               LIMIT 1
          ) cos_vig ON true
         WHERE l.lote_id = :lote_id
           AND (
              CAST(:u AS integer) IS NULL
              OR EXISTS (SELECT 1 FROM sigat.usuario u JOIN sigat.rol r ON r.rol_id = u.rol_id WHERE u.usua_id = :u AND r.rol_codigo = 'ADMINISTRADOR')
              OR l.lote_id IN (SELECT lote_id FROM sigat.fn_lotes_visibles(:u))
           )
        """
        fila = (await self._con.execute(text(sql), {"u": usua_id, "lote_id": lote_id})).mappings().first()
        if not fila:
            return None
        datos = dict(fila)

        # Periodo actual (abierto)
        sql_peri = """
        SELECT peri_id, lote_id, vigi_id, prog_id, peri_inicio, peri_fin, peri_estado
          FROM sigat.periodo_trampeo
         WHERE lote_id = :lote_id AND peri_estado = 'abierto'
         ORDER BY peri_inicio DESC
         LIMIT 1
        """
        fila_peri = (await self._con.execute(text(sql_peri), {"lote_id": lote_id})).mappings().first()
        datos["periodo_actual"] = dict(fila_peri) if fila_peri else None

        # EtapaLote
        derivada = datos.pop("etapa_derivada", None)
        declarada = datos.pop("etapa_declarada", None)
        declarada_el = datos.pop("etapa_declarada_el", None)
        discrepancia = bool(derivada and declarada and derivada.strip().lower() != declarada.strip().lower())
        datos["etapa"] = {
            "derivada": derivada,
            "declarada": declarada,
            "declarada_el": declarada_el,
            "discrepancia": discrepancia,
        }

        return datos

    async def serie(
        self, lote_id: int, desde: date | None = None, hasta: date | None = None, usua_id: int | None = None
    ) -> list[dict[str, Any]]:
        """fn_mtd_serie_lote(:usua)."""
        condiciones = ["s.lote_id = :lote_id"]
        params: dict[str, Any] = {"lote_id": lote_id, "u": usua_id}
        if usua_id is not None:
            condiciones.append("""(
                EXISTS (SELECT 1 FROM sigat.usuario u JOIN sigat.rol r ON r.rol_id = u.rol_id WHERE u.usua_id = :u AND r.rol_codigo = 'ADMINISTRADOR')
                OR s.lote_id IN (SELECT lote_id FROM sigat.fn_lotes_visibles(:u))
            )""")
        if desde:
            condiciones.append("s.peri_inicio >= :desde")
            params["desde"] = desde
        if hasta:
            condiciones.append("s.peri_fin <= :hasta")
            params["hasta"] = hasta

        where_clause = " AND ".join(condiciones)
        sql = f"""
        SELECT s.peri_id,
               s.peri_inicio,
               s.peri_fin,
               s.mtd,
               s.indi_moscas AS moscas,
               s.indi_trampas AS trampas,
               s.indi_dias_exposicion AS dias_exposicion,
               s.umbral,
               s.contraste,
               e.esce_descripcion AS escenario
          FROM sigat.v_mtd_serie_lote s
          LEFT JOIN sigat.escenario_trampeo e ON e.esce_id = s.esce_id
         WHERE {where_clause}
         ORDER BY s.peri_inicio ASC
        """  # noqa: S608
        filas = await self._con.execute(text(sql), params)
        return [dict(f) for f in filas.mappings()]

    async def periodos(self, lote_id: int) -> list[dict[str, Any]]:
        sql = """
        SELECT peri_id, lote_id, vigi_id, prog_id, peri_inicio, peri_fin, peri_estado
          FROM sigat.periodo_trampeo
         WHERE lote_id = :lote_id
         ORDER BY peri_inicio DESC, peri_id DESC
        """
        filas = await self._con.execute(text(sql), {"lote_id": lote_id})
        return [dict(f) for f in filas.mappings()]

    async def versiones_mtd(self, peri_id: int) -> list[dict[str, Any]]:
        sql = """
        SELECT i.indi_id,
               i.indi_version,
               i.indi_valor,
               i.indi_moscas,
               i.indi_trampas,
               i.indi_dias_exposicion,
               i.indi_umbral_aplicado,
               i.indi_resultado_contraste,
               e.esce_descripcion AS escenario,
               i.indi_calculado_en,
               i.indi_motivo_recalculo
          FROM sigat.indice_mtd i
          LEFT JOIN sigat.escenario_trampeo e ON e.esce_id = i.esce_id
         WHERE i.peri_id = :peri_id AND i.espm_id IS NULL
         ORDER BY i.indi_version DESC
        """
        filas = await self._con.execute(text(sql), {"peri_id": peri_id})
        return [dict(f) for f in filas.mappings()]

    async def conceptos(self, lote_id: int) -> list[dict[str, Any]]:
        sql = """
        SELECT c.conc_id,
               c.lote_id,
               c.peri_id,
               c.conc_version,
               c.conc_resultado,
               c.conc_fecha_efecto,
               n.norm_codigo AS norma,
               c.conc_emitido_en,
               c.conc_motivo,
               i.indi_valor AS mtd
          FROM sigat.concepto_aptitud c
          LEFT JOIN sigat.norma n ON n.norm_id = c.norm_id
          LEFT JOIN sigat.indice_mtd i ON i.indi_id = c.indi_id
         WHERE c.lote_id = :lote_id
         ORDER BY c.conc_fecha_efecto DESC, c.conc_version DESC, c.conc_id DESC
        """
        filas = await self._con.execute(text(sql), {"lote_id": lote_id})
        return [dict(f) for f in filas.mappings()]

    async def concepto_a_fecha(self, lote_id: int, fecha: date) -> dict[str, Any] | None:
        """Versión vigente con conc_fecha_efecto <= fecha y conc_emitido_en <= fin de ese día."""
        sql = """
        SELECT c.conc_id,
               c.lote_id,
               c.peri_id,
               c.conc_version,
               c.conc_resultado,
               c.conc_fecha_efecto,
               n.norm_codigo AS norma,
               c.conc_emitido_en,
               c.conc_motivo,
               i.indi_valor AS mtd
          FROM sigat.concepto_aptitud c
          JOIN sigat.lote l ON l.lote_id = c.lote_id
          JOIN sigat.predio p ON p.pred_id = l.pred_id
          LEFT JOIN sigat.norma n ON n.norm_id = c.norm_id
          LEFT JOIN sigat.indice_mtd i ON i.indi_id = c.indi_id
         WHERE c.lote_id = :lote_id
           AND c.conc_fecha_efecto <= :fecha
           AND (c.conc_emitido_en AT TIME ZONE p.pred_zona_horaria)::date <= :fecha
         ORDER BY c.conc_fecha_efecto DESC, c.conc_version DESC, c.conc_id DESC
         LIMIT 1
        """
        fila = (await self._con.execute(text(sql), {"lote_id": lote_id, "fecha": fecha})).mappings().first()
        return dict(fila) if fila else None

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

    async def etapa(self, lote_id: int, fecha: date) -> dict[str, Any]:
        sql = """
        SELECT (SELECT f.feno_descripcion
                  FROM sigat.fenologia f
                 WHERE f.feno_id = sigat.fn_etapa_derivada(:lote_id, :fecha)) AS derivada,
               ed.feno_descripcion AS declarada,
               ed.etap_t_observacion AS declarada_el
          FROM (SELECT 1) base
          LEFT JOIN LATERAL (
              SELECT f.feno_descripcion, e.etap_t_observacion
                FROM (SELECT DISTINCT ON (etap_registro_id) *
                        FROM sigat.etapa_declarada
                       ORDER BY etap_registro_id, etap_version DESC) e
                JOIN sigat.fenologia f USING (feno_id)
               WHERE e.lote_id = :lote_id AND NOT e.etap_anulada
               ORDER BY e.etap_t_observacion DESC
               LIMIT 1
          ) ed ON true
        """
        fila = (await self._con.execute(text(sql), {"lote_id": lote_id, "fecha": fecha})).mappings().one()
        derivada = fila["derivada"]
        declarada = fila["declarada"]
        discrepancia = bool(derivada and declarada and derivada.strip().lower() != declarada.strip().lower())
        return {
            "derivada": derivada,
            "declarada": declarada,
            "declarada_el": fila["declarada_el"],
            "discrepancia": discrepancia,
        }

    async def cultivo_productor(self, usua_id: int) -> dict[str, Any]:
        raise NoImplementado("Cultivo del productor")
