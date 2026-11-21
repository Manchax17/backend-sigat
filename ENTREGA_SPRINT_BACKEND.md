# Entrega Sprint — Rol Backend (backend-sigat)

**Sprint:** *El técnico trabaja sin señal y consulta lo que hizo*  
**Fecha:** Noviembre 2026  
**Responsable:** Rol 1 — Backend  
**Estado:** ✅ Implementado, verificado con pruebas unitarias y contrato OpenAPI sincronizado.

---

## 1. Resumen Ejecutivo

Se completó la implementación integral del alcance de Backend para el sprint, permitiendo que el técnico de campo pueda descargar la configuración completa de su predio para trabajar sin conexión y consultar su historial de visitas y el estado fitosanitario de los lotes:

1. **Paquete de Campo Offline (`GET /predios/{id}/paquete-campo` y `.../contenido`):**
   - Metadatos de versión y hash SHA-256.
   - Generación determinista bajo demanda o por invalidación.
   - Incorporación de las 4 geometrías GeoJSON (`predio`, `lotes`, `celdas`, `trampas`).
   - Soporte de caché condicional HTTP 304 (*Not Modified*) mediante cabecera `If-None-Match`.
2. **Historial de Visitas (`GET /visitas` y `GET /visitas/{id}`):**
   - Listado paginado mediante cursor opaco.
   - Filtro de seguridad por rol: el técnico únicamente visualiza sus propias visitas; agrónomos y productores visualizan las visitas de sus predios accesibles.
   - Detalle de la visita con la lista completa de lecturas vigentes (`v_lectura_revision`), autor, condición de la trampa y cantidad de evidencias asociadas.
3. **Resumen Fitosanitario del Lote (`GET /lotes/{id}/resumen`):**
   - MTD consolidado, umbral, contraste y concepto vigente.
   - Comparación de etapa fenológica derivada (regla biológica por días desde plantación) vs. etapa declarada con indicador de discrepancia.
   - Serie temporal histórica de puntos MTD por periodo de trampeo.

---

## 2. Endpoints Implementados y Contratos HTTP

Todos los endpoints respetan estrictamente la especificación OpenAPI (`contracts/openapi.json`), manejan errores con formato estándar RFC 9457 (`application/problem+json`) y requieren autenticación Bearer Token (`Authorization: Bearer <token>`) y cabecera de versión `X-App-Version`.

### 2.1 Paquete de Campo

#### A. `GET /api/v1/predios/{pred_id}/paquete-campo`
Devuelve la información de la versión vigente del paquete de campo para saber si es necesario sincronizar.

* **Permiso requerido:** `paquete_campo.descargar` (Técnico, Agrónomo, Productor).
* **Respuesta 200 OK (`PaqueteCampoInfo`):**
```json
{
  "paqu_id": "c1f7b8e2-9b24-4f93-a4ef-4b4708a28791",
  "pred_id": 1,
  "paqu_version": 1,
  "prog_id": 1,
  "paqu_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "generado_en": "2026-11-20T22:30:00Z",
  "invalidado_en": null,
  "tamano_bytes": null
}
```

#### B. `GET /api/v1/predios/{pred_id}/paquete-campo/contenido`
Descarga la totalidad de datos y cartografía que el dispositivo móvil necesita para operar sin señal.

* **Cabecera opcional:** `If-None-Match: "<paqu_hash>"`
* **Respuesta 304 Not Modified:** Si el hash coincide con el vigente, el servidor responde 304 sin cuerpo (ahorro de ancho de banda).
* **Respuesta 200 OK (`PaqueteCampoContenido`):**
  - Cabecera `ETag: "<paqu_hash>"`
  - Objeto JSON con:
    - `paqu_version`: Entero incremental.
    - `paqu_hash`: Cadena SHA-256 de 64 caracteres hexadecimales calculada mediante JSON Canónico (RFC 8785).
    - `predio`: Datos generales de la finca, municipio, departamento, hectáreas y responsable técnico.
    - `programa`: Programa de inspección vigente con normas, escenarios y criterios BPA.
    - `mapa`: `MapaPredio` con las 4 capas GeoJSON en EPSG:4326 (`predio`, `lotes`, `celdas`, `trampas`).
    - `catalogos`: Diccionario con tablas operativas requeridas sin red (`condicion_trampa`, `atrayente`, `especie_mosca`, `tipo_trampa`, `producto_fitosanitario`, `unidad_medida`).
    - `mapa_base`: Configuración del mapa base con atribución y límites (`limites: [minx, miny, maxx, maxy]`) calculados de la geometría del predio.

---

### 2.2 Historial del Técnico

#### A. `GET /api/v1/visitas`
Lista las visitas de campo realizadas.

* **Parámetros de consulta:**
  - `pred_id` (opcional): Filtrar por predio.
  - `desde` / `hasta` (opcional): Rango de fechas ISO 8601.
  - `cursor` (opcional): Token opaco para paginación `(t_inicio_obs|visi_id)`.
* **Regla de negocio:**
  - Si el actor es `TECNICO`, la consulta restringe automáticamente a `v.usua_id = :actor_id`.
* **Respuesta 200 OK (`Pagina[Visita]`):**
```json
{
  "items": [
    {
      "visi_id": "84a32b90-b184-4632-9df8-019d36e2f183",
      "usua_id": 4,
      "tecnico": "Pedro Técnico",
      "pred_id": 1,
      "pred_nombre": "Finca El Duraznal de Prueba",
      "ruta_id": null,
      "t_inicio_obs": "2026-11-20T08:00:00Z",
      "t_fin_obs": "2026-11-20T09:30:00Z",
      "t_captura": "2026-11-20T09:30:00Z",
      "t_sincronizacion": "2026-11-20T09:45:00Z",
      "deriva_reloj_seg": 0,
      "estado_revision": "pendiente",
      "motivo_revision": null,
      "revisada_por": null,
      "revisada_en": null,
      "lote_ids": [1],
      "lotes": ["01"],
      "trampas_leidas": 6
    }
  ],
  "siguiente": null
}
```

