"""add chat_upload_operations (Idempotenz beim Chat-Upload: Doppelklick, parallele Requests, Wiederholung)

Revision ID: schritt3_033
Revises: schritt3_032
Create Date: 2026-10-17 00:00:00.000000

§59: eine Zeile je Chat-Upload-Vorgang; die serverseitig ausgestellte Vorgangs-ID ist Primaerschluessel, damit genau
ein Request den Upload ausfuehrt. Siehe app/models/chat_upload_operation.py. Rein additiv.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_033'
down_revision: Union[str, Sequence[str], None] = 'schritt3_032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_upload_operations',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('document_ids', sa.Text(), nullable=True),
        sa.Column('upload_message_id', sa.String(length=36), nullable=True),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['upload_message_id'], ['chat_messages.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chat_upload_operations_user_id', 'chat_upload_operations', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_chat_upload_operations_user_id', table_name='chat_upload_operations')
    op.drop_table('chat_upload_operations')
