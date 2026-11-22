# Cambios de la base de datos

Este documento establece las convenciones para administrar y mantener la estructura de la base de datos de SIGAT.

Su objetivo es mantener la trazabilidad de los cambios, facilitar el mantenimiento y garantizar que las modificaciones sean consistentes entre los diferentes entornos del sistema.

## Gestión de la estructura

La estructura de la base de datos se administra mediante scripts SQL y migraciones de Alembic.

Este mecanismo permite:

- Crear la estructura de la base de datos.
- Incorporar nuevas tablas, relaciones y restricciones.
- Actualizar índices, vistas y funciones.
- Mantener los catálogos y datos de referencia.
- Aplicar modificaciones de forma controlada.
- Conservar la consistencia entre los entornos de ejecución.

Las migraciones deben ejecutarse siguiendo el orden establecido por el proyecto.

Para aplicar las migraciones:

```bash
alembic upgrade head
```

Para consultar el historial:

```bash
alembic history
```

Para consultar la revisión actual:

```bash
alembic current
```

## Organización de los cambios

Los cambios de estructura se gestionan mediante dos mecanismos:

| Mecanismo | Responsabilidad |
|---|---|
| Scripts SQL | Definición y mantenimiento de los elementos de la base de datos |
| Alembic | Control de versiones y ejecución ordenada de migraciones |

Los archivos SQL se organizan en el directorio `bd/sql/`, mientras que las migraciones se almacenan en `migrations/`.

Cada modificación debe estar documentada y asociada con su correspondiente cambio en el proyecto.

## Principios para modificar la base de datos

Las modificaciones deben seguir estas buenas prácticas:

1. **Conservar el historial:** no modificar migraciones que ya hayan sido aplicadas en otros entornos. Los cambios nuevos deben incorporarse mediante nuevas revisiones.
2. **Priorizar cambios compatibles:** procurar que las modificaciones no afecten innecesariamente la información existente.
3. **Mantener la integridad:** conservar las relaciones, restricciones y reglas de validación de los datos.
4. **Documentar los cambios:** describir el propósito y el alcance de cada modificación.
5. **Verificar las migraciones:** comprobar que los cambios puedan aplicarse correctamente.
6. **Evitar pérdidas de información:** evaluar cuidadosamente cualquier modificación que pueda alterar o eliminar datos existentes.

## Procedimiento para incorporar cambios

Cuando sea necesario modificar la estructura de la base de datos, se recomienda seguir este procedimiento:

### 1. Identificar la modificación

Determinar qué elemento necesita cambiarse y cuál es el propósito de la modificación.

### 2. Crear una nueva migración

Generar una revisión mediante Alembic:

```bash
alembic revision -m "descripcion_del_cambio"
```

### 3. Implementar el cambio

Incorporar las operaciones necesarias en la migración, procurando mantener la compatibilidad con la estructura existente.

### 4. Documentar la modificación

Registrar qué cambia, cuál es su propósito y qué consideraciones deben tenerse en cuenta al aplicarlo.

### 5. Ejecutar las verificaciones

Aplicar las migraciones:

```bash
alembic upgrade head
```

Ejecutar las pruebas de integración:

```bash
pytest -m integracion
```

### 6. Revisar y compartir

Comprobar los resultados, revisar los cambios y seguir el flujo de contribución establecido en el repositorio.

## Integridad y protección de los datos

La administración de la base de datos debe considerar las siguientes medidas:

- Mantener las relaciones y restricciones de integridad referencial.
- Evitar modificaciones que comprometan información existente.
- Evaluar los cambios antes de aplicarlos en entornos compartidos.
- Realizar respaldos cuando las operaciones puedan afectar información almacenada.
- No incorporar credenciales, contraseñas, tokens ni información sensible en los scripts SQL.
- No almacenar volcados de bases de datos ni información personal real dentro del repositorio.

## Convenciones de desarrollo

Para mantener una estructura organizada y consistente:

- Utilizar nombres descriptivos para las migraciones.
- Mantener un orden lógico de ejecución.
- Evitar duplicar definiciones existentes.
- Documentar las modificaciones relevantes.
- Mantener sincronizados los scripts SQL y las migraciones correspondientes.
- Verificar los cambios antes de integrarlos a la rama principal.

## Historial de migraciones

### Revisión `0001` — Esquema institucional SIGAT v9

Establece la línea base de la base de datos SIGAT v9. Cuando el esquema está vacío, ejecuta los scripts SQL institucionales en el orden definido. Si la estructura v9 ya existe, registra la revisión sin reconstruirla. Si encuentra una versión incompatible, detiene el proceso para evitar modificaciones no controladas.

### Revisión `0002` — Esquema técnico de sesiones API

Crea el esquema `api` y las tablas técnicas necesarias para gestionar sesiones de actualización de tokens e intentos de inicio de sesión. Incluye relaciones con usuarios y dispositivos, restricciones de validación e índices para las consultas.

### Revisión `0003` — Categoría de solicitudes de cuenta

