"""Tests fuer app/documents/service.py (Prompt 06).

Prueft insbesondere den sicheren Default: OCR-bedürftige Dokumente bleiben
bei deaktiviertem OCR im Status "pending", statt stillschweigend als
erledigt zu gelten."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.documents.extraction import ExtractionResult
from app.documents.service import DocumentProcessingService
from app.models import AuditEvent, Client, Deadline, Document, Matter
from app.models.base import Base

FIXTURES = Path(__file__).parent / "fixtures"


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


def _document_for_fixture(
    tmp_path: Path, filename: str, db: Session, *, matter_id: str | None = None
) -> Document:
    # Kopie in tmp_path, damit Tests das Original in tests/fixtures/ nie
    # veraendern.
    dest = tmp_path / filename
    shutil.copy2(FIXTURES / filename, dest)
    document = Document(file_path=str(dest), original_filename=filename, matter_id=matter_id)
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _matter(db: Session) -> Matter:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Testakte")
    db.add_all([client, matter])
    db.commit()
    return matter


def test_pdf_with_text_is_marked_not_needed(tmp_path: Path, db_session: Session) -> None:
    document = _document_for_fixture(tmp_path, "text_document.pdf", db_session)
    service = DocumentProcessingService(ocr_enabled=False)

    result = service.process_document(document, db_session)

    assert result.ocr_status == "not_needed"
    assert result.extracted_text is not None
    assert "Synthetisches Testschreiben" in result.extracted_text


def test_scanned_pdf_stays_pending_when_ocr_disabled(
    tmp_path: Path, db_session: Session
) -> None:
    """Sicherer Default: ohne aktiviertes OCR darf ein Scan-Dokument NIE
    stillschweigend als erledigt gelten."""
    document = _document_for_fixture(tmp_path, "scanned_document.pdf", db_session)
    service = DocumentProcessingService(ocr_enabled=False)

    result = service.process_document(document, db_session)

    assert result.ocr_status == "pending"
    assert result.extracted_text is None


@pytest.mark.usefixtures("use_bundled_tesseract_if_no_system_install")
def test_scanned_pdf_is_processed_when_ocr_enabled(
    tmp_path: Path, db_session: Session
) -> None:
    document = _document_for_fixture(tmp_path, "scanned_document.pdf", db_session)
    service = DocumentProcessingService(ocr_enabled=True)

    result = service.process_document(document, db_session)

    assert result.ocr_status == "done"
    assert result.extracted_text is not None
    assert "TESTTEXT" in result.extracted_text.upper()


def test_unsupported_format_is_marked_accordingly(
    tmp_path: Path, db_session: Session
) -> None:
    document = _document_for_fixture(tmp_path, "unbekannt.xyz", db_session)
    service = DocumentProcessingService(ocr_enabled=True)

    result = service.process_document(document, db_session)

    assert result.ocr_status == "unsupported_format"
    assert result.extracted_text is None


def test_original_file_is_never_modified(tmp_path: Path, db_session: Session) -> None:
    document = _document_for_fixture(tmp_path, "scanned_document.pdf", db_session)
    original_bytes = Path(document.file_path).read_bytes()
    service = DocumentProcessingService(ocr_enabled=True)

    service.process_document(document, db_session)

    assert Path(document.file_path).read_bytes() == original_bytes


def test_processing_creates_audit_event(tmp_path: Path, db_session: Session) -> None:
    document = _document_for_fixture(tmp_path, "text_document.pdf", db_session)
    service = DocumentProcessingService(ocr_enabled=False)

    result = service.process_document(document, db_session)

    events = db_session.query(AuditEvent).filter_by(entity_id=result.id).all()
    assert len(events) == 1
    assert events[0].entity_type == "Document"
    assert events[0].event_type == "document_text_extracted"
    assert events[0].actor == "system"


# --- §64: automatische Fristenerkennung nach erfolgreicher Verarbeitung ---


def test_successful_extraction_with_matter_triggers_deadline_analysis(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    matter = _matter(db_session)
    document = _document_for_fixture(
        tmp_path, "text_document.pdf", db_session, matter_id=matter.id
    )
    monkeypatch.setattr(
        "app.documents.service.extract_text",
        lambda path, min_text_length: ExtractionResult(
            text="Bitte antworten Sie bis zum 15.03.2027.", needs_ocr=False
        ),
    )
    service = DocumentProcessingService(ocr_enabled=False)

    result = service.process_document(document, db_session)

    assert result.ocr_status == "not_needed"
    deadlines = db_session.query(Deadline).filter_by(document_id=result.id).all()
    assert len(deadlines) == 1
    assert deadlines[0].matter_id == matter.id
    assert deadlines[0].review_status == "unreviewed"


def test_successful_extraction_without_matter_creates_no_deadline(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Aktenzuordnung wuerde die Fristenanalyse ohnehin nur no-op +
    Audit-Rauschen erzeugen (siehe app/deadlines/service.py) - der Aufruf
    wird deshalb bewusst gar nicht erst ausgeloest."""
    document = _document_for_fixture(tmp_path, "text_document.pdf", db_session)
    monkeypatch.setattr(
        "app.documents.service.extract_text",
        lambda path, min_text_length: ExtractionResult(
            text="Bitte antworten Sie bis zum 15.03.2027.", needs_ocr=False
        ),
    )
    service = DocumentProcessingService(ocr_enabled=False)

    service.process_document(document, db_session)

    assert db_session.query(Deadline).count() == 0


def test_failed_extraction_creates_no_deadline(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    matter = _matter(db_session)
    document = _document_for_fixture(
        tmp_path, "text_document.pdf", db_session, matter_id=matter.id
    )

    def _raise(path, min_text_length):
        raise FileNotFoundError("simulierter Fehler")

    monkeypatch.setattr("app.documents.service.extract_text", _raise)
    service = DocumentProcessingService(ocr_enabled=False)

    result = service.process_document(document, db_session)

    assert result.ocr_status == "failed"
    assert db_session.query(Deadline).count() == 0


def test_pending_ocr_creates_no_deadline(
    tmp_path: Path, db_session: Session
) -> None:
    """OCR erforderlich, aber deaktiviert -> kein verwertbarer Text, also
    auch keine Fristenanalyse (siehe test_scanned_pdf_stays_pending_when_ocr_disabled)."""
    matter = _matter(db_session)
    document = _document_for_fixture(
        tmp_path, "scanned_document.pdf", db_session, matter_id=matter.id
    )
    service = DocumentProcessingService(ocr_enabled=False)

    result = service.process_document(document, db_session)

    assert result.ocr_status == "pending"
    assert db_session.query(Deadline).count() == 0


def test_repeated_processing_does_not_duplicate_deadlines(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotenz: ein erneuter process_document-Aufruf fuer dasselbe
    Dokument (z. B. ueber den Retry-Pfad) darf keine doppelten
    Deadline-Datensaetze erzeugen."""
    matter = _matter(db_session)
    document = _document_for_fixture(
        tmp_path, "text_document.pdf", db_session, matter_id=matter.id
    )
    monkeypatch.setattr(
        "app.documents.service.extract_text",
        lambda path, min_text_length: ExtractionResult(
            text="Bitte antworten Sie bis zum 15.03.2027.", needs_ocr=False
        ),
    )
    service = DocumentProcessingService(ocr_enabled=False)

    service.process_document(document, db_session)
    service.process_document(document, db_session)

    assert db_session.query(Deadline).filter_by(document_id=document.id).count() == 1
