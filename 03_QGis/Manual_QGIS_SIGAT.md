# Manual QGIS — Carga de GeoJSON en SIGAT v6

Este manual explica cómo cargar los archivos `.geojson` de la carpeta `QGis/` en la base de datos `sigat_db_v6` y cómo usarlos en QGIS Desktop.

> Los comandos usan marcadores genéricos (`<HOST>`, `<PUERTO>`, `<BD>`, `<USUARIO>`).
> Reemplázalos por los datos de tu servidor. No escribas la clave dentro del comando:
> expórtala con `PGPASSWORD` o usa el archivo `~/.pgpass`.

---

## 1. Archivos incluidos

| Archivo | Geometría | Destino | ¿Se guarda en la BD? |
|---|---|---|---|
| `predio.geojson` | Polygon | `sigat.predio` → `pred_geom`, `pred_geojson` | Sí |
| `lotes.geojson` | Polygon | `sigat.lote_geometria` (nueva versión) | Sí |
| `trampas.geojson` | Point | `sigat.ubicacion_trampa` (coordenada y GeoJSON) | Sí |
| `celdas.geojson` | Polygon | `sigat.reticula_celda` | Sí |
| `entrada.geojson` | Point | `sigat_import.entrada` | No: capa auxiliar de QGIS |
| `area_trabajo.geojson` | Polygon | `sigat_import.area_trabajo` | No: capa auxiliar de QGIS |

Archivos de apoyo: `metadata_sigat.json` (resumen del levantamiento) y `recorrido.json` (recorrido de campo). No son capas.

Los datos de estos archivos **no son de demostración**: corresponden a un levantamiento real.

---

## 2. Requisitos

- PostgreSQL 16 o superior con PostGIS 3.3 o superior.
- `psql` (cliente de PostgreSQL).
- `ogr2ogr` (GDAL; viene incluido con QGIS).
- Opcional: QGIS Desktop para revisar o editar las capas.

Verificación rápida:

```bash
psql --version
ogr2ogr --version
```

---

## 3. Conexión a la base

Elige una de las dos formas. En ambos casos usa **tus** datos, no los de un ejemplo.

### Opción A — Variables de entorno (recomendada)

```bash
export PGHOST=<HOST>
export PGPORT=<PUERTO>
export PGDATABASE=<BD>
export PGUSER=<USUARIO>
export PGPASSWORD=<CLAVE>     # opcional; si no, psql la pedirá o usará ~/.pgpass
```

Con eso, `psql` y `ogr2ogr` se conectan sin repetir la cadena.

### Opción B — Cadena de conexión

```bash
psql "host=<HOST> port=<PUERTO> dbname=<BD> user=<USUARIO>"
```

Si la base pide clave, exporta `PGPASSWORD` antes de ejecutar. Evita escribir `password=...` dentro del comando: queda visible en el historial y en la lista de procesos.

---

## 4. Cargar la estructura de la base

El archivo `BD/sigat_db_v6/sql/sigat_bd_completa.sql` crea el esquema completo (74 tablas, catálogos, vistas y funciones). Úsalo en una base **vacía**:

```bash
psql -v ON_ERROR_STOP=1 -d <BD> -f BD/sigat_db_v6/sql/sigat_bd_completa.sql
```

Verifica:

```sql
-- Deben aparecer 74 tablas
SELECT count(*) FROM pg_tables WHERE schemaname = 'sigat';

-- Debe existir la vista de exportación
SELECT viewname FROM pg_views
WHERE schemaname = 'sigat' AND viewname = 'v_geojson_export';

-- Deben existir las columnas GeoJSON
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'sigat' AND column_name LIKE '%geojson%'
ORDER BY table_name, column_name;
```

### Si la base ya existe

`sigat_bd_completa.sql` crea tablas desde cero. No lo ejecutes sobre una base que ya tiene datos: fallará en el primer `CREATE TABLE`. Aplica solo los cambios pendientes:

