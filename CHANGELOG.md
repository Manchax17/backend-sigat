# Cambios del backend (API SIGAT Durazno)

Registro de cambios del código del backend. **La base de datos tiene su propio registro:**
`bd/CAMBIOS.md` (una entrada por revisión, como exige la convención del equipo).

Los comentarios inline del código solo documentan el *por qué* inmediato de una línea
(SQLSTATE, regla de BD, decisión de seguridad). La rationale de nivel sprint y los
pendientes viven aquí, no en el código.

## Pendientes técnicos

Extraídos del código en el cierre del Sprint 2 backend (seguían siendo comentarios
«PENDIENTE» en el fuente; aquí siguen siendo visibles sin ensuciar el código):

1. **Worker de la cola** — `src/sigat_api/worker.py` ya consume
   `generar_paquete_campo` → `configuracion.servicio.ServicioPaqueteCampo.generar`.
   Quedan por registrar `reprocesar_periodo` →
   `sincronizacion.procesador.ProcesadorReproceso`, `verificar_archivo` →
   `imagenes.servicio.ServicioArchivos` y `enviar_correo` → `infra.correo.enviar`.
2. **Bloques nulos del inicio** — `api/v1/rutas/yo.py`: los bloques `tecnico` /
   `productor` / `agronomo` de `InicioResumen` van en `null` (nunca valores inventados)
   hasta que sus módulos estén implementados.
3. **Indicadores de lote sin fuente en la BD** — `configuracion.servicio`: no existe
   vista ni función en la BD que calcule los campos obligatorios de `IndicadoresLote`;
   el bloque va en `null` (lo admite el esquema). Depende de la BD (ver `bd/CAMBIOS.md`).
4. **Foto de perfil** — `identidad.servicio`: `foto_url=None` hasta que el módulo de
   imágenes implemente la URL firmada.

## Reglas sin implementación (backlog de pruebas)

Auditoría regla por regla de `tests/modules/*/test_reglas.py`: los stubs cuya regla **no
tiene código que probar** se conservan con skip y motivo documentado (nunca asserts vacíos).
Corresponden a Sprints 3–5 del cronograma, no al Sprint 2:

- **planificación** (Sprint 5): 01 C01 creador=actor, 03 plan ACEPTADA + ≥1 lote, 04 solo
  destinatario y solo pendiente → triggers SQL (`fn_planificacion_*`); 02 documento u
  observación ≥ 10 → solo docstring; 06 justificación/no_ejecutada → CHECK SQL; 07 completar
  al resolver lotes → ausente.
- **sincronización** (Sprint 5): 01 idempotencia R-T4, 02 conflicto_contenido, 03 respuesta
  por operación, 04 reintentable=false, 05 R-T6 revisar, 06 disp_id 403, 07 reproceso →
  `ServicioSincronizacion`/`ProcesadorReproceso` son `NoImplementado`; la idempotencia vive
  en BD (`UNIQUE (disp_id, oper_cliente_id)`, `recepcion_sync` append-only).
- **catálogo**: 01 mayúsculas, 02 retiro lógico, 03 invalida paquetes → `reemplazar()` es
  `NoImplementado` (efecto en BD/cola).
- **concepto**: 01 reemisión, 02 a una fecha (RF-024), 03 sin umbral → `ServicioConcepto`
  completo `NoImplementado`.
- **imágenes**: 01 hash, 02 MIME por contenido, 03 foto ajena → `ServicioArchivos`,
  `AlmacenS3` y `ServicioPerfil.poner_foto` `NoImplementado`.
- **configuración 02** (ADR-05): pipeline de versiones de programa `NoImplementado`.
- **escenarios MVP** (`tests/integracion/test_escenarios_mvp.py`): requieren el pipeline de
  sincronización completo; el job `integracion` del CI los ejecutará cuando existan.

Para llevar cada skip a test real hay que **implementar el feature** (el test ya está
diseñado: docstring con la regla y el camino a ejercer). Probar el esqueleto equivaldría a
afirmar «el código es un stub», no la regla.

## Paquete de campo y resumen del lote (Sprint 3)

