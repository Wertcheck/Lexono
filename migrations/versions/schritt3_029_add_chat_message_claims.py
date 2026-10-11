"""add chat_message_claims (prozessuebergreifende Doppelversand-Sperre)

Revision ID: schritt3_029
Revises: schritt3_028
Create Date: 2026-10-12 00:00:00.000000

Eine Zeile je gerade verarbeiteter Chat-Nutzernachricht; die Nachrichten-ID ist Primaerschluessel, damit genau ein
Prozess die Verarbeitung beginnen kann. Siehe app/chat/processing_claims.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_029'
down_revision: Union[str, Sequence[str], None] = 'schritt3_028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_message_claims',
        sa.Column('message_id', sa.String(length=36), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('owner', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['chat_messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('message_id'),
    )


def downgrade() -> None:
    op.drop_table('chat_message_claims')
