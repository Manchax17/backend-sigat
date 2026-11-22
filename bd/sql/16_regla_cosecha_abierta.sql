-- =====================================================================
-- SIGAT — 16. Regla P-04: UNA SOLA cosecha abierta por lote (revisión Alembic 0007)
-- Necesita: 01 a 15. Se puede volver a ejecutar (CREATE OR REPLACE). Solo agrega una función y un
-- disparador: no crea ni modifica tablas, columnas, restricciones, índices ni datos.
--
-- Decisión del responsable del proyecto (2026-11-05): un lote tiene como máximo UNA cosecha abierta.
--   * abierta  = la versión VIGENTE (la de mayor cose_version) tiene cose_fecha_real nula y no está
--                anulada (misma definición que v_cosecha_lote_vigente, 13_vistas_productor.sql).
--
-- Por qué un disparador y no un índice único parcial (el cronograma lo llamaba ux_cosecha_abierta):
-- cosecha es append-only (trg_cosecha_ro) y cada corrección es una FILA nueva con el mismo
-- cose_registro_id. Un índice UNIQUE (lote_id) WHERE cose_fecha_real IS NULL AND NOT cose_anulada
-- vería la v1 y la v2 de la MISMA cosecha como dos abiertas e impediría corregirla; y no puede saber
-- qué versión es la vigente (eso depende de otras filas). Por eso se valida en un disparador.
--
-- Qué se revisa (solo en INSERT; UPDATE/DELETE ya están bloqueados por trg_cosecha_ro):
--   * Se rechaza la versión nueva SOLO si deja ABIERTA una cosecha que antes no lo estaba en ese lote
--     (alta nueva abierta, reapertura de una cerrada o anulada, o corrección que la mueve de lote) y en
--     el lote ya hay OTRA cosecha (otro cose_registro_id) abierta.
--   * Siempre se permite: corregir una cosecha que ya estaba abierta en el mismo lote, cerrarla
--     (fecha y volumen reales) y anularla. Ninguna de esas aumenta las abiertas del lote, así que
--     los datos históricos que ya incumplan la regla se pueden seguir corrigiendo, cerrar o anular.
--   * Las filas existentes NO se modifican ni se revisan al instalar: si algún lote ya incumple la
--     regla, se avisa (WARNING) y v_cosecha_lote_vigente.cosechas_abiertas lo sigue mostrando.
--
-- Concurrencia: dos transacciones que abran a la vez cosechas en el mismo lote se ordenan con un
-- bloqueo consultivo de transacción por lote (pg_advisory_xact_lock); la segunda espera a que la
-- primera termine y, como el aislamiento del backend es READ COMMITTED (el predeterminado), su
-- consulta posterior ya ve la cosecha confirmada y se rechaza. Con REPEATABLE READ o SERIALIZABLE la
-- consulta usaría la instantánea del inicio de la transacción: el backend no usa esos niveles.
--
-- Error: SQLSTATE 23505 (unique_violation) con CONSTRAINT = 'cosecha_una_abierta_por_lote'.
-- El backend lo traduce a 409 COSECHA_ABIERTA_EXISTENTE (src/sigat_api/infra/db/errores_pg.py).
-- =====================================================================
SET search_path = sigat, public;

CREATE OR REPLACE FUNCTION sigat.fn_cosecha_una_abierta() RETURNS trigger
LANGUAGE plpgsql SET search_path = sigat, public AS $$
DECLARE
  previa sigat.cosecha%ROWTYPE;
  otra uuid;
BEGIN
  -- Cerrar o anular nunca aumenta las cosechas abiertas del lote.
  IF NEW.cose_fecha_real IS NOT NULL OR NEW.cose_anulada THEN
    RETURN NEW;
  END IF;

  SELECT * INTO previa FROM sigat.cosecha
   WHERE cose_registro_id = NEW.cose_registro_id
   ORDER BY cose_version DESC LIMIT 1;
  IF NEW.cose_version <> coalesce(previa.cose_version, 0) + 1 THEN
    RETURN NEW;  -- versión fuera de secuencia: la rechaza trg_cosecha_version con su propio mensaje
  END IF;
  IF FOUND AND previa.cose_fecha_real IS NULL AND NOT previa.cose_anulada AND previa.lote_id = NEW.lote_id THEN
    RETURN NEW;  -- corrección de una cosecha que ya estaba abierta en este lote: no cambia el total
  END IF;

  -- La versión nueva abre una cosecha en NEW.lote_id: se serializa por lote.
  PERFORM pg_advisory_xact_lock(hashtext('sigat.cosecha_una_abierta'), NEW.lote_id);

  SELECT c.cose_registro_id INTO otra
    FROM sigat.cosecha c
   WHERE c.lote_id = NEW.lote_id
     AND c.cose_registro_id <> NEW.cose_registro_id
     AND c.cose_fecha_real IS NULL
     AND NOT c.cose_anulada
     AND NOT EXISTS (SELECT 1 FROM sigat.cosecha s
                      WHERE s.cose_registro_id = c.cose_registro_id AND s.cose_version > c.cose_version)
   LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'El lote % ya tiene una cosecha abierta (registro %): ciérrela o anúlela antes de abrir otra (P-04).',
          NEW.lote_id, otra
      USING ERRCODE = 'unique_violation',
            CONSTRAINT = 'cosecha_una_abierta_por_lote',
            HINT = 'Registre la fecha y el volumen reales de la cosecha abierta, o anúlela con un motivo.';
  END IF;
  RETURN NEW;
END $$;
COMMENT ON FUNCTION sigat.fn_cosecha_una_abierta() IS
  'P-04: rechaza (23505, cosecha_una_abierta_por_lote) una versión de cosecha que deja abierta una cosecha en un lote que ya tiene otra abierta. Permite corregir la que ya está abierta, cerrarla y anularla. Serializa por lote con pg_advisory_xact_lock.';

CREATE OR REPLACE TRIGGER trg_cosecha_una_abierta
  BEFORE INSERT ON sigat.cosecha
  FOR EACH ROW EXECUTE FUNCTION sigat.fn_cosecha_una_abierta();
COMMENT ON TRIGGER trg_cosecha_una_abierta ON sigat.cosecha IS
  'P-04 (revisión 0007): una sola cosecha abierta por lote. Ver sigat.fn_cosecha_una_abierta().';

-- Aviso (sin tocar filas) si datos anteriores a la regla ya la incumplen.
DO $$
DECLARE
  lotes text;
BEGIN
  SELECT string_agg(lote_id::text, ', ' ORDER BY lote_id) INTO lotes
    FROM (SELECT c.lote_id
            FROM sigat.cosecha c
           WHERE c.cose_fecha_real IS NULL AND NOT c.cose_anulada
             AND NOT EXISTS (SELECT 1 FROM sigat.cosecha s
                              WHERE s.cose_registro_id = c.cose_registro_id AND s.cose_version > c.cose_version)
           GROUP BY c.lote_id
          HAVING count(*) > 1) x;
  IF lotes IS NOT NULL THEN
    RAISE WARNING 'P-04: los lotes % ya tienen más de una cosecha abierta. No se modifican; se pueden cerrar o anular, pero no abrir otra hasta que quede una sola.', lotes;
  END IF;
END $$;