Incorpora la categoría `solicitudes_cuenta` al catálogo de notificaciones. Permite identificar los avisos relacionados con solicitudes de recuperación de acceso. La operación es idempotente y no elimina categorías ni notificaciones existentes.

### Revisión `0004` — Vistas de consulta del productor

Ejecuta `bd/sql/13_vistas_productor.sql`, que **solo agrega** tres vistas y tres funciones de lectura; no crea ni modifica tablas, columnas, restricciones, índices ni datos, y no reemplaza vistas existentes. El archivo también se incluye en `00_instalar_todo.sql`, así que una instalación por psql y una por Alembic quedan iguales. Es idempotente (`CREATE OR REPLACE`).

- `v_actividad_lote_detalle` (F1 del informe de consumo): historial por lote con responsable, rol, título y detalle. Cuenta las trampas solo del lote de cada fila y del día de la visita, con lecturas vigentes y trampas distintas. El texto de fecha no depende del idioma del servidor. `linaje` y `es_inspeccion_oficial` son nulos en las vigilancias porque `visita` no registra linaje.
- `v_aplicacion_ultima_lote` (F3 / RF-041): última aplicación vigente de cada lote, incluidos los lotes sin aplicaciones, y los días transcurridos en la zona horaria del predio.
- `v_cosecha_lote_vigente` (F2 / RF-042): cosecha vigente de cada lote (esperada + real), incluidos los lotes sin cosechas. Aplica la regla **P-04, una sola cosecha abierta por lote** (decisión del responsable del proyecto, 2026-11-05): elige la abierta y, si no hay ninguna, la más reciente. `cosechas_abiertas` muestra si un lote incumple la regla, que todavía no se impone en la base de datos.
- `fn_actividad_lote_detalle(usua)`, `fn_cosecha_lote_vigente(usua)` y `fn_aplicacion_ultima_lote(usua)`: las mismas vistas filtradas con `fn_lotes_visibles`. Las vistas no son un mecanismo de autorización; el backend debe consultar las funciones.
- No se edita `03_vistas.sql`, porque la revisión 0001 lo ejecuta al instalar desde cero.
- `downgrade` elimina solo estos seis objetos.

Detalle, decisiones y limitaciones: `docs/VISTAS_PRODUCTOR.md`.

### Revisión `0005` — Semilla provisional de escenarios de trampeo

Ejecuta `bd/sql/14_semilla_escenarios_provisionales.sql`, también incluido en `00_instalar_todo.sql`. Inserta los 4 escenarios de RN-002 con los valores del informe de consumo de datos (§10.1; Tabla 1, OIEA 2005), **como provisionales**: así lo decidió el responsable del proyecto el 2026-11-04 (pendiente P-02). `esce_fuente` lo indica en cada fila.

| Escenario | `esce_orden` | desde | hasta |
|---|:-:|---|---|
| Área infestada | 1 | 1,0000 | nulo (sin límite) |
| Supresión | 2 | 0,1000 | 1,0000 |
| Erradicación | 3 | 0,0000 | 0,1000 |
| Exclusión | 4 | 0,0000 | 0,0000 |

- Solo inserta, y solo si la tabla está vacía. Si ya contiene exactamente estos 4 escenarios, no hace nada. Si contiene otra cosa (una carga parcial, distinta u oficial con otra fuente), **se detiene** con un error que enumera las diferencias, sin completar, modificar ni borrar filas.
- `downgrade` no borra filas: pueden estar referenciadas por `programa_umbral` e `indice_mtd`.
- Cómo se interpretan los límites al clasificar un MTD (bordes abiertos o cerrados) es la pendiente CT-04; esta revisión solo carga el catálogo.

### Revisión `0006` — Semilla de permisos por rol

Ejecuta `bd/sql/15_semilla_permisos.sql`, también incluido en `00_instalar_todo.sql`. Siembra `sigat.permiso` (**25 permisos**) y `sigat.rol_permiso` (**54 pares**) con exactamente la matriz que la API aplica hoy (`src/sigat_api/modules/identidad/permisos.py`, usada por `requiere(...)`).

- Usa los mismos códigos `area.accion`, con punto, como exige el CHECK `permiso_codigo_formato`, y las mismas asignaciones por rol. Así lo decidió el responsable del proyecto el 2026-11-04: no se siembran los códigos alternativos de la documentación ni se duplican capacidades.
- `permisos.py` sigue siendo la fuente de verdad del backend; la base de datos queda como reflejo exacto. `tests/unit/test_semilla_permisos_migracion.py` falla si las dos divergen o si alguna ruta usa un código que no está en la matriz.
- Los roles se buscan por `rol_codigo`, nunca por id. El PRODUCTOR **no** recibe `visitas.consultar`, igual que en `permisos.py`.
- Solo inserta, y solo si las dos tablas están vacías. Si ya contienen exactamente esta matriz, no hace nada. Si contienen otra cosa (carga parcial, distinta o con otras descripciones), **se detiene** con un error que enumera las diferencias, sin completar, modificar ni borrar filas.
- La auditoría `trg_aud_rol_permiso` registra cada par en `bitacora_auditoria` sin usuario, porque no hay actor durante una migración.
- `downgrade` no borra filas: la matriz no se distingue con seguridad de una carga institucional idéntica.

