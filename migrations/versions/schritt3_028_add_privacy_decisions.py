"""add privacy_decisions and privacy_feedback_stats (Human in the Loop)

Revision ID: schritt3_028
Revises: schritt3_027
Create Date: 2026-10-12 00:00:00.000000

Entscheidungen des Anwalts zu unsicheren Begriffen (aktenbezogen, optional unterhaltungsbezogen) und eine
datensparsame, klartextfreie Statistik (HMAC des Begriffs). Siehe PRIVACY_REVIEW_ARCHITECTURE.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_028'
down_revision: Union[str, Sequence[str], None] = 'schritt3_027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'privacy_decisions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('matter_id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('term', sa.String(length=80), nullable=False),
        sa.Column('decision', sa.String(length=16), nullable=False),
        sa.Column('decided_by_user_id', sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(['matter_id'], ['matters.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('matter_id', 'conversation_id', 'term', name='uq_privacy_decision_scope_term'),
    )
    op.create_index('ix_privacy_decisions_matter_id', 'privacy_decisions', ['matter_id'])
    op.create_index('ix_privacy_decisions_conversation_id', 'privacy_decisions', ['conversation_id'])
    op.create_table(
        'privacy_feedback_stats',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('term_hmac', sa.String(length=64), nullable=False),
        sa.Column('decision', sa.String(length=16), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('features_json', sa.Text(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('term_hmac', 'decision', name='uq_privacy_feedback_term_decision'),
    )
    op.create_index('ix_privacy_feedback_stats_term_hmac', 'privacy_feedback_stats', ['term_hmac'])


def downgrade() -> None:
    op.drop_index('ix_privacy_feedback_stats_term_hmac', table_name='privacy_feedback_stats')
    op.drop_table('privacy_feedback_stats')
    op.drop_index('ix_privacy_decisions_conversation_id', table_name='privacy_decisions')
    op.drop_index('ix_privacy_decisions_matter_id', table_name='privacy_decisions')
    op.drop_table('privacy_decisions')
