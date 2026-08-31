"""Gemeinsame Aufräumlogik für temporäre Download-Archive (Backup-Voll-
export, Akten-Export, Mandanten-DSGVO-Datenauszug) - alle drei landen im
selben `%TEMP%\\kanzlei_ai_dashboard_exports\\`-Verzeichnis (siehe
`_DOWNLOAD_STAGING_DIR` in `backup_router.py`/`clients_router.py`) und
enthalten vollständige, unpseudonymisierte Mandanteninhalte.

Bisher wurden diese ZIPs nach dem Download NIE gelöscht (Pilot-Readiness-
Review, bekannter Härtungspunkt) - `FileResponse` streamt die Datei nur,
räumt sie aber nicht auf. Zwei sich ergänzende Mechanismen schließen das:

1. `delete_after_send`: löscht die konkrete Datei, sobald der Download
   abgeschlossen ist (Starlette führt `BackgroundTask`s nach dem
   vollständigen Senden der Response aus - auch wenn die Verbindung vom
   Client abgebrochen wurde, da der ASGI-Send-Zyklus in beiden Fällen
   beendet wird).
2. `cleanup_stale_files`: Sicherheitsnetz für den Fall, dass (1) nicht
   greift (z. B. harter Prozessabbruch zwischen Archiv-Erzeugung und
   Response-Ende) - löscht beim NÄCHSTEN Aufruf einer Export-/Backup-Route
   alle Dateien im Staging-Verzeichnis, die älter als `max_age_seconds`
   sind. Bewusst kein Hintergrund-Scheduler: die Anwendung läuft nicht
   dauerhaft im Hintergrund (Desktop-App, kein Server-Daemon), ein
   Cleanup-Zeitpunkt "bei der nächsten Nutzung dieser Funktion" ist daher
   der zuverlässigste verfügbare Hook.
"""

from __future__ import annotations

import time
from pathlib import Path

from starlette.background import BackgroundTask

#: 15 Minuten - reicht für einen langsamen Download, minimiert aber die
#: Zeit, in der eine liegen gebliebene, unpseudonymisierte Mandanten-ZIP
#: unnötig auf der Platte steht.
_DEFAULT_MAX_AGE_SECONDS = 15 * 60


def cleanup_stale_files(
    staging_dir: Path, *, max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS
) -> None:
    """Löscht alle Dateien in `staging_dir`, die älter als `max_age_seconds`
    sind. Fehler beim Löschen einzelner Dateien (z. B. noch offen durch
    einen laufenden Download) werden verschluckt - dieses Aufräumen darf
    niemals die eigentliche Export-/Backup-Aktion zum Scheitern bringen."""
    if not staging_dir.exists():
        return
    cutoff = time.time() - max_age_seconds
    for entry in staging_dir.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def delete_after_send(path: Path) -> BackgroundTask:
    """`BackgroundTask` für `FileResponse(..., background=...)` - löscht
    `path`, nachdem die Response vollständig gesendet wurde."""

    def _delete() -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return BackgroundTask(_delete)
