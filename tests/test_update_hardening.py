"""Update-Haertung (ARCHITECTURE.md §74): transaktionale SQLite-Migrationen, Sicherung vor ausstehenden Migrationen,
verstaendlicher Abbruch. Nur synthetische, temporaere Datenbanken; keine echte .env, kein Netz."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

import run
from app.backup.pre_migration import (
    PreMigrationBackupError,
    create_pre_migration_backup,
    pending_revisions,
)
from app.backup.restore_service import RestoreService

ROOT = Path(__file__).resolve().parents[1]


pytestmark = pytest.mark.usefixtures("restore_logging_after_alembic")


def _cfg(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def _old_db_with_data(tmp_path: Path) -> Path:
    """Synthetische Bestands-DB auf schritt3_029 mit Frist und Audit-Eintrag."""
    db_path = tmp_path / "data" / "kanzlei_ai.db"
    db_path.parent.mkdir(parents=True)
    command.upgrade(_cfg(db_path), "schritt3_029")
    con = sqlite3.connect(db_path)
    con.execute(
        "insert into deadlines (id, created_at, updated_at, matter_id, source_text, due_date, review_status, status)"
        " values ('d1', '2026-01-01', '2026-01-01', 'm1', '15.03.2027 :: synthetisch', '2027-03-15', 'confirmed', 'open')"
    )
    con.execute(
        "insert into audit_events (id, created_at, entity_type, entity_id, event_type, actor, details)"
        " values ('a1', '2026-01-01', 'Deadline', 'd1', 'deadline_review_status_changed', 'x', 'y')"
    )
    con.commit()
    con.close()
    return db_path


def _version(db_path: Path) -> str:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        con.close()


def test_interrupted_migration_is_rolled_back_and_the_next_start_succeeds(tmp_path, monkeypatch) -> None:
    """Reproduziert: Abbruch nach der ersten von zwei Spalten liess die Spalte stehen (Version blieb 029) - jeder
    weitere Start scheiterte an "duplicate column name". Mit transaktionalem DDL wird alles zurueckgerollt."""
    import alembic.op as alembic_op

    db_path = _old_db_with_data(tmp_path)
    real_add_column = alembic_op.add_column
    calls = {"n": 0}

    def failing_add_column(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulierter Abbruch")
        return real_add_column(*args, **kwargs)

    monkeypatch.setattr(alembic_op, "add_column", failing_add_column)
    with pytest.raises(RuntimeError):
        command.upgrade(_cfg(db_path), "head")
    monkeypatch.setattr(alembic_op, "add_column", real_add_column)

    con = sqlite3.connect(db_path)
    columns = {r[1] for r in con.execute("pragma table_info(deadlines)")}
    con.close()
    assert "finding_key" not in columns and _version(db_path) == "schritt3_029"

    command.upgrade(_cfg(db_path), "head")
    assert _version(db_path) == "schritt3_030"


def test_pending_revisions_only_for_existing_outdated_databases(tmp_path) -> None:
    missing = tmp_path / "neu.db"
    assert pending_revisions(_cfg(missing), f"sqlite:///{missing.as_posix()}") is None
    assert not missing.exists()  # nichts angelegt

    db_path = _old_db_with_data(tmp_path)
    url = f"sqlite:///{db_path.as_posix()}"
    assert pending_revisions(_cfg(db_path), url) == ("schritt3_029", "schritt3_030")
    command.upgrade(_cfg(db_path), "head")
    assert pending_revisions(_cfg(db_path), url) is None


def test_pre_migration_backup_is_restorable_with_the_existing_restore_command(tmp_path) -> None:
    db_path = _old_db_with_data(tmp_path)
    url = f"sqlite:///{db_path.as_posix()}"
    archive = create_pre_migration_backup(url, "schritt3_029", "schritt3_030")
    assert archive.parent == db_path.parent / "backups" and archive.name.startswith("vor-update_schritt3_029_auf_")
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["database.db"]

    command.upgrade(_cfg(db_path), "head")
    con = sqlite3.connect(db_path)
    con.execute("update deadlines set review_status='rejected' where id='d1'")  # Aenderung nach dem Update
    con.commit()
    con.close()

    result = RestoreService(database_url=url, intake_storage_dir=str(tmp_path / "intake"),
                            mail_attachment_storage_dir=str(tmp_path / "mail")).restore_from_backup(archive, confirm=True)
    assert result.database_restored and result.pre_restore_database_backup.exists()
    con = sqlite3.connect(db_path)
    assert con.execute("select review_status from deadlines where id='d1'").fetchone() == ("confirmed",)
    assert con.execute("select count(*) from audit_events").fetchone() == (1,)
    con.close()
    assert _version(db_path) == "schritt3_029"  # Stand vor dem Update, naechster Start migriert erneut


def test_backup_failure_is_reported_and_leaves_no_partial_archive(tmp_path, monkeypatch) -> None:
    db_path = _old_db_with_data(tmp_path)

    import app.backup.pre_migration as pre

    class FailingZip(zipfile.ZipFile):
        def write(self, *args, **kwargs):  # noqa: ARG002 - z. B. Datentraeger voll mitten im Schreiben
            raise OSError("No space left on device")

    monkeypatch.setattr(pre.zipfile, "ZipFile", FailingZip)
    with pytest.raises(PreMigrationBackupError):
        create_pre_migration_backup(f"sqlite:///{db_path.as_posix()}", "schritt3_029", "schritt3_030")
    assert list((db_path.parent / "backups").glob("*.zip")) == []


def _patch_settings(monkeypatch, db_path: Path) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(run, "_bundle_base_dir", lambda: ROOT)


def test_cmd_migrate_backs_up_then_migrates(tmp_path, monkeypatch, capsys) -> None:
    db_path = _old_db_with_data(tmp_path)
    _patch_settings(monkeypatch, db_path)
    try:
        assert run.cmd_migrate() == 0
    finally:
        from app.config import get_settings
        get_settings.cache_clear()
    assert _version(db_path) == "schritt3_030"
    backups = list((db_path.parent / "backups").glob("vor-update_*.zip"))
    assert len(backups) == 1 and str(backups[0]) in capsys.readouterr().out
    con = sqlite3.connect(db_path)
    assert con.execute("select review_status, finding_key from deadlines where id='d1'").fetchone() == ("confirmed", None)
    con.close()

    assert run.cmd_migrate() == 0  # zweiter Start: nichts ausstehend, keine weitere Sicherung
    assert len(list((db_path.parent / "backups").glob("*.zip"))) == 1


def test_cmd_migrate_fails_closed_without_backup(tmp_path, monkeypatch, capsys) -> None:
    import app.backup.pre_migration as pre

    db_path = _old_db_with_data(tmp_path)
    _patch_settings(monkeypatch, db_path)
    monkeypatch.setattr(pre, "create_pre_migration_backup",
                        lambda *a, **k: (_ for _ in ()).throw(PreMigrationBackupError("kein Platz")))
    try:
        assert run.cmd_migrate() == 1
    finally:
        from app.config import get_settings
        get_settings.cache_clear()
    assert _version(db_path) == "schritt3_029"  # Migration NICHT ausgefuehrt
    con = sqlite3.connect(db_path)
    columns = {r[1] for r in con.execute("pragma table_info(deadlines)")}
    assert "finding_key" not in columns  # nicht einmal begonnen
    assert con.execute("select review_status, due_date from deadlines where id='d1'").fetchone() == (
        "confirmed", "2027-03-15")
    assert con.execute("select count(*) from audit_events").fetchone() == (1,)
    con.close()
    err = capsys.readouterr().err
    assert "nicht gesichert" in err and "NICHT ausgefuehrt" in err and "Traceback" not in err


def test_database_from_a_newer_version_is_rejected_without_backup_or_change(tmp_path, monkeypatch, capsys) -> None:
    """Downgrade-Schutz (§74.1, reproduziert): vorher "Update fehlgeschlagen" plus Sicherung in Gegenrichtung."""
    from app.backup.pre_migration import DatabaseNewerThanProgramError

    db_path = _old_db_with_data(tmp_path)
    command.upgrade(_cfg(db_path), "head")
    con = sqlite3.connect(db_path)
    con.execute("update alembic_version set version_num='schritt3_999_zukunft'")
    con.commit()
    con.close()
    before = db_path.read_bytes()
    with pytest.raises(DatabaseNewerThanProgramError):
        pending_revisions(_cfg(db_path), f"sqlite:///{db_path.as_posix()}")

    _patch_settings(monkeypatch, db_path)
    try:
        assert run.cmd_migrate() == 1
    finally:
        from app.config import get_settings
        get_settings.cache_clear()
    assert db_path.read_bytes() == before
    assert not (db_path.parent / "backups").exists()
    err = capsys.readouterr().err
    assert "NEUEREN Lexono-Version" in err and "schritt3_999_zukunft" in err and "nichts veraendert" in err
    assert "Update der Datenbank ist fehlgeschlagen" not in err


def test_installer_clears_shipped_migrations_before_copying() -> None:
    """Downgrade-Schutz (§74.1, im echten Installer-Lauf reproduziert): ohne [InstallDelete] blieben Migrationen einer
    neueren Version liegen und die aeltere Version lief scheinbar erfolgreich weiter."""
    iss = (ROOT / "windows" / "installer.iss").read_text(encoding="utf-8")
    section = iss.split("[InstallDelete]", 1)[1].split("\n[", 1)[0]
    assert 'Type: filesandordirs; Name: "{app}\\_internal\\migrations\\versions"' in section
    assert iss.index("\n[InstallDelete]") < iss.index("\n[Files]")  # Inno fuehrt InstallDelete vor dem Kopieren aus
    assert "PROGRAMDATA" not in section.upper() and "{commonappdata}" not in section  # nie das Datenverzeichnis


def test_cmd_migrate_reports_failed_migration_with_restore_hint(tmp_path, monkeypatch, capsys) -> None:
    import alembic.command as alembic_command

    db_path = _old_db_with_data(tmp_path)
    _patch_settings(monkeypatch, db_path)
    monkeypatch.setattr(alembic_command, "upgrade",
                        lambda cfg, rev: (_ for _ in ()).throw(RuntimeError("simulierter Migrationsfehler")))
    try:
        assert run.cmd_migrate() == 1
    finally:
        from app.config import get_settings
        get_settings.cache_clear()
    err = capsys.readouterr().err
    assert "fehlgeschlagen" in err and "zurueckgerollt" in err and "Lexono.exe restore --archive" in err
    assert "simulierter Migrationsfehler" in err and "Traceback" not in err


def test_serve_shows_error_dialog_and_does_not_start_when_migration_fails(monkeypatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(run, "_acquire_single_instance_lock", lambda: object())
    monkeypatch.setattr(run, "_release_single_instance_lock", lambda handle: None)
    monkeypatch.setattr(run, "cmd_migrate", lambda: 1)
    monkeypatch.setattr(run, "_show_startup_error", shown.append)
    monkeypatch.setattr(run, "_serve_with_window", lambda settings: pytest.fail("darf nicht starten"))

    assert run.cmd_serve(open_window=True) == 1
    assert len(shown) == 1 and "app.log" in shown[0]
    assert run.cmd_serve(open_window=False) == 1 and len(shown) == 1  # ohne Fenster kein Dialog
