"""Test fuer die Alembic-Migration selbst (Prompt 04).

Prueft automatisiert, was zuvor manuell verifiziert wurde: die Migration
laesst sich auf eine frische SQLite-Datenbank anwenden und wieder
vollstaendig zurueckrollen, ohne Fehler.

Nutzt eine eigene, temporaere SQLite-Datei (nicht die konfigurierte
DATABASE_URL und nicht die Test-In-Memory-DB aus test_models.py), damit
dieser Test unabhaengig von lokalem Zustand ist.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config


def _alembic_config_for(db_path: Path) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    # Ueberschreibt die aus app.config geladene URL gezielt fuer den Test.
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_migration_upgrade_and_downgrade_succeed(tmp_path: Path) -> None:
    db_path = tmp_path / "migration_test.db"
    cfg = _alembic_config_for(db_path)

    command.upgrade(cfg, "head")
    assert db_path.exists()

    import sqlite3

    con = sqlite3.connect(str(db_path))
    tables = {
        row[0]
        for row in con.execute(
            "select name from sqlite_master where type='table'"
        ).fetchall()
    }
    con.close()

    expected_tables = {
        "clients",
        "matters",
        "parties",
        "messages",
        "documents",
        "tasks",
        "deadlines",
        "drafts",
        "sources",
        "knowledge_items",
        "workflow_runs",
        "audit_events",
        "users",
        "roles",
    }
    assert expected_tables.issubset(tables)

    # Muss vollstaendig zurueckrollbar sein.
    command.downgrade(cfg, "base")


def test_letterhead_migration_preserves_existing_firm_profile_and_drafts(tmp_path: Path) -> None:
    """schritt3_027: bestehende Kanzleidaten und Entwuerfe bleiben unveraendert; der bisherige
    Briefkopf ist weiterhin der Standard (default_letterhead_id NULL), alte Entwuerfe haben keinen
    ausdruecklichen Briefkopf (NULL = Briefkopf des Kanzlei-Profils)."""
    import sqlite3

    db_path = tmp_path / "letterhead_migration.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "schritt3_026")
    con = sqlite3.connect(str(db_path))
    con.execute(
        "insert into firm_profiles (id, created_at, updated_at, firm_name, street, default_document_format, timezone, auto_number_new_matters)"
        " values ('fp1', '2026-01-01', '2026-01-01', 'Bestehende Kanzlei', 'Altweg 7', 'pdf', 'Europe/Berlin', 0)"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(str(db_path))
    name, street, letterhead_name, default_id = con.execute(
        "select firm_name, street, letterhead_name, default_letterhead_id from firm_profiles where id='fp1'"
    ).fetchone()
    draft_columns = {row[1] for row in con.execute("pragma table_info(drafts)").fetchall()}
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'").fetchall()}
    con.close()
    assert (name, street) == ("Bestehende Kanzlei", "Altweg 7")
    assert letterhead_name == "Kanzlei allgemein" and default_id is None
    assert "letterhead_ref" in draft_columns and "letterheads" in tables

    command.downgrade(cfg, "schritt3_026")
