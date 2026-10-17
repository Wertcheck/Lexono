"""schriftsatz_operations: Besitzer und Plan (harter Abbruch im Schriftsatz-Generator)

Revision ID: schritt3_035
Revises: schritt3_034
Create Date: 2026-10-17 00:00:00.000000

§61 P2: `owner` und `plan` wie bei chat_upload_operations (schritt3_034) - siehe app/models/schriftsatz_operation.py.
Rein additiv, beide Spalten nullable (Altbestand: Besitzer unbekannt -> nie automatisch uebernommen).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_035'
down_revision: Union[str, Sequence[str], None] = 'schritt3_034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('schriftsatz_operations') as batch:
        batch.add_column(sa.Column('owner', sa.String(length=200), nullable=True))
        batch.add_column(sa.Column('plan', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('schriftsatz_operations') as batch:
        batch.drop_column('plan')
        batch.drop_column('owner')
