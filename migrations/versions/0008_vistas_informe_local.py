"""Vistas de informe local y guarda geométrica: ``v_ultima_sincronizacion``, ``v_operacion_confirmada``,
``v_mtd_por_lote_escenario``, ``v_resumen_productor`` y ``fn_lote_geometria_valida`` con guarda.

Qué hace: ejecuta ``bd/sql/17_vistas_informe_local.sql``, que SOLO AGREGA cuatro vistas y reemplaza
la función del disparador ``sigat.fn_lote_geometria_valida`` por una versión que solo revalida la
contención RF-084 cuando ``lotg_geom`` se inserta o cambia (cerrar una versión o editar metadatos ya
no revalida el polígono cargado). No crea ni modifica tablas, columnas, restricciones, índices ni
datos, y no reemplaza ninguna vista existente. Es idempotente (``CREATE OR REPLACE``): una base
instalada con ``00_instalar_todo.sql``, que ya incluye el archivo, queda igual.

Por qué en un archivo nuevo y no en ``03_vistas.sql``: la revisión 0001 lo ejecuta al instalar desde
cero; editarlo cambiaría retroactivamente lo que hace 0001. Mismo patrón que 0003 / 0004.

``downgrade`` elimina SOLO las cuatro vistas y restaura la función sin la guarda (cuerpo de
``bd/sql/04_funciones.sql``). No toca ninguna otra vista ni tabla.

Revisión: 0008
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

ARCHIVO = Path(__file__).resolve().parents[2] / "bd" / "sql" / "17_vistas_informe_local.sql"

# Cuerpo de fn_lote_geometria_valida tal como lo estableció la revisión 0001 (bd/sql/04_funciones.sql),
# sin la guarda: revalida la contención en todo INSERT y UPDATE.
FN_ANTERIOR = """
CREATE OR REPLACE FUNCTION sigat.fn_lote_geometria_valida() RETURNS trigger LANGUAGE plpgsql SET search_path = sigat, public AS $$
DECLARE g geometry;
BEGIN
  SELECT p.pred_geom INTO g FROM lote l JOIN predio p USING (pred_id) WHERE l.lote_id = NEW.lote_id;
  IF g IS NOT NULL AND NOT ST_Covers(g, NEW.lotg_geom) THEN
    RAISE EXCEPTION 'La geometría del lote % queda fuera del límite del predio (RF-084)', NEW.lote_id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
"""

VISTAS = (
    "sigat.v_resumen_productor",
    "sigat.v_mtd_por_lote_escenario",
    "sigat.v_operacion_confirmada",
    "sigat.v_ultima_sincronizacion",
)


def upgrade() -> None:
    crudo = op.get_bind().connection.driver_connection
    await_only(crudo.execute(ARCHIVO.read_text(encoding="utf-8")))  # type: ignore[union-attr]


def downgrade() -> None:
    for vista in VISTAS:
        op.execute(f"DROP VIEW IF EXISTS {vista}")
    crudo = op.get_bind().connection.driver_connection
    await_only(crudo.execute(FN_ANTERIOR))  # type: ignore[union-attr]
