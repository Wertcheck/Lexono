"""add source_name/doknr/source_url to law_sections (Gesetze im Internet)

Revision ID: schritt3_011
Revises: schritt3_010
Create Date: 2026-09-13 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_011'
down_revision: Union[str, Sequence[str], None] = 'schritt3_010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'law_sections',
        sa.Column(
            'source_name', sa.String(length=64), nullable=False,
            server_default='Kuratierte Auswahl',
        ),
    )
    op.add_column('law_sections', sa.Column('doknr', sa.String(length=64), nullable=True))
    op.add_column('law_sections', sa.Column('source_url', sa.String(length=512), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('law_sections', 'source_url')
    op.drop_column('law_sections', 'doknr')
    op.drop_column('law_sections', 'source_name')
