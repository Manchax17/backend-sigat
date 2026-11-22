"""Regla P-04: una sola cosecha abierta por lote (``trg_cosecha_una_abierta``).

Qué hace: ejecuta ``bd/sql/16_regla_cosecha_abierta.sql``, que SOLO AGREGA la función
``sigat.fn_cosecha_una_abierta()`` y el disparador ``BEFORE INSERT`` ``trg_cosecha_una_abierta`` sobre
``sigat.cosecha``. No crea ni modifica tablas, columnas, restricciones, índices ni datos. Es idempotente
(``CREATE OR REPLACE``): una base instalada con ``00_instalar_todo.sql``, que ya incluye el archivo,
queda igual.

La regla respeta el versionamiento append-only: rechaza solo la versión que ABRE una cosecha en un
lote que ya tiene otra abierta (409 ``COSECHA_ABIERTA_EXISTENTE``); corregir la que ya está abierta,
cerrarla o anularla siempre se permite. Las filas existentes no se revisan ni se tocan: si algún lote
ya incumple la regla, la migración solo emite un WARNING.

``downgrade`` elimina SOLO el disparador y la función que crea esta revisión (no toca filas).

Revisión: 0007
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ARCHIVO = Path(__file__).resolve().parents[2] / "bd" / "sql" / "16_regla_cosecha_abierta.sql"


def upgrade() -> None:
    crudo = op.get_bind().connection.driver_connection
    await_only(crudo.execute(ARCHIVO.read_text(encoding="utf-8")))  # type: ignore[union-attr]


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cosecha_una_abierta ON sigat.cosecha")
    op.execute("DROP FUNCTION IF EXISTS sigat.fn_cosecha_una_abierta()")