#### B. `GET /api/v1/visitas/{visi_id}`
Detalle de una visita con sus lecturas asociadas.

* **Respuesta 200 OK (`VisitaDetalle`):** Incluye todos los campos de `Visita` más la lista `lecturas: list[Lectura]`.
* **Seguridad:** Si un técnico intenta acceder al detalle de una visita perteneciente a otro técnico, el servidor responde `404 Not Found` (`VISITA_NO_ENCONTRADA`) para proteger la privacidad.

---

### 2.3 Resumen Fitosanitario del Lote

#### `GET /api/v1/lotes/{lote_id}/resumen`
Alimenta las pantallas de resumen del lote en el móvil y en la web.

* **Respuesta 200 OK (`ResumenLote`):**
```json
{
  "lote_id": 1,
  "lote_descripcion": "Lote 01 Durazno",
  "mtd": "0.0000",
  "mtd_es_estimacion": false,
  "periodo_actual": {
    "peri_id": 1,
    "lote_id": 1,
    "vigi_id": 1,
    "prog_id": 1,
    "peri_inicio": "2026-11-09",
    "peri_fin": "2026-11-24",
    "peri_estado": "abierto"
  },
  "escenario": "Baja prevalencia",
  "umbral": "0.1000",
  "contraste": "por_debajo_umbral",
  "concepto": "apto",
  "concepto_fecha_efecto": "2026-11-09",
  "inspecciones": 1,
  "inspecciones_mes": 1,
  "aplicaciones": 0,
  "ultima_aplicacion": null,
  "lecturas": 6,
  "especimenes": 0,
  "trampas_vigentes": 6,
  "trampas_con_capturas": 0,
  "ultima_visita": "2026-11-20T09:30:00Z",
  "etapa": {
    "derivada": "Desarrollo vegetativo",
    "declarada": "Desarrollo vegetativo",
    "declarada_el": null,
    "discrepancia": false
  },
  "ultima_cosecha": null,
  "cosecha_esperada": null,
  "rendimiento_estimado": null,
  "rendimiento_real": null,
  "hallazgos": 0,
  "revision_pendiente": null,
  "serie": [
    {
      "peri_id": 1,
      "peri_inicio": "2026-11-09",
      "peri_fin": "2026-11-24",
      "mtd": "0.0000",
      "moscas": 0,
      "trampas": 6,
      "dias_exposicion": "7.0",
      "umbral": "0.1000",
      "contraste": "por_debajo_umbral",
      "escenario": "Baja prevalencia"
    }
  ]
}
```

---

## 3. Arquitectura de Geometrías y Mapas (PostGIS)

Tal como se acordó en el refinamiento del sprint:
* **Sin bloqueos por esquemas externos:** En el esquema oficial SIGAT las geometrías se leen de forma nativa mediante PostGIS con `ST_AsGeoJSON(...)` directamente desde:
  1. `sigat.predio.pred_geom` → Polígono o Multipolígono del contorno de la finca.
  2. `sigat.lote_geometria.lotg_geom` → Geometría versionada vigente en el instante de consulta.
  3. `sigat.reticula_celda.retc_geom` → Celdas de la cuadrícula de inspección.
  4. `sigat.ubicacion_trampa.ubic_geom` → Coordenadas puntuales de cada trampa (columna generada automáticamente a partir de `ubic_long` y `ubic_lat`).
* **MapLibre fuera de alcance:** El backend provee los GeoJSON validados en EPSG:4326. La app móvil los almacena localmente; cuando el equipo correspondiente integre MapLibre o el visor cartográfico, solo tendrá que pintar las colecciones que ya están en el teléfono.

---

## 4. Datos de Prueba en Base de Datos Local

Para el equipo de **Frontend Móvil** e **Integración**, el contenedor de desarrollo PostgreSQL (`puerto 5434`, base `sigat`) cuenta con el script semilla `backend-sigat/scripts/dev/seed_tecnico_pruebas.sql`:

* **Usuario Técnico:** `tecnico.prueba` (contraseña: `Tecnico123!`, `pers_cc: 1098765432`)
* **Usuario Agrónomo:** `agronomo.prueba` (contraseña: `Agronomo123!`, `pers_cc: 1098765433`)
* **Predio:** ID `1` (*Finca El Duraznal de Prueba*, Pamplona, N. de S.)
* **Lote:** ID `1` (*Lote 01 Durazno*)
* **Trampas Asignadas:** 6 trampas oficiales con códigos ICA válidos:
  - `54518-A-001`
  - `54518-A-002`
  - `54518-A-003`
  - `54518-A-004`
  - `54518-A-005`
  - `54518-A-006`

---

## 5. Verificación de Calidad

* **Linter & Formatter:** `ruff check` y `ruff format` pasando sin advertencias.
* **Tipado Estático:** `mypy` pasando en modo estricto en los 17 archivos de los módulos involucrados.
* **Pruebas Unitarias:** 11 pruebas unitarias en verde (`pytest`), incluyendo:
  - Manejo de `If-None-Match` y respuesta HTTP 304.
  - Aislamiento de visitas por técnico y permisos de acceso.
  - Validación de lotes, detección de discrepancias de etapa y series temporales.
* **Contrato Sincronizado:** `tests/contrato/test_contrato_sincronizado.py` en verde con `contracts/openapi.json`.
