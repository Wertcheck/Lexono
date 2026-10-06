"""Tests für app/drafting/editor_service.py (04.10., Dokumenten-Editor).

Isolierte Tests der Backend-Logik (Autosave/KI-Vorschlag/Verwerfen/Als
Vorlage speichern) OHNE echten Claude-Aufruf - `apply_ai_suggestion`
nutzt einen Fake-`AttorneyInstructionService`, der `create_instruction`/
`apply_instruction` deterministisch simuliert (identisches Prinzip wie
tests/test_web_drafts.py: ein echter Claude-Aufruf ist für diese Tests
weder nötig noch gewünscht)."""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.attorney_instructions.schema import ApplyInstructionResult, AttorneyInstructionInput
from app.drafting.editor_service import AI_SUGGESTIONS, EditorService, _build_instruction_text
from app.drafting.schema import DraftingResult
from app.drafting.versioning import AI_SUGGESTION_DISCARDED_STATUS, create_new_draft_version
from app.models import AttorneyInstruction, Client, Draft, DocumentTemplate, Matter
from app.models.base import Base


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
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


def _draft(db: Session, matter: Matter, **overrides) -> Draft:
    kwargs = {
        "matter_id": matter.id,
        "content": "Ursprünglicher Entwurfstext",
        "actor": "system",
        "event_type": "draft_created",
    }
    kwargs.update(overrides)
    return create_new_draft_version(db, **kwargs)


class _FakeAttorneyInstructionService:
    """Simuliert AttorneyInstructionService ohne echten Claude-Aufruf -
    `next_result` steuert, was `apply_instruction` zurückgibt."""

    def __init__(self, next_result: ApplyInstructionResult | None = None) -> None:
        self.next_result = next_result
        self.created_instructions: list[str] = []

    def create_instruction(self, draft, data: AttorneyInstructionInput, db, *, actor):
        self.created_instructions.append(data.instruction_text)
        instruction = AttorneyInstruction(
            matter_id=draft.matter_id,
            draft_id=draft.id,
            instruction_text=data.instruction_text,
            status="open",
            actor=actor,
        )
        db.add(instruction)
        db.commit()
        db.refresh(instruction)
        return instruction

    def apply_instruction(self, instruction, db, *, purpose, actor):
        return self.next_result


def test_autosave_updates_content_subject_recipient_without_new_version(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter)
    service = EditorService(_FakeAttorneyInstructionService())

    updated = service.autosave_draft(
        db_session,
        draft=draft,
        content="<p>Neuer Text</p>",
        subject="Betreff X",
        recipient="Empfänger Y",
        content_format="html",
        actor="anwalt@kanzlei.test",
    )

    assert updated.id == draft.id
    assert updated.version == 1  # kein neuer Versionssprung
    assert updated.content == "<p>Neuer Text</p>"
    assert updated.subject == "Betreff X"
    assert updated.recipient == "Empfänger Y"
    assert updated.content_format == "html"
    assert updated.last_autosaved_at is not None
    assert db_session.query(Draft).count() == 1  # keine zusaetzliche Zeile


def test_autosave_sanitizes_html_content(db_session: Session) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter)
    service = EditorService(_FakeAttorneyInstructionService())

    updated = service.autosave_draft(
        db_session,
        draft=draft,
        content="<p>Text</p><script>alert(1)</script>",
        subject=None,
        recipient=None,
        content_format="html",
        actor="a",
    )

    assert "<script" not in updated.content
    assert "<p>Text</p>" in updated.content


def test_autosave_does_not_sanitize_plain_text_format(db_session: Session) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter)
    service = EditorService(_FakeAttorneyInstructionService())

    raw = "Zeile 1\nZeile 2 <nicht als HTML gemeint>"
    updated = service.autosave_draft(
        db_session, draft=draft, content=raw, subject=None, recipient=None,
        content_format="text", actor="a",
    )

    assert updated.content == raw