```sql
ALTER TABLE sigat.lote_geometria ADD COLUMN IF NOT EXISTS lotg_geojson jsonb;
ALTER TABLE sigat.lote_geometria ADD COLUMN IF NOT EXISTS lotg_geojson_fuente varchar;

ALTER TABLE sigat.predio ADD COLUMN IF NOT EXISTS pred_geojson jsonb;
ALTER TABLE sigat.predio ADD COLUMN IF NOT EXISTS pred_geojson_fuente varchar;

ALTER TABLE sigat.reticula_celda ADD COLUMN IF NOT EXISTS retc_geojson jsonb;
ALTER TABLE sigat.reticula_celda ADD COLUMN IF NOT EXISTS retc_geojson_fuente varchar;

ALTER TABLE sigat.ubicacion_trampa ADD COLUMN IF NOT EXISTS ubic_geojson jsonb;
ALTER TABLE sigat.ubicacion_trampa ADD COLUMN IF NOT EXISTS ubic_geojson_fuente varchar;
```

Y crea la vista `v_geojson_export` con el bloque que está en `BD/sigat_db_v6/sql/03_vistas.sql`.

---

## 5. Cargar los GeoJSON

La carga tiene dos pasos porque las tablas definitivas tienen reglas que un `ogr2ogr` directo no puede resolver:

- `lote_geometria` exige `lote_id` (llave foránea), `lotg_version` y vigencia.
- `pred_geom` y `lotg_geom` son `MultiPolygon`; los archivos traen `Polygon`.
- `ubicacion_trampa` exige trampa, técnico y vigilancia existentes.

Por eso primero se cargan los archivos en una **zona de staging** (`sigat_import`) y después se pasan a las tablas reales con los IDs de la base.

### 5.1 Forma recomendada: script incluido

```bash
cd <RUTA_DEL_PROYECTO>
./QGis/importar_geojson.sh
```

o, con cadena de conexión:

```bash
./QGis/importar_geojson.sh "host=<HOST> port=<PUERTO> dbname=<BD> user=<USUARIO>"
```

El script crea el esquema `sigat_import` y carga cada archivo en su tabla de staging. No toca las tablas definitivas.

Luego consulta los IDs reales:

```sql
SELECT lote_id, pred_id, lote_codigo, lote_descripcion
FROM sigat.lote ORDER BY lote_id;

SELECT pred_id, pred_nombre FROM sigat.predio ORDER BY pred_id;

SELECT reti_id, pred_id, reti_resolucion_m FROM sigat.reticula ORDER BY reti_id;

SELECT ubic_id, tram_id, pred_id FROM sigat.ubicacion_trampa ORDER BY ubic_id;
```

Y ejecuta la plantilla con los IDs que correspondan:

```bash
psql "host=<HOST> port=<PUERTO> dbname=<BD> user=<USUARIO>" \
  -v pred_id=<ID_PREDIO> \
  -v lote_id=<ID_LOTE> \
  -v reti_id=<ID_RETICULA> \
  -v ubic_id=<ID_UBICACION_TRAMPA> \
  -f QGis/importar_geojson.sql
```

| Variable | Qué actualiza | Obligatoria para |
|---|---|---|
| `pred_id` | `sigat.predio.pred_geom` y `pred_geojson` | `predio.geojson` |
| `lote_id` | Nueva versión en `sigat.lote_geometria` | `lotes.geojson` |
| `reti_id` | Altas/actualizaciones en `sigat.reticula_celda` | `celdas.geojson` |
| `ubic_id` | Coordenada y GeoJSON de `sigat.ubicacion_trampa` | `trampas.geojson` |

Si omites una variable, ese bloque no se ejecuta y el script lo avisa. Así no se escribe en el registro equivocado.

> `entrada.geojson` y `area_trabajo.geojson` quedan en `sigat_import` como capas de apoyo para QGIS. El modelo no tiene una tabla para esos dos elementos.

### 5.2 Forma manual: `ogr2ogr` solo a staging

Si prefieres cargar un archivo a mano, crea primero la zona de staging y luego usa `ogr2ogr` **siempre hacia `sigat_import`**, nunca hacia las tablas definitivas:

```bash
psql "host=<HOST> port=<PUERTO> dbname=<BD> user=<USUARIO>" \
  -c "CREATE SCHEMA IF NOT EXISTS sigat_import;"

ogr2ogr -f PostgreSQL \
  PG:"host=<HOST> port=<PUERTO> dbname=<BD> user=<USUARIO>" \
  QGis/<ARCHIVO>.geojson \
  -nln sigat_import.<TABLA> \
  -nlt PROMOTE_TO_MULTI \
  -t_srs EPSG:4326 \
  -lco GEOMETRY_NAME=geom \
  -lco FID=gid \
  -overwrite
```

