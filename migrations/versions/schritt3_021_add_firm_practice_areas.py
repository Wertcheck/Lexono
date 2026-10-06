"""add firm_practice_areas table (Kanzleifachprofil und juristische Wissenssteuerung)

Revision ID: schritt3_021
Revises: schritt3_020
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'schritt3_021'
down_revision: Union[str, Sequence[str], None] = 'schritt3_020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'firm_practice_areas',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('firm_profile_id', sa.String(), nullable=False),
        sa.Column('practice_area', sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(['firm_profile_id'], ['firm_profiles.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'firm_profile_id', 'practice_area', name='uq_firm_practice_areas_profile_area'
        ),
    )
    op.create_index(
        'ix_firm_practice_areas_firm_profile_id',
        'firm_practice_areas',
        ['firm_profile_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_firm_practice_areas_firm_profile_id', table_name='firm_practice_areas')
    op.drop_table('firm_practice_areas')
