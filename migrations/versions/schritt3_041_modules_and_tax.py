"""Module & Lizenz + Lexono Steuern (Datenmodell): module_licenses, tax_filings, tax_filing_parts, tax_field_values,
tax_submissions

Revision ID: schritt3_041
Revises: schritt3_040
Create Date: 2026-10-28 00:00:00.000000

Modularisierung (28.10.2026): Basispaket "Lexono Kanzlei" plus optionale, lizenzpflichtige Module "Lexono Finanzen"
und "Lexono Steuern" (app/modules). Ausschliesslich NEUE Tabellen, keine bestehende Tabelle/Zeile wird veraendert.
Keine Lizenz wird vorbelegt -> nach dem Update sind Zusatzmodule gesperrt, bis eine gueltige, signierte Lizenz
importiert wird; bereits erfasste Finanzbuchhaltungsdaten bleiben unveraendert erhalten.

Downgrade entfernt die fuenf Tabellen samt Inhalt.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_041'
down_revision: Union[str, Sequence[str], None] = 'schritt3_040'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts() -> list[sa.Column]:
    return [sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        'module_licenses',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('license_text', sa.Text(), nullable=False),
        sa.Column('license_id', sa.String(length=64), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('imported_by', sa.String(length=255), nullable=False),
        *_ts(),
    )
    op.create_table(
        'tax_filings',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('client_id', sa.String(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('matter_id', sa.String(), sa.ForeignKey('matters.id'), nullable=True),
        sa.Column('tax_type', sa.String(length=32), nullable=False),
        sa.Column('filing_kind', sa.String(length=16), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('period_kind', sa.String(length=8), nullable=False),
        sa.Column('period_no', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('form_version', sa.String(length=32), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='draft'),
        sa.Column('correction_of_id', sa.String(), sa.ForeignKey('tax_filings.id'), nullable=True),
        sa.Column('approved_by', sa.String(length=255), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.Column('updated_by', sa.String(length=255), nullable=False),
        *_ts(),
    )
    op.create_index('ix_tax_filings_client_id', 'tax_filings', ['client_id'])
    op.create_index('ix_tax_filings_matter_id', 'tax_filings', ['matter_id'])
    op.create_table(
        'tax_filing_parts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('filing_id', sa.String(), sa.ForeignKey('tax_filings.id'), nullable=False),
        sa.Column('part_code', sa.String(length=64), nullable=False),
        sa.Column('instance_no', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('label', sa.String(length=200), nullable=False),
        *_ts(),
        sa.UniqueConstraint('filing_id', 'part_code', 'instance_no', name='uq_tax_filing_parts_instance'),
    )
    op.create_index('ix_tax_filing_parts_filing_id', 'tax_filing_parts', ['filing_id'])
    op.create_table(
        'tax_field_values',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('part_id', sa.String(), sa.ForeignKey('tax_filing_parts.id'), nullable=False),
        sa.Column('field_key', sa.String(length=64), nullable=False),
        sa.Column('value_type', sa.String(length=16), nullable=False),
        sa.Column('value_text', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='manual'),
        sa.Column('updated_by', sa.String(length=255), nullable=False),
        *_ts(),
        sa.UniqueConstraint('part_id', 'field_key', name='uq_tax_field_values_key'),
    )
    op.create_index('ix_tax_field_values_part_id', 'tax_field_values', ['part_id'])
    op.create_table(
        'tax_submissions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('filing_id', sa.String(), sa.ForeignKey('tax_filings.id'), nullable=False),
        sa.Column('environment', sa.String(length=12), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('transfer_ticket', sa.String(length=64), nullable=True),
        sa.Column('protocol_text', sa.Text(), nullable=True),
        sa.Column('error_text', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        *_ts(),
    )
    op.create_index('ix_tax_submissions_filing_id', 'tax_submissions', ['filing_id'])


def downgrade() -> None:
    op.drop_index('ix_tax_submissions_filing_id', table_name='tax_submissions')
    op.drop_table('tax_submissions')
    op.drop_index('ix_tax_field_values_part_id', table_name='tax_field_values')
    op.drop_table('tax_field_values')
    op.drop_index('ix_tax_filing_parts_filing_id', table_name='tax_filing_parts')
    op.drop_table('tax_filing_parts')
    op.drop_index('ix_tax_filings_matter_id', table_name='tax_filings')
    op.drop_index('ix_tax_filings_client_id', table_name='tax_filings')
    op.drop_table('tax_filings')
    op.drop_table('module_licenses')
