-- =====================================================================
-- SIGAT v9.1 - Vistas de informe local y guarda del disparador geométrico
-- (P01, P02, P06, P07) — revisión Alembic 0008
-- Necesita: 01 a 16. Se puede volver a ejecutar (CREATE OR REPLACE). Solo agrega
-- cuatro vistas y reemplaza la función del disparador fn_lote_geometria_valida.
-- No crea ni modifica tablas, columnas, restricciones, índices ni datos.
--
-- Vistas (todas nuevas, con categoría «Vista nueva» del informe de consumo):
--   * v_ultima_sincronizacion (P01, P06, P07): última recepción y número de operaciones
--     consolidadas por usuario y dispositivo.
--   * v_operacion_confirmada  (P07b, P07c, P07d): resultado del servidor por operación del
--     cliente; cierra el ciclo del móvil.
--   * v_mtd_por_lote_escenario (P02, P02-P): serie del MTD por lote y escenario con el
--     umbral aplicado y el contraste contra el escenario.
--   * v_resumen_productor     (P01, P01-P, PW01): predio, lotes, MTD, escenario y estado
--     fitosanitario por lote.
--
-- fn_lote_geometria_valida: misma regla RF-084, pero solo revalida la contención cuando
-- NEW.lotg_geom es distinta de OLD.lotg_geom (en altas, siempre). Cerrar una versión
-- (lotg_vigente_hasta) o editar metadatos ya no revalida un polígono cargado.
--
-- Las vistas NO son un mecanismo de autorización: devuelven todas las filas. El backend
-- debe leer SIEMPRE con el filtro del usuario del token (RNF-015).
-- =====================================================================

-- ---------------------------------------------------------------------------------------
-- fn_lote_geometria_valida con guarda de cambio de geometría (RF-084)
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sigat.fn_lote_geometria_valida() RETURNS trigger LANGUAGE plpgsql SET search_path = sigat, public AS $$
DECLARE g geometry; v_validar boolean;
BEGIN
  -- La contención solo se exige cuando la geometría cambia. Cerrar una
  -- versión (lotg_vigente_hasta) o editar metadatos no debe revalidar un
  -- polígono que ya estaba cargado.
  IF TG_OP = 'INSERT' THEN
    v_validar := true;
  ELSE
    v_validar := NEW.lotg_geom IS DISTINCT FROM OLD.lotg_geom;
  END IF;

  IF v_validar THEN
    SELECT p.pred_geom INTO g FROM lote l JOIN predio p USING (pred_id) WHERE l.lote_id = NEW.lote_id;
    IF g IS NOT NULL AND NOT ST_Covers(g, NEW.lotg_geom) THEN
      RAISE EXCEPTION 'La geometría del lote % queda fuera del límite del predio (RF-084)', NEW.lote_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
COMMENT ON FUNCTION sigat.fn_lote_geometria_valida() IS 'RF-084: la geometría de un lote debe quedar contenida en el límite del predio (si el límite ya está cargado). Solo valida cuando lotg_geom se inserta o cambia; cerrar una versión no revalida la geometría existente.';

-- ---------------------------------------------------------------------------------------
-- 1. Última sincronización por usuario y dispositivo
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW sigat.v_ultima_sincronizacion AS
SELECT
    d.disp_id,
    u.usua_id,
    d.disp_plataforma,
    max(o.oper_t_recepcion) AS ultima_recepcion,
    count(o.oper_id)        AS operaciones_recibidas
FROM sigat.dispositivo d
JOIN sigat.operacion_sync o USING (disp_id)
JOIN sigat.usuario u ON u.usua_id = o.usua_id
GROUP BY d.disp_id, u.usua_id, d.disp_plataforma;
COMMENT ON VIEW sigat.v_ultima_sincronizacion IS 'Última sincronización por usuario y dispositivo (Vista nueva / P01, P06, P07).';

