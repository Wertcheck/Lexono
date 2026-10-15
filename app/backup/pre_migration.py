"""Automatische Datenbanksicherung vor ausstehenden Migrationen (Update-Haertung, ARCHITECTURE.md §74).

Vor jedem Update mit ausstehenden Alembic-Migrationen entsteht eine konsistente Kopie der SQLite-Datenbank (SQLite-
Backup-API, wie app/backup/service.py) als ZIP mit `database.db` - genau das Format, das die bestehende
Wiederherstellung (`Lexono.exe restore --archive <Datei>`, app/backup/restore_service.py) erwartet. Bewusst NUR die
Datenbank: Migrationen aendern keine Dokumentdateien, und ein vollstaendiges Archiv aller Dokumente bei jedem Update
waere unverhaeltnismaessig gross.

Fail-Closed: schlaegt die Sicherung fehl, darf die Migration NICHT laufen (Aufrufer: run.py::cmd_migrate).
Keine Sicherung bei einer neuen, leeren Datenbank (noch keine Revision) oder wenn nichts aussteht.
Die Sicherung enthaelt dieselben unpseudonymisierten Mandantendaten wie die Datenbank selbst und liegt deshalb im
selben geschuetzten Datenverzeichnis (neben der Datenbank, Unterordner `backups`). Es werden keine alten Sicherungen
automatisch geloescht."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine


class PreMigrationBackupError(Exception):
    pass


class DatabaseNewerThanProgramError(Exception):
    """Die Datenbank traegt eine Revision, die dieses Programm nicht kennt - sie wurde von einer NEUEREN Lexono-Version
    aktualisiert (Downgrade-Schutz, ARCHITECTURE.md §74.1). Dann weder sichern noch migrieren noch starten."""

    def __init__(self, revision: str) -> None:
        super().__init__(revision)
        self.revision = revision


def sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    return Path(database_url.removeprefix("sqlite:///"))


def pending_revisions(cfg: Config, database_url: str) -> tuple[str | None, str] | None:
    """(aktuelle Revision, Ziel-Revision), wenn Migrationen ausstehen; sonst None. Liest nur, aendert nichts.
    Wirft DatabaseNewerThanProgramError, wenn die Datenbank von einer neueren Programmversion stammt."""
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    path = sqlite_path(database_url)
    if path is not None and not path.exists():
        return None  # neue Installation: es gibt nichts zu sichern
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    if current is None or current == head:
        return None
    try:
        known = script.get_revision(current) is not None
    except Exception:  # noqa: BLE001 - Alembic meldet unbekannte Revisionen per Ausnahme
        known = False
    if not known:
        raise DatabaseNewerThanProgramError(current)
    return current, head


def create_pre_migration_backup(database_url: str, current: str, head: str, *, now: datetime | None = None) -> Path:
    path = sqlite_path(database_url)
    if path is None:
        raise PreMigrationBackupError(
            "Automatische Sicherung vor dem Update wird nur fuer SQLite unterstuetzt - bitte vor dem Update manuell "
            "sichern."
        )
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    target_dir = path.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / f"vor-update_{current}_auf_{head}_{stamp}.zip"
    try:
        with tempfile.TemporaryDirectory(dir=target_dir) as tmp:
            snapshot = Path(tmp) / "database.db"
            source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            destination = sqlite3.connect(snapshot)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, "database.db")
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None or "database.db" not in archive.namelist():
                raise PreMigrationBackupError(f"Sicherung unvollstaendig: {archive_path}")
    except PreMigrationBackupError:
        raise
    except Exception as exc:  # noqa: BLE001 - jede Ursache fuehrt fail-closed zum Abbruch des Updates
        archive_path.unlink(missing_ok=True)
        raise PreMigrationBackupError(f"Sicherung vor dem Update fehlgeschlagen: {exc}") from exc
    return archive_path
