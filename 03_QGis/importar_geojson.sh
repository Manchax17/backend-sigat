#!/usr/bin/env bash
# =====================================================================
# Carga los GeoJSON de esta carpeta en una zona de importación (staging).
#
# NO escribe directamente en las tablas definitivas: primero deja los
# archivos en el esquema sigat_import y luego se ejecuta importar_geojson.sql
# para pasarlos a sigat.predio / sigat.lote_geometria / etc. con los IDs
# reales de la base.
#
# Uso:
#   1) Con variables estándar de PostgreSQL:
#        export PGHOST=<HOST>
#        export PGPORT=<PUERTO>
#        export PGDATABASE=<BD>
#        export PGUSER=<USUARIO>
#        export PGPASSWORD=<CLAVE>        # opcional; evita escribirla en el comando
#        ./importar_geojson.sh
#
#   2) Con una cadena de conexión como primer argumento:
#        ./importar_geojson.sh "host=<HOST> port=<PUERTO> dbname=<BD> user=<USUARIO>"
#        # Si la base pide clave, expórtala antes con PGPASSWORD.
#
# Requisitos: ogr2ogr (GDAL, incluido con QGIS) y psql.
# =====================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v ogr2ogr >/dev/null 2>&1; then
  echo "ERROR: no se encontró ogr2ogr. Instala GDAL o usa la consola de QGIS." >&2
  exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
  echo "ERROR: no se encontró psql. Instala el cliente de PostgreSQL." >&2
  exit 1
fi

CONEXION="${1:-}"
if [[ -z "$CONEXION" ]]; then
  : "${PGDATABASE:?Define PGDATABASE (o pasa una cadena de conexión como primer argumento)}"
  CONEXION="host=${PGHOST:-localhost} port=${PGPORT:-5432} dbname=${PGDATABASE} user=${PGUSER:-postgres}"
fi

# No mostrar la clave si viene dentro de la cadena de conexión.
DESTINO="$(printf '%s' "$CONEXION" | sed -E 's/(password=)[^ ]+/\1*****/g')"
echo "Destino: $DESTINO"

psql "$CONEXION" -v ON_ERROR_STOP=1 -c "CREATE SCHEMA IF NOT EXISTS sigat_import;"

cargar() {
  local archivo="$1"
  local tabla="$2"
  local tipo="$3"

  if [[ ! -f "$DIR/$archivo" ]]; then
    echo "ERROR: no existe $DIR/$archivo" >&2
    exit 1
  fi

  echo "-> $archivo  =>  sigat_import.$tabla"
  ogr2ogr \
    -f PostgreSQL "PG:$CONEXION" "$DIR/$archivo" \
    -nln "sigat_import.$tabla" \
    -nlt "$tipo" \
    -t_srs EPSG:4326 \
    -lco GEOMETRY_NAME=geom \
    -lco FID=gid \
    -lco PRECISION=NO \
    -overwrite
}

# Polígonos que en el modelo son MultiPolygon.
cargar "predio.geojson"      "predio"       "PROMOTE_TO_MULTI"
cargar "lotes.geojson"       "lotes"        "PROMOTE_TO_MULTI"

# Puntos.
cargar "trampas.geojson"     "trampas"      "POINT"
cargar "entrada.geojson"     "entrada"      "POINT"

# Polígonos que en el modelo son Polygon.
cargar "celdas.geojson"      "celdas"       "POLYGON"
cargar "area_trabajo.geojson" "area_trabajo" "POLYGON"

cat <<'EOF'

Carga en staging terminada.

Siguiente paso: pasar los datos a las tablas definitivas con los IDs reales.
Primero consulta los IDs disponibles:

  psql "<cadena de conexión>" -c \
    "SELECT lote_id, pred_id, lote_codigo, lote_descripcion FROM sigat.lote ORDER BY lote_id;"

  psql "<cadena de conexión>" -c \
    "SELECT pred_id, pred_nombre FROM sigat.predio ORDER BY pred_id;"

Luego ejecuta la plantilla (todos los -v son opcionales; si falta uno,
ese bloque se omite):

  psql "<cadena de conexión>" \
    -v pred_id=<ID_PREDIO> \
    -v lote_id=<ID_LOTE> \
    -v reti_id=<ID_RETICULA> \
    -v ubic_id=<ID_UBICACION_TRAMPA> \
    -f QGis/importar_geojson.sql

Nota: entrada.geojson y area_trabajo.geojson quedan en staging como capas
auxiliares de QGIS; el esquema no tiene una tabla destino para ellas.
EOF