- **Paquete de campo (ADR-09)**: `GET /predios/{id}/paquete-campo` devuelve
  `paqu_version`, `paqu_hash` (SHA-256, ETag), `generado_en` e `invalidado_en`;
  `GET /predios/{id}/paquete-campo/contenido` entrega el paquete completo y responde
  `304` cuando el `If-None-Match` coincide con el hash vigente.
- **Contenido**: predio, programa vigente, mapa en GeoJSON (`FeatureCollection` de
  predio, lotes, celdas y trampas), catálogos y configuración del mapa base. El móvil
  lo guarda para trabajar sin red.
- **Resumen del lote (RF-024)**: `GET /lotes/{id}/resumen` devuelve MTD, umbral,
  concepto y fecha de efecto del lote.
- **Invalidación y cola**: los cambios de predio, lote, geometría, trampa, programa y
  retícula invalidan el paquete vigente y encolan `generar_paquete_campo`. El worker
  (`python -m sigat_api.worker`) consume la tarea y regenera el paquete en una
  transacción con actor de sistema.
- **Catálogos y programa**: el paquete usa `unid_nombre`/`unid_magnitud` de
  `sigat.unidad_medida` y no filtra `norm_id` cuando no hay norma vigente.
- **Contrato**: `contracts/openapi.json` regenerado y verificado con
  `make contrato-verificar`.
- **Pruebas**: `make calidad` en verde (322 pruebas, 6 skip justificados, contrato
  sincronizado).
- **Fuera de alcance**: dibujo con MapLibre, mosaicos e imágenes satelitales;
  `GET /sincronizacion/cambios` sigue pendiente.

## Cierre del Sprint 2 (backend)

- **Contrato OpenAPI regenerado** (`contracts/openapi.json`): la frase del código 422
  pasó de «Unprocessable Entity» a «Unprocessable Content» en starlette >= 1.x /
  CPython 3.13 (RFC 9110). El contrato es el mismo para todos, así que
  `scripts/exportar_openapi.py` ahora **normaliza las cuatro frases renombradas
  (413/414/416/422) a su forma RFC 9110 al generar y al verificar**; la puerta del
  CI y el desarrollo local producen bytes idénticos aunque el intérprete difiera
  (el CI usa Python 3.12, el desarrollo 3.14).
- **`pyproject.toml`**: se eliminó `readme = "README.md"` (el documento vive fuera del
  repo por decisión de equipo); sin esto, `pip install -e .` fallaba y con él el job de
  calidad del CI.
- **Puerta de cobertura restablecida** (RNF-017 / ADR-17): el CI deja de correr
  `pytest` con `--no-cov` y vuelve a medir sobre las reglas de negocio. Por decisión
  del equipo el `fail_under` arranca bajo (60 %) y se sube sprint a sprint hasta el
  80 % exigido para fusionar. Resultado en este cierre: **198 pruebas pasan (antes
  173), 23 skip justificados, 0 fallos; cobertura 72.8 %** (antes 70.5 %).
- **Pruebas de reglas revisadas una a una**: los stubs con `@pytest.mark.skip("Por
  implementar")` se auditaron contra el código real. Los que tienen implementación
  testeable ya son **pruebas reales** (registro_01/03/04/11, auditoría_01/02,
  notificaciones_01/02/03, planificación_05, configuración_03/06). Los de registro
  sin implementación (02, 05–10) son **tripwires**: asserts sobre el 501
  `NoImplementado` + los tokens de contrato de cada regla; fallan en cuanto alguien
  programe la regla. Los que **no tienen implementación** — el módulo es esqueleto
  (`NoImplementado` en servicio y repositorio) o la regla vive solo en
  triggers/CHECKs de PostgreSQL o en docstrings (Sprints 3–5) — se conservan como
  skip **con motivo documentado**, nunca con asserts vacíos. Ver la sección
  «Reglas sin implementación (backlog de pruebas)» de arriba.