def test_autosave_refuses_to_modify_non_draft_status(db_session: Session) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter, status="approved")
    service = EditorService(_FakeAttorneyInstructionService())

    result = service.autosave_draft(
        db_session, draft=draft, content="Neuer Inhalt", subject=None, recipient=None,
        content_format="html", actor="a",
    )

    assert result.content == "Ursprünglicher Entwurfstext"  # unveraendert
    assert result.last_autosaved_at is None


def test_apply_ai_suggestion_creates_new_version_via_attorney_instruction_service(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter)

    new_draft = create_new_draft_version(
        db_session, matter_id=matter.id, content="KI-Vorschlag", previous_draft=draft,
        actor="system", event_type="draft_version_created",
    )
    fake_instruction = AttorneyInstruction(
        matter_id=matter.id, draft_id=draft.id, instruction_text="x", status="applied", actor="a",
    )
    fake_result = ApplyInstructionResult(
        instruction=fake_instruction,
        drafting_result=DraftingResult(success=True, draft_id=new_draft.id, draft_text="KI-Vorschlag"),
        new_draft=new_draft,
    )
    service = EditorService(_FakeAttorneyInstructionService(next_result=fake_result))

    result = service.apply_ai_suggestion(
        db_session, draft=draft, instruction_text="Kürze den Text.", purpose="optimize_style",
        actor="anwalt@kanzlei.test",
    )

    assert result.new_draft is not None
    assert result.new_draft.id == new_draft.id
    assert result.drafting_result.success is True


def test_apply_ai_suggestion_with_selection_builds_scoped_instruction(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter)
    fake_service = _FakeAttorneyInstructionService(
        next_result=ApplyInstructionResult(
            instruction=AttorneyInstruction(
                matter_id=matter.id, draft_id=draft.id, instruction_text="x", status="applied", actor="a"
            ),
            drafting_result=DraftingResult(success=False, blocked_reasons=["irrelevant für diesen Test"]),
            new_draft=None,
        )
    )
    service = EditorService(fake_service)

    service.apply_ai_suggestion(
        db_session, draft=draft, instruction_text="Formuliere präziser.", purpose="improve_clarity",
        actor="a", selected_text="Dieser markierte Satz.",
    )

    assert len(fake_service.created_instructions) == 1
    sent = fake_service.created_instructions[0]
    assert "Formuliere präziser." in sent
    assert "Dieser markierte Satz." in sent


def test_build_instruction_text_without_selection_is_unmodified() -> None:
    scoped = _build_instruction_text("Kürze den Text.", None)
    assert scoped.instruction_text == "Kürze den Text."
    assert scoped.is_selection_scoped is False


def test_discard_ai_suggestion_delegates_to_versioning(db_session: Session) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter)
    suggestion = create_new_draft_version(
        db_session, matter_id=matter.id, content="Vorschlag", previous_draft=draft,
        actor="system", event_type="draft_version_created",
    )
    service = EditorService(_FakeAttorneyInstructionService())

    discarded = service.discard_ai_suggestion(db_session, draft=suggestion, actor="a")

    assert discarded.status == AI_SUGGESTION_DISCARDED_STATUS
    assert discarded.version == 2


def test_save_as_template_creates_real_document_template(db_session: Session) -> None:
    matter = _matter(db_session)
    draft = _draft(db_session, matter, content="Sehr geehrte Damen und Herren, ...")
    service = EditorService(_FakeAttorneyInstructionService())

    template = service.save_as_template(
        db_session, draft=draft, name="Mahnung Standard", category="Mahnung", actor="a",
    )

    assert template.id is not None
    assert template.name == "Mahnung Standard"
    assert template.content == "Sehr geehrte Damen und Herren, ..."
    assert db_session.query(DocumentTemplate).count() == 1


def test_ai_suggestions_cover_the_four_reference_actions() -> None:
    ids = {s["id"] for s in AI_SUGGESTIONS}
    assert ids == {"precise_wording", "shorten", "legal_review", "alternative_wording"}
    for suggestion in AI_SUGGESTIONS:
        assert suggestion["instruction_text"].strip()
        assert suggestion["purpose"]
