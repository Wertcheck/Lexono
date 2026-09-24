"""RetryService – zentrale Fehler-/Retry-Verwaltung (Prompt 31).

Verantwortlich für:
1. Einen Fehlschlag protokollieren (`record_failure`) - legt einen neuen
   `ProcessingError` an ODER erhöht den Zähler eines bestehenden
   offenen Fehlers für dieselbe (entity_type, entity_id, operation).
2. Einen Erfolg protokollieren (`record_success`) - markiert einen
   zuvor offenen Fehler als aufgelöst, falls vorhanden. Kein Fehler
   vorhanden = kein Effekt (Normalfall).
3. Fällige Wiederholungen ermitteln (`list_due_for_retry`) - für den
   manuellen "Erneut versuchen"-Button im Dashboard UND für ein
   optionales periodisches Skript (siehe scripts/retry_failed_items.py).

Exponentielles Backoff: 1. Wiederholung nach ~2 Minuten, 2. nach ~8
Minuten, 3. nach ~32 Minuten (Basis 2 Minuten, Faktor 4) - bewusst
moderat, kein Sekundentakt (die meisten hier abgedeckten Fehlerursachen
- OCR-Engine kurzzeitig nicht erreichbar, IMAP-Verbindung gestört - lösen
sich nicht in Sekunden).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AuditEvent, ProcessingError

logger = logging.getLogger(__name__)

_BACKOFF_BASE_SECONDS = 120  # 2 Minuten
_BACKOFF_FACTOR = 4


def mask_path_like(value: str) -> str:
    """Maskiert einen Wert, der wie ein Dateisystempfad aussieht (enthält
    "/" oder "\\") - `entity_id`/`error_message` sind bei
    `entity_type="IntakeFile"` der volle Quelldateipfad im überwachten
    Scan-Ordner und tragen daher (anders als eine `Document`-UUID) den
    UNVERÄNDERTEN ursprünglichen Dateinamen, der einen echten Personen-/
    Mandantennamen enthalten kann (siehe `record_failure` unten für die
    ursprüngliche, bislang NUR auf den Logeintrag/das AuditEvent
    angewendete Fassung dieser Regel). Wiederverwendet von
    `app/web/errors_router.py`, um dieselbe Maskierung auch auf die
    Fehler-/Retry-DASHBOARD-ANZEIGE anzuwenden (real gefundene Lücke: die
    zugrunde liegende `ProcessingError`-Zeile selbst blieb unmaskiert in
    der Datenbank - für den Retry-Mechanismus notwendig, siehe
    `execute_retry`/`list_due_for_retry`, die den echten Pfad brauchen -,
    wurde aber auf der für ALLE angemeldeten Nutzer sichtbaren
    `/dashboard/errors`-Seite roh gerendert, unabhängig von Aktenzuordnung
    - Verletzung der Aktenisolation)."""
    if "/" in value or "\\" in value:
        return "***"
    return value


def _compute_next_retry_at(attempt_count: int) -> datetime:
    delay_seconds = _BACKOFF_BASE_SECONDS * (_BACKOFF_FACTOR ** (attempt_count - 1))
    return datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)


class RetryService:
    def record_failure(
        self,
        db: Session,
        *,
        entity_type: str,
        entity_id: str,
        operation: str,
        error_category: str,
        error_message: str,
        max_attempts: int = 3,
        actor: str = "system",
    ) -> ProcessingError:
        """Protokolliert einen Fehlschlag. `error_message` MUSS bereits
        frei von Mandanteninhalten sein (siehe app/errors/models.py) -
        dieser Service prüft das nicht selbst, das liegt in der
        Verantwortung des Aufrufers (dieselbe Grundregel wie beim
        Security-Check-Kategorie-System, app/privacy/api_logger.py)."""
        # Sucht nach JEDEM nicht-abgeschlossenen bestehenden Eintrag für
        # dieselbe (entity_type, entity_id, operation) - unabhängig vom
        # genauen Status (pending_retry/failed_permanent/retrying).
        # WICHTIG: verhindert, dass für denselben wiederholt fehl-
        # schlagenden Vorgang mehrere separate ProcessingError-Zeilen
        # entstehen (z. B. wenn nach einem bereits "failed_permanent"
        # eingestuften Fehler ein manueller Retry erneut fehlschlägt -
        # ohne diese Erweiterung wäre dafür fälschlich ein zweiter,
        # unabhängiger Datensatz angelegt worden).
        existing = (
            db.query(ProcessingError)
            .filter_by(entity_type=entity_type, entity_id=entity_id, operation=operation)
            .filter(ProcessingError.status != "resolved")
            .first()
        )

        if existing is not None:
            existing.attempt_count += 1
            existing.error_message = error_message
            # ECHTER FUND (20.09., beim Beheben des "haengt dauerhaft auf
            # retrying"-Funds selbst entdeckt): `error_category="permanent"`
            # wurde hier bisher komplett ignoriert - nur die Erstanlage
            # (Zweig unten) beruecksichtigte es, ein WIEDERHOLTER Fehlschlag
            # fuer einen BEREITS bestehenden Eintrag wurde ausschliesslich
            # anhand der Versuchsanzahl entschieden. Ein Aufrufer, der
            # explizit "permanent" meldet (z. B. "das referenzierte
            # Dokument existiert nicht mehr - kein weiterer Versuch kann
            # jemals gelingen"), wurde damit trotzdem auf einen weiteren
            # Backoff-Versuch eingeplant, wenn `attempt_count` das Limit
            # noch nicht erreicht hatte.
            if existing.attempt_count >= existing.max_attempts or error_category == "permanent":
                existing.status = "failed_permanent"
                existing.next_retry_at = None
            else:
                existing.status = "pending_retry"
                existing.next_retry_at = _compute_next_retry_at(existing.attempt_count)
            processing_error = existing
        else:
            status = "failed_permanent" if error_category == "permanent" else "pending_retry"
            processing_error = ProcessingError(
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
                error_category=error_category,
                error_message=error_message,
                attempt_count=1,
                max_attempts=max_attempts,
                status=status,
                next_retry_at=_compute_next_retry_at(1) if status == "pending_retry" else None,
            )
            db.add(processing_error)

        db.flush()
        # SICHERHEITSKRITISCH (Prompt 32): `entity_id` ist bei
        # `entity_type="IntakeFile"` der volle Quelldateipfad im
        # überwachten Scan-Ordner - anders als bei einem `Document`
        # (immer eine UUID) trägt dieser Pfad den UNVERÄNDERTEN
        # ursprünglichen Dateinamen und kann daher einen echten
        # Personen-/Mandantennamen enthalten. Nur bei UUID-artigen
        # entity_ids (kein "/" enthalten) wird der Wert geloggt.
        safe_entity_id = mask_path_like(entity_id)
        logger.warning(
            "%s fehlgeschlagen: %s %s (Versuch %d/%d, Status %s)",
            operation,
            entity_type,
            safe_entity_id,
            processing_error.attempt_count,
            processing_error.max_attempts,
            processing_error.status,
        )
        db.add(
            AuditEvent(
                entity_type="ProcessingError",
                entity_id=processing_error.id,
                event_type="processing_failed",
                actor=actor,
                details=(
                    f"{operation} fehlgeschlagen für {entity_type} {safe_entity_id} "
                    f"(Versuch {processing_error.attempt_count}/{processing_error.max_attempts})"
                ),
            )
        )
        db.commit()
        db.refresh(processing_error)
        return processing_error

    def record_success(
        self,
        db: Session,
        *,
        entity_type: str,
        entity_id: str,
        operation: str,
        actor: str = "system",
    ) -> None:
        """Markiert einen zuvor offenen Fehler als aufgelöst. Kein Effekt,
        wenn keiner vorlag (Normalfall - die meisten Operationen
        gelingen sofort)."""
        existing = (
            db.query(ProcessingError)
            .filter_by(
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
            )
            .filter(ProcessingError.status != "resolved")
            .first()
        )
        if existing is None:
            return
        existing.status = "resolved"
        existing.resolved_at = datetime.now(timezone.utc)
        db.add(
            AuditEvent(
                entity_type="ProcessingError",
                entity_id=existing.id,
                event_type="processing_recovered",
                actor=actor,
                details=f"{operation} für {entity_type} {entity_id} nach Wiederholung erfolgreich",
            )
        )
        db.commit()

    def list_due_for_retry(self, db: Session) -> list[ProcessingError]:
        """Fehler, deren Backoff-Wartezeit abgelaufen ist - Grundlage für
        ein periodisches Retry-Skript (scripts/retry_failed_items.py)."""
        now = datetime.now(timezone.utc)
        candidates = (
            db.query(ProcessingError).filter_by(status="pending_retry").all()
        )
        due = []
        for error in candidates:
            next_retry_at = error.next_retry_at
            if next_retry_at is not None and next_retry_at.tzinfo is None:
                next_retry_at = next_retry_at.replace(tzinfo=timezone.utc)
            if next_retry_at is None or next_retry_at <= now:
                due.append(error)
        return due

    def list_all_unresolved(self, db: Session) -> list[ProcessingError]:
        """Für die Dashboard-Fehlerübersicht - pending_retry UND
        failed_permanent, sortiert nach dringlichstem zuerst."""
        return (
            db.query(ProcessingError)
            .filter(ProcessingError.status != "resolved")
            .order_by(ProcessingError.created_at.desc())
            .all()
        )

    def execute_retry(
        self, db: Session, error: ProcessingError, *, actor: str = "system"
    ) -> bool:
        """Führt EINEN konkreten Wiederholungsversuch tatsächlich aus -
        dispatcht anhand von `error.operation` an die zuständige
        Pipeline-Stufe. Wiederverwendet von `scripts/retry_failed_items.py`
        UND der manuellen "Erneut versuchen"-Aktion im Dashboard, damit
        beide Wege garantiert dasselbe Verhalten haben.

        Parallelitätsschutz: setzt den Status VOR der eigentlichen Arbeit
        sofort auf "retrying" und committet das sofort - ein zweiter,
        (nahezu) gleichzeitiger Aufruf für DENSELBEN Fehlereintrag (z. B.
        Doppelklick auf "Erneut versuchen", oder das periodische Skript
        UND ein manueller Klick zur gleichen Zeit) sieht diesen
        Zwischenstatus und bricht sofort ohne Doppelausführung ab, statt
        denselben Vorgang zweimal parallel auszuführen.

        Gibt True zurück, wenn der Versuch erfolgreich war (Fehler wurde
        aufgelöst), sonst False (inkl. wenn der Aufruf wegen eines bereits
        laufenden/abgeschlossenen Versuchs übersprungen wurde). Wirft
        `ValueError` bei unbekannter Operation - bewusst kein stiller
        Fehlschlag."""
        if error.status not in ("pending_retry", "failed_permanent"):
            # "retrying" (bereits in Bearbeitung) oder "resolved" (nichts
            # zu tun) - in beiden Fällen KEIN erneuter Ausführungsversuch.
            return False

        error.status = "retrying"
        db.commit()

        from app.config import get_settings

        settings = get_settings()

        if error.operation == "ocr":
            from app.documents.service import DocumentProcessingService
            from app.models import Document

            document = db.get(Document, error.entity_id)
            if document is None:
                # ECHTER FUND (20.09., beim GUI-Durchgang durch die reale
                # installierte Anwendung entdeckt): das zugrunde liegende
                # `Document` kann zwischenzeitlich geloescht worden sein
                # (z. B. durch einen Demo-Daten-Reset ODER durch die neue
                # Dokument-Loeschfunktion, siehe app/documents/lifecycle.py)
                # - der Status wurde oben bereits fest auf "retrying"
                # gesetzt, ein einfaches `return False` liess den Eintrag
                # DAUERHAFT in diesem Zwischenzustand haengen: die
                # Bedingung oben (`status not in ("pending_retry",
                # "failed_permanent")`) schliesst "retrying" explizit von
                # jedem weiteren Versuch aus, der Eintrag war damit fuer
                # immer weder abschliessbar noch erneut versuchbar. Real
                # reproduziert an der installierten Instanz: ein Klick auf
                # "Jetzt erneut versuchen" fuer ein bereits geloeschtes
                # Dokument blieb seit dem 18.09. dauerhaft auf "retrying"
                # stehen. `record_failure` mit `error_category="permanent"`
                # ueberfuehrt den Eintrag stattdessen in den echten,
                # abschliessenden Zustand "failed_permanent" - ehrlich
                # sichtbar als endgueltig gescheitert statt eines stillen,
                # unsichtbaren Haengers.
                self.record_failure(
                    db,
                    entity_type=error.entity_type,
                    entity_id=error.entity_id,
                    operation=error.operation,
                    error_category="permanent",
                    error_message="Dokument nicht mehr vorhanden (gelöscht).",
                    actor=actor,
                )
                return False
            processor = DocumentProcessingService(
                ocr_enabled=settings.ocr_enabled,
                ocr_languages=settings.ocr_languages,
                tesseract_cmd=settings.tesseract_cmd,
                retry_service=self,
            )
            processor.process_document(document, db, actor=actor)
            db.refresh(error)
            return error.status == "resolved"

        if error.operation == "intake":
            from pathlib import Path

            from app.ingestion.intake import IntakeError, IntakeService

            source_path = Path(error.entity_id)
            intake_service = IntakeService(settings.intake_storage_dir)
            try:
                intake_service.ingest_file(source_path, db)
            except IntakeError as exc:
                self.record_failure(
                    db,
                    entity_type="IntakeFile",
                    entity_id=error.entity_id,
                    operation="intake",
                    error_category="permanent",
                    error_message=str(exc),
                    actor=actor,
                )
                return False
            self.record_success(
                db,
                entity_type="IntakeFile",
                entity_id=error.entity_id,
                operation="intake",
                actor=actor,
            )
            return True

        raise ValueError(f"Unbekannte Operation '{error.operation}' - kein Retry möglich")
