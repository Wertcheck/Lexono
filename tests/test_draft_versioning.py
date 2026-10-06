"""Tests für app/drafting/versioning.py (Prompt 23).

Isolierte Tests für die EINZIGE Stelle im Projekt, die neue Draft-Zeilen
anlegt - hier wird die Kernregel "nie überschreiben, immer neue Zeile"
einmal gründlich geprüft, statt sie in jedem aufrufenden Service erneut
zu verifizieren (die Aufrufer-Tests prüfen nur noch die Integration).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.drafting.versioning import (
    AI_SUGGESTION_DISCARDED_STATUS,
    create_new_draft_version,
    discard_ai_suggestion,
    resolve_visible_draft,
)
from app.models import AuditEvent, Client, Draft, Matter
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


def test_first_version_has_no_previous_version_and_version_one(
    db_session: Session,
) -> None:
    matter = _matter(db_session)

    draft = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="Erster Entwurf",
        actor="system",
        event_type="draft_created",
    )

    assert draft.version == 1
    assert draft.previous_version_id is None
    assert draft.content == "Erster Entwurf"
    assert draft.status == "draft"  # Standardwert


def test_subsequent_version_links_to_previous_and_increments(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v1",
        actor="system",
        event_type="draft_created",
    )

    v2 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v2",
        previous_draft=v1,
        actor="anwalt@kanzlei.test",
        event_type="draft_version_created",
    )

    assert v2.version == 2
    assert v2.previous_version_id == v1.id
    assert v2.id != v1.id


def test_creating_new_version_never_mutates_the_previous_row(
    db_session: Session,
) -> None:
    """Kernanforderung: die Vorgaenger-Zeile bleibt nach dem Anlegen einer
    neuen Version in JEDEM Feld unveraendert."""
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="Ursprünglicher Inhalt",
        status="legal_review",
        actor="system",
        event_type="draft_created",
    )
    v1_id, v1_content, v1_status, v1_version = v1.id, v1.content, v1.status, v1.version

    create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="Geänderter Inhalt",
        status="approved",
        previous_draft=v1,
        actor="anwalt@kanzlei.test",
        event_type="draft_version_created",
    )

    # v1 frisch aus der DB laden, um sicherzugehen, dass wirklich nichts
    # persistiert wurde, nicht nur das Python-Objekt unveraendert aussieht.
    db_session.expire_all()
    reloaded_v1 = db_session.get(Draft, v1_id)
    assert reloaded_v1.content == v1_content == "Ursprünglicher Inhalt"
    assert reloaded_v1.status == v1_status == "legal_review"
    assert reloaded_v1.version == v1_version == 1


def test_three_version_chain_is_fully_traceable(db_session: Session) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session, matter_id=matter.id, content="v1", actor="system", event_type="draft_created"
    )
    v2 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v2",
        previous_draft=v1,
        actor="a",
        event_type="draft_version_created",
    )
    v3 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v3",
        previous_draft=v2,
        actor="a",
        event_type="draft_version_created",
    )

    # Kette rueckwaerts nachvollziehbar.
    assert v3.previous_version_id == v2.id
    assert v2.previous_version_id == v1.id
    assert v1.previous_version_id is None
    assert [v1.version, v2.version, v3.version] == [1, 2, 3]
    assert db_session.query(Draft).count() == 3


def test_message_id_is_inherited_from_previous_draft_when_not_given(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v1",
        message_id="msg-123",
        actor="system",
        event_type="draft_created",
    )

    v2 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v2",
        previous_draft=v1,
        actor="a",
        event_type="draft_version_created",
    )

    assert v2.message_id == "msg-123"


def test_each_call_writes_exactly_one_audit_event_with_given_type(
    db_session: Session,
) -> None:
    matter = _matter(db_session)

    draft = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v1",
        actor="system",
        event_type="draft_created",
        details="Testdetail",
    )

    events = db_session.query(AuditEvent).filter_by(entity_id=draft.id).all()
    assert len(events) == 1
    assert events[0].event_type == "draft_created"
    assert events[0].details == "Testdetail"


# --- Dokumenten-Editor-Erweiterung (04.10.) ---------------------------------


def test_first_version_defaults_to_text_content_format_with_no_subject(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    draft = create_new_draft_version(
        db_session, matter_id=matter.id, content="v1", actor="system", event_type="draft_created"
    )
    assert draft.content_format == "text"
    assert draft.subject is None
    assert draft.recipient is None


def test_subject_recipient_and_content_format_are_inherited_when_not_given(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v1",
        subject="Fristverlängerung",
        recipient="Herr Müller",
        content_format="html",
        actor="system",
        event_type="draft_created",
    )

    v2 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v2",
        previous_draft=v1,
        actor="a",
        event_type="draft_version_created",
    )

    assert v2.subject == "Fristverlängerung"
    assert v2.recipient == "Herr Müller"
    assert v2.content_format == "html"


def test_subject_recipient_and_content_format_can_be_explicitly_overridden(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v1",
        subject="Alt",
        content_format="html",
        actor="system",
        event_type="draft_created",
    )

    v2 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v2",
        previous_draft=v1,
        subject="Neu",
        content_format="text",
        actor="a",
        event_type="draft_version_created",
    )

    assert v2.subject == "Neu"
    assert v2.content_format == "text"


def test_discard_ai_suggestion_marks_status_without_bumping_version(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session, matter_id=matter.id, content="v1", actor="system", event_type="draft_created"
    )
    v2 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v2 (KI-Vorschlag)",
        previous_draft=v1,
        actor="a",
        event_type="draft_version_created",
    )

    discarded = discard_ai_suggestion(db_session, draft=v2, actor="anwalt@kanzlei.test")

    assert discarded.id == v2.id
    assert discarded.version == 2  # unveraendert, kein neuer Versionssprung
    assert discarded.status == AI_SUGGESTION_DISCARDED_STATUS
    assert discarded.content == "v2 (KI-Vorschlag)"  # Inhalt bleibt erhalten

    events = db_session.query(AuditEvent).filter_by(entity_id=v2.id).all()
    assert any(e.event_type == "draft_ai_suggestion_discarded" for e in events)


def test_resolve_visible_draft_skips_discarded_tip_and_returns_active_parent(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session, matter_id=matter.id, content="v1", actor="system", event_type="draft_created"
    )
    v2 = create_new_draft_version(
        db_session,
        matter_id=matter.id,
        content="v2",
        previous_draft=v1,
        actor="a",
        event_type="draft_version_created",
    )
    discard_ai_suggestion(db_session, draft=v2, actor="a")

    by_id = {v1.id: v1, v2.id: v2}
    visible = resolve_visible_draft(v2, by_id)

    assert visible.id == v1.id


def test_resolve_visible_draft_returns_self_when_entire_chain_discarded(
    db_session: Session,
) -> None:
    """Ehrlicher Rueckfallfall (siehe resolve_visible_draft-Docstring):
    ist sogar v1 verworfen (kein Vorgaenger mehr), wird der verworfene
    Draft selbst zurueckgegeben statt eine nicht existierende Version zu
    erfinden."""
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session, matter_id=matter.id, content="v1", actor="system", event_type="draft_created"
    )
    discard_ai_suggestion(db_session, draft=v1, actor="a")

    visible = resolve_visible_draft(v1, {v1.id: v1})

    assert visible.id == v1.id


def test_resolve_visible_draft_returns_input_unchanged_when_not_discarded(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    v1 = create_new_draft_version(
        db_session, matter_id=matter.id, content="v1", actor="system", event_type="draft_created"
    )
    assert resolve_visible_draft(v1, {v1.id: v1}).id == v1.id
