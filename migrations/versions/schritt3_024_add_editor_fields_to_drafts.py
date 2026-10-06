"""add subject/recipient/content_format/autosave fields to drafts (Dokumenten-Editor)

Revision ID: schritt3_024
Revises: schritt3_023
Create Date: 2026-10-04 00:00:00.000000

Ermoeglicht den neuen Rich-Text-Dokumenten-Editor (Owner-Direktive
"LEXONO - Dokumenten-Editor produktionsnah implementieren"), OHNE die
bestehende Versionsketten-Architektur zu verdoppeln:

- `subject`/`recipient`: strukturierte Felder (Betreff/Empfaenger), die
  `Draft` bisher nicht hatte (nur `content` als Freitext) - NULL fuer
  jede bestehende Zeile (ehrlicher Leerzustand, kein erfundener Wert).
- `content_format`: "text" (Standard, server_default - JEDE bestehende
  Zeile bleibt unveraendert als Klartext interpretiert, siehe
  app/export/letterhead.py/pdf_export_service.py/docx_export_service.py)
  oder "html" (neu, nur vom Rich-Text-Editor erzeugt) - steuert, ob
  Vorschau/Export den Inhalt als vorformatiertes Klartext oder als
  (serverseitig sanitisiertes) HTML behandeln.
- `last_autosaved_at`: Zeitstempel fuer Autosave auf der jeweils
  aktuellen Zeile - "nur die jeweils aktuelle/neueste Zeile einer Kette
  erhaelt Status-Updates ohne Versionssprung" ist bereits ein
  bestehendes Prinzip (siehe app/models/draft.py-Moduldocstring), hier
  fuer Autosave wiederverwendet statt eines neuen Mechanismus.

KEINE neuen Spalten fuer KI-Bearbeitungsvorschlaege (Accept/Discard):
die bestehende `create_new_draft_version`-Kette (app/drafting/
versioning.py) bleibt die EINZIGE Stelle, die neue Draft-Zeilen anlegt -
ein verworfener Vorschlag wird ueber den bereits vorhandenen `status`
(neuer erlaubter Wert "ai_suggestion_discarded") markiert, keine
zusaetzliche Persistenz-/Vorschauschicht.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_024'
down_revision: Union[str, Sequence[str], None] = 'schritt3_023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('drafts', sa.Column('subject', sa.String(length=255), nullable=True))
    op.add_column('drafts', sa.Column('recipient', sa.String(length=255), nullable=True))
    op.add_column(
        'drafts',
        sa.Column(
            'content_format', sa.String(length=16), nullable=False, server_default='text'
        ),
    )
    op.add_column('drafts', sa.Column('last_autosaved_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('drafts', 'last_autosaved_at')
    op.drop_column('drafts', 'content_format')
    op.drop_column('drafts', 'recipient')
    op.drop_column('drafts', 'subject')
