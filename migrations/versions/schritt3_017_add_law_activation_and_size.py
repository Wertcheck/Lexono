"""add is_active and source_size_bytes to laws (Kanzleiwissen Final Product Implementation)

Revision ID: schritt3_017
Revises: schritt3_016
Create Date: 2026-09-26 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_017'
down_revision: Union[str, Sequence[str], None] = 'schritt3_016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'laws',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
    )
    op.add_column(
        'laws',
        sa.Column('source_size_bytes', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('laws', 'source_size_bytes')
    op.drop_column('laws', 'is_active')
