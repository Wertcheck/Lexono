"""prompt_templates: Startseiten-Verwaltung (icon, show_on_home, attach_on_use, sort_order)

Revision ID: schritt3_031
Revises: schritt3_030
Create Date: 2026-10-28 00:00:00.000000

Die bisher fest in chat.html verdrahteten Startseiten-Prompts (Dokument analysieren, Schriftsatz erstellen,
Zusammenfassen) werden als echte Prompt-Vorlagen übernommen und auf der Startseite aktiviert, sofern noch keine
Vorlage gleichen Namens existiert. Vorhandene Kanzlei-Vorlagen bleiben unverändert (nicht auf der Startseite) und
werden hinter den Startseiten-Prompts einsortiert. Es gehen keine Inhalte verloren.
"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_031'
down_revision: Union[str, Sequence[str], None] = 'schritt3_030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Bewusst eingebettet (Migrationen sind eine Momentaufnahme, kein Import aus dem App-Code).
_DEFAULTS = [
    ("Dokument analysieren", "Inhalte verstehen und auswerten",
     "Bitte analysiere das angehängte Dokument und fasse die wichtigsten Punkte zusammen.", "document", True),
    ("Schriftsatz erstellen", "Z. B. an Mandanten, Gericht oder Behörde",
     "Bitte erstelle einen Schriftsatz-Entwurf zu folgendem Sachverhalt: ", "printer", False),
    ("Zusammenfassen", "Wesentliche Inhalte auf den Punkt bringen",
     "Bitte fasse das angehängte Dokument in wenigen Sätzen zusammen.", "bulb", True),
]


def upgrade() -> None:
    with op.batch_alter_table('prompt_templates') as batch:
        batch.add_column(sa.Column('icon', sa.String(length=32), nullable=False, server_default='document'))
        batch.add_column(sa.Column('show_on_home', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        batch.add_column(sa.Column('attach_on_use', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        batch.add_column(sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')))

    bind = op.get_bind()
    table = sa.table(
        'prompt_templates',
        sa.column('id', sa.String), sa.column('name', sa.String), sa.column('description', sa.String),
        sa.column('content', sa.Text), sa.column('version', sa.Integer), sa.column('created_by_actor', sa.String),
        sa.column('updated_by_actor', sa.String), sa.column('icon', sa.String), sa.column('show_on_home', sa.Boolean),
        sa.column('attach_on_use', sa.Boolean), sa.column('sort_order', sa.Integer),
        sa.column('created_at', sa.DateTime(timezone=True)), sa.column('updated_at', sa.DateTime(timezone=True)),
    )
    existing = {row[0] for row in bind.execute(sa.select(table.c.name))}
    now = datetime.now(timezone.utc)
    position = 0
    for name, description, content, icon, attach in _DEFAULTS:
        if name in existing:
            # gleichnamige Kanzlei-Vorlage: Inhalt NICHT überschreiben, nur auf der Startseite aktivieren
            # (und die neuen Spalten Icon/Dateidialog mit den bisherigen Kachelwerten belegen)
            bind.execute(
                table.update().where(table.c.name == name).values(show_on_home=True, sort_order=position, icon=icon, attach_on_use=attach)
            )
        else:
            bind.execute(table.insert().values(
                id=str(uuid.uuid4()), name=name, description=description, content=content, version=1,
                created_by_actor='migration', updated_by_actor='migration', icon=icon, show_on_home=True,
                attach_on_use=attach, sort_order=position, created_at=now, updated_at=now,
            ))
        position += 1
    # übrige Vorlagen alphabetisch dahinter (bisherige Sortierung der Bibliothek)
    rest = bind.execute(
        sa.select(table.c.id).where(table.c.show_on_home.is_(False)).order_by(table.c.name)
    ).fetchall()
    for (row_id,) in rest:
        bind.execute(table.update().where(table.c.id == row_id).values(sort_order=position))
        position += 1


def downgrade() -> None:
    with op.batch_alter_table('prompt_templates') as batch:
        batch.drop_column('sort_order')
        batch.drop_column('attach_on_use')
        batch.drop_column('show_on_home')
        batch.drop_column('icon')
