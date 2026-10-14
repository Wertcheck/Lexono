"""Tests fuer app/matching/service.py (Prompt 09)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.matching.matcher import MatterMatchingService
from app.matching.service import MatterAssignmentService
from app.models import AuditEvent, Client, Document, Matter, Message
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


def _service(threshold: float = 0.6) -> MatterAssignmentService:
    matcher = MatterMatchingService(auto_assign_threshold=0.85, review_threshold=0.4)
    return MatterAssignmentService(
        matcher, classification_low_confidence_threshold=threshold
    )


def test_auto_assignment_sets_matter_id_and_cascades_to_documents(
    db_session: Session,
) -> None:
    # P1.2 (§39): automatisch nur mit Aktenzeichen UND bekanntem Absender derselben Akte
    client = Client(name="Testmandant", contact_email="mandant@example.test")
    matter = Matter(client=client, title="Testakte", reference_number="A-3003")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(direction="inbound", sender="mandant@example.test", body_text="Az.: A-3003")
    document = Document(
        file_path="/tmp/anhang.pdf",
        classification_confidence=0.9,  # ausreichend sicher klassifiziert
    )
    message.documents.append(document)
    db_session.add(message)
    db_session.commit()

    result = _service().assign_matter(message, db_session)

    assert result.decision == "auto_assigned"
    assert message.matter_id == matter.id
    assert document.matter_id == matter.id


def test_needs_review_does_not_touch_matter_id(db_session: Session) -> None:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Unklare Akte ohne starke Signale")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(direction="inbound", sender="unbekannt@example.test")
    db_session.add(message)
    db_session.commit()

    result = _service().assign_matter(message, db_session)

    assert result.decision in {"needs_review", "no_match"}
    assert message.matter_id is None


def test_missing_classification_blocks_auto_assignment(db_session: Session) -> None:
    """Dokument ganz ohne Klassifikation gilt als NICHT ausreichend sicher
    (sicherer Default) - selbst bei starkem Matching-Signal."""
    client = Client(name="Testmandant")
    matter = Matter(client=client, title="Testakte", reference_number="A-4004")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(direction="inbound", body_text="Az.: A-4004")
    document = Document(file_path="/tmp/anhang.pdf")  # classification_confidence=None
    message.documents.append(document)
    db_session.add(message)
    db_session.commit()

    result = _service().assign_matter(message, db_session)

    assert result.decision == "needs_review"
    assert message.matter_id is None
    assert document.matter_id is None


def test_message_without_attachments_can_still_auto_assign(
    db_session: Session,
) -> None:
    """Keine Anhänge -> keine Klassifikation nötig -> classification_ok
    bleibt True."""
    client = Client(name="Testmandant", contact_email="mandant@example.test")
    matter = Matter(client=client, title="Testakte", reference_number="A-5005")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(direction="inbound", sender="mandant@example.test", body_text="Az.: A-5005")
    db_session.add(message)
    db_session.commit()

    result = _service().assign_matter(message, db_session)

    assert result.decision == "auto_assigned"
    assert message.matter_id == matter.id


def test_assignment_creates_audit_event(db_session: Session) -> None:
    client = Client(name="Testmandant", contact_email="mandant@example.test")
    matter = Matter(client=client, title="Testakte", reference_number="A-6006")
    db_session.add_all([client, matter])
    db_session.commit()

    message = Message(direction="inbound", sender="mandant@example.test", body_text="Az.: A-6006")
    db_session.add(message)
    db_session.commit()

    _service().assign_matter(message, db_session)

    events = db_session.query(AuditEvent).filter_by(entity_id=message.id).all()
    assert len(events) == 1
    assert events[0].entity_type == "Message"
    assert events[0].event_type == "matter_match_auto_assigned"


def _message_with_attachment(db_session: Session, *, sender: str, body: str) -> tuple[Message, Document]:
    message = Message(direction="inbound", sender=sender, body_text=body)
    document = Document(file_path="/tmp/anhang.pdf", classification_confidence=0.9)
    message.documents.append(document)
    db_session.add(message)
    db_session.commit()
    return message, document


def test_reference_alone_never_moves_attachments(db_session: Session) -> None:
    """P1.2 (§39, Owner-Entscheidung): ein Aktenzeichen allein (unbekannter Absender) legt Nachricht und Anhang NICHT
    mehr automatisch in die Akte - Vorschlag zur Bestaetigung, Anhang bleibt unzugeordnet."""
    client = Client(name="Testmandant", contact_email="mandant@example.test")
    matter = Matter(client=client, title="Testakte", reference_number="A-7007")
    db_session.add_all([client, matter])
    db_session.commit()
    message, document = _message_with_attachment(db_session, sender="extern@example.test", body="Ihr Zeichen: A-7007")

    result = _service().assign_matter(message, db_session)

    assert result.decision == "needs_review"
    assert result.candidates[0].matter_id == matter.id
    assert message.matter_id is None and document.matter_id is None


def test_two_references_of_different_clients_never_move_attachments(db_session: Session) -> None:
    """P1.2 (§39): vorher wurde nur das ERSTE Aktenzeichen gelesen - eine Nachricht, die die Akten zweier Mandanten
    nennt, landete samt Anhang automatisch in der zuerst genannten. Jetzt: mehrdeutig, Pruefung, nichts verschoben -
    auch wenn der Absender als Kontakt einer der beiden Akten bekannt ist."""
    client_a = Client(name="Mandantin A", contact_email="a@example.test")
    client_b = Client(name="Mandant B", contact_email="b@example.test")
    matter_a = Matter(client=client_a, title="Akte A", reference_number="A-8008")
    matter_b = Matter(client=client_b, title="Akte B", reference_number="B-9009")
    db_session.add_all([client_a, client_b, matter_a, matter_b])
    db_session.commit()
    message, document = _message_with_attachment(
        db_session, sender="extern@example.test", body="Az.: A-8008, siehe auch Ihr Zeichen: B-9009"
    )

    result = _service().assign_matter(message, db_session)

    assert result.decision == "needs_review" and "Ambiguität" in result.reasoning
    assert {c.matter_id for c in result.candidates[:2]} == {matter_a.id, matter_b.id}
    assert message.matter_id is None and document.matter_id is None
