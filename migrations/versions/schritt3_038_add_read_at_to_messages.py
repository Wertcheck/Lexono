"""Lesestatus fuer Nachrichten (messages.read_at)

Revision ID: schritt3_038
Revises: schritt3_037
Create Date: 2026-10-26 00:00:00.000000

Posteingang-Referenzumsetzung (26.10.): Nachrichtenzeilen zeigen einen Lesestatus. "Gelesen" heisst: die Nachricht
wurde im Posteingang ausdruecklich geoeffnet (Klick auf die Zeile bzw. direkter Aufruf) oder per "Als gelesen
markieren" markiert - siehe app/web/router.py.

Bestehende Nachrichten gelten als gelesen (read_at = created_at): sonst erschiene nach dem Update der gesamte Bestand
schlagartig als ungelesen, obwohl er bereits bearbeitet wurde. Neu eingehende Nachrichten sind ungelesen (NULL).

Downgrade entfernt nur die Spalte.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_038'
down_revision: Union[str, Sequence[str], None] = 'schritt3_037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('messages') as batch_op:
        batch_op.add_column(sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE messages SET read_at = created_at WHERE read_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('messages') as batch_op:
        batch_op.drop_column('read_at')
