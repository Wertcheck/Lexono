"""add deleted_at to matters (Aktenuebersicht finalisieren - Akte loeschen, Soft-Delete)

Revision ID: schritt3_019
Revises: schritt3_018
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_019'
down_revision: Union[str, Sequence[str], None] = 'schritt3_018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'matters',
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_matters_deleted_at', 'matters', ['deleted_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_matters_deleted_at', table_name='matters')
    op.drop_column('matters', 'deleted_at')
