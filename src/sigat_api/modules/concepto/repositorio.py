"""Acceso a datos de concepto y vistas de resumen."""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado


class RepositorioConcepto:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

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
               (SELECT max(c.cose_fecha_esperada)
                  FROM sigat.cosecha c
                 WHERE c.lote_id = l.lote_id AND NOT c.cose_anulada) AS cosecha_esperada,
               (SELECT c.cose_rendimiento_estimado
                  FROM sigat.cosecha c
                 WHERE c.lote_id = l.lote_id AND NOT c.cose_anulada
                 ORDER BY c.cose_fecha_registro DESC LIMIT 1) AS rendimiento_estimado,
               (SELECT c.cose_rendimiento_real
                  FROM sigat.cosecha c
                 WHERE c.lote_id = l.lote_id AND NOT c.cose_anulada
                 ORDER BY c.cose_fecha_registro DESC LIMIT 1) AS rendimiento_real,
               COALESCE(vra.hallazgos, 0) AS hallazgos,
               (SELECT v.visi_estado_revision
                  FROM sigat.visita v
                  JOIN sigat.visita_lote vl USING (visi_id)
                 WHERE vl.lote_id = l.lote_id AND v.visi_estado_revision IN ('pendiente', 'en_revision')
                 ORDER BY v.visi_t_inicio_obs DESC LIMIT 1) AS revision_pendiente,
               f.feno_descripcion AS etapa_derivada,
               vra.etapa_declarada
          FROM sigat.lote l
          LEFT JOIN sigat.v_lote_resumen_app vra ON vra.lote_id = l.lote_id
          LEFT JOIN sigat.v_resumen_lote rl ON rl.lote_id = l.lote_id
          LEFT JOIN sigat.fenologia f ON f.feno_id = sigat.fn_etapa_derivada(l.lote_id, CURRENT_DATE)
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
        discrepancia = bool(derivada and declarada and derivada.strip().lower() != declarada.strip().lower())
        datos["etapa"] = {
            "derivada": derivada,
            "declarada": declarada,
            "declarada_el": None,
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
        raise NoImplementado("Insertar concepto")

    async def cultivo_productor(self, usua_id: int) -> dict[str, Any]:
        raise NoImplementado("Cultivo del productor")
