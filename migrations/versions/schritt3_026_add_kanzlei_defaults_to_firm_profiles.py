"""add kanzlei-defaults fields to firm_profiles

Revision ID: schritt3_026
Revises: schritt3_025
Create Date: 2026-10-06 00:00:00.000000

Owner-Direktive "SETTINGS -> KANZLEI FINAL UI/UX": das Referenzbild
fordert "Kanzlei-Defaults" (Standard-Aktenpraefix, Standard-Dokument-
format, Zeitzone) und "Kanzlei-Einstellungen" (Akten automatisch
nummerieren) als echte, bearbeitbare Panels - bisher gab es dafuer keine
Datengrundlage. Vier neue, nullable/mit sinnvollem Server-Default
versehene Spalten auf dem bereits bestehenden FirmProfile-Singleton
(gleiches Muster wie schritt3_025: kleine Erweiterung einer bestehenden
Komponente, kein neues Modell). `default_document_format`/`timezone`
bekommen einen Server-Default, damit bestehende Zeilen sofort einen
gueltigen, validierten Wert tragen (dieselbe Validierungsregel wie
`Settings.ui_language`/`ui_theme`: aktuell genau EIN zulaessiger Wert,
ehrlich statt vorgetaeuschter Mehrfachauswahl).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_026'
down_revision: Union[str, Sequence[str], None] = 'schritt3_025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('firm_profiles', sa.Column('matter_reference_prefix', sa.String(length=32), nullable=True))
    op.add_column(
        'firm_profiles',
        sa.Column('default_document_format', sa.String(length=16), nullable=False, server_default='pdf'),
    )
    op.add_column(
        'firm_profiles',
        sa.Column('timezone', sa.String(length=64), nullable=False, server_default='Europe/Berlin'),
    )
    op.add_column(
        'firm_profiles',
        sa.Column('auto_number_new_matters', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('firm_profiles', 'auto_number_new_matters')
    op.drop_column('firm_profiles', 'timezone')
    op.drop_column('firm_profiles', 'default_document_format')
    op.drop_column('firm_profiles', 'matter_reference_prefix')
