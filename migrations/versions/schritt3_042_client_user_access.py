"""Mandantenzuweisung fuer Mitarbeiter in den Zusatzmodulen: client_user_access

Revision ID: schritt3_042
Revises: schritt3_041
Create Date: 2026-10-28 00:00:00.000000

Mitarbeiter duerfen in Lexono Finanzen/Steuern erfassen und vorbereiten - aber nur fuer ausdruecklich zugewiesene
Mandanten (deny by default, app/modules/client_access.py). Nur eine NEUE Tabelle, keine Vorbelegung: nach dem Update
hat kein Mitarbeiter Zugriff auf Finanz-/Steuerdaten, bis Admin oder Anwalt ihn zuweist. Admin/Anwalt unveraendert.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_042'
down_revision: Union[str, Sequence[str], None] = 'schritt3_041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'client_user_access',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('client_id', sa.String(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('granted_by', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('client_id', 'user_id', name='uq_client_user_access'),
    )
    op.create_index('ix_client_user_access_client_id', 'client_user_access', ['client_id'])
    op.create_index('ix_client_user_access_user_id', 'client_user_access', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_client_user_access_user_id', table_name='client_user_access')
    op.drop_index('ix_client_user_access_client_id', table_name='client_user_access')
    op.drop_table('client_user_access')
