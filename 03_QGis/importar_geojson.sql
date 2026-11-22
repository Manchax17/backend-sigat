-- =====================================================================
-- Pasa los GeoJSON cargados en sigat_import a las tablas definitivas.
--
-- Requiere haber ejecutado antes:
--   QGis/importar_geojson.sh
--
-- Uso:
--   psql "<cadena de conexión>" \
--     -v pred_id=<ID_PREDIO> \
--     -v lote_id=<ID_LOTE> \
--     -v reti_id=<ID_RETICULA> \
--     -v ubic_id=<ID_UBICACION_TRAMPA> \
--     -f QGis/importar_geojson.sql
--
-- Todos los -v son opcionales: si falta uno, el bloque correspondiente se
-- omite y se informa por consola. Esto evita escribir en el registro
-- equivocado.
--
-- Los IDs son los de la base (integer), NO los ids de la aplicación que
-- vienen dentro de los .geojson.
-- =====================================================================
\set ON_ERROR_STOP on

\echo ''
\echo '== SIGAT: paso de staging a tablas definitivas'
\echo ''

-- ---------------------------------------------------------------------
-- 1. Predio: límite del predio y GeoJSON de origen.
--    Variable: pred_id
-- ---------------------------------------------------------------------
\if :{?pred_id}
\echo '-> predio (pred_id =' :pred_id ')'
UPDATE sigat.predio p
SET pred_geom = ST_Multi(s.geom),
    pred_geojson = jsonb_build_object(
      'type', 'Feature',
      'geometry', ST_AsGeoJSON(s.geom, 6)::jsonb,
      'properties', to_jsonb(s) - 'gid' - 'geom'
    ),
    pred_geojson_fuente = 'predio.geojson',
    pred_fecha_actualizacion = CURRENT_DATE
FROM sigat_import.predio s
WHERE p.pred_id = :pred_id
  AND s.gid = (SELECT min(gid) FROM sigat_import.predio);

\if :ROW_COUNT
\echo '   predio actualizado.'
\else
\echo '   AVISO: no se encontró pred_id =' :pred_id '. No se actualizó nada.'
\endif
\else
\echo 'AVISO: falta -v pred_id=...; se omite el predio.'
\endif

-- ---------------------------------------------------------------------
-- 2. Lote: nueva versión de geometría (no sobrescribe la anterior).
--    Variables: lote_id
--    El trigger exige que el lote quede dentro del predio si pred_geom
--    ya está cargado; por eso conviene cargar primero el predio.
-- ---------------------------------------------------------------------
\if :{?lote_id}
\echo '-> lote_geometria (lote_id =' :lote_id ')'
BEGIN;

UPDATE sigat.lote_geometria
SET lotg_vigente_hasta = now()
WHERE lote_id = :lote_id
  AND lotg_vigente_hasta IS NULL
  AND lotg_vigente_desde < now();

INSERT INTO sigat.lote_geometria (
  lote_id, lotg_version, lotg_geom, lotg_vigente_desde,
  lotg_fuente, lotg_geojson, lotg_geojson_fuente
)
SELECT
  :lote_id,
  COALESCE((SELECT max(lotg_version) FROM sigat.lote_geometria WHERE lote_id = :lote_id), 0) + 1,
  ST_Multi(s.geom),
  now(),
  'QGIS (importación)',
  jsonb_build_object(
    'type', 'Feature',
    'geometry', ST_AsGeoJSON(s.geom, 6)::jsonb,
    'properties', to_jsonb(s) - 'gid' - 'geom'
  ),
  'lotes.geojson'
FROM sigat_import.lotes s
WHERE s.gid = (SELECT min(gid) FROM sigat_import.lotes);

COMMIT;
\else
\echo 'AVISO: falta -v lote_id=...; se omite el lote.'
\endif

-- ---------------------------------------------------------------------
-- 3. Celdas de retícula: alta o actualización por código de celda.
--    Variables: reti_id
--    Requiere una fila previa en sigat.reticula con ese reti_id.
-- ---------------------------------------------------------------------
\if :{?reti_id}
\echo '-> reticula_celda (reti_id =' :reti_id ')'
INSERT INTO sigat.reticula_celda (
  reti_id, retc_codigo, retc_geom, retc_geojson, retc_geojson_fuente
)
SELECT
  :reti_id,
  s.codigo,
  s.geom,
  jsonb_build_object(
    'type', 'Feature',
    'geometry', ST_AsGeoJSON(s.geom, 6)::jsonb,
    'properties', to_jsonb(s) - 'gid' - 'geom'
  ),
  'celdas.geojson'
FROM sigat_import.celdas s
ON CONFLICT (reti_id, retc_codigo) DO UPDATE
SET retc_geom = EXCLUDED.retc_geom,
    retc_geojson = EXCLUDED.retc_geojson,
    retc_geojson_fuente = EXCLUDED.retc_geojson_fuente;
\else
\echo 'AVISO: falta -v reti_id=...; se omiten las celdas.'
\endif

-- ---------------------------------------------------------------------
-- 4. Trampas: actualiza la coordenada de una ubicación existente.
--    Variables: ubic_id
--    No crea trampas ni ubicaciones nuevas: eso lo hace el flujo normal
--    de la aplicación (requiere trampa, técnico y vigilancia).
-- ---------------------------------------------------------------------
\if :{?ubic_id}
\echo '-> ubicacion_trampa (ubic_id =' :ubic_id ')'
UPDATE sigat.ubicacion_trampa u
SET ubic_long = round(ST_X(s.geom)::numeric, 5),
    ubic_lat  = round(ST_Y(s.geom)::numeric, 5),
    ubic_geojson = jsonb_build_object(
      'type', 'Feature',
      'geometry', ST_AsGeoJSON(s.geom, 6)::jsonb,
      'properties', to_jsonb(s) - 'gid' - 'geom'
    ),
    ubic_geojson_fuente = 'trampas.geojson'
FROM sigat_import.trampas s
WHERE u.ubic_id = :ubic_id
  AND s.gid = (SELECT min(gid) FROM sigat_import.trampas);
\else
\echo 'AVISO: falta -v ubic_id=...; se omiten las trampas.'
\endif

-- ---------------------------------------------------------------------
-- 5. Resumen de staging (no modifica nada).
-- ---------------------------------------------------------------------
\echo ''
\echo '== Resumen de archivos en sigat_import'
SELECT 'predio'       AS archivo, count(*) AS features FROM sigat_import.predio
UNION ALL SELECT 'lotes',        count(*) FROM sigat_import.lotes
UNION ALL SELECT 'trampas',      count(*) FROM sigat_import.trampas
UNION ALL SELECT 'celdas',       count(*) FROM sigat_import.celdas
UNION ALL SELECT 'entrada',      count(*) FROM sigat_import.entrada
UNION ALL SELECT 'area_trabajo', count(*) FROM sigat_import.area_trabajo
ORDER BY archivo;

\echo ''
\echo 'Listo. Verifica con:'
\echo '  SELECT lote_id, lotg_version, lotg_geojson_fuente, lotg_vigente_desde, lotg_vigente_hasta FROM sigat.lote_geometria ORDER BY lote_id, lotg_version;'
\echo '  SELECT pred_id, pred_geojson_fuente, pred_fecha_actualizacion FROM sigat.predio ORDER BY pred_id;'
\echo '  SELECT * FROM sigat.v_geojson_export;'
\echo ''
\echo 'Para descartar la zona de staging:  DROP SCHEMA sigat_import CASCADE;'
