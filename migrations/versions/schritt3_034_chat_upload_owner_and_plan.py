"""chat_upload_operations: Besitzer und Plan (Wiederaufnahme nach hartem Abbruch)

Revision ID: schritt3_034
Revises: schritt3_033
Create Date: 2026-10-17 00:00:00.000000

§60: `owner` (Rechner|Prozess-ID|Startzeit) und `plan` (vorab vergebene IDs) - siehe app/models/chat_upload_operation.py.
Rein additiv, beide Spalten nullable (Altbestand: Besitzer unbekannt -> nie automatisch uebernommen).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_034'
down_revision: Union[str, Sequence[str], None] = 'schritt3_033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('chat_upload_operations') as batch:
        batch.add_column(sa.Column('owner', sa.String(length=200), nullable=True))
        batch.add_column(sa.Column('plan', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('chat_upload_operations') as batch:
        batch.drop_column('plan')
        batch.drop_column('owner')
