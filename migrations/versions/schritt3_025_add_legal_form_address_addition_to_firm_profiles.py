"""add legal_form/address_addition fields to firm_profiles

Revision ID: schritt3_025
Revises: schritt3_024
Create Date: 2026-10-06 00:00:00.000000

Owner-Direktive "SETTINGS -> KANZLEI": der Referenzgrafik-Abgleich zeigte
zwei echte, bisher fehlende Stammdatenfelder, die ein deutsches
Kanzlei-Impressum/Briefkopf ueblicherweise traegt - `legal_form`
(Rechtsform, z. B. "Partnerschaft mbB") und `address_addition`
(Adresszusatz, z. B. "c/o, Gebaeude, Etage"). Beide als einfache
nullable Strings ergaenzt, exakt dasselbe Muster wie `signatory_name`
(schritt3_005) - KEIN neues Modell/keine neue Engine, nur zwei weitere
Spalten auf dem bereits bestehenden FirmProfile-Singleton. NULL fuer
jede bestehende Zeile (ehrlicher Leerzustand, kein erfundener Wert).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_025'
down_revision: Union[str, Sequence[str], None] = 'schritt3_024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('firm_profiles', sa.Column('legal_form', sa.String(length=255), nullable=True))
    op.add_column('firm_profiles', sa.Column('address_addition', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('firm_profiles', 'address_addition')
    op.drop_column('firm_profiles', 'legal_form')
