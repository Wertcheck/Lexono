"""add notes table (Akte-Notizen, UI/UX-Referenzabgleich 19.09.)

Revision ID: schritt3_013
Revises: schritt3_012
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_013'
down_revision: Union[str, Sequence[str], None] = 'schritt3_012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'notes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('matter_id', sa.String(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('author', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['matter_id'], ['matters.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notes_matter_id'), 'notes', ['matter_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_notes_matter_id'), table_name='notes')
    op.drop_table('notes')
