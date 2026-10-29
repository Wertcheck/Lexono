"""Test fuer die Alembic-Migration selbst (Prompt 04).

Prueft automatisiert, was zuvor manuell verifiziert wurde: die Migration
laesst sich auf eine frische SQLite-Datenbank anwenden und wieder
vollstaendig zurueckrollen, ohne Fehler.

Nutzt eine eigene, temporaere SQLite-Datei (nicht die konfigurierte
DATABASE_URL und nicht die Test-In-Memory-DB aus test_models.py), damit
dieser Test unabhaengig von lokalem Zustand ist.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.usefixtures("restore_logging_after_alembic")


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


def test_privacy_decision_tables_upgrade_and_downgrade(tmp_path) -> None:
    """schritt3_028 (Human in the Loop, 12.10.2026): Tabellen entstehen beim Upgrade und verschwinden beim Downgrade."""
    import sqlite3

    from alembic import command

    db_path = tmp_path / "hitl.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'").fetchall()}
    stat_columns = {row[1] for row in con.execute("pragma table_info(privacy_feedback_stats)").fetchall()}
    con.close()
    assert {"privacy_decisions", "privacy_feedback_stats"} <= tables
    assert "term" not in stat_columns and "term_hmac" in stat_columns  # Statistik ohne Klartext-Spalte
    command.downgrade(cfg, "schritt3_027")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'").fetchall()}
    con.close()
    assert "privacy_decisions" not in tables and "privacy_feedback_stats" not in tables


def test_chat_message_claims_table_upgrade_and_downgrade(tmp_path) -> None:
    """schritt3_029 (prozessuebergreifende Doppelversand-Sperre, 12.10.2026)."""
    import sqlite3

    from alembic import command

    db_path = tmp_path / "claims.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    pk = [row[1] for row in con.execute("pragma table_info(chat_message_claims)").fetchall() if row[5]]
    con.close()
    assert pk == ["message_id"]  # Primaerschluessel = atomare Eindeutigkeit je Nachricht
    command.downgrade(cfg, "schritt3_028")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'").fetchall()}
    con.close()
    assert "chat_message_claims" not in tables


def test_deadline_finding_key_migration_preserves_existing_deadlines(tmp_path: Path) -> None:
    """schritt3_030 (ARCHITECTURE.md §73.1): bestehende Fristen bleiben unveraendert, die neuen Spalten sind NULL -
    keine stillschweigende Nachberechnung fuer Altdaten; Rueckrollen entfernt nur die neuen Spalten."""
    import sqlite3

    db_path = tmp_path / "deadline_finding_key.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "schritt3_029")
    con = sqlite3.connect(str(db_path))
    con.execute(
        "insert into deadlines (id, created_at, updated_at, matter_id, source_text, due_date, review_status, status)"
        " values ('d1', '2026-01-01', '2026-01-01', 'm1', '15.03.2027 :: synthetisch', '2027-03-15', 'confirmed', 'open')"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "select source_text, due_date, review_status, finding_key, original_source_text from deadlines where id='d1'"
    ).fetchone()
    con.close()
    assert row == ("15.03.2027 :: synthetisch", "2027-03-15", "confirmed", None, None)

    command.downgrade(cfg, "schritt3_029")
    con = sqlite3.connect(str(db_path))
    columns = {r[1] for r in con.execute("pragma table_info(deadlines)").fetchall()}
    kept = con.execute("select review_status from deadlines where id='d1'").fetchone()
    con.close()
    assert "finding_key" not in columns and kept == ("confirmed",)


def test_quality_reports_migration_is_additive_and_reversible(tmp_path: Path) -> None:
    """schritt3_031 (ARCHITECTURE.md §75): legt nur die zwei neuen Tabellen an; bestehende Fristen bleiben."""
    import sqlite3

    db_path = tmp_path / "quality_reports.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "schritt3_030")
    con = sqlite3.connect(str(db_path))
    con.execute(
        "insert into deadlines (id, created_at, updated_at, matter_id, source_text, review_status, status)"
        " values ('d1', '2026-01-01', '2026-01-01', 'm1', 'synthetisch', 'confirmed', 'open')"
    )
    con.commit()
    con.close()
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    kept = con.execute("select review_status from deadlines where id='d1'").fetchone()
    con.close()
    assert {"quality_reports", "regression_cases"} <= tables and kept == ("confirmed",)
    command.downgrade(cfg, "schritt3_030")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    con.close()
    assert not {"quality_reports", "regression_cases"} & tables


def test_schriftsatz_operations_migration_is_additive_and_reversible(tmp_path) -> None:
    """schritt3_032 (§58, Idempotenz im Schriftsatz-Generator): Vorgangs-ID als Primaerschluessel."""
    import sqlite3

    from alembic import command

    db_path = tmp_path / "operations.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    pk = [row[1] for row in con.execute("pragma table_info(schriftsatz_operations)").fetchall() if row[5]]
    con.close()
    assert pk == ["id"]  # atomare Eindeutigkeit je Vorgang
    command.downgrade(cfg, "schritt3_031")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'").fetchall()}
    con.close()
    assert "schriftsatz_operations" not in tables and "quality_reports" in tables


def test_chat_upload_operations_migration_is_additive_and_reversible(tmp_path) -> None:
    """schritt3_033 (§59, Idempotenz beim Chat-Upload): Vorgangs-ID als Primaerschluessel."""
    import sqlite3

    from alembic import command

    db_path = tmp_path / "chat_operations.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    pk = [row[1] for row in con.execute("pragma table_info(chat_upload_operations)").fetchall() if row[5]]
    con.close()
    assert pk == ["id"]
    command.downgrade(cfg, "schritt3_032")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'").fetchall()}
    con.close()
    assert "chat_upload_operations" not in tables and "schriftsatz_operations" in tables


def test_chat_upload_owner_and_plan_migration_is_additive_and_reversible(tmp_path) -> None:
    """schritt3_034 (§60, Wiederaufnahme nach hartem Abbruch): Besitzer und Plan, beide nullable."""
    import sqlite3

    from alembic import command

    db_path = tmp_path / "chat_owner.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    columns = {row[1]: row[3] for row in con.execute("pragma table_info(chat_upload_operations)").fetchall()}
    con.close()
    assert columns["owner"] == 0 and columns["plan"] == 0  # nullable: Altbestand bleibt gueltig
    command.downgrade(cfg, "schritt3_033")
    con = sqlite3.connect(str(db_path))
    columns = {row[1] for row in con.execute("pragma table_info(chat_upload_operations)").fetchall()}
    con.close()
    assert "owner" not in columns and "plan" not in columns and "fingerprint" in columns
