"""Finanzbuchhaltung (MVP): fibu_client_profiles, fibu_accounts, fibu_receipts, fibu_bookings, fibu_tasks

Revision ID: schritt3_040
Revises: schritt3_039
Create Date: 2026-10-28 00:00:00.000000

Neues, mandantenbezogenes Modul "Finanzbuchhaltung" (28.10.2026, Referenzbild). Ausschliesslich NEUE Tabellen - keine
bestehende Tabelle und keine bestehenden Daten werden veraendert. Es werden keine Konten, Buchungen oder Belege
vorbelegt (ein Beispiel-Kontenplan wird nur auf ausdruecklichen Klick je Mandant angelegt, siehe app/fibu/service.py).

Downgrade entfernt die fuenf Tabellen (inklusive ihrer Inhalte; abgelegte Belegdateien bleiben im Dateisystem).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_040'
down_revision: Union[str, Sequence[str], None] = 'schritt3_039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        'fibu_client_profiles',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('client_id', sa.String(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('legal_form', sa.String(length=64), nullable=True),
        sa.Column('tax_number', sa.String(length=32), nullable=True),
        sa.Column('vat_id', sa.String(length=32), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        *_timestamps(),
    )
    op.create_index('ix_fibu_client_profiles_client_id', 'fibu_client_profiles', ['client_id'], unique=True)

    op.create_table(
        'fibu_accounts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('client_id', sa.String(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('number', sa.String(length=10), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.UniqueConstraint('client_id', 'number', name='uq_fibu_accounts_client_number'),
    )
    op.create_index('ix_fibu_accounts_client_id', 'fibu_accounts', ['client_id'])

    op.create_table(
        'fibu_receipts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('client_id', sa.String(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('file_path', sa.String(length=1024), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('processing_status', sa.String(length=16), nullable=False, server_default='stored'),
        sa.Column('uploaded_by', sa.String(length=255), nullable=False),
        *_timestamps(),
    )
    op.create_index('ix_fibu_receipts_client_id', 'fibu_receipts', ['client_id'])

    op.create_table(
        'fibu_bookings',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('client_id', sa.String(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('fiscal_year', sa.Integer(), nullable=False),
        sa.Column('booking_date', sa.Date(), nullable=False),
        sa.Column('receipt_number', sa.String(length=64), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('gross_cents', sa.BigInteger(), nullable=False),
        sa.Column('net_cents', sa.BigInteger(), nullable=False),
        sa.Column('tax_cents', sa.BigInteger(), nullable=False),
        sa.Column('tax_rate', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.String(), sa.ForeignKey('fibu_accounts.id'), nullable=False),
        sa.Column('contra_account_id', sa.String(), sa.ForeignKey('fibu_accounts.id'), nullable=False),
        sa.Column('receipt_id', sa.String(), sa.ForeignKey('fibu_receipts.id'), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='open'),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_reason', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.Column('updated_by', sa.String(length=255), nullable=False),
        *_timestamps(),
    )
    op.create_index('ix_fibu_bookings_client_id', 'fibu_bookings', ['client_id'])
    op.create_index('ix_fibu_bookings_fiscal_year', 'fibu_bookings', ['fiscal_year'])
    op.create_index('ix_fibu_bookings_receipt_id', 'fibu_bookings', ['receipt_id'])

    op.create_table(
        'fibu_tasks',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('client_id', sa.String(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=8), nullable=False, server_default='open'),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        *_timestamps(),
    )
    op.create_index('ix_fibu_tasks_client_id', 'fibu_tasks', ['client_id'])


def downgrade() -> None:
    op.drop_index('ix_fibu_tasks_client_id', table_name='fibu_tasks')
    op.drop_table('fibu_tasks')
    op.drop_index('ix_fibu_bookings_receipt_id', table_name='fibu_bookings')
    op.drop_index('ix_fibu_bookings_fiscal_year', table_name='fibu_bookings')
    op.drop_index('ix_fibu_bookings_client_id', table_name='fibu_bookings')
    op.drop_table('fibu_bookings')
    op.drop_index('ix_fibu_receipts_client_id', table_name='fibu_receipts')
    op.drop_table('fibu_receipts')
    op.drop_index('ix_fibu_accounts_client_id', table_name='fibu_accounts')
    op.drop_table('fibu_accounts')
    op.drop_index('ix_fibu_client_profiles_client_id', table_name='fibu_client_profiles')
    op.drop_table('fibu_client_profiles')
