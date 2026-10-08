"""Tests für app/attorney_instructions/service.py (Prompt 23).

Nutzt dasselbe Fake-Setup wie tests/test_drafting_service.py (kein echter
Modell-Download, kein echter Claude-API-Aufruf). Ein FakeClaudeWritingProvider
zeichnet zusätzlich die tatsächlich empfangene Payload auf - damit lässt
sich beweisen, dass eine Anmerkung NUR pseudonymisiert bei "Claude" ankommt
(hier simuliert), nie im Klartext.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.attorney_instructions.schema import AttorneyInstructionInput
from app.attorney_instructions.service import AttorneyInstructionService
from app.drafting.service import DraftingService
from app.models import AttorneyInstruction, AuditEvent, Client, Draft, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.privacy.security_check import SecurityCheckService
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider


class RecordingClaudeWritingProvider:
    """Wie FakeClaudeWritingProvider in test_drafting_service.py, zeichnet
    zusätzlich jede empfangene Payload auf - für den Beweis, dass nur
    bereits pseudonymisierter Text 'Claude' erreicht."""

    def __init__(self, response_text: str = "Neu formulierte Antwort.") -> None:
        self.response_text = response_text
        self.received_payloads: list[ClaudeRequestPayload] = []

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        self.received_payloads.append(payload)
        return ClaudeWritingResult(text=self.response_text, token_count=42)


class AlwaysBlockSecurityCheck(SecurityCheckService):
    """Simuliert eine Blockierung durch den Privacy Gateway, unabhängig
    vom Inhalt - für den Test, dass eine fehlgeschlagene Neugenerierung
    die AttorneyInstruction NICHT auf 'applied' setzt."""

    def check(self, pseudonymized_text, mappings, *, purpose, unrecognized_name_scan_text=None, skip_residual_categories=frozenset(), residual_ignore_ranges=None):  # noqa: ANN001
        from app.privacy.security_check_schema import SecurityCheckResult

        return SecurityCheckResult(passed=False, reasons=["Testblockierung"])


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


def _matter(db: Session, client_name: str = "Max Mustermann", title: str = "Testakte") -> Matter:
    client = Client(name=client_name)
    matter = Matter(client=client, title=title)
    db.add_all([client, matter])
    db.commit()
    return matter


def _services(
    writing_provider=None, gateway: ClaudePrivacyGateway | None = None
) -> tuple[AttorneyInstructionService, DraftingService, RecordingClaudeWritingProvider]:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    provider = writing_provider or RecordingClaudeWritingProvider()
    drafting_service = DraftingService(
        RuleBasedLocalAIProvider(),
        research_service,
        search_service,
        gateway or ClaudePrivacyGateway(),
        provider,
        model_name="claude-sonnet-5",
    )
    return AttorneyInstructionService(drafting_service), drafting_service, provider


def _initial_draft(db: Session, matter: Matter, drafting_service: DraftingService) -> Draft:
    result = drafting_service.create_draft(matter.id, "formulate_draft", db)
    assert result.success is True
    return db.get(Draft, result.draft_id)


# --- create_instruction: nur speichern, keine Neugenerierung ---


def test_create_instruction_saves_with_open_status(db_session: Session) -> None:
    matter = _matter(db_session)
    service, drafting_service, provider = _services()
    draft = _initial_draft(db_session, matter, drafting_service)

    instruction = service.create_instruction(
        draft,
        AttorneyInstructionInput(instruction_text="Auf Punkt 3 ausdrücklich eingehen."),
        db_session,
        actor="anwalt@kanzlei.test",
    )

    assert instruction.status == "open"
    assert instruction.draft_id == draft.id
    assert instruction.matter_id == matter.id
    assert instruction.resulting_draft_id is None
    # KEINE zusätzliche Neugenerierung ausgelöst - nur der EINE Aufruf aus
    # der initialen Entwurfserstellung (_initial_draft) ist gezählt.
    assert len(provider.received_payloads) == 1


def test_create_instruction_writes_audit_event(db_session: Session) -> None:
    matter = _matter(db_session)
    service, drafting_service, _ = _services()
    draft = _initial_draft(db_session, matter, drafting_service)

    instruction = service.create_instruction(
        draft,
        AttorneyInstructionInput(instruction_text="Ton bestimmter formulieren."),
        db_session,
        actor="anwalt@kanzlei.test",
    )

    events = db_session.query(AuditEvent).filter_by(entity_id=instruction.id).all()
    assert len(events) == 1
    assert events[0].event_type == "attorney_instruction_created"


# --- apply_instruction: löst Neugenerierung aus, erzeugt neue Version ---


def test_apply_instruction_creates_new_draft_version_linked_to_previous(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    service, drafting_service, _ = _services()
    draft_v1 = _initial_draft(db_session, matter, drafting_service)
    instruction = service.create_instruction(
        draft_v1,
        AttorneyInstructionInput(instruction_text="Schadensersatzhöhe nicht anerkennen."),
        db_session,
        actor="anwalt@kanzlei.test",
    )

    result = service.apply_instruction(
        instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )

    assert result.drafting_result.success is True
    assert result.new_draft is not None
    assert result.new_draft.id != draft_v1.id
    assert result.new_draft.previous_version_id == draft_v1.id
    assert result.new_draft.version == draft_v1.version + 1
    # v1 bleibt unveraendert.
    db_session.expire_all()
    reloaded_v1 = db_session.get(Draft, draft_v1.id)
    assert reloaded_v1.version == 1


def test_apply_instruction_marks_instruction_as_applied_with_resulting_draft(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    service, drafting_service, _ = _services()
    draft_v1 = _initial_draft(db_session, matter, drafting_service)
    instruction = service.create_instruction(
        draft_v1,
        AttorneyInstructionInput(instruction_text="§ 286 BGB berücksichtigen."),
        db_session,
        actor="anwalt@kanzlei.test",
    )

    result = service.apply_instruction(
        instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )

    assert instruction.status == "applied"
    assert instruction.resulting_draft_id == result.new_draft.id
    # Die AttorneyInstruction verweist weiterhin korrekt auf die
    # UrsprÃ¼ngliche Version, auf die sie sich bezog.
    assert instruction.draft_id == draft_v1.id


def test_apply_instruction_writes_applied_audit_event(db_session: Session) -> None:
    matter = _matter(db_session)
    service, drafting_service, _ = _services()
    draft_v1 = _initial_draft(db_session, matter, drafting_service)
    instruction = service.create_instruction(
        draft_v1,
        AttorneyInstructionInput(instruction_text="Diesen Absatz streichen."),
        db_session,
        actor="anwalt@kanzlei.test",
    )

    service.apply_instruction(
        instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )

    events = db_session.query(AuditEvent).filter_by(
        entity_id=instruction.id, event_type="attorney_instruction_applied"
    ).all()
    assert len(events) == 1


def test_apply_instruction_rejects_already_applied_instruction(db_session: Session) -> None:
    matter = _matter(db_session)
    service, drafting_service, _ = _services()
    draft_v1 = _initial_draft(db_session, matter, drafting_service)
    instruction = service.create_instruction(
        draft_v1,
        AttorneyInstructionInput(instruction_text="Argumentation aus Schreiben vom 12.05."),
        db_session,
        actor="anwalt@kanzlei.test",
    )
    service.apply_instruction(
        instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )

    with pytest.raises(ValueError):
        service.apply_instruction(
            instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
        )


# --- Versionskette über mehrere Anmerkungsrunden ---


def test_two_instruction_rounds_build_traceable_version_chain(db_session: Session) -> None:
    matter = _matter(db_session)
    service, drafting_service, _ = _services()
    draft_v1 = _initial_draft(db_session, matter, drafting_service)

    first_instruction = service.create_instruction(
        draft_v1,
        AttorneyInstructionInput(instruction_text="Auf Punkt 3 eingehen."),
        db_session,
        actor="anwalt@kanzlei.test",
    )
    first_result = service.apply_instruction(
        first_instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )
    draft_v2 = first_result.new_draft

    second_instruction = service.create_instruction(
        draft_v2,
        AttorneyInstructionInput(instruction_text="Ton bestimmter formulieren."),
        db_session,
        actor="anwalt@kanzlei.test",
    )
    second_result = service.apply_instruction(
        second_instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )
    draft_v3 = second_result.new_draft

    assert draft_v2.previous_version_id == draft_v1.id
    assert draft_v3.previous_version_id == draft_v2.id
    assert [draft_v1.version, draft_v2.version, draft_v3.version] == [1, 2, 3]
    assert first_instruction.resulting_draft_id == draft_v2.id
    assert second_instruction.resulting_draft_id == draft_v3.id
    assert db_session.query(Draft).count() == 3
    assert db_session.query(AttorneyInstruction).count() == 2


# --- drafting_service=None: "Anmerkung speichern" braucht keinen DraftingService ---


def test_create_instruction_works_without_drafting_service(db_session: Session) -> None:
    """Regressionstest für einen echten Bug: die Web-Schicht baute für
    'Anmerkung speichern' faelschlich einen vollen DraftingService auf
    (inkl. Pruefung auf konfigurierten Claude-API-Key) - obwohl
    create_instruction diesen nie braucht. Muss auch mit
    drafting_service=None funktionieren."""
    matter = _matter(db_session)
    _, drafting_service, _ = _services()
    draft = _initial_draft(db_session, matter, drafting_service)

    service_without_drafting = AttorneyInstructionService(drafting_service=None)
    instruction = service_without_drafting.create_instruction(
        draft,
        AttorneyInstructionInput(instruction_text="Ton bestimmter formulieren."),
        db_session,
        actor="anwalt@kanzlei.test",
    )

    assert instruction.status == "open"


def test_apply_instruction_without_drafting_service_raises_clear_error(
    db_session: Session,
) -> None:
    matter = _matter(db_session)
    manual_draft = Draft(matter_id=matter.id, content="Testentwurf")
    db_session.add(manual_draft)
    db_session.commit()
    instruction = AttorneyInstruction(
        matter_id=matter.id,
        draft_id=manual_draft.id,
        instruction_text="Testanmerkung.",
        status="open",
        actor="anwalt@kanzlei.test",
    )
    db_session.add(instruction)
    db_session.commit()

    service_without_drafting = AttorneyInstructionService(drafting_service=None)
    with pytest.raises(ValueError):
        service_without_drafting.apply_instruction(
            instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
        )


def test_apply_instruction_sends_only_pseudonymized_text_to_writing_provider(
    db_session: Session,
) -> None:
    """Beweist: der Klartext der Anmerkung (mit dem bekannten Mandantennamen)
    erreicht den 'Claude'-Provider NICHT - nur die pseudonymisierte
    Fassung mit Platzhaltern."""
    matter = _matter(db_session, client_name="Max Mustermann")
    service, drafting_service, provider = _services()
    draft_v1 = _initial_draft(db_session, matter, drafting_service)

    instruction = service.create_instruction(
        draft_v1,
        AttorneyInstructionInput(
            instruction_text="Die Forderung von Max Mustermann ausdrücklich zurückweisen."
        ),
        db_session,
        actor="anwalt@kanzlei.test",
    )

    result = service.apply_instruction(
        instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )

    assert result.drafting_result.success is True
    # Zwei Aufrufe insgesamt: der erste aus _initial_draft (v1), der
    # zweite aus apply_instruction (v2) - nur der ZWEITE ist relevant.
    assert len(provider.received_payloads) == 2
    assert provider.received_payloads[0].anonymisierte_anwaltliche_anmerkungen is None
    received = provider.received_payloads[-1]
    assert received.anonymisierte_anwaltliche_anmerkungen is not None
    assert "Max Mustermann" not in received.anonymisierte_anwaltliche_anmerkungen
    assert "[MANDANT_01]" in received.anonymisierte_anwaltliche_anmerkungen


def test_apply_instruction_blocked_by_gateway_keeps_instruction_open(
    db_session: Session,
) -> None:
    """Wenn der Privacy Gateway blockiert, darf die Anmerkung NICHT als
    'applied' markiert werden - es ist ja nichts angewendet worden.

    Der Testentwurf wird hier bewusst DIREKT in der DB angelegt (nicht
    über drafting_service.create_draft), weil der DraftingService mit
    diesem absichtlich immer blockierenden Gateway konfiguriert ist - ein
    Aufruf darüber würde bereits bei der Entwurfserstellung selbst
    scheitern, nicht erst bei apply_instruction."""
    blocking_gateway = ClaudePrivacyGateway(security_check=AlwaysBlockSecurityCheck())
    service, drafting_service, provider = _services(gateway=blocking_gateway)

    matter = _matter(db_session, client_name="Erika Musterfrau", title="Testakte")
    manual_draft = Draft(matter_id=matter.id, content="Manuell angelegter Testentwurf")
    db_session.add(manual_draft)
    db_session.commit()

    instruction = AttorneyInstruction(
        matter_id=matter.id,
        draft_id=manual_draft.id,
        instruction_text="Testanmerkung.",
        status="open",
        actor="anwalt@kanzlei.test",
    )
    db_session.add(instruction)
    db_session.commit()

    result = service.apply_instruction(
        instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )

    assert result.drafting_result.success is False
    assert result.new_draft is None
    assert len(provider.received_payloads) == 0  # Writing Provider nie erreicht
    db_session.expire_all()
    reloaded_instruction = db_session.get(AttorneyInstruction, instruction.id)
    assert reloaded_instruction.status == "open"
    assert reloaded_instruction.resulting_draft_id is None
    # KEINE neue Draft-Zeile fuer diese Akte entstanden.
    assert db_session.query(Draft).filter_by(matter_id=matter.id).count() == 1


def test_apply_instruction_blocked_writes_apply_failed_audit_event(
    db_session: Session,
) -> None:
    blocking_gateway = ClaudePrivacyGateway(security_check=AlwaysBlockSecurityCheck())
    service, drafting_service, _ = _services(gateway=blocking_gateway)

    matter = _matter(db_session)
    manual_draft = Draft(matter_id=matter.id, content="Testentwurf")
    db_session.add(manual_draft)
    db_session.commit()
    instruction = AttorneyInstruction(
        matter_id=matter.id,
        draft_id=manual_draft.id,
        instruction_text="Testanmerkung.",
        status="open",
        actor="anwalt@kanzlei.test",
    )
    db_session.add(instruction)
    db_session.commit()

    service.apply_instruction(
        instruction, db_session, purpose="formulate_draft", actor="anwalt@kanzlei.test"
    )

    events = db_session.query(AuditEvent).filter_by(
        entity_id=instruction.id, event_type="attorney_instruction_apply_failed"
    ).all()
    assert len(events) == 1