Cambia `-nlt` según el archivo:

| Archivo | `-nlt` | `-nln` |
|---|---|---|
| `predio.geojson` | `PROMOTE_TO_MULTI` | `sigat_import.predio` |
| `lotes.geojson` | `PROMOTE_TO_MULTI` | `sigat_import.lotes` |
| `trampas.geojson` | `POINT` | `sigat_import.trampas` |
| `celdas.geojson` | `POLYGON` | `sigat_import.celdas` |
| `entrada.geojson` | `POINT` | `sigat_import.entrada` |
| `area_trabajo.geojson` | `POLYGON` | `sigat_import.area_trabajo` |

Después de cargar a staging, usa `QGis/importar_geojson.sql` como en el paso 5.1.

**No uses** `-nln sigat.lote_geometria`, `-lco FID=lote_id` ni `-lco GEOMETRY_NAME=pred_geom` para intentar escribir directo en las tablas finales: `ogr2ogr` no calcula la versión, no valida la vigencia, no convierte a `MultiPolygon` y no respeta las llaves foráneas. El resultado sería una tabla suelta o un error.

### 5.3 Alternativa con `ST_GeomFromGeoJSON`

Para un ajuste puntual desde `psql`, puedes convertir una geometría sin pasar por `ogr2ogr`:

```sql
-- Reemplaza <GEOMETRY_JSON> por el contenido de "geometry" del Feature.
UPDATE sigat.predio
SET pred_geom = ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON('<GEOMETRY_JSON>'), 4326)),
    pred_geojson = '<FEATURE_JSON>'::jsonb,
    pred_geojson_fuente = 'predio.geojson',
    pred_fecha_actualizacion = CURRENT_DATE
WHERE pred_id = <ID_PREDIO>;
```

Para leer el archivo desde el servidor existe `pg_read_file`, pero requiere superusuario y que el archivo esté en una ruta accesible por PostgreSQL. En equipo local es más simple el staging.

---

## 6. Verificación después de cargar

```sql
-- Predio
SELECT pred_id, pred_geojson_fuente, pred_fecha_actualizacion,
       ST_IsValid(pred_geom) AS valido
FROM sigat.predio ORDER BY pred_id;

-- Versiones de geometría del lote (la vigente tiene lotg_vigente_hasta NULL)
SELECT lote_id, lotg_version, lotg_fuente, lotg_geojson_fuente,
       lotg_vigente_desde, lotg_vigente_hasta,
       round(lotg_area_m2::numeric, 2) AS area_m2
FROM sigat.lote_geometria
ORDER BY lote_id, lotg_version;

-- Celdas
SELECT reti_id, retc_codigo, ST_IsValid(retc_geom) AS valido
FROM sigat.reticula_celda ORDER BY reti_id, retc_codigo;

-- Trampas
SELECT ubic_id, tram_id, pred_id, ubic_long, ubic_lat, ubic_geojson_fuente
FROM sigat.ubicacion_trampa ORDER BY ubic_id;

-- Vista de exportación
SELECT * FROM sigat.v_geojson_export;
```

Compara áreas:

```sql
SELECT lote_id, round(lotg_area_m2::numeric, 2) AS area_bd_m2,
       round((lotg_geojson -> 'properties' ->> 'area_m2')::numeric, 2) AS area_geojson_m2
FROM sigat.lote_geometria
WHERE lotg_vigente_hasta IS NULL;
```

---

## 7. Uso en QGIS Desktop

### 7.1 Capa PostGIS

1. `Capa` → `Agregar capa` → `Agregar capa PostGIS`.
2. Configura la conexión con `<HOST>`, `<PUERTO>`, `<BD>` y `<USUARIO>`.
3. Agrega `sigat.lote_geometria` o `sigat.predio` como capas espaciales.
4. Si agregas `sigat.v_geojson_export`, elige `geom` como columna de geometría: es la geometría vigente del lote. La vista también trae `pred_geom` (límite del predio) y `geometria` (el mismo lote en JSON); no uses `pred_geom` como geometría de la capa de lotes.