### Revisión `0007` — Regla P-04: una sola cosecha abierta por lote

Ejecuta `bd/sql/16_regla_cosecha_abierta.sql`, también incluido en `00_instalar_todo.sql`. **Solo agrega** la función `sigat.fn_cosecha_una_abierta()` y el disparador `BEFORE INSERT` `trg_cosecha_una_abierta` sobre `sigat.cosecha`; no crea ni modifica tablas, columnas, restricciones, índices ni datos. Desde esta revisión la regla P-04 (decisión del responsable del proyecto, 2026-11-05), que la 0004 solo hacía visible en `v_cosecha_lote_vigente.cosechas_abiertas`, se impone en la base de datos.

- Una cosecha está **abierta** cuando su versión vigente (la de mayor `cose_version`) no tiene `cose_fecha_real` y no está anulada, igual que en `v_cosecha_lote_vigente`.
- Se rechaza solo la versión que **abre** una cosecha en un lote que ya tiene otra abierta: un alta nueva abierta, la reapertura de una cerrada o anulada, o una corrección que la cambia de lote.
- Siempre se permite corregir la cosecha que ya está abierta (versión n+1 en el mismo lote), cerrarla y anularla. El versionamiento append-only (`trg_cosecha_ro`, `trg_cosecha_version`) no cambia.
- No se usa un índice único parcial (el `ux_cosecha_abierta` del cronograma): cada corrección es una fila nueva, y el índice vería las versiones de la misma cosecha como dos abiertas e impediría corregirla.
- Concurrencia: `pg_advisory_xact_lock` por lote serializa dos aperturas simultáneas en el mismo lote. Con el aislamiento READ COMMITTED del backend, la segunda transacción ve la primera ya confirmada y se rechaza.
- Error: SQLSTATE `23505` con restricción `cosecha_una_abierta_por_lote`. `infra/db/errores_pg.py` lo traduce a **409 `COSECHA_ABIERTA_EXISTENTE`**.
- **Datos históricos:** no se revisan, modifican ni borran. Si algún lote ya tiene más de una cosecha abierta, la migración emite un `WARNING` con los lotes y continúa; esas cosechas se pueden corregir, cerrar o anular, pero no se puede abrir otra hasta que quede una sola.
- `downgrade` elimina solo el disparador y la función.

### Revisión `0008` — Vistas de informe local y guarda geométrica

Ejecuta `bd/sql/17_vistas_informe_local.sql`, también incluido en `00_instalar_todo.sql`. **Solo agrega** cuatro vistas de informe y reemplaza la función del disparador `sigat.fn_lote_geometria_valida`; no crea ni modifica tablas, columnas, restricciones, índices ni datos. Es idempotente (`CREATE OR REPLACE`).

- `v_ultima_sincronizacion` (P01, P06, P07): última recepción y número de operaciones consolidadas por usuario y dispositivo (`dispositivo` × `operacion_sync`).
- `v_operacion_confirmada` (P07b, P07c, P07d): resultado del servidor por operación del cliente y número de reenvíos (`recepcion_sync`, el más reciente por `rece_t`). Cierra el ciclo del móvil.
- `v_mtd_por_lote_escenario` (P02, P02-P): serie del MTD por lote y escenario con umbral aplicado y contraste (`periodo_trampeo` × `v_mtd_vigente` × `escenario_trampeo` × `programa_umbral`).
- `v_resumen_productor` (P01, P01-P, PW01): predio, lotes, MTD y escenario más recientes, y estado fitosanitario por lote (`predio` × `lote` × `v_resumen_lote`, lateral sobre `v_mtd_por_lote_escenario`). Solo lotes en predios cargados y con concepto vigente.
- `fn_lote_geometria_valida`: la regla RF-084 sigue vigente, pero la contención solo se exige cuando `lotg_geom` se inserta o cambia (`IS DISTINCT FROM` en UPDATE). Cerrar una versión (`lotg_vigente_hasta`) o editar metadatos ya no revalida un polígono cargado. El resto del cuerpo no cambia.
- Las vistas **no** son un mecanismo de autorización: devuelven todas las filas. El backend debe consultar solo con el filtro del usuario del token.
- `downgrade` elimina las cuatro vistas y restaura la función sin la guarda (cuerpo de `04_funciones.sql` de la revisión 0001).

### Consideraciones de revisión

Las modificaciones de restricciones de `operacion_sync` en los ajustes v8 y v9 amplían los valores permitidos para incorporar nuevas entidades sincronizables. Como utilizan `DROP CONSTRAINT` seguido de `ADD CONSTRAINT`, deben contar con revisión técnica y aprobación del responsable de la base de datos antes de integrarse.
---

**SIGAT — Sistema de Información para la Gestión y Análisis de la Vigilancia Fitosanitaria.**