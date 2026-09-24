"""generalize notes to also allow client-scoped notes (19.09.)

Revision ID: schritt3_014
Revises: schritt3_013
Create Date: 2026-09-19 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_014'
down_revision: Union[str, Sequence[str], None] = 'schritt3_013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('notes') as batch_op:
        batch_op.alter_column('matter_id', existing_type=sa.String(), nullable=True)
        batch_op.add_column(sa.Column('client_id', sa.String(), nullable=True))
        batch_op.create_foreign_key(
            'fk_notes_client_id', 'clients', ['client_id'], ['id']
        )
    op.create_index(op.f('ix_notes_client_id'), 'notes', ['client_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_notes_client_id'), table_name='notes')
    with op.batch_alter_table('notes') as batch_op:
        batch_op.drop_constraint('fk_notes_client_id', type_='foreignkey')
        batch_op.drop_column('client_id')
        batch_op.alter_column('matter_id', existing_type=sa.String(), nullable=False)
