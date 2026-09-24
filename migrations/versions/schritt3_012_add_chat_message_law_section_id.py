"""add law_section_id to chat_messages (direct norm-question answers)

Revision ID: schritt3_012
Revises: schritt3_011
Create Date: 2026-09-13 21:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_012'
down_revision: Union[str, Sequence[str], None] = 'schritt3_011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('chat_messages', sa.Column('law_section_id', sa.String(), nullable=True))
    with op.batch_alter_table('chat_messages') as batch_op:
        batch_op.create_foreign_key(
            'fk_chat_messages_law_section_id', 'law_sections', ['law_section_id'], ['id']
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('chat_messages') as batch_op:
        batch_op.drop_constraint('fk_chat_messages_law_section_id', type_='foreignkey')
    op.drop_column('chat_messages', 'law_section_id')
