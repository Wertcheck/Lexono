"""add priority to tasks/deadlines and status to deadlines (Aufgaben & Fristen)

Revision ID: schritt3_023
Revises: schritt3_022
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_023'
down_revision: Union[str, Sequence[str], None] = 'schritt3_022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tasks', sa.Column('priority', sa.String(length=16), nullable=True))
    op.add_column('deadlines', sa.Column('priority', sa.String(length=16), nullable=True))
    # server_default="open" noetig: bestehende Zeilen muessen beim Hinzufuegen
    # dieser NOT-NULL-Spalte einen Wert bekommen - jede bereits vorhandene
    # Frist ist realistischerweise noch offen (kein erfundener Datenwert,
    # sondern die einzig sinnvolle Annahme fuer eine neue, vorher nicht
    # existierende Spalte).
    op.add_column(
        'deadlines',
        sa.Column('status', sa.String(length=32), nullable=False, server_default='open'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('deadlines', 'status')
    op.drop_column('deadlines', 'priority')
    op.drop_column('tasks', 'priority')
