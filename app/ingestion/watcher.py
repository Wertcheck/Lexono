"""IntakeWatcher – überwacht konfigurierte Ordner rekursiv auf neue Dateien.

Nutzt `watchdog`, das rekursives Monitoring unterstuetzt und damit fuer den
Scan-Eingang geeignet ist (siehe ARCHITECTURE.md §3). Jede neu erkannte
Datei wird an den `IntakeService` uebergeben. Fehler bei einzelner Datei
(z. B. nie stabil) duerfen die Ueberwachung der uebrigen Dateien/Ordner
nicht abbrechen.

Seit Prompt 31: ein Fehlschlag wird zusätzlich im Fehler-/Retry-System
(app/errors/) protokolliert, statt nur geloggt zu werden - vorher gab es
KEINEN Weg, eine fehlgeschlagene Datei erneut zu versuchen, außer sie
manuell aus dem überwachten Ordner zu entfernen und neu abzulegen (was
u. U. gar nicht möglich ist, wenn der überwachte Ordner z. B. ein
Scanner-Ausgabeverzeichnis ohne Schreibrechte für den Anwalt ist). Der
Dateipfad selbst dient als `entity_id` - zum Zeitpunkt des Fehlschlags
existiert noch kein `Document`-Datensatz (der entsteht erst bei Erfolg).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.errors.service import RetryService, mask_path_like
from app.ingestion.intake import IntakeError, IntakeService
from app.models import Document

logger = logging.getLogger(__name__)

# Callback-Typ: bekommt eine Quelldatei und liefert (bei Erfolg) das
# erzeugte Document zurueck, oder None bei Fehler.
SessionFactory = Callable[[], object]


class _NewFileEventHandler(FileSystemEventHandler):
    """Reagiert auf neu erstellte Dateien und stoesst deren Erfassung an.

    Bewusst nicht auf Verzeichnis-Events oder reine Modify-Events: ein
    "created"-Event reicht als Trigger, die eigentliche Stabilitaetspruefung
    uebernimmt der IntakeService selbst.
    """

    def __init__(
        self,
        intake_service: IntakeService,
        session_factory: SessionFactory,
        on_ingested: Callable[[Document], None] | None = None,
        retry_service: RetryService | None = None,
    ) -> None:
        self._intake_service = intake_service
        self._session_factory = session_factory
        self._on_ingested = on_ingested
        self._retry_service = retry_service or RetryService()

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle_new_file(Path(str(event.src_path)))

    def _handle_new_file(self, path: Path) -> None:
        db = self._session_factory()
        try:
            document = self._intake_service.ingest_file(path, db)  # type: ignore[arg-type]
            logger.info("Datei erfolgreich erfasst: %s -> %s", mask_path_like(str(path)), document.id)
            if self._on_ingested is not None:
                self._on_ingested(document)
        except IntakeError as exc:
            # Ohne Traceback (Datenschutz-Pruefung §26): Fehlertext und Traceback enthalten den vollen Pfad mit Dateinamen
            logger.warning("Datei konnte nicht erfasst werden: %s (%s)", mask_path_like(str(path)), type(exc).__name__)
            self._retry_service.record_failure(
                db,  # type: ignore[arg-type]
                entity_type="IntakeFile",
                entity_id=str(path),
                operation="intake",
                # Eine Datei, die nie stabil wird oder ein Symlink ist,
                # ist meist ein dauerhaftes Problem (falsche Datei,
                # bewusst abgelehnter Symlink) - nicht automatisch
                # wiederholen, sondern menschliche Prüfung verlangen.
                error_category="permanent",
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - Ueberwachung darf nicht abbrechen
            logger.error("Unerwarteter Fehler bei der Erfassung von %s (%s)", mask_path_like(str(path)), type(exc).__name__)
            self._retry_service.record_failure(
                db,  # type: ignore[arg-type]
                entity_type="IntakeFile",
                entity_id=str(path),
                operation="intake",
                # Ein unerwarteter Fehler (z. B. Netzlaufwerk kurz nicht
                # erreichbar) ist eher vorübergehend - automatischer
                # Wiederholungsversuch sinnvoll.
                error_category="transient",
                error_message="Unerwarteter Fehler bei der Dateierfassung",
            )
        finally:
            db.close()  # type: ignore[attr-defined]


class IntakeWatcher:
    """Ueberwacht eine Liste konfigurierter Ordner rekursiv.

    Ordner, die (noch) nicht existieren, werden mit einer Warnung
    uebersprungen statt die gesamte Ueberwachung zum Absturz zu bringen
    (z. B. wenn ein Netzlaufwerk kurzzeitig nicht erreichbar ist).
    """

    def __init__(
        self,
        watched_folders: list[str],
        intake_service: IntakeService,
        session_factory: SessionFactory,
        on_ingested: Callable[[Document], None] | None = None,
    ) -> None:
        self._watched_folders = watched_folders
        self._observer = Observer()
        self._handler = _NewFileEventHandler(
            intake_service, session_factory, on_ingested
        )

    def start(self) -> None:
        scheduled_any = False
        for folder in self._watched_folders:
            folder_path = Path(folder)
            if not folder_path.is_dir():
                logger.warning(
                    "Überwachter Ordner existiert nicht, wird übersprungen: %s",
                    folder,
                )
                continue
            self._observer.schedule(self._handler, str(folder_path), recursive=True)
            scheduled_any = True

        if not scheduled_any:
            logger.info(
                "Keine gültigen überwachten Ordner konfiguriert - "
                "IntakeWatcher startet, überwacht aber nichts."
            )

        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
