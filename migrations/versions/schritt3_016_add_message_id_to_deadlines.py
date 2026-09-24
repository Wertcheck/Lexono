"""add message_id to deadlines (Fristenerkennung direkt aus Posteingang-Nachrichten)

Revision ID: schritt3_016
Revises: schritt3_015
Create Date: 2026-09-20 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_016'
down_revision: Union[str, Sequence[str], None] = 'schritt3_015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('deadlines', sa.Column('message_id', sa.String(), nullable=True))
    with op.batch_alter_table('deadlines') as batch_op:
        batch_op.create_foreign_key(
            'fk_deadlines_message_id', 'messages', ['message_id'], ['id']
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('deadlines') as batch_op:
        batch_op.drop_constraint('fk_deadlines_message_id', type_='foreignkey')
    op.drop_column('deadlines', 'message_id')
