"""add client_type and city to clients (Referenzgetreue Mandantenuebersicht)

Revision ID: schritt3_022
Revises: schritt3_021
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_022'
down_revision: Union[str, Sequence[str], None] = 'schritt3_021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('clients', sa.Column('client_type', sa.String(length=32), nullable=True))
    op.add_column('clients', sa.Column('city', sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('clients', 'city')
    op.drop_column('clients', 'client_type')
