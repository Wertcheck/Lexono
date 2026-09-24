"""Tests fuer app/deadlines/service.py (Prompt 10)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.deadlines.extractor import PlaceholderDeadlineExtractor
from app.deadlines.service import DeadlineAnalysisService
from app.models import AuditEvent, Client, Deadline, Document, Matter, Message
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


def _matter(db: Session) -> Matter:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Testakte")
    db.add_all([client, matter])
    db.commit()
    return matter


def test_creates_deadlines_for_document_with_matter(db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/schreiben.pdf",
        matter_id=matter.id,
        extracted_text="Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(document)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_document(document, db_session)

    assert len(created) == 1
    assert created[0].matter_id == matter.id
    assert created[0].document_id == document.id
    assert created[0].review_status == "unreviewed"

    persisted = db_session.query(Deadline).all()
    assert len(persisted) == 1


def test_skips_when_no_extracted_text(db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(file_path="/tmp/x.pdf", matter_id=matter.id, extracted_text=None)
    db_session.add(document)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_document(document, db_session)

    assert created == []
    events = db_session.query(AuditEvent).filter_by(entity_id=document.id).all()
    assert events[0].event_type == "deadline_analysis_skipped"


def test_skips_when_document_has_no_matter(db_session: Session) -> None:
    """Ohne Aktenzuordnung (Prompt 09) darf keine Deadline erzeugt werden -
    Deadline.matter_id ist nicht nullable."""
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=None,
        extracted_text="Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(document)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_document(document, db_session)

    assert created == []
    assert db_session.query(Deadline).count() == 0
    events = db_session.query(AuditEvent).filter_by(entity_id=document.id).all()
    assert events[0].event_type == "deadline_analysis_skipped"
    assert "Akte" in events[0].details


def test_no_dates_found_creates_no_deadlines_but_logs_completion(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=matter.id,
        extracted_text="Ein Text ganz ohne Datumsangaben.",
    )
    db_session.add(document)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_document(document, db_session)

    assert created == []
    events = db_session.query(AuditEvent).filter_by(entity_id=document.id).all()
    assert events[0].event_type == "deadline_analysis_completed"


def test_analyzing_same_document_twice_does_not_duplicate(db_session: Session) -> None:
    """Idempotenz (§64): ein zweiter Aufruf fuer dasselbe Dokument erzeugt
    keine Duplikate, sondern liefert die bereits vorhandenen Fristen
    unveraendert zurueck."""
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=matter.id,
        extracted_text="Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(document)
    db_session.commit()
    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())

    first_run = service.analyze_document(document, db_session)
    second_run = service.analyze_document(document, db_session)

    assert len(first_run) == 1
    assert [d.id for d in second_run] == [d.id for d in first_run]
    assert db_session.query(Deadline).filter_by(document_id=document.id).count() == 1
    events = db_session.query(AuditEvent).filter_by(entity_id=document.id).all()
    assert any(e.event_type == "deadline_analysis_already_done" for e in events)


def test_multiple_deadlines_all_stay_unreviewed(db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=matter.id,
        extracted_text=(
            "Erste Frist bis zum 01.01.2027. Zweite Frist bis zum 15.06.2027."
        ),
    )
    db_session.add(document)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_document(document, db_session)

    assert len(created) == 2
    assert all(d.review_status == "unreviewed" for d in created)


def test_source_text_contains_raw_date_and_context(db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=matter.id,
        extracted_text="Bitte antworten Sie bis zum 15.03.2027 auf unser Schreiben.",
    )
    db_session.add(document)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_document(document, db_session)

    assert "15.03.2027" in created[0].source_text


def test_reasoning_is_persisted_and_states_not_binding(db_session: Session) -> None:
    """Der vom Extractor berechnete Reasoning-Text (siehe
    app/deadlines/extractor.py::ExtractedDeadline.reasoning) darf nicht
    verworfen werden - er ist die maschinenlesbare Begruendung dafuer,
    warum diese Frist NICHT automatisch als verbindlich gilt."""
    matter = _matter(db_session)
    document = Document(
        file_path="/tmp/x.pdf",
        matter_id=matter.id,
        extracted_text="Bitte antworten Sie bis zum 15.03.2027 auf unser Schreiben.",
    )
    db_session.add(document)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_document(document, db_session)

    assert created[0].reasoning is not None
    assert "NICHT als verbindlich bestätigt" in created[0].reasoning
    assert "regelbasiert, kein LLM" in created[0].reasoning


# --- analyze_message (20.09., Posteingang-Fristenerkennung) ---


def test_creates_deadlines_for_message_with_matter(db_session: Session) -> None:
    matter = _matter(db_session)
    message = Message(
        direction="inbound",
        matter_id=matter.id,
        body_text="Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(message)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_message(message, db_session)

    assert len(created) == 1
    assert created[0].matter_id == matter.id
    assert created[0].message_id == message.id
    assert created[0].document_id is None
    assert created[0].review_status == "unreviewed"


def test_skips_message_when_no_body_text(db_session: Session) -> None:
    matter = _matter(db_session)
    message = Message(direction="inbound", matter_id=matter.id, body_text=None)
    db_session.add(message)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_message(message, db_session)

    assert created == []
    events = db_session.query(AuditEvent).filter_by(entity_id=message.id).all()
    assert events[0].event_type == "deadline_analysis_skipped"


def test_skips_message_when_not_assigned_to_matter(db_session: Session) -> None:
    message = Message(
        direction="inbound",
        matter_id=None,
        body_text="Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(message)
    db_session.commit()

    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())
    created = service.analyze_message(message, db_session)

    assert created == []
    assert db_session.query(Deadline).count() == 0
    events = db_session.query(AuditEvent).filter_by(entity_id=message.id).all()
    assert events[0].event_type == "deadline_analysis_skipped"
    assert "Akte" in events[0].details


def test_analyzing_same_message_twice_does_not_duplicate(db_session: Session) -> None:
    matter = _matter(db_session)
    message = Message(
        direction="inbound",
        matter_id=matter.id,
        body_text="Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(message)
    db_session.commit()
    service = DeadlineAnalysisService(PlaceholderDeadlineExtractor())

    first_run = service.analyze_message(message, db_session)
    second_run = service.analyze_message(message, db_session)

    assert len(first_run) == 1
    assert [d.id for d in second_run] == [d.id for d in first_run]
    assert db_session.query(Deadline).filter_by(message_id=message.id).count() == 1
    events = db_session.query(AuditEvent).filter_by(entity_id=message.id).all()
    assert any(e.event_type == "deadline_analysis_already_done" for e in events)
