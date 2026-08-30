"""Tests fuer app/drafting/service.py (Prompt 17).

Nutzt FakeEmbeddingProvider (kein echter Modell-Download) und einen Fake
ClaudeWritingProvider (kein echter API-Aufruf)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.drafting.service import DraftingService
from app.models import ApiCallLog, AuditEvent, Client, Deadline, Draft, DraftKnowledgeItemLink, DraftSourceLink, KnowledgeItem, Matter, Source
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider


class FakeClaudeWritingProvider:
    def __init__(self, response_text: str = "Formulierte Antwort.") -> None:
        self.response_text = response_text
        self.received_payloads: list[ClaudeRequestPayload] = []

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        self.received_payloads.append(payload)
        return ClaudeWritingResult(text=self.response_text, token_count=99)


class FakeLocalLLMProvider:
    """Test-Double fuer app/ai_providers/local_llm_provider.py::LocalLLMProvider
    (§65) - kein echter Ollama-Aufruf. `structured_result` steuert das
    Ergebnis von `generate_structured()` (genutzt von der Claude-
    Antwortpruefung, siehe app/drafting/response_validation.py) - Default
    "passed" haelt bestehende Tests, die nur den Vorabanalyse-Schritt
    pruefen, unveraendert gruen."""

    def __init__(
        self,
        response_text: str = "Lokale Zusammenfassung.",
        structured_result: dict | None = None,
    ) -> None:
        self.response_text = response_text
        self.structured_result = structured_result or {"passed": True, "issues": []}
        self.received_payloads: list[ClaudeRequestPayload] = []
        self.structured_calls: list[tuple[str, dict]] = []

    def process(self, payload: ClaudeRequestPayload):
        from app.ai_providers.local_llm_provider import LocalLLMResult

        self.received_payloads.append(payload)
        return LocalLLMResult(text=self.response_text, model="fake-model")

    def check_health(self):
        raise NotImplementedError("nicht benoetigt in diesen Tests")

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        self.structured_calls.append((prompt, schema))
        return self.structured_result


class FailingLocalLLMProvider:
    """Simuliert ein nicht erreichbares Ollama (§65 Punkt 10) - sowohl fuer
    den Vorabanalyse-Schritt (`process`) als auch fuer die Claude-
    Antwortpruefung (`generate_structured`)."""

    def process(self, payload: ClaudeRequestPayload):
        from app.ai_providers.local_llm_provider import LocalLLMUnavailableError

        raise LocalLLMUnavailableError("Ollama nicht erreichbar (simuliert)")

    def check_health(self):
        raise NotImplementedError("nicht benoetigt in diesen Tests")

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        from app.ai_providers.local_llm_provider import LocalLLMUnavailableError

        raise LocalLLMUnavailableError("Ollama nicht erreichbar (simuliert)")


class SucceedingProcessFailingStructuredLocalLLMProvider:
    """Simuliert: Vorabanalyse (process) funktioniert, aber die lokale
    Antwortpruefung (generate_structured) schlaegt fehl - z. B. Ollama faellt
    genau zwischen dem Claude-Aufruf und der Antwortpruefung aus. Claude
    wurde in diesem Fall bereits aufgerufen; die Rekonstruktion darf trotzdem
    NIE stattfinden (Fail-Closed)."""

    def __init__(self) -> None:
        self.received_payloads: list[ClaudeRequestPayload] = []

    def process(self, payload: ClaudeRequestPayload):
        from app.ai_providers.local_llm_provider import LocalLLMResult

        self.received_payloads.append(payload)
        return LocalLLMResult(text="Kurzfassung.", model="fake-model")

    def check_health(self):
        raise NotImplementedError("nicht benoetigt in diesen Tests")

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        from app.ai_providers.local_llm_provider import LocalLLMUnavailableError

        raise LocalLLMUnavailableError("Ollama Timeout bei Antwortpruefung (simuliert)")


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


def _matter(db: Session, client_name: str = "Max Mustermann", title: str = "Testakte", **kwargs) -> Matter:
    client = Client(name=client_name)
    matter = Matter(client=client, title=title, **kwargs)
    db.add_all([client, matter])
    db.commit()
    return matter


def _service(
    writing_provider=None, min_score: float = 0.0, local_llm_provider=None
) -> tuple[DraftingService, DocumentSearchService]:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=min_score)
    service = DraftingService(
        RuleBasedLocalAIProvider(),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        writing_provider or FakeClaudeWritingProvider(),
        model_name="claude-sonnet-5",
        local_llm_provider=local_llm_provider,
    )
    return service, search_service


def test_empty_matter_id_auto_creates_matter(db_session: Session) -> None:
    """Schriftsatz-Generator (20.08.): eine leere/fehlende matter_id wirft
    KEINEN Fehler mehr, sondern legt automatisch Mandant+Akte an, damit der
    Entwurf trotzdem gespeichert werden kann (Draft.matter_id ist NICHT
    nullable)."""
    assert db_session.query(Matter).count() == 0
    service, _ = _service()

    # Bewusst EIN Wort als Titel - zwei aufeinanderfolgende grossgeschriebene
    # Woerter wuerden vom SecurityCheckService als moegliche unerkannte
    # Namen/Entitaeten markiert (_find_possible_unrecognized_names, siehe
    # app/privacy/security_check.py) und den Aufruf blockieren - hier soll
    # ausschliesslich die Auto-Create-Verdrahtung getestet werden.
    result = service.create_draft(
        "", "formulate_draft", db_session, new_matter_title="Schnellentwurfsakte"
    )

    assert result.success is True
    matter = db_session.query(Matter).one()
    assert matter.title == "Schnellentwurfsakte"
    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert persisted.matter_id == matter.id


def test_none_matter_id_auto_creates_matter_with_default_title(db_session: Session) -> None:
    service, _ = _service()

    result = service.create_draft(None, "formulate_draft", db_session)

    assert result.success is True
    matter = db_session.query(Matter).one()
    assert "Schnellentwurf" in matter.title
    assert matter.client.name == "Ohne Mandantenzuordnung"


def test_auto_created_matter_logs_audit_event(db_session: Session) -> None:
    service, _ = _service()

    service.create_draft(None, "formulate_draft", db_session, actor="anwalt@kanzlei.test")

    matter = db_session.query(Matter).one()
    events = db_session.query(AuditEvent).filter_by(
        entity_id=matter.id, event_type="matter_auto_created"
    ).all()
    assert len(events) == 1
    assert events[0].actor == "anwalt@kanzlei.test"


def test_raises_for_unknown_matter(db_session: Session) -> None:
    service, _ = _service()
    with pytest.raises(ValueError):
        service.create_draft("nicht-vorhanden", "formulate_draft", db_session)


def test_successful_draft_is_persisted(db_session: Session) -> None:
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    assert result.draft_id is not None
    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert persisted is not None
    assert persisted.status == "draft"
    assert persisted.content == result.draft_text


def test_draft_creation_logs_audit_event(db_session: Session) -> None:
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    events = db_session.query(AuditEvent).filter_by(
        entity_id=result.draft_id, event_type="draft_created"
    ).all()
    assert len(events) == 1


def test_source_list_contains_matching_approved_source(db_session: Session) -> None:
    matter = _matter(db_session, title="Einspruch Steuerbescheid")
    service, search_service = _service(min_score=0.0)
    source = Source(
        title="Einspruch Steuerbescheid Regelung",
        source_type="Gesetz",
        reference="§ 355 AO",
        approval_level="freigegeben",
    )
    db_session.add(source)
    db_session.commit()
    search_service.index_source(source, db_session)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert any(s.source_id == source.id for s in result.source_list)
    assert any(s.reference == "§ 355 AO" for s in result.source_list)


def test_knowledge_items_used_contains_matching_approved_item(db_session: Session) -> None:
    matter = _matter(db_session, title="Einspruch Steuerbescheid")
    service, search_service = _service()
    knowledge_item = KnowledgeItem(
        title="Einspruch Steuerbescheid Baustein",
        content="Textbaustein Einspruch Steuerbescheid",
        approval_status="approved",
    )
    db_session.add(knowledge_item)
    db_session.commit()
    search_service.index_knowledge_item(knowledge_item, db_session)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert any(k.knowledge_item_id == knowledge_item.id for k in result.knowledge_items_used)


def test_used_source_is_persisted_as_draft_source_link(db_session: Session) -> None:
    """Prompt 24: die tatsaechliche Verwendung wird persistiert, nicht nur
    transient im DraftingResult zurueckgegeben (Grundlage fuer das
    Quellen-Panel im Dashboard)."""
    matter = _matter(db_session, title="Einspruch Steuerbescheid")
    service, search_service = _service(min_score=0.0)
    source = Source(
        title="Einspruch Steuerbescheid Regelung",
        source_type="Gesetz",
        reference="§ 355 AO",
        approval_level="freigegeben",
    )
    db_session.add(source)
    db_session.commit()
    search_service.index_source(source, db_session)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    links = db_session.query(DraftSourceLink).filter_by(draft_id=result.draft_id).all()
    assert len(links) == 1
    assert links[0].source_id == source.id


def test_used_knowledge_item_is_persisted_as_draft_knowledge_item_link(
    db_session: Session,
) -> None:
    matter = _matter(db_session, title="Einspruch Steuerbescheid")
    service, search_service = _service()
    knowledge_item = KnowledgeItem(
        title="Einspruch Steuerbescheid Baustein",
        content="Textbaustein Einspruch Steuerbescheid",
        approval_status="approved",
    )
    db_session.add(knowledge_item)
    db_session.commit()
    search_service.index_knowledge_item(knowledge_item, db_session)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    links = (
        db_session.query(DraftKnowledgeItemLink).filter_by(draft_id=result.draft_id).all()
    )
    assert len(links) == 1
    assert links[0].knowledge_item_id == knowledge_item.id


def test_reference_links_are_not_shared_across_versions(db_session: Session) -> None:
    """Jede Version bekommt EIGENE Links - keine Wiederverwendung."""
    matter = _matter(db_session, title="Einspruch Steuerbescheid")
    service, search_service = _service(min_score=0.0)
    source = Source(
        title="Einspruch Steuerbescheid Regelung",
        source_type="Gesetz",
        reference="§ 355 AO",
        approval_level="freigegeben",
    )
    db_session.add(source)
    db_session.commit()
    search_service.index_source(source, db_session)

    first = service.create_draft(matter.id, "formulate_draft", db_session)
    first_draft = db_session.query(Draft).filter_by(id=first.draft_id).first()
    second = service.create_draft(
        matter.id, "improve_draft", db_session, previous_draft=first_draft
    )

    first_links = db_session.query(DraftSourceLink).filter_by(draft_id=first.draft_id).all()
    second_links = db_session.query(DraftSourceLink).filter_by(draft_id=second.draft_id).all()
    assert len(first_links) == 1
    assert len(second_links) == 1
    assert first_links[0].id != second_links[0].id


def test_no_sources_or_knowledge_items_means_no_links(db_session: Session) -> None:
    matter = _matter(db_session, title="Völlig unbekanntes Thema ohne Treffer xyz123")
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert db_session.query(DraftSourceLink).filter_by(draft_id=result.draft_id).count() == 0
    assert (
        db_session.query(DraftKnowledgeItemLink).filter_by(draft_id=result.draft_id).count()
        == 0
    )


def test_insufficient_research_becomes_open_review_point(db_session: Session) -> None:
    matter = _matter(db_session, title="Völlig unbekanntes Thema ohne Quelle")
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert any("Nicht ausreichend belegt" in point for point in result.open_review_points)


def test_unreviewed_deadline_becomes_uncertainty(db_session: Session) -> None:
    matter = _matter(db_session, title="Testakte")
    deadline = Deadline(matter=matter, source_text="Frist am 15.03.2027", confidence=0.4)
    db_session.add(deadline)
    db_session.commit()
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert any("unbestätigte Frist" in u for u in result.uncertainties)


def test_no_deadlines_means_no_uncertainty_about_deadlines(db_session: Session) -> None:
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert not any("Frist" in u for u in result.uncertainties)


def test_disallowed_purpose_creates_no_draft(db_session: Session) -> None:
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(matter.id, "analyze_full_file", db_session)

    assert result.success is False
    assert result.draft_id is None
    assert len(result.blocked_reasons) > 0
    assert db_session.query(Draft).count() == 0


def test_blocked_request_is_logged_without_pii(db_session: Session) -> None:
    """Deterministisch ueber einen nicht erlaubten Zweck blockiert (siehe
    test_disallowed_purpose_creates_no_draft) - seit Presidio (§63) wird ein
    Name wie "Peter Müller" zuverlaessig erkannt UND pseudonymisiert, waere
    also kein zuverlaessiger Block-Ausloeser mehr (siehe
    tests/test_ai_providers_orchestrator.py fuer den analogen
    Verhaltenswechsel). Kernaussage bleibt: selbst wenn das Dokument echte
    PII enthaelt, landet nie Klartext im Blocked-Log."""
    matter = _matter(db_session, title="Testakte")
    from app.models import Document

    document = Document(
        matter=matter,
        file_path="/tmp/x.pdf",
        extracted_text="Bitte informieren Sie auch Herrn Peter Müller.",
    )
    db_session.add(document)
    db_session.commit()
    service, _ = _service()

    service.create_draft(matter.id, "analyze_full_file", db_session)

    logs = db_session.query(ApiCallLog).filter_by(result_status="blocked").all()
    assert len(logs) == 1
    assert "Peter" not in (logs[0].error_status or "")


def test_successful_call_is_logged_with_token_count(db_session: Session) -> None:
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    service.create_draft(matter.id, "formulate_draft", db_session)

    logs = db_session.query(ApiCallLog).filter_by(result_status="success").all()
    assert len(logs) == 1
    assert logs[0].token_count == 99


def test_context_never_contains_data_from_other_matter(db_session: Session) -> None:
    """Aktenisolation - dasselbe wiederkehrende Muster wie im gesamten Projekt."""
    from app.models import Document

    matter_a = _matter(db_session, client_name="Mandant A", title="Akte A")
    matter_b = _matter(db_session, client_name="Mandant B", title="Akte B")
    doc_a = Document(matter=matter_a, file_path="/tmp/a.pdf", extracted_text="Geheimer Vertrag Akte A wurde geprüft.")
    doc_b = Document(matter=matter_b, file_path="/tmp/b.pdf", extracted_text="Geheimer Vertrag Akte B wurde geprüft.")
    db_session.add_all([doc_a, doc_b])
    db_session.commit()

    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider)
    service.create_draft(matter_a.id, "formulate_draft", db_session)

    draft = db_session.query(Draft).filter_by(matter_id=matter_a.id).first()
    assert draft is not None
    # Die andere Akte darf ueberhaupt nicht in dieser Aktion beruehrt worden sein.
    assert db_session.query(Draft).filter_by(matter_id=matter_b.id).count() == 0


# --- §65: lokaler KI-Zwischenschritt (Ollama) ---


def test_without_local_llm_provider_behaves_exactly_as_before(db_session: Session) -> None:
    """Standardfall (settings.local_ai_enabled=False -> local_llm_provider=None,
    siehe app/ai_providers/factory.py::build_local_llm_provider): unveraendertes
    Verhalten wie vor §65."""
    matter = _matter(db_session)
    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider, local_llm_provider=None)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    assert len(writing_provider.received_payloads) == 1


def test_local_llm_result_is_added_as_argumentationspunkt(db_session: Session) -> None:
    """Test 3: der lokale KI-Provider verarbeitet die bereits pseudonymisierte
    Anfrage, sein Ergebnis fliesst als zusaetzlicher, klar gekennzeichneter
    Argumentationspunkt in die Claude-Anfrage ein."""
    matter = _matter(db_session)
    writing_provider = FakeClaudeWritingProvider()
    local_llm = FakeLocalLLMProvider(response_text="Kurzfassung des Sachverhalts.")
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    assert len(local_llm.received_payloads) == 1
    sent_payload = writing_provider.received_payloads[0]
    assert any(
        "Kurzfassung des Sachverhalts." in punkt
        for punkt in sent_payload.anonymisierte_argumentationspunkte
    )
    assert any(
        punkt.startswith("Lokale Vorabanalyse")
        for punkt in sent_payload.anonymisierte_argumentationspunkte
    )


def test_claude_never_receives_original_plaintext_with_local_llm_enabled(
    db_session: Session,
) -> None:
    """Test 4: Claude erhaelt niemals den urspruenglichen Klartext - aktiv
    ueberprueft, auch wenn der lokale KI-Schritt aktiv ist."""
    matter = _matter(db_session, client_name="Max Mustermann")
    document_text = "Mandant Max Mustermann bittet um Rueckmeldung."
    from app.models import Document

    db_session.add(
        Document(matter_id=matter.id, file_path="/tmp/x.pdf", extracted_text=document_text)
    )
    db_session.commit()
    # Antworttext enthaelt bewusst den erwarteten Platzhalter - ein
    # generischer, entitaetsfreier Text wuerde seit der neuen
    # Antwortvalidierung (Platzhalter-Integritaetspruefung, s. o.) zu Recht
    # als Inkonsistenz abgelehnt; dieser Test soll ausschliesslich die
    # Datenisolation pruefen, nicht die Antwortvalidierung.
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrter Herr [MANDANT_01], vielen Dank für Ihre Nachricht."
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    sent_payload = writing_provider.received_payloads[0]
    assert "Max Mustermann" not in sent_payload.anonymisierter_sachverhalt
    assert "Max Mustermann" not in local_llm.received_payloads[0].anonymisierter_sachverhalt
    assert "[MANDANT_01]" in sent_payload.anonymisierter_sachverhalt


def test_ollama_unavailable_blocks_request_and_claude_is_never_called(
    db_session: Session,
) -> None:
    """Test 5+6: Ollama nicht verfuegbar -> Claude erhaelt KEINEN Aufruf
    (auch keinen mit Klartext), Ergebnis ist ein kontrollierter Fehler,
    kein stiller Fallback."""
    matter = _matter(db_session)
    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider, local_llm_provider=FailingLocalLLMProvider())

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert len(result.blocked_reasons) > 0
    assert writing_provider.received_payloads == []  # NIE aufgerufen
    assert db_session.query(Draft).count() == 0


def test_ollama_unavailable_is_logged_without_pii(db_session: Session) -> None:
    matter = _matter(db_session, client_name="Peter Beispiel")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandant Peter Beispiel bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    service, _ = _service(local_llm_provider=FailingLocalLLMProvider())

    service.create_draft(matter.id, "formulate_draft", db_session)

    logs = db_session.query(ApiCallLog).filter_by(result_status="error").all()
    assert len(logs) == 1
    assert logs[0].error_status == "local_ai_unavailable"
    assert "Peter" not in (logs[0].error_status or "")


def test_full_orchestrated_path_presidio_local_ai_claude_reconstruction(
    db_session: Session,
) -> None:
    """Test 8: vollstaendiger orchestrierter Pfad mit Mock-Providern:
    Input -> Presidio -> Local AI -> Claude -> Reconstruction."""
    matter = _matter(db_session, client_name="Anna Beispielperson")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Anna Beispielperson bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrte Frau [MANDANT_01], vielen Dank für Ihre Nachricht."
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    # Local AI hat die pseudonymisierte (nicht die rohe) Payload gesehen.
    assert "Anna Beispielperson" not in local_llm.received_payloads[0].anonymisierter_sachverhalt
    # Claude hat ebenfalls nur die pseudonymisierte Payload gesehen.
    assert "Anna Beispielperson" not in writing_provider.received_payloads[0].anonymisierter_sachverhalt
    # Die lokale Rekonstruktion liefert am Ende wieder den echten Namen.
    assert "Anna Beispielperson" in result.draft_text
    assert "[MANDANT_01]" not in result.draft_text


# --- Antwortvalidierung: deterministische + lokale semantische Pruefung der
# Claude-Antwort vor der Rekonstruktion (Increment "lokale KI als
# Datenschutz-/Qualitaetsschicht") ---


def test_missing_placeholder_in_response_fails_closed(db_session: Session) -> None:
    """Fall 1: Claude "vergisst" einen erwarteten Platzhalter -> kontrollierter
    Abbruch, KEINE Rekonstruktion, KEIN fertiges Dokument. Die deterministische
    Pruefung greift VOR dem lokalen LLM - generate_structured wird bei einem
    deterministischen Fehlschlag bewusst NICHT aufgerufen."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(response_text="Vielen Dank fuer Ihre Nachricht.")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert len(result.blocked_reasons) > 0
    assert db_session.query(Draft).count() == 0
    assert local_llm.structured_calls == []


