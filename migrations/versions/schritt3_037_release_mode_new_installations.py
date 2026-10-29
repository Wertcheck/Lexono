"""Freigabemodus: Neuinstallationen mit Modus C, bestehende Installationen unveraendert (explizit B)

Revision ID: schritt3_037
Revises: schritt3_036
Create Date: 2026-10-17 00:00:00.000000

§63 P1. Die Migrationen laufen bei einer Neuinstallation VOR dem Anlegen des ersten Benutzers (app/setup/wizard.py:
run_migrations -> create_admin; Lexono.exe migriert bei jedem Start). Daran - und nur daran - ist eine Neuinstallation
zuverlaessig erkennbar:

- Leere Datenbank (kein Benutzer, kein Kanzleiprofil): Kanzleiprofil mit Modus "auto_review" (C) anlegen.
- Bestehende Installation: ein Kanzleiprofil OHNE Wert explizit auf "auto" (B, bisheriges Verhalten) setzen - eine
  bereits gewaehlte Einstellung wird NIE ueberschrieben. Ohne Profil bleibt es beim bisherigen bedarfsweisen Anlegen
  (ohne Wert = B).

Downgrade: keine Datenaenderung (welche Werte vorher leer waren, ist nicht mehr bekannt; leer und "auto" bedeuten
dasselbe).
"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'schritt3_037'
down_revision: Union[str, Sequence[str], None] = 'schritt3_036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_INSTALLATION_MODE = "auto_review"
EXISTING_INSTALLATION_MODE = "auto"


def upgrade() -> None:
    bind = op.get_bind()
    # Neuinstallation nur, wenn die Datenbank WIRKLICH leer ist (Review §63: auch keine Fachdaten - z. B. Altdaten
    # nach abgebrochenem Anlegen des ersten Benutzers gelten als bestehende Installation)
    tables = ("users", "firm_profiles", "clients", "matters", "documents")
    if all(not bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() for table in tables):
        now = datetime.now(timezone.utc)
        firm_profiles = sa.table(
            "firm_profiles",
            sa.column("id", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)), sa.column("firm_name", sa.String),
            sa.column("letterhead_name", sa.String), sa.column("default_document_format", sa.String),
            sa.column("timezone", sa.String), sa.column("auto_number_new_matters", sa.Boolean),
            sa.column("document_release_mode", sa.String),
        )
        # Werte = Modell-Standards (app/models/firm_profile.py) - wie ein bedarfsweise angelegtes Profil
        op.bulk_insert(firm_profiles, [{
            "id": str(uuid.uuid4()), "created_at": now, "updated_at": now, "firm_name": "",
            "letterhead_name": "Kanzlei allgemein", "default_document_format": "pdf", "timezone": "Europe/Berlin",
            "auto_number_new_matters": False, "document_release_mode": NEW_INSTALLATION_MODE,
        }])
    else:
        bind.execute(sa.text("UPDATE firm_profiles SET document_release_mode = :mode "
                             "WHERE document_release_mode IS NULL"), {"mode": EXISTING_INSTALLATION_MODE})


def downgrade() -> None:
    pass
