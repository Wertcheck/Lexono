"""add schriftsatz_operations (Idempotenz im Schriftsatz-Generator: F5, Doppelklick, Wiederholung)

Revision ID: schritt3_032
Revises: schritt3_031
Create Date: 2026-10-17 00:00:00.000000

§58: eine Zeile je Generator-Vorgang; die serverseitig ausgestellte Vorgangs-ID ist Primaerschluessel, damit genau ein
Request den Vorgang ausfuehrt. Siehe app/models/schriftsatz_operation.py. Rein additiv - bestehende Tabellen und
Daten bleiben unveraendert.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_032'
down_revision: Union[str, Sequence[str], None] = 'schritt3_031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'schriftsatz_operations',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('matter_id', sa.String(length=36), nullable=True),
        sa.Column('document_ids', sa.Text(), nullable=True),
        sa.Column('draft_id', sa.String(length=36), nullable=True),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['matter_id'], ['matters.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['draft_id'], ['drafts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_schriftsatz_operations_user_id', 'schriftsatz_operations', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_schriftsatz_operations_user_id', table_name='schriftsatz_operations')
    op.drop_table('schriftsatz_operations')
