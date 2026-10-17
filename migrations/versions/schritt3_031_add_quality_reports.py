"""add quality_reports and regression_cases (Qualitaetsmeldung, Diagnose, Regressionsfall)

Revision ID: schritt3_031
Revises: schritt3_030
Create Date: 2026-10-16 00:00:00.000000

ARCHITECTURE.md §75: Meldungen zu einer Akte mit lokaler Diagnose (nur Kategorien/Zaehler) und von Hand geschriebene
synthetische Regressionsfaelle. Rein additiv - bestehende Tabellen und Daten bleiben unveraendert.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_031'
down_revision: Union[str, Sequence[str], None] = 'schritt3_030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'quality_reports',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('matter_id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=True),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('excerpt', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('reported_by', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('diagnosis_json', sa.Text(), nullable=True),
        sa.Column('suspected_stage', sa.String(length=32), nullable=True),
        sa.Column('confirmed_stage', sa.String(length=32), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('fix_reference', sa.String(length=200), nullable=True),
        sa.Column('reviewed_by', sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(['matter_id'], ['matters.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_quality_reports_matter_id', 'quality_reports', ['matter_id'])
    op.create_index('ix_quality_reports_document_id', 'quality_reports', ['document_id'])
    op.create_table(
        'regression_cases',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('report_id', sa.String(length=36), nullable=True),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('synthetic_text', sa.Text(), nullable=False),
        sa.Column('expected_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_by', sa.String(length=128), nullable=False),
        sa.Column('approved_by', sa.String(length=128), nullable=True),
        sa.Column('test_reference', sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(['report_id'], ['quality_reports.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_regression_cases_report_id', 'regression_cases', ['report_id'])


def downgrade() -> None:
    op.drop_index('ix_regression_cases_report_id', table_name='regression_cases')
    op.drop_table('regression_cases')
    op.drop_index('ix_quality_reports_document_id', table_name='quality_reports')
    op.drop_index('ix_quality_reports_matter_id', table_name='quality_reports')
    op.drop_table('quality_reports')
