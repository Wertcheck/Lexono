"""add chat tables

Revision ID: schritt3_010
Revises: schritt3_009
Create Date: 2026-08-31 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_010'
down_revision: Union[str, Sequence[str], None] = 'schritt3_009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'chat_conversations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('matter_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(['matter_id'], ['matters.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_chat_conversations_matter_id'), 'chat_conversations', ['matter_id']
    )
    op.create_index(
        op.f('ix_chat_conversations_user_id'), 'chat_conversations', ['user_id']
    )

    op.create_table(
        'chat_messages',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('conversation_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('blocked', sa.Boolean(), nullable=False),
        sa.Column('draft_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversations.id']),
        sa.ForeignKeyConstraint(['draft_id'], ['drafts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_chat_messages_conversation_id'), 'chat_messages', ['conversation_id']
    )

    op.create_table(
        'chat_message_documents',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('message_id', sa.String(), nullable=False),
        sa.Column('document_id', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['chat_messages.id']),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_chat_message_documents_message_id'),
        'chat_message_documents',
        ['message_id'],
    )
    op.create_index(
        op.f('ix_chat_message_documents_document_id'),
        'chat_message_documents',
        ['document_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_chat_message_documents_document_id'), table_name='chat_message_documents')
    op.drop_index(op.f('ix_chat_message_documents_message_id'), table_name='chat_message_documents')
    op.drop_table('chat_message_documents')
    op.drop_index(op.f('ix_chat_messages_conversation_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_chat_conversations_user_id'), table_name='chat_conversations')
    op.drop_index(op.f('ix_chat_conversations_matter_id'), table_name='chat_conversations')
    op.drop_table('chat_conversations')