### 7.2 Capa local

1. `Capa` → `Agregar capa` → `Agregar capa vectorial`.
2. Selecciona `QGis/predio.geojson`, `QGis/lotes.geojson`, etc.

### 7.3 Validaciones útiles

- **Unión espacial** (`Join attributes by location`): comparar `sigat.lote_geometria` con `lotes.geojson`.
- **Diferencia** (`Difference`): detectar áreas del lote fuera del predio.
- **Validar geometrías**: `Vector` → `Geometry Tools` → `Check Validity`.
- **Comparar áreas**: `Field Calculator` con `$area` y comparar contra `lotg_area_m2`.

---

## 8. Reglas para no perder información

1. **No sobrescribas `lotg_geom`**: cada carga crea una versión nueva (`lotg_version + 1`). La anterior se cierra con `lotg_vigente_hasta`.
2. **No dejes dos versiones vigentes**: la restricción `lotg_vigencia_sin_solape` lo impide.
3. **Carga el predio antes que los lotes**: el trigger `fn_lote_geometria_valida` exige que el lote quede dentro de `pred_geom` cuando el predio ya tiene límite.
4. **Valida las geometrías**: usa `ST_IsValid`; si falla, corrige con `ST_MakeValid` antes de insertar.
5. **Mantén `EPSG:4326`**: todas las capas y columnas usan WGS84.
6. **No uses los IDs de la aplicación como FID**: los `id` de los `.geojson` (por ejemplo `1793217673509274`) no caben en un `integer` de PostgreSQL. El staging usa `gid` automático y la plantilla usa los IDs reales de la base.
7. **No borres `sigat_import` hasta confirmar** que los datos quedaron en las tablas definitivas.

---

## 9. Regenerar el SQL consolidado

Si modificas `01_esquema_completo.sql`, `02_catalogos_basicos.sql`, `03_vistas.sql` o `04_funciones.sql`, regenera el archivo único:

```bash
BD/sigat_db_v6/herramientas/generar_consolidado.sh
```

El script verifica que no haya comandos interactivos de `psql` y escribe `BD/sigat_db_v6/sql/sigat_bd_completa.sql`.

---

## 10. Problemas comunes

| Error | Causa | Solución |
|---|---|---|
| `type "geometry" does not exist` | PostGIS no instalado en la base | `CREATE EXTENSION postgis;` |
| `function st_multi(geometry) does not exist` | Igual que el anterior | Instala/activa PostGIS |
| `lote_geometria_geom_valida` | Geometría inválida o vacía | `ST_MakeValid` y revisar en QGIS |
| `La geometría del lote ... queda fuera del límite del predio` | El lote no está contenido en `pred_geom` | Cargar/corregir el predio primero |
| `lotg_vigencia_sin_solape` | Ya hay una versión vigente o una futura | Cerrar la versión anterior; revisar fechas |
| `violates foreign key constraint ... lote_id` | El `lote_id` no existe | Crear el lote o usar el ID correcto |
| `ubicacion_trampa_lote_obligatorio` | `ubic_origen = 'operacion'` sin lote | Asignar `lote_id` o usar el flujo de la app |
| `numeric field overflow` o `integer out of range` | Se usó el `id` de la app como FID | Dejar que el staging genere `gid` |
| `Geometry type (MultiPolygon) does not match column type (Polygon)` | Se cargó con `PROMOTE_TO_MULTI` en una columna `Polygon` | Usar `-nlt POLYGON` para celdas |
| `relation "sigat_import..." does not exist` | No se ejecutó el paso de staging | Ejecutar `./QGis/importar_geojson.sh` |

---

## 11. Resumen del flujo

```text
1. psql -f sigat_bd_completa.sql          -> crea el esquema
2. ./QGis/importar_geojson.sh             -> carga los .geojson en sigat_import
3. Consultar IDs reales                   -> pred_id, lote_id, reti_id, ubic_id
4. psql -v ... -f importar_geojson.sql    -> pasa staging a las tablas definitivas
5. SELECT * FROM sigat.v_geojson_export;  -> verifica
6. QGIS: capa PostGIS + capa local        -> revisa y valida
```