-- ---------------------------------------------------------------------------------------
-- 2. Resultado del servidor por operación del cliente
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW sigat.v_operacion_confirmada AS
SELECT
    o.usua_id,
    o.disp_id,
    o.oper_cliente_id,
    o.oper_id,
    o.oper_entidad,
    o.oper_clasificacion,
    o.oper_t_captura,
    o.oper_t_recepcion,
    (SELECT r.rece_resultado
     FROM sigat.recepcion_sync r
     WHERE r.oper_id = o.oper_id
     ORDER BY r.rece_t DESC LIMIT 1) AS ultimo_resultado,
    (SELECT count(*) FROM sigat.recepcion_sync r WHERE r.oper_id = o.oper_id) AS reenvios
FROM sigat.operacion_sync o;
COMMENT ON VIEW sigat.v_operacion_confirmada IS 'Resultado del servidor por operación de cliente (Vista nueva / P07b, P07c, P07d): cierra el ciclo del móvil.';

-- ---------------------------------------------------------------------------------------
-- 3. Serie del MTD por lote y escenario
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW sigat.v_mtd_por_lote_escenario AS
SELECT
    p.lote_id,
    p.peri_id,
    p.peri_inicio,
    p.peri_fin,
    m.indi_valor AS mtd,
    m.indi_moscas,
    m.indi_trampas,
    m.indi_dias_exposicion,
    m.indi_umbral_aplicado AS umbral,
    m.indi_resultado_contraste AS contraste,
    CASE WHEN m.esce_id IS NOT NULL THEN es.esce_descripcion ELSE 'No asignado' END AS escenario,
    CASE WHEN m.esce_id IS NOT NULL THEN es.esce_mtd_desde_defecto ELSE pu.prou_mtd_desde END AS mtd_desde,
    CASE WHEN m.esce_id IS NOT NULL THEN es.esce_mtd_hasta_defecto ELSE pu.prou_mtd_hasta END AS mtd_hasta
FROM sigat.periodo_trampeo p
JOIN sigat.v_mtd_vigente m ON m.peri_id = p.peri_id AND m.espm_id IS NULL
LEFT JOIN sigat.escenario_trampeo es ON es.esce_id = m.esce_id
LEFT JOIN sigat.programa_umbral pu ON pu.prog_id = p.prog_id AND pu.esce_id = m.esce_id;
COMMENT ON VIEW sigat.v_mtd_por_lote_escenario IS 'Serie del MTD por lote y escenario (Vista nueva / P02, P02-P): valor, periodo y escenario.';

-- ---------------------------------------------------------------------------------------
-- 4. Resumen productor
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW sigat.v_resumen_productor AS
SELECT
    p.pred_id,
    p.pred_nombre,
    p.pred_geom,
    p.pred_area,
    mu.muni_descripcion AS municipio,
    l.lote_id,
    l.lote_codigo,
    l.lote_descripcion,
    l.lote_area,
    COALESCE(msc.mtd, 0) AS mtd,
    COALESCE(msc.escenario, 'Sin periodo') AS escenario,
    rl.trampas_vigentes,
    rl.ultima_aplicacion,
    rl.ultima_visita,
    rl.concepto_vigente
FROM sigat.predio p
JOIN sigat.municipio mu ON mu.muni_id = p.muni_id
JOIN sigat.lote l ON l.pred_id = p.pred_id AND l.lote_fecha_baja IS NULL
JOIN sigat.v_resumen_lote rl ON rl.lote_id = l.lote_id
LEFT JOIN LATERAL (
    SELECT msc2.mtd, msc2.escenario
    FROM sigat.v_mtd_por_lote_escenario msc2
    WHERE msc2.lote_id = l.lote_id
    ORDER BY msc2.peri_inicio DESC, msc2.peri_id DESC
    LIMIT 1
) msc ON true
WHERE rl.concepto_vigente IS NOT NULL;
COMMENT ON VIEW sigat.v_resumen_productor IS 'Resumen productor: predio, lotes, MTD, escenario y estado fitosanitario por lote (Vista nueva / P01, P01-P, PW01).';