def test_altered_placeholder_in_response_fails_closed(db_session: Session) -> None:
    """Fall 2: veraenderte/erfundene Platzhalter-ID in der Claude-Antwort
    (Struktur-/ID-Manipulation) -> kontrollierter Abbruch."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrte Frau [MANDANT_99], vielen Dank fuer Ihre Nachricht."
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert db_session.query(Draft).count() == 0
    assert local_llm.structured_calls == []


def test_original_pii_in_response_fails_closed(db_session: Session) -> None:
    """Fall 3: der pseudonymisierte Originalwert taucht zusaetzlich zum
    korrekten Platzhalter im Klartext der Claude-Antwort auf - deterministisch
    erkennbarer Datenschutzverstoss -> kontrollierter Abbruch."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text=(
            "Sehr geehrte Frau [MANDANT_01], vielen Dank fuer Ihre Nachricht. "
            "Wir haben mit Frau Erika Mustermann bereits telefoniert."
        )
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert any("Datenschutzverstoss" in reason for reason in result.blocked_reasons)
    assert db_session.query(Draft).count() == 0
    assert local_llm.structured_calls == []


def test_correct_placeholders_triggers_local_semantic_check(db_session: Session) -> None:
    """Fall 4a: bei korrekten Platzhaltern laeuft (nach bestandener
    deterministischer Pruefung) tatsaechlich die lokale semantische Pruefung -
    generate_structured wird mit dem erwarteten kleinen Schema aufgerufen."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrte Frau [MANDANT_01], vielen Dank fuer Ihre Nachricht."
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    assert len(local_llm.structured_calls) == 1
    prompt, schema = local_llm.structured_calls[0]
    assert "passed" in schema["properties"]
    assert "[MANDANT_01]" in prompt


def test_semantically_conspicuous_response_fails_closed(db_session: Session) -> None:
    """Fall 5: lokale semantische Pruefung meldet eine Auffaelligkeit (z. B.
    Platzhalter falscher Entitaet zugeordnet) -> kontrollierter Abbruch,
    obwohl die deterministische Pruefung bestanden hat."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrte Frau [MANDANT_01], vielen Dank fuer Ihre Nachricht."
    )
    local_llm = FakeLocalLLMProvider(
        structured_result={
            "passed": False,
            "issues": [
                {
                    "type": "placeholder_inconsistency",
                    "severity": "high",
                    "description": "Platzhalter wirkt der falschen Person zugeordnet.",
                }
            ],
        }
    )
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert any("placeholder_inconsistency" in reason for reason in result.blocked_reasons)
    assert db_session.query(Draft).count() == 0


def test_ollama_timeout_during_response_validation_fails_closed(db_session: Session) -> None:
    """Ollama faellt genau bei der Antwortpruefung aus (nach dem bereits
    erfolgten Claude-Aufruf) -> kontrollierter Abbruch, KEINE Rekonstruktion,
    KEIN fertiges Dokument - trotz bereits erfolgtem Claude-Aufruf."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrte Frau [MANDANT_01], vielen Dank fuer Ihre Nachricht."
    )
    local_llm = SucceedingProcessFailingStructuredLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert len(writing_provider.received_payloads) == 1  # Claude WURDE aufgerufen
    assert db_session.query(Draft).count() == 0  # aber es gibt kein fertiges Dokument
    logs = db_session.query(ApiCallLog).filter_by(result_status="error").all()
    assert any(log.error_status == "local_ai_unavailable" for log in logs)