- **Integración (PostGIS real)**: los 9 fallos de `tests/integracion/` eran expectativas
  obsoletas tras la revisión 0008 (334c1fa): 7 aserciones esperaban `alembic head = 0007`
  y la comparación de vistas esperaba solo las de la 0004. Actualizadas a 0008 y a
  `VISTAS_0004 | VISTAS_0008`. Verificado localmente contra PostgreSQL/PostGIS real
  (135 passed). Head sigue fijado a mano en estos tests: la próxima revisión (0009)
  deberá actualizarlos otra vez.
- **Limpieza de comentarios** (solo Python; `bd/` intacto por convención): se eliminaron
  los marcadores «PENDIENTE» del código (están en «Pendientes técnicos» de arriba) y la
  referencia colgante a `docs/07_prefijos.md`. Se conservaron deliberadamente los
  comentarios que explican el *por qué* junto al código (mapeo de SQLSTATE, P-04,
  RFC 8785, argon2id, seguridad de listas blancas…): sacarlos a un md obligaría a
  saltar entre archivos para entender una línea. Los 387 comentarios de `bd/sql/` y los
  docstrings de las migraciones tampoco se tocaron: son la documentación por revisión
  que exige `bd/CAMBIOS.md` y que `scripts/revisar_migraciones.py` usa.

## Sincronización del técnico (Sprint 2)

- **Rutas del sprint** (`api/v1/rutas/sincronizacion.py`): `POST /sincronizacion/lotes`
  recibe de 1 a 200 operaciones y `GET /sincronizacion/operaciones` consulta lo recibido
  por el usuario. El lote responde **200 siempre que se pudo leer**: el resultado de cada
  operación va en `resultados`, y una operación rechazada no tumba las demás. 403 solo si
  el `disp_id` no es el del token o el dispositivo está dado de baja.
- **Idempotencia**: `UNIQUE (disp_id, oper_cliente_id)` en `operacion_sync` más la huella
  canónica SHA-256 del contenido (JSON RFC 8785). Reenviar la misma operación devuelve
  `idempotente_repetida` sin duplicar; la misma identidad con contenido distinto devuelve
  `conflicto_contenido`.
- **Resultado por operación** (`ResultadoOperacion`): `alta_nueva`, `alta_retroactiva`,
  `correccion`, `idempotente_repetida`, `conflicto_contenido`, `revisar` o `rechazada`,
  con `motivo`, `codigo` y `reintentable`. Las decisiones de negocio no son
  reintentables: reintentarlas automáticamente es un bucle.
- **Fecha real de observación**: `t_observacion` es el instante en que el técnico vio la
  trampa o el lote, no el de recepción. Las validaciones de período, lote activo y
  pertenencia de la trampa se evalúan contra esa fecha. `t_captura` y `deriva_reloj_seg`
  quedan para medir el reloj del teléfono.
- **Corrección como versión nueva**: `version = 1` es el alta; `n + 1` es corrección o
  anulación. Nunca se sobrescribe la versión anterior: la nueva fila conserva el linaje y
  la anterior queda en el histórico. La anulación marca la lectura sin borrarla.
- **«No leída» ≠ «cero moscas»**: `lect_leida = false` exige justificación y prohíbe
  `lect_especimenes`; una trampa leída con `0` especímenes es un cero real. Son estados
  distintos y no se normalizan entre sí.
- **Contrato recortado** (`contracts/openapi.json`): se quitaron
  `POST /sincronizacion/operaciones/{oper_id}/resolucion` y `GET /sincronizacion/cambios`,
  que solo tenían `NoImplementado` detrás. Quedan fuera del Sprint 2 la resolución de
  operaciones, la bajada de cambios, el recálculo de MTD/concepto y el paquete de campo.
  El contrato ya no anuncia lo que no existe.
- **Sin cambios de esquema**: el sprint usa las tablas de la revisión 0008
  (`operacion_sync`, `recepcion_sync`, `dispositivo`); no hay migración nueva.
- **Pruebas**: 228 pasan / 16 skip justificados, `mypy` sin errores y cobertura 75,75 %.
  Las reglas del sprint son pruebas reales en
  `tests/modules/sincronizacion/test_reglas.py` (idempotencia, huella inválida, no leída
  frente a cero, corrección sin sobrescribir, error aislado por operación) y en
  `test_huella.py` (vectores compartidos con el móvil).
