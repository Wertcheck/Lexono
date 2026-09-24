"""add deleted_at (soft-delete) to documents (20.09.)

Revision ID: schritt3_015
Revises: schritt3_014
Create Date: 2026-09-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_015'
down_revision: Union[str, Sequence[str], None] = 'schritt3_014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('documents') as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.create_index(
        op.f('ix_documents_deleted_at'), 'documents', ['deleted_at']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_documents_deleted_at'), table_name='documents')
    with op.batch_alter_table('documents') as batch_op:
        batch_op.drop_column('deleted_at')
