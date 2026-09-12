"""BackupService – vollständige Systemsicherung (Prompt 35).

Erzeugt EIN ZIP-Archiv mit:
1. Einer konsistenten Kopie der SQLite-Datenbankdatei (über die
   SQLite-eigene Backup-API, nicht durch bloßes Kopieren der Datei -
   ein rohes Dateisystem-Kopieren während eines laufenden Schreibvorgangs
   könnte eine inkonsistente/beschädigte Kopie erzeugen; `sqlite3.
   Connection.backup()` garantiert einen konsistenten Snapshot).
2. Dem kompletten Inhalt der beiden Dokumentenspeicher-Verzeichnisse
   (`intake_storage_dir`, `mail_attachment_storage_dir`).
3. Einem NICHT-geheimen Einstellungs-Schnappschuss (`settings.json`,
   Schritt 3) - dieselbe Allowlist wie der `/api/settings`-Endpunkt
   (`SettingsOut.from_settings`, app/api/schemas.py). Bewusst OHNE die
   `.env`-Datei selbst und OHNE jedes Secret (`mail_password`,
   `anthropic_api_key`, `session_secret_key`) - ein Backup-Archiv kann in
   andere Hände geraten (Versand, externer Speicherort) und ein darin
   enthaltener gültiger API-Schlüssel wäre ein eigenständiges,
   kategorisch anderes Risiko als die (ohnehin bereits als
   schützenswert behandelten) Mandantendaten selbst - siehe
   app/api/routers/settings.py.

WICHTIG: Dies ist eine VOLLSTÄNDIGE Rohdatensicherung - sie enthält ALLE
Mandanteninhalte im Klartext (die Datenbank selbst enthält unpseudonymisierte
Daten, siehe Grundarchitektur - Pseudonymisierung passiert erst beim
Verlassen des Systems Richtung Claude API). Ein Backup-Archiv ist daher
GENAUSO schützenswert wie die Produktionsdatenbank selbst - keine
Sonderbehandlung, keine Reduzierung der Sensibilität. Backups sollten
verschlüsselt und an einem gesicherten Ort aufbewahrt werden (liegt in
der Verantwortung des Betreibers, nicht Teil dieses Moduls - siehe
Betriebsdokumentation).

Nur die SQLite-Variante wird unterstützt (`settings.database_url` muss
mit "sqlite:///" beginnen) - passend zur aktuellen Ein-Datenbank-
Architektur des Projekts.
"""

from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path


class BackupError(Exception):
    pass


class BackupService:
    def __init__(
        self,
        *,
        database_url: str,
        intake_storage_dir: str,
        mail_attachment_storage_dir: str,
    ) -> None:
        self.database_url = database_url
        self.intake_storage_dir = Path(intake_storage_dir)
        self.mail_attachment_storage_dir = Path(mail_attachment_storage_dir)

    def _sqlite_db_path(self) -> Path:
        if not self.database_url.startswith("sqlite:///"):
            raise BackupError(
                "Backup wird aktuell nur für SQLite unterstützt "
                f"(database_url={self.database_url!r})"
            )
        return Path(self.database_url.removeprefix("sqlite:///"))

    def create_backup(self, output_dir: str | Path) -> Path:
        """Erzeugt ein Backup-ZIP im angegebenen Verzeichnis und gibt den
        Pfad zurück. Dateiname enthält einen UTC-Zeitstempel, damit
        wiederholte Backups sich nie gegenseitig überschreiben."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive_path = output_dir / f"lexono_backup_{timestamp}.zip"

        db_path = self._sqlite_db_path()
        if not db_path.exists():
            raise BackupError(f"Datenbankdatei nicht gefunden: {db_path}")

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            self._add_database_snapshot(archive, db_path)
            self._add_directory(archive, self.intake_storage_dir, "intake")
            self._add_directory(archive, self.mail_attachment_storage_dir, "mail_attachments")
            self._add_settings_snapshot(archive)
            archive.writestr(
                "BACKUP_INFO.txt",
                (
                    f"Lexono Backup, erstellt {timestamp} UTC\n"
                    "Enthaelt: Datenbank-Snapshot (database.db), "
                    "intake/, mail_attachments/, settings.json\n"
                    "WICHTIG: Enthaelt vollstaendige, unpseudonymisierte "
                    "Mandanteninhalte - wie die Produktionsdatenbank selbst "
                    "zu behandeln (verschluesselt aufbewahren). settings.json "
                    "enthaelt bewusst KEINE Secrets (kein API-Schluessel, kein "
                    "Mail-Passwort) - diese muessen bei einer Wiederherstellung "
                    "separat/manuell in der .env gesetzt werden, siehe "
                    "scripts/restore_backup.py.\n"
                ),
            )

        return archive_path

    def _add_settings_snapshot(self, archive: zipfile.ZipFile) -> None:
        from app.api.schemas import SettingsOut
        from app.config import get_settings

        snapshot = SettingsOut.from_settings(get_settings())
        archive.writestr("settings.json", snapshot.model_dump_json(indent=2))

    def _add_database_snapshot(self, archive: zipfile.ZipFile, db_path: Path) -> None:
        """Nutzt `sqlite3`s eingebaute Backup-API statt eines rohen
        Datei-Kopiervorgangs - garantiert einen konsistenten Snapshot
        auch bei einer (theoretisch) gleichzeitig aktiven Schreibtransaktion."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "database.db"
            source_conn = sqlite3.connect(str(db_path))
            dest_conn = sqlite3.connect(str(snapshot_path))
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
                source_conn.close()
            archive.write(snapshot_path, arcname="database.db")

    def _add_directory(
        self, archive: zipfile.ZipFile, directory: Path, arcname_prefix: str
    ) -> None:
        if not directory.exists():
            return
        for file_path in directory.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(directory)
                archive.write(file_path, arcname=f"{arcname_prefix}/{relative}")
