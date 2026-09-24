"""Tests fuer app/audit/service.py (Prompt 19).

Schwerpunkt: Aktenisolation - Abfrage fuer Akte A darf niemals Ereignisse
aus Akte B enthalten."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.audit import AuditLogService
from app.models import (
    AuditEvent,
    ChatConversation,
    Client,
    Deadline,
    Document,
    Draft,
    GeneratedDocument,
    Matter,
    OutboxEntry,
    Party,
    User,
)
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


def _matter(db: Session, title: str = "Testakte") -> Matter:
    client = Client(name=f"Mandant für {title}")
    matter = Matter(client=client, title=title)
    db.add_all([client, matter])
    db.commit()
    return matter


def test_list_events_for_entity_returns_matching_events(db_session: Session) -> None:
    service = AuditLogService()
    db_session.add(
        AuditEvent(entity_type="Document", entity_id="doc-1", event_type="x", actor="system")
    )
    db_session.add(
        AuditEvent(entity_type="Document", entity_id="doc-2", event_type="y", actor="system")
    )
    db_session.commit()

    events = service.list_events_for_entity("Document", "doc-1", db_session)

    assert len(events) == 1
    assert events[0].event_type == "x"


def test_list_events_for_matter_requires_matter_id() -> None:
    service = AuditLogService()
    with pytest.raises(ValueError):
        service.list_events_for_matter("", db=None)  # type: ignore[arg-type]


def test_list_events_for_matter_includes_matter_level_events(db_session: Session) -> None:
    matter = _matter(db_session)
    db_session.add(
        AuditEvent(
            entity_type="Matter", entity_id=matter.id, event_type="legal_research_performed", actor="system"
        )
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert len(events) == 1
    assert events[0].event_type == "legal_research_performed"


def test_list_events_for_matter_includes_document_events(db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(matter=matter, file_path="/tmp/x.pdf")
    db_session.add(document)
    db_session.commit()
    db_session.add(
        AuditEvent(
            entity_type="Document", entity_id=document.id, event_type="document_classified", actor="system"
        )
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert any(e.event_type == "document_classified" for e in events)


def test_list_events_for_matter_includes_party_events(db_session: Session) -> None:
    """ECHTER FUND (18.09.): `Party` (17.09. dieser Sitzung angelegt)
    schrieb bereits echte AuditEvents, fehlte aber in
    `_MATTER_SCOPED_MODELS` - Beteiligte-Aenderungen waren dadurch bei
    einer aktenweiten Verlaufsabfrage unsichtbar."""
    matter = _matter(db_session)
    party = Party(matter=matter, name="Vermieter Schmidt GmbH", role="Gegner")
    db_session.add(party)
    db_session.commit()
    db_session.add(
        AuditEvent(
            entity_type="Party", entity_id=party.id, event_type="party_added", actor="system"
        )
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert any(e.event_type == "party_added" for e in events)


def test_list_events_for_matter_includes_outbox_events(db_session: Session) -> None:
    """ECHTER FUND (20.09., Overnight-Autonomielauf, beim Live-
    Verifizieren des Postausgang-Workflows gefunden): `OutboxEntry`
    schreibt bereits echte AuditEvents (draft_added_to_outbox/
    draft_marked_sent, siehe app/outbox/service.py) und traegt bereits
    eine direkte `matter_id`-Spalte (extra dafuer angelegt) - fehlte
    aber, EXAKT dieselbe Art Luecke wie zuvor bei Party/Note, in
    `_MATTER_SCOPED_MODELS`. Freigabe/Versand-Bestaetigung eines
    Entwurfs war dadurch in der Akte-Verlaufsansicht unsichtbar."""
    matter = _matter(db_session)
    draft = Draft(matter_id=matter.id, content="Testinhalt.")
    db_session.add(draft)
    db_session.commit()
    entry = OutboxEntry(matter_id=matter.id, draft_id=draft.id, status="sent")
    db_session.add(entry)
    db_session.commit()
    db_session.add(
        AuditEvent(
            entity_type="OutboxEntry",
            entity_id=entry.id,
            event_type="draft_marked_sent",
            actor="anwalt@kanzlei.test",
        )
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert any(e.event_type == "draft_marked_sent" for e in events)


def test_list_events_for_matter_includes_chat_conversation_events(
    db_session: Session,
) -> None:
    """ECHTER FUND (20.09., systematische Suche nach ALLEN Modellen mit
    `matter_id`, ausgeloest durch den OutboxEntry-Fund oben):
    `ChatConversation` schreibt bereits echte AuditEvents (u. a.
    "chat_relinked_to_matter", wenn ein Anwalt eine Unterhaltung
    nachtraeglich einer anderen Akte zuordnet - app/web/chat_router.py)
    und traegt bereits `matter_id` - fehlte aber ebenfalls in
    `_MATTER_SCOPED_MODELS`."""
    matter = _matter(db_session)
    user = User(email="anwalt@kanzlei.test")
    db_session.add(user)
    db_session.commit()
    conversation = ChatConversation(matter_id=matter.id, user_id=user.id, title="Testchat")
    db_session.add(conversation)
    db_session.commit()
    db_session.add(
        AuditEvent(
            entity_type="ChatConversation",
            entity_id=conversation.id,
            event_type="chat_relinked_to_matter",
            actor="anwalt@kanzlei.test",
        )
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert any(e.event_type == "chat_relinked_to_matter" for e in events)


def test_list_events_for_matter_includes_generated_document_events(
    db_session: Session,
) -> None:
    """ECHTER FUND (20.09.), dieselbe Suche wie oben: `GeneratedDocument`
    schreibt bereits echte AuditEvents ("document_generated"/
    "document_edited", app/document_generator/service.py) und traegt
    bereits `matter_id` (dort sogar explizit als "Pflicht" dokumentiert) -
    fehlte aber ebenfalls in `_MATTER_SCOPED_MODELS`."""
    matter = _matter(db_session)
    document = GeneratedDocument(matter_id=matter.id, title="Testschreiben", content="Inhalt.")
    db_session.add(document)
    db_session.commit()
    db_session.add(
        AuditEvent(
            entity_type="GeneratedDocument",
            entity_id=document.id,
            event_type="document_generated",
            actor="anwalt@kanzlei.test",
        )
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert any(e.event_type == "document_generated" for e in events)


def test_list_events_for_matter_includes_deadline_and_draft_events(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    deadline = Deadline(matter=matter, source_text="Frist")
    draft = Draft(matter=matter, content="Text")
    db_session.add_all([deadline, draft])
    db_session.commit()
    db_session.add(
        AuditEvent(entity_type="Deadline", entity_id=deadline.id, event_type="deadline_created", actor="system")
    )
    db_session.add(
        AuditEvent(entity_type="Draft", entity_id=draft.id, event_type="draft_created", actor="system")
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)
    event_types = {e.event_type for e in events}

    assert "deadline_created" in event_types
    assert "draft_created" in event_types


def test_list_events_for_matter_never_includes_other_matter(db_session: Session) -> None:
    """Kernanforderung: strikte Aktenisolation."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B")
    document_a = Document(matter=matter_a, file_path="/tmp/a.pdf")
    document_b = Document(matter=matter_b, file_path="/tmp/b.pdf")
    db_session.add_all([document_a, document_b])
    db_session.commit()
    db_session.add(
        AuditEvent(entity_type="Document", entity_id=document_a.id, event_type="a_event", actor="system")
    )
    db_session.add(
        AuditEvent(entity_type="Document", entity_id=document_b.id, event_type="b_event", actor="system")
    )
    db_session.add(
        AuditEvent(entity_type="Matter", entity_id=matter_b.id, event_type="b_matter_event", actor="system")
    )
    db_session.commit()
    service = AuditLogService()

    events_a = service.list_events_for_matter(matter_a.id, db_session)
    event_types_a = {e.event_type for e in events_a}

    assert "a_event" in event_types_a
    assert "b_event" not in event_types_a
    assert "b_matter_event" not in event_types_a


def test_list_events_for_matter_with_no_events_returns_empty_list(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert events == []


def test_list_events_for_matter_orders_chronologically(db_session: Session) -> None:
    matter = _matter(db_session)
    first = AuditEvent(entity_type="Matter", entity_id=matter.id, event_type="first", actor="system")
    db_session.add(first)
    db_session.commit()
    second = AuditEvent(entity_type="Matter", entity_id=matter.id, event_type="second", actor="system")
    db_session.add(second)
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert [e.event_type for e in events] == ["first", "second"]


def test_excludes_firm_wide_knowledge_and_source_events(db_session: Session) -> None:
    """KnowledgeItem/Source/Policy sind kanzleiweit, nicht aktenbezogen -
    ihre Events duerfen NICHT in einer Akten-Abfrage auftauchen."""
    matter = _matter(db_session)
    db_session.add(
        AuditEvent(
            entity_type="KnowledgeItem", entity_id="ki-1", event_type="knowledge_item_approved", actor="system"
        )
    )
    db_session.commit()
    service = AuditLogService()

    events = service.list_events_for_matter(matter.id, db_session)

    assert not any(e.entity_type == "KnowledgeItem" for e in events)
