"""add generated_from_draft_id to documents (Document Workspace / Schriftsatz Product-Completion)

Revision ID: schritt3_018
Revises: schritt3_017
Create Date: 2026-09-26 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_018'
down_revision: Union[str, Sequence[str], None] = 'schritt3_017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'documents',
        sa.Column('generated_from_draft_id', sa.String(), nullable=True),
    )
    op.create_index(
        'ix_documents_generated_from_draft_id',
        'documents',
        ['generated_from_draft_id'],
    )
    with op.batch_alter_table('documents') as batch_op:
        batch_op.create_foreign_key(
            'fk_documents_generated_from_draft_id',
            'drafts',
            ['generated_from_draft_id'],
            ['id'],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('documents') as batch_op:
        batch_op.drop_constraint(
            'fk_documents_generated_from_draft_id', type_='foreignkey'
        )
    op.drop_index('ix_documents_generated_from_draft_id', table_name='documents')
    op.drop_column('documents', 'generated_from_draft_id')
