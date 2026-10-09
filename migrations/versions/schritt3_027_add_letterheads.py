"""add letterheads table, firm_profiles letterhead name/default, drafts.letterhead_ref

Revision ID: schritt3_027
Revises: schritt3_026
Create Date: 2026-10-09 00:00:00.000000

Mehrere Briefkoepfe pro Kanzlei. Der bisherige Briefkopf (Briefkopffelder des FirmProfile) bleibt
unveraendert der erste Briefkopf und der Standard (`default_letterhead_id` NULL); zusaetzliche
Briefkoepfe stehen in `letterheads`. `drafts.letterhead_ref` NULL (alle bestehenden Entwuerfe) =
Briefkopf des Kanzlei-Profils, damit sich kein bestehender Entwurf aendert.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_027'
down_revision: Union[str, Sequence[str], None] = 'schritt3_026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'letterheads',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('firm_name', sa.String(length=255), nullable=False),
        sa.Column('legal_form', sa.String(length=255), nullable=True),
        sa.Column('street', sa.String(length=255), nullable=True),
        sa.Column('address_addition', sa.String(length=255), nullable=True),
        sa.Column('postal_code', sa.String(length=32), nullable=True),
        sa.Column('city', sa.String(length=128), nullable=True),
        sa.Column('phone', sa.String(length=64), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('website', sa.String(length=255), nullable=True),
        sa.Column('signatory_name', sa.String(length=255), nullable=True),
        sa.Column('logo_path', sa.String(length=1024), nullable=True),
        sa.Column('logo_original_filename', sa.String(length=255), nullable=True),
        sa.Column('signature_path', sa.String(length=1024), nullable=True),
        sa.Column('signature_original_filename', sa.String(length=255), nullable=True),
        sa.Column('updated_by_actor', sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column(
        'firm_profiles',
        sa.Column('letterhead_name', sa.String(length=120), nullable=False, server_default='Kanzlei allgemein'),
    )
    op.add_column('firm_profiles', sa.Column('default_letterhead_id', sa.String(length=36), nullable=True))
    op.add_column('drafts', sa.Column('letterhead_ref', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('drafts', 'letterhead_ref')
    op.drop_column('firm_profiles', 'default_letterhead_id'
)
    op.drop_column('firm_profiles', 'letterhead_name')
    op.drop_table('letterheads')
