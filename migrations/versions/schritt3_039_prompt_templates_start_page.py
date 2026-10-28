"""Standard-Prompts auf der Chat-Startseite (prompt_templates: show_on_start, sort_order, icon, start_action)

Revision ID: schritt3_039
Revises: schritt3_038
Create Date: 2026-10-28 00:00:00.000000

"Standard-Prompts verwalten" (28.10.2026): die Chat-Startseite zeigt ausschliesslich die aktivierten Kanzlei-Prompts
(hoechstens vier) in der gespeicherten Reihenfolge. Bisher waren die vier Startkarten fest im Template verdrahtet
(chat.html: "Dokument analysieren", "Schriftsatz erstellen", "Zusammenfassen", "Akte öffnen").

Uebernahme ohne Datenverlust:
- die vier bisherigen Startkarten werden als Prompt-Vorlagen angelegt (gleiche Titel, Beschreibungen und
  Eingabetexte wie bisher) und aktiviert, in der bisherigen Reihenfolge - sofern es noch keine Vorlage mit
  gleichem Titel gibt (dann wird diese aktiviert, ihr Text bleibt unveraendert);
- vorhandene Kanzlei-Prompts bleiben unveraendert erhalten, inaktiv, hinter den Startkarten (alphabetisch).

Downgrade entfernt nur die Spalten (die angelegten Vorlagen bleiben als normale Bibliothekseintraege erhalten).
"""
from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_039'
down_revision: Union[str, Sequence[str], None] = 'schritt3_038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Inhalt wortgleich zu den bisherigen Startkarten (chat.html, Stand fde5c71)
_DEFAULTS = [
    ("Dokument analysieren", "Inhalte verstehen und auswerten",
     "Bitte analysiere das angehängte Dokument und fasse die wichtigsten Punkte zusammen.", "document",
     "prefill_attach"),
    ("Schriftsatz erstellen", "z. B. an Mandanten, Gericht oder Behörde",
     "Bitte erstelle einen Schriftsatz-Entwurf zu folgendem Sachverhalt: ", "pencil", "prefill"),
    ("Zusammenfassen", "Wesentliche Inhalte auf den Punkt bringen",
     "Bitte fasse das angehängte Dokument in wenigen Sätzen zusammen.", "list", "prefill_attach"),
    ("Akte öffnen", "Vorhandene Dokumente und Kontext nutzen",
     "Öffnet die Aktenauswahl: Der Chat beginnt mit der gewählten Akte und kann deren Dokumente und Kontext nutzen.",
     "folder", "open_matter"),
]


def upgrade() -> None:
    with op.batch_alter_table('prompt_templates') as batch_op:
        batch_op.add_column(sa.Column('show_on_start', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('icon', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('start_action', sa.String(length=20), nullable=False,
                                      server_default='prefill'))
    conn = op.get_bind()
    table = sa.table('prompt_templates', sa.column('id', sa.String), sa.column('name', sa.String),
                     sa.column('description', sa.String), sa.column('content', sa.Text),
                     sa.column('version', sa.Integer), sa.column('created_by_actor', sa.String),
                     sa.column('updated_by_actor', sa.String), sa.column('show_on_start', sa.Boolean),
                     sa.column('sort_order', sa.Integer), sa.column('icon', sa.String),
                     sa.column('start_action', sa.String), sa.column('created_at', sa.DateTime(timezone=True)),
                     sa.column('updated_at', sa.DateTime(timezone=True)))
    existing = {row.name: row.id for row in conn.execute(sa.select(table.c.id, table.c.name))}
    now = datetime.now(timezone.utc)
    for position, (name, description, content, icon, action) in enumerate(_DEFAULTS):
        if name in existing:
            conn.execute(table.update().where(table.c.id == existing[name]).values(
                show_on_start=True, sort_order=position, icon=icon, start_action=action))
        else:
            conn.execute(table.insert().values(
                id=str(uuid4()), name=name, description=description, content=content, version=1,
                created_by_actor="migration:schritt3_039", updated_by_actor="migration:schritt3_039",
                show_on_start=True, sort_order=position, icon=icon, start_action=action,
                created_at=now, updated_at=now))
    others = sorted((name, id_) for name, id_ in existing.items() if name not in {d[0] for d in _DEFAULTS})
    for offset, (_name, id_) in enumerate(others):
        conn.execute(table.update().where(table.c.id == id_).values(sort_order=len(_DEFAULTS) + offset))


def downgrade() -> None:
    with op.batch_alter_table('prompt_templates') as batch_op:
        batch_op.drop_column('start_action')
        batch_op.drop_column('icon')
        batch_op.drop_column('sort_order')
        batch_op.drop_column('show_on_start')
