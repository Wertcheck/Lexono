"""Tests für app/errors/ (ProcessingError, RetryService) – Prompt 31.

Deckt gezielt ab: korrekte Speicherung, Retry-Zähler, exponentielles
Backoff, maximale Retry-Anzahl, Endzustand nach ausgeschöpften Retries,
Zustandsrücksetzung bei Erfolg, nachvollziehbare Fehlerhistorie bei
erneutem Fehlschlag, keine Endlosschleife, kein doppelter
Fehlerdatensatz, kein paralleler Doppelausführung, Audit-Trail,
keine PII/Secrets in Fehlermeldungen.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.errors import RetryService
from app.models import AuditEvent, Client, Document, Matter, ProcessingError
from app.models.base import Base


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_document(db: Session) -> Document:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Testakte")
    document = Document(
        matter=matter, original_filename="scan.pdf", file_path="/data/scan.pdf"
    )
    db.add_all([client, matter, document])
    db.commit()
    return document


# ==========================================================================
# 1. Grundfunktion: Fehler wird korrekt gespeichert
# ==========================================================================


def test_record_failure_creates_processing_error(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)

    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Tesseract nicht gefunden",
        actor="system",
    )

    assert error.entity_type == "Document"
    assert error.entity_id == document.id
    assert error.operation == "ocr"
    assert error.error_category == "transient"
    assert error.error_message == "Tesseract nicht gefunden"
    assert error.status == "pending_retry"
    assert error.attempt_count == 1


def test_record_failure_persists_to_database(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    assert db_session.query(ProcessingError).count() == 1


# ==========================================================================
# 2. Retry-Zähler
# ==========================================================================


def test_repeated_failure_increments_attempt_count(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)

    error = None
    for _ in range(2):
        error = service.record_failure(
            db_session,
            entity_type="Document",
            entity_id=document.id,
            operation="ocr",
            error_category="transient",
            error_message="Testfehler",
        )

    assert error.attempt_count == 2


def test_repeated_failure_updates_error_message(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Erster Fehler",
    )
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Zweiter, aktuellerer Fehler",
    )
    assert error.error_message == "Zweiter, aktuellerer Fehler"


def test_record_failure_resets_status_from_retrying_back_to_pending_retry(
    db_session: Session,
) -> None:
    """ECHTER FUND (20.09., beim Beheben eines verwandten Funds selbst
    entdeckt - siehe test_execute_retry_document_missing_does_not_leave_
    error_stuck_in_retrying): `record_failure` setzte fuer einen
    BESTEHENDEN Eintrag bisher NUR bei erschoepfter Versuchsanzahl den
    Status explizit (`failed_permanent`) - im "noch Versuche uebrig"-Zweig
    wurde `next_retry_at` aktualisiert, der Status selbst aber unveraendert
    gelassen. `execute_retry` setzt den Status VOR jedem Versuch auf
    "retrying" - ein GANZ NORMALER, erneut transient fehlschlagender
    OCR-Versuch (Dokument existiert weiterhin, nur die Extraktion
    schlaegt wieder fehl) hinterliess den Eintrag dadurch faelschlich auf
    "retrying" haengen, obwohl `next_retry_at` bereits einen neuen,
    zukuenftigen Versuchszeitpunkt trug - fuer immer unerreichbar fuer
    `list_due_for_retry` (fragt ausschliesslich `status="pending_retry"`
    ab)."""
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Erster Fehler",
        max_attempts=5,
    )
    error.status = "retrying"  # wie execute_retry es vor jedem Versuch setzt
    db_session.commit()

    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Zweiter, erneut transienter Fehler",
        max_attempts=5,
    )

    assert error.status == "pending_retry"
    assert error.next_retry_at is not None


# ==========================================================================
# 3. Exponentielles Backoff
# ==========================================================================


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite gibt DateTime(timezone=True)-Werte als naive Datetimes
    zurueck - siehe auch app/auth/permissions.py fuer dasselbe Muster."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def test_backoff_increases_with_each_attempt(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)

    error1 = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Fehler 1",
        max_attempts=5,
    )
    delay_1 = (_as_aware_utc(error1.next_retry_at) - datetime.now(timezone.utc)).total_seconds()

    error2 = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Fehler 2",
        max_attempts=5,
    )
    delay_2 = (_as_aware_utc(error2.next_retry_at) - datetime.now(timezone.utc)).total_seconds()

    error3 = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Fehler 3",
        max_attempts=5,
    )
    delay_3 = (_as_aware_utc(error3.next_retry_at) - datetime.now(timezone.utc)).total_seconds()

    assert delay_1 < delay_2 < delay_3
    # Basis 120s, Faktor 4: grob 120s / 480s / 1920s.
    assert 100 < delay_1 < 140
    assert 450 < delay_2 < 510
    assert 1800 < delay_3 < 1980


def test_next_retry_at_is_none_for_permanent_category(db_session: Session) -> None:
    """Ein als 'permanent' eingestufter Fehler wird NICHT automatisch
    wiederholt - kein next_retry_at, kein Backoff sinnvoll."""
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="permanent",
        error_message="Nicht unterstütztes Format",
    )
    assert error.next_retry_at is None
    assert error.status == "failed_permanent"


# ==========================================================================
# 4. Maximale Retry-Anzahl + Endzustand
# ==========================================================================


def test_reaches_failed_permanent_after_max_attempts(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)

    error = None
    for _ in range(3):
        error = service.record_failure(
            db_session,
            entity_type="Document",
            entity_id=document.id,
            operation="ocr",
            error_category="transient",
            error_message="Testfehler",
            max_attempts=3,
        )

    assert error.status == "failed_permanent"
    assert error.attempt_count == 3
    assert error.next_retry_at is None


def test_below_max_attempts_stays_pending_retry(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
        max_attempts=3,
    )
    assert error.status == "pending_retry"
    assert error.next_retry_at is not None


# ==========================================================================
# 5. Keine Endlosschleife
# ==========================================================================


def test_failed_permanent_never_appears_in_due_for_retry(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)

    error = None
    for _ in range(3):
        error = service.record_failure(
            db_session,
            entity_type="Document",
            entity_id=document.id,
            operation="ocr",
            error_category="transient",
            error_message="Testfehler",
            max_attempts=3,
        )
    assert error.status == "failed_permanent"

    due = service.list_due_for_retry(db_session)
    assert error.id not in {e.id for e in due}


def test_max_attempts_bounds_total_retry_count(db_session: Session) -> None:
    """Auch bei weiteren Fehlschlagsmeldungen nach Erreichen der
    Obergrenze kippt der Status nicht zurück in eine automatische
    Wiederholungsschleife."""
    service = RetryService()
    document = _make_document(db_session)

    error = None
    for _ in range(3):
        error = service.record_failure(
            db_session,
            entity_type="Document",
            entity_id=document.id,
            operation="ocr",
            error_category="transient",
            error_message="Testfehler",
            max_attempts=3,
        )
    assert error.status == "failed_permanent"

    error_again = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Noch ein Fehler",
        max_attempts=3,
    )
    assert error_again.status == "failed_permanent"
    assert error_again.id == error.id  # derselbe Datensatz, kein neuer


# ==========================================================================
# 6. Erfolgreicher Retry setzt den Fehlerzustand korrekt zurück
# ==========================================================================


def test_record_success_resolves_existing_error(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )

    service.record_success(
        db_session, entity_type="Document", entity_id=document.id, operation="ocr"
    )

    db_session.refresh(error)
    assert error.status == "resolved"
    assert error.resolved_at is not None


def test_record_success_without_prior_error_is_noop(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    service.record_success(
        db_session, entity_type="Document", entity_id=document.id, operation="ocr"
    )
    assert db_session.query(ProcessingError).count() == 0


def test_resolved_error_excluded_from_unresolved_list(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    service.record_success(
        db_session, entity_type="Document", entity_id=document.id, operation="ocr"
    )
    assert service.list_all_unresolved(db_session) == []


# ==========================================================================
# 7. Fehlgeschlagener Retry erzeugt nachvollziehbaren neuen Fehlerzustand
# ==========================================================================


def test_new_failure_after_resolution_creates_fresh_incident(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)

    first_error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Erster Vorfall",
    )
    service.record_success(
        db_session, entity_type="Document", entity_id=document.id, operation="ocr"
    )

    second_error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Zweiter, unabhängiger Vorfall",
    )

    assert second_error.id != first_error.id
    assert second_error.attempt_count == 1
    assert db_session.query(ProcessingError).count() == 2


def test_repeated_failures_never_create_duplicate_open_rows(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)

    for _ in range(5):
        service.record_failure(
            db_session,
            entity_type="Document",
            entity_id=document.id,
            operation="ocr",
            error_category="transient",
            error_message="Testfehler",
            max_attempts=10,
        )

    assert db_session.query(ProcessingError).count() == 1


# ==========================================================================
# 8. Kein paralleler Doppelversuch (execute_retry-Sperre)
# ==========================================================================


def test_execute_retry_blocked_while_already_retrying(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    error.status = "retrying"
    db_session.commit()

    result = service.execute_retry(db_session, error)
    assert result is False


def test_execute_retry_skips_already_resolved_error(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    error.status = "resolved"
    db_session.commit()

    result = service.execute_retry(db_session, error)
    assert result is False


def test_execute_retry_document_missing_returns_false_not_crash(
    db_session: Session,
) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    db_session.delete(document)
    db_session.commit()

    result = service.execute_retry(db_session, error)
    assert result is False


def test_execute_retry_document_missing_does_not_leave_error_stuck_in_retrying(
    db_session: Session,
) -> None:
    """ECHTER FUND (20.09., beim GUI-Durchgang durch die installierte
    Anwendung entdeckt): `execute_retry` setzte `error.status = "retrying"`
    VOR der eigentlichen Arbeit, aber das fruehe `return False`, wenn das
    Dokument nicht mehr existiert, ueberfuehrte den Status NIE wieder in
    einen abschliessenden Zustand - der Eintrag blieb fuer immer auf
    "retrying" haengen (ausgeschlossen von jedem weiteren Versuch UND von
    `list_due_for_retry`, das nur "pending_retry" abfragt). Real in der
    Produktions-DB gefunden: ein Eintrag, der seit einem einzigen Klick am
    18.09. bis heute unveraendert auf "retrying" stand. Erwartet jetzt
    stattdessen den echten, abschliessenden Zustand "failed_permanent"."""
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    db_session.delete(document)
    db_session.commit()

    service.execute_retry(db_session, error)

    db_session.refresh(error)
    assert error.status == "failed_permanent"
    assert error.status != "retrying"


def test_execute_retry_document_missing_is_visible_via_list_all_unresolved(
    db_session: Session,
) -> None:
    """Nach dem Fix bleibt der Eintrag als "failed_permanent" weiterhin
    ehrlich in der Fehleruebersicht sichtbar (nicht heimlich geloescht) -
    nur nicht mehr in einem toten, unerreichbaren Zwischenzustand."""
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    db_session.delete(document)
    db_session.commit()
    service.execute_retry(db_session, error)

    unresolved = service.list_all_unresolved(db_session)

    assert any(e.id == error.id for e in unresolved)


def test_execute_retry_unknown_operation_raises_value_error(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="unbekannte_operation",
        error_category="transient",
        error_message="Testfehler",
    )
    with pytest.raises(ValueError):
        service.execute_retry(db_session, error)


# ==========================================================================
# 9. Audit-Trail
# ==========================================================================


def test_record_failure_writes_audit_event(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
        actor="anwalt@kanzlei.test",
    )
    events = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="ProcessingError", entity_id=error.id)
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == "processing_failed"
    assert events[0].actor == "anwalt@kanzlei.test"


def test_record_success_writes_audit_event(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    service.record_success(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        actor="anwalt@kanzlei.test",
    )
    events = (
        db_session.query(AuditEvent)
        .filter_by(
            entity_type="ProcessingError",
            entity_id=error.id,
            event_type="processing_recovered",
        )
        .all()
    )
    assert len(events) == 1
    assert events[0].actor == "anwalt@kanzlei.test"


def test_manual_retry_via_execute_retry_attributes_actor_correctly(
    db_session: Session,
) -> None:
    """Ein über execute_retry (z. B. vom Dashboard) ausgelöster Versuch
    muss im Audit-Log dem TATSÄCHLICH handelnden Nutzer zugeordnet sein,
    nicht pauschal 'system'."""
    service = RetryService()
    document = _make_document(db_session)
    document.file_path = "/nicht/vorhanden.pdf"
    db_session.commit()
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Testfehler",
    )
    error.status = "pending_retry"
    db_session.commit()

    service.execute_retry(db_session, error, actor="anwalt@kanzlei.test")

    latest_event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="ProcessingError", entity_id=error.id)
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    assert latest_event.actor == "anwalt@kanzlei.test"


# ==========================================================================
# 10. Keine PII/vertraulichen Mandantendaten, keine Secrets in Meldungen
# ==========================================================================


def test_ocr_error_message_never_contains_original_filename() -> None:
    """Regressionstest für einen während Prompt 31 gefundenen und
    behobenen Fund: die OCR-Fehlermeldung enthielt zuvor den vollen
    Dateipfad (inkl. urspruenglichem, potenziell personenbezogenem
    Dateinamen aus einem E-Mail-Anhang)."""
    from app.documents.ocr import run_ocr

    fake_path = Path("/data/intake/uuid1234_Max_Mustermann_Steuerbescheid.pdf")
    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - OcrError-Unterklasse
        run_ocr(fake_path)

    assert "Max_Mustermann" not in str(exc_info.value)
    assert "Steuerbescheid" not in str(exc_info.value)


def test_error_message_contains_no_known_secret_patterns(db_session: Session) -> None:
    service = RetryService()
    document = _make_document(db_session)
    error = service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Tesseract nicht gefunden oder nicht ausführbar",
    )
    forbidden_patterns = ("sk-ant-", "Bearer ", "AKIA", "-----BEGIN")
    for pattern in forbidden_patterns:
        assert pattern not in error.error_message


def test_processing_error_model_docstring_states_no_pii_rule() -> None:
    """Stellt sicher, dass die Grundregel im Modul selbst dokumentiert
    bleibt - einfacher, aber wirksamer Schutz gegen versehentliches
    Vergessen bei künftigen Erweiterungen."""
    import app.models.processing_error as module

    assert "NIEMALS den Inhalt eines Dokuments" in module.__doc__
