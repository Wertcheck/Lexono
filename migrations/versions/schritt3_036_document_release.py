"""Dokumentfreigabe: Freigabemodus (Kanzlei), Anzeigepraeferenz (Benutzer), Pruefergebnisse, manuelle Freigaben

Revision ID: schritt3_036
Revises: schritt3_035
Create Date: 2026-10-17 00:00:00.000000

§62: rein additiv. `firm_profiles.document_release_mode` nullable (kein Wert = Standard "auto", das bisherige
Verhalten - keine stillschweigende Aenderung bestehender Installationen). `users.show_auto_release_notice` mit
server_default true (die informative Meldung bleibt fuer alle sichtbar, bis jemand sie selbst ausblendet).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_036'
down_revision: Union[str, Sequence[str], None] = 'schritt3_035'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('firm_profiles') as batch:
        batch.add_column(sa.Column('document_release_mode', sa.String(length=16), nullable=True))
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('show_auto_release_notice', sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
    op.create_table(
        'document_privacy_checks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('text_version', sa.String(length=32), nullable=False),
        sa.Column('context_key', sa.String(length=32), nullable=False),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('candidate_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'text_version', 'context_key', name='uq_document_privacy_check'),
    )
    op.create_index('ix_document_privacy_checks_document_id', 'document_privacy_checks', ['document_id'])
    op.create_table(
        'document_releases',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('text_version', sa.String(length=32), nullable=False),
        sa.Column('released_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('mode_at_release', sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['released_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'text_version', name='uq_document_release_version'),
    )
    op.create_index('ix_document_releases_document_id', 'document_releases', ['document_id'])


def downgrade() -> None:
    op.drop_index('ix_document_releases_document_id', table_name='document_releases')
    op.drop_table('document_releases')
    op.drop_index('ix_document_privacy_checks_document_id', table_name='document_privacy_checks')
    op.drop_table('document_privacy_checks')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('show_auto_release_notice')
    with op.batch_alter_table('firm_profiles') as batch:
        batch.drop_column('document_release_mode')
