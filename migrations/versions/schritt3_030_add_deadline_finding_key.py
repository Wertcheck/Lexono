"""add deadlines.finding_key and deadlines.original_source_text (stabile Fund-Zuordnung)

Revision ID: schritt3_030
Revises: schritt3_029
Create Date: 2026-10-15 00:00:00.000000

ARCHITECTURE.md §73.1: Ein KI-Vorschlag behaelt seine Herkunft auch nach einer Korrektur. `finding_key` ordnet den
Fund des Extraktors (Art, Originalangabe, Vorkommen) dem Vorschlag dauerhaft zu, `original_source_text` haelt die
urspruengliche Fundstelle fest. Beide werden nur bei der Analyse gesetzt und nie veraendert.

Bestehende Zeilen bleiben unveraendert (NULL) - keine stillschweigende Nachberechnung fuer Altdaten; die erneute
Analyse leitet fuer sie den Schluessel nur zur Laufzeit aus `source_text` ab (app/deadlines/service.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_030'
down_revision: Union[str, Sequence[str], None] = 'schritt3_029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('deadlines', sa.Column('finding_key', sa.String(length=255), nullable=True))
    op.add_column('deadlines', sa.Column('original_source_text', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('deadlines', 'original_source_text')
    op.drop_column('deadlines', 'finding_key')
