"""add update tracking fields to laws (Reliable Legal Knowledge Updates)

Revision ID: schritt3_020
Revises: schritt3_019
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_020'
down_revision: Union[str, Sequence[str], None] = 'schritt3_019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('laws', sa.Column('source_etag', sa.String(length=128), nullable=True))
    op.add_column('laws', sa.Column('last_checked_at', sa.DateTime(), nullable=True))
    op.add_column('laws', sa.Column('last_check_status', sa.String(length=32), nullable=True))
    op.add_column('laws', sa.Column('last_check_error', sa.Text(), nullable=True))
    op.add_column('laws', sa.Column('last_source_update_at', sa.DateTime(), nullable=True))
    op.create_index('ix_laws_last_checked_at', 'laws', ['last_checked_at'])
    op.create_index('ix_laws_last_check_status', 'laws', ['last_check_status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_laws_last_check_status', table_name='laws')
    op.drop_index('ix_laws_last_checked_at', table_name='laws')
    op.drop_column('laws', 'last_source_update_at')
    op.drop_column('laws', 'last_check_error')
    op.drop_column('laws', 'last_check_status')
    op.drop_column('laws', 'last_checked_at')
    op.drop_column('laws', 'source_etag')
