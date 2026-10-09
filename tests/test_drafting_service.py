"""Tests fuer app/drafting/service.py (Prompt 17).

Nutzt FakeEmbeddingProvider (kein echter Modell-Download) und einen Fake
ClaudeWritingProvider (kein echter API-Aufruf)."""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.drafting.service import DraftingService, _should_skip_llm_privacy_layers
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
    # 05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS" Phase D:
    # `persisted.content` ist jetzt vom rohen `result.draft_text`
    # (Markdown) zu Editor-darstellbarem HTML gewandelt (siehe
    # app/drafting/markdown_to_draft_html.py) - der reine Text bleibt
    # inhaltlich enthalten, nur um HTML-Tags ergaenzt.
    assert persisted.content_format == "html"
    assert result.draft_text is not None
    assert result.draft_text in persisted.content


def test_unlinked_general_chat_skips_organization_pseudonymization(db_session: Session) -> None:
    """ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
    Pipeline", 07.10., per direktem Reproduktionsskript VOR dieser
    Korrektur gefunden): `matter_id=None` legt ueber `create_quick_matter`
    automatisch eine Akte mit dem Sammel-Mandanten "Ohne
    Mandantenzuordnung" an - `known_entities["mandant"]` war dadurch
    NIEMALS wirklich leer (siehe `_has_only_placeholder_known_entities`
    in app/drafting/service.py), eine naive `not known_entities`-Pruefung
    haette `skip_general_knowledge_pseudonymization` fuer JEDEN echten Chat
    nie ausgeloest. End-to-End bewiesen: eine allgemeine Wissensfrage mit
    einem Organisationsnamen erreicht Claude (hier: den Fake-Writing-
    Provider) mit dem Klartextnamen, NICHT einem Platzhalter."""
    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider=writing_provider)

    result = service.create_draft(
        None,
        "chat_response",
        db_session,
        attorney_anmerkungen="Was ist die World Health Organization?",
        actor="test@example.invalid",
    )

    assert result.success is True
    assert len(writing_provider.received_payloads) == 1
    payload = writing_provider.received_payloads[0]
    assert payload.anonymisierte_anwaltliche_anmerkungen == "Was ist die World Health Organization?"


def test_linked_matter_with_real_client_does_not_skip_general_knowledge_pseudonymization(
    db_session: Session,
) -> None:
    """Gegenprobe zum vorherigen Test: sobald eine Akte mit einem ECHTEN,
    benannten Mandanten verknuepft ist, bleibt das Verhalten unveraendert
    streng - ein Organisationsname wird weiterhin pseudonymisiert, auch
    in einer allgemeinen Wissensfrage innerhalb dieser Akte."""
    matter = _matter(db_session, client_name="Müller GmbH", title="Testakte")
    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider=writing_provider)

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Was ist die World Health Organization?",
        actor="test@example.invalid",
    )

    assert result.success is True
    payload = writing_provider.received_payloads[0]
    assert "World Health Organization" not in payload.anonymisierte_anwaltliche_anmerkungen
    assert "[ORGANISATION_01]" in payload.anonymisierte_anwaltliche_anmerkungen


def test_chat_response_purpose_persists_as_chat_reference_status(db_session: Session) -> None:
    """05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §11/§12 -
    eine normale Chat-Antwort (purpose="chat_response", keine erkannte
    Schriftsatz-Absicht, siehe app/chat/service.py::
    _looks_like_drafting_request) persistiert weiterhin eine Draft-Zeile
    (Traeger fuer Quellen-/Wissens-Verknuepfungen), aber NICHT mit dem
    normalen "draft"-Status - sonst wuerde sie wie ein echter, freigabe-
    pflichtiger Schriftsatz in jeder Entwuerfe-Liste auftauchen und einen
    "Vollstaendigen Editor oeffnen"-Link anbieten, obwohl der Nutzer nie
    einen Schriftsatz angefordert hat."""
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(matter.id, "chat_response", db_session)

    assert result.success is True
    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert persisted.status == "chat_reference"


def test_formulate_draft_purpose_persists_as_normal_draft_status(db_session: Session) -> None:
    """Gegenprobe zu test_chat_response_purpose_persists_as_chat_reference_
    status - ein echter Schriftsatz-Intent bleibt unveraendert ein ganz
    normaler "draft"-Status (erscheint in allen Entwuerfe-Listen, bietet
    den Editor an)."""
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert persisted.status == "draft"


def test_create_draft_with_message_id_persists_it_on_the_draft(db_session: Session) -> None:
    """ECHTER FUND (17.09., Overnight-Direktive §6/§7 "Workflows
    verbinden"): `create_new_draft_version` (app/drafting/versioning.py)
    unterstuetzte `message_id` bereits laenger, aber `DraftingService.
    create_draft` reichte es nie durch - das "Original links"-Panel in
    draft_detail.html konnte dadurch strukturell nie eine Ursprungs-
    nachricht anzeigen, unabhaengig davon, wie der Entwurf entstand."""
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(
        matter.id, "formulate_draft", db_session, message_id="msg-123"
    )

    assert result.success is True
    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert persisted.message_id == "msg-123"


def test_create_draft_without_message_id_leaves_it_none(db_session: Session) -> None:
    """Gegenprobe: unveraendertes Verhalten fuer alle bestehenden Aufrufer
    (Schriftsatz-Generator, anwaltliche Anweisungen), die dieses Feld nicht
    kennen."""
    matter = _matter(db_session, title="Testakte")
    service, _ = _service()

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert persisted.message_id is None


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


def test_create_draft_records_a_perf_trace_step_per_real_pipeline_stage(
    db_session: Session,
) -> None:
    """P0 Performance-Root-Cause-Run (13.09.): jeder create_draft()-Aufruf
    muss ueber eine PerfTrace nachvollziehbar sein - ohne uebergebene
    Instanz wird intern automatisch eine erzeugt (bestehende Aufrufer/
    Tests bleiben unveraendert), mit uebergebener Instanz landen alle
    Schritte darin, in der tatsaechlichen Ausfuehrungsreihenfolge."""
    from app.observability.perf_trace import PerfTrace

    matter = _matter(db_session)
    writing_provider = FakeClaudeWritingProvider()
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)
    trace = PerfTrace()

    result = service.create_draft(
        matter.id, "formulate_draft", db_session, trace=trace
    )

    assert result.success is True
    step_names = [name for name, _ in trace.steps]
    assert step_names == [
        "retrieval",
        "privacy_gateway",
        "local_ai_preanalysis",
        "claude",
        "validation",
        "reconstruction",
    ]
    assert all(duration >= 0 for _, duration in trace.steps)


class TestShouldSkipLlmPrivacyLayers:
    """P0 Performance-Follow-up (13.09.): Einheitstests fuer die
    Entscheidungsfunktion selbst - siehe deren Docstring und
    DECISIONS.md fuer die volle, evidenzbasierte Herleitung aus
    LEXONO_MASTER_PRODUCT.md §4 ("the actual sensitive document/context
    reasoning" bleibt lokal - existiert bei chat_response ohne Dokument
    und ohne erkanntes PII schlicht nicht)."""

    def test_skips_for_simple_chat_without_document_or_pii(self) -> None:
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response", has_document_context=False, mappings=[]
        ) is True

    def test_does_not_skip_when_document_context_present(self) -> None:
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response", has_document_context=True, mappings=[]
        ) is False

    def test_does_not_skip_when_unknown_pii_was_detected(self) -> None:
        """Unveraendert: eine Entitaet, die zu KEINER bekannten Akten-Person
        gehoert, erzwingt weiterhin die volle Pipeline."""
        mapping = SimpleNamespace(original_value="Unbekannter Name")
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response",
            has_document_context=False,
            mappings=[mapping],
            known_entities={"mandant": ["Erika Mustermann"]},
        ) is False

    def test_does_not_skip_for_explicit_drafting_purpose(self) -> None:
        assert _should_skip_llm_privacy_layers(
            purpose="formulate_draft", has_document_context=False, mappings=[]
        ) is False

    def test_skips_when_only_the_matters_own_known_client_was_detected(self) -> None:
        """CHAT-04 (15.09.), der eigentliche Fix: der Sachverhalt ohne
        Dokument ist exakt "Akte: {Titel}" - ein Aktentitel wie
        "Muster, Anna offen 1" enthaelt fast immer den Mandantennamen, der
        dann von Presidio pseudonymisiert wird, OBWOHL die eigentliche
        Chatnachricht ("Hallo") nichts Sensibles enthaelt. Ein bereits der
        Akte bekannter Name ist keine NEUE, ungeschuetzte Information."""
        mapping = SimpleNamespace(original_value="Erika Mustermann")
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response",
            has_document_context=False,
            mappings=[mapping],
            known_entities={"mandant": ["Erika Mustermann"]},
        ) is True

    def test_skip_is_case_insensitive_for_known_names(self) -> None:
        mapping = SimpleNamespace(original_value="erika mustermann")
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response",
            has_document_context=False,
            mappings=[mapping],
            known_entities={"mandant": ["Erika Mustermann"]},
        ) is True

    def test_does_not_skip_when_only_some_entities_are_known(self) -> None:
        """Ein einziger unbekannter Treffer neben bekannten reicht, um die
        volle Pipeline zu erzwingen - kein Mehrheitsentscheid."""
        known = SimpleNamespace(original_value="Erika Mustermann")
        unknown = SimpleNamespace(original_value="Peter Andersen")
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response",
            has_document_context=False,
            mappings=[known, unknown],
            known_entities={"mandant": ["Erika Mustermann"]},
        ) is False

    def test_does_not_skip_without_known_entities_info(self) -> None:
        """Sicherer Standardfall: ohne `known_entities` (Aufrufer liefert es
        nicht) laesst sich Sicherheit nicht nachweisen - kein Skip, auch
        wenn der Name zufaellig bekannt waere."""
        mapping = SimpleNamespace(original_value="Erika Mustermann")
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response",
            has_document_context=False,
            mappings=[mapping],
            known_entities=None,
        ) is False
        assert _should_skip_llm_privacy_layers(
            purpose="chat_response",
            has_document_context=False,
            mappings=[mapping],
            known_entities={},
        ) is False


def test_simple_chat_without_document_or_pii_skips_local_llm_calls(
    db_session: Session,
) -> None:
    """Integrationstest (echter create_draft()-Aufruf, kein Unit-Test der
    reinen Funktion): eine chat_response-Anfrage OHNE Aktendokument und
    OHNE von Presidio erkanntes PII darf weder `process()` (Vorabanalyse)
    noch `generate_structured()` (semantische Antwortvalidierung, Stufe 2)
    aufrufen - Presidio/Pseudonymisierung selbst bleibt unveraendert
    Pflicht (siehe FakeClaudeWritingProvider.write, das den bereits
    pseudonymisierten Sachverhalt woertlich zurueckgibt)."""
    matter = _matter(db_session)
    writing_provider = FakeClaudeWritingProvider(response_text="Eine normale Antwort ohne PII.")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Was steht in § 558 BGB?",
        actor="Testnutzer",
    )

    assert result.success is True
    assert local_llm.received_payloads == []
    assert local_llm.structured_calls == []


def test_chat_greeting_on_a_matter_titled_with_the_client_name_skips_local_llm(
    db_session: Session,
) -> None:
    """CHAT-04 (15.09.), der eigentliche, real gemeldete Fall: eine Akte
    mit einem Titel wie "Muster, Anna offen 1" (kanzleiueblich, enthaelt
    den Mandantennamen) und eine simple Begruessung ("Hallo") ohne
    Aktendokument. VORHER: die volle lokale Vorabanalyse lief trotzdem,
    weil der Aktentitel selbst schon einen Presidio-Treffer erzeugt (real
    gemessen 10,8 s warm / 48,1 s cold). NACHHER: da dieser Treffer
    exakt der bereits bekannte Mandantenname ist, wird die lokale
    Vorabanalyse uebersprungen - Presidio/Pseudonymisierung selbst bleibt
    unveraendert Pflicht (siehe FakeClaudeWritingProvider, das den
    pseudonymisierten Text woertlich zurueckgibt)."""
    matter = _matter(db_session, client_name="Erika Mustermann", title="Erika Mustermann offen 1")
    writing_provider = FakeClaudeWritingProvider(response_text="Guten Tag, wie kann ich helfen?")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        matter.id, "chat_response", db_session, attorney_anmerkungen="Hallo", actor="Testnutzer"
    )

    assert result.success is True
    assert local_llm.received_payloads == [], "lokale Vorabanalyse haette uebersprungen werden muessen"
    assert local_llm.structured_calls == [], "Stufe 2 der Antwortvalidierung haette uebersprungen werden muessen"


def test_chat_message_with_a_new_unknown_name_still_runs_the_full_pipeline(
    db_session: Session,
) -> None:
    """Gegenprobe zum Fix: tippt der Anwalt in der Chatnachricht selbst
    einen NEUEN, der Akte nicht bekannten Namen, bleibt die volle Pipeline
    (inkl. lokaler Vorabanalyse) Pflicht - genau der Fall, den CHAT-04
    weiterhin schuetzen soll."""
    matter = _matter(db_session, client_name="Erika Mustermann", title="Erika Mustermann offen 1")
    writing_provider = FakeClaudeWritingProvider()
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Bitte notiere, dass auch Herr Klaus Andersen beteiligt ist.",
        actor="Testnutzer",
    )

    assert result.success is True
    assert local_llm.received_payloads != [], "lokale Vorabanalyse haette laufen muessen (neuer Name)"


def test_document_context_forces_full_pipeline_even_for_chat_purpose(
    db_session: Session,
) -> None:
    """Ein Aktendokument allein (auch OHNE von Presidio erkanntes PII im
    Dokumenttext) erzwingt weiterhin die volle Pipeline - verlaesst sich
    NICHT allein auf Presidios Entitaetserkennung."""
    from app.models import Document

    matter = _matter(db_session)
    document = Document(
        matter=matter,
        file_path="/tmp/x.pdf",
        extracted_text="Ein Dokumenttext ohne erkennbare Namen oder Adressen.",
        classified_type="Sonstiges",
    )
    db_session.add(document)
    db_session.commit()

    writing_provider = FakeClaudeWritingProvider()
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Bitte fasse das Dokument zusammen.",
        actor="Testnutzer",
    )

    assert result.success is True
    assert len(local_llm.received_payloads) == 1


# ==========================================================================
# Owner-Direktive "Schriftsatz-Workflow, Pseudonymisierung, lokale KI und
# DIN-A4-Dokumentdarstellung" (06.10.) - Phase 12, Tests 1-4. Decken
# ausdruecklich den PRODUKTIONS-STANDARDFALL ab (`local_llm_provider=None`,
# `settings.local_ai_enabled=False`) - die bereits bestehenden
# Pseudonymisierungs-/Rekonstruktions-Tests oben (z. B.
# test_full_orchestrated_path_presidio_local_ai_claude_reconstruction)
# nutzen durchgaengig einen FakeLocalLLMProvider und beweisen daher NICHT,
# dass derselbe Datenschutz ohne lokale KI greift - genau diese Luecke
# schliessen die folgenden Tests (/local-ai-causality-test).
# ==========================================================================


def test_pseudonymization_and_restoration_work_without_local_ai(
    db_session: Session,
) -> None:
    """TEST 1 + TEST 2 kombiniert, OHNE lokale KI (lokale KI ist in der
    Produktion standardmaessig deaktiviert, siehe app/config/settings.py::
    local_ai_enabled). Cloud-Payload darf den Klarnamen NICHT enthalten,
    MUSS den Platzhalter enthalten; das finale Dokument muss den Klarnamen
    wieder enthalten und darf den Platzhalter NICHT mehr enthalten."""
    matter = _matter(db_session, client_name="Max Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandant Max Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrter Herr [MANDANT_01], vielen Dank für Ihre Nachricht."
    )
    service, _ = _service(writing_provider, local_llm_provider=None)
    assert service.local_llm_provider is None

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    sent_payload = writing_provider.received_payloads[0]
    # CLOUD PAYLOAD -> enthaelt Max Mustermann NICHT
    assert "Max Mustermann" not in sent_payload.anonymisierter_sachverhalt
    # CLOUD PAYLOAD -> enthaelt [MANDANT_01]
    assert "[MANDANT_01]" in sent_payload.anonymisierter_sachverhalt
    # FINAL LOCAL DOCUMENT -> enthaelt Max Mustermann
    assert "Max Mustermann" in result.draft_text
    # FINAL LOCAL DOCUMENT -> enthaelt [MANDANT_01] NICHT
    assert "[MANDANT_01]" not in result.draft_text


def test_mapping_is_isolated_per_draft_not_reused_across_matters(
    db_session: Session,
) -> None:
    """TEST 3 - Mapping-Isolation: zwei unabhaengige create_draft-Aufrufe
    fuer zwei verschiedene Akten/Mandanten duerfen das Mapping des jeweils
    ANDEREN Aufrufs nicht sehen. Jeder `prepare_request()`-Aufruf baut
    `value_to_placeholder`/`counters` lokal in der Methode neu auf (siehe
    app/privacy/pseudonymizer.py::Pseudonymizer.pseudonymize) - kein
    globaler/geteilter Zustand."""
    from app.models import Document

    matter_a = _matter(db_session, client_name="Anna Beispiel", title="Akte A")
    db_session.add(
        Document(matter_id=matter_a.id, file_path="/tmp/a.pdf", extracted_text="Mandantin Anna Beispiel bittet um Rueckmeldung.")
    )
    matter_b = _matter(db_session, client_name="Bernd Muster", title="Akte B")
    db_session.add(
        Document(matter_id=matter_b.id, file_path="/tmp/b.pdf", extracted_text="Mandant Bernd Muster bittet um Rueckmeldung.")
    )
    db_session.commit()

    writer_a = FakeClaudeWritingProvider(response_text="Sehr geehrte Frau [MANDANT_01], danke.")
    writer_b = FakeClaudeWritingProvider(response_text="Sehr geehrter Herr [MANDANT_01], danke.")
    service_a, _ = _service(writer_a, local_llm_provider=None)
    service_b, _ = _service(writer_b, local_llm_provider=None)

    result_a = service_a.create_draft(matter_a.id, "formulate_draft", db_session)
    result_b = service_b.create_draft(matter_b.id, "formulate_draft", db_session)

    assert result_a.success is True
    assert result_b.success is True
    # Jeder Aufruf loest "[MANDANT_01]" korrekt gegen das EIGENE Mapping auf -
    # keine Vermischung zwischen den beiden unabhaengigen Akten.
    assert "Anna Beispiel" in result_a.draft_text
    assert "Bernd Muster" not in result_a.draft_text
    assert "Bernd Muster" in result_b.draft_text
    assert "Anna Beispiel" not in result_b.draft_text


def test_unmapped_placeholder_in_claude_response_is_blocked_even_without_local_ai(
    db_session: Session,
) -> None:
    """TEST 4 - Missing Mapping: enthaelt die Cloud-Antwort einen
    Platzhalter, der zu KEINEM echten Mapping-Eintrag gehoert (von Claude
    "erfunden"/vertauscht), darf er NICHT geraten/stillschweigend
    durchgereicht werden - der Entwurf muss sicher BLOCKIERT werden.

    ECHTER FUND (06.10., Visual-Verification-Direktive): dieser Fall war
    bis zu diesem Fix NICHT abgesichert, wenn lokale KI deaktiviert ist
    (Produktions-Standardkonfiguration) - die deterministische Pruefung
    (app/privacy/security_check.py::check_response_placeholder_integrity)
    lief nur, wenn `local_llm_provider` konfiguriert war, obwohl sie selbst
    KEIN LLM benoetigt (siehe app/drafting/service.py::
    _finish_non_streaming_stream, `else`-Zweig). Vor dem Fix lieferte dieser
    exakte Testfall `result.success=True` mit dem rohen Platzhalter sichtbar
    im Text."""
    matter = _matter(db_session, client_name="Max Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandant Max Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    # "[UNBEKANNT_99]" gehoert zu KEINER Kategorie/keinem echten Mapping.
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrter Herr [UNBEKANNT_99], vielen Dank für Ihre Nachricht."
    )
    service, _ = _service(writing_provider, local_llm_provider=None)
    assert service.local_llm_provider is None

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert db_session.query(Draft).count() == 0


def test_long_document_loses_no_text_through_the_full_pipeline(
    db_session: Session,
) -> None:
    """TEST 7 - Long Document: ein mehrseitiger Schriftsatz (mehrere
    Absaetze, insgesamt mehrere tausend Zeichen) darf die Pseudonymisierungs-
    /Rekonstruktions-Pipeline vollstaendig durchlaufen, OHNE dass Text
    verloren geht - jeder einzelne Absatz muss im finalen Dokument
    wiederzufinden sein."""
    matter = _matter(db_session, client_name="Max Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandant Max Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    paragraphs = [
        f"Dies ist Absatz Nummer {i} des Schriftsatzes mit etwas Fuelltext, "
        f"damit das Dokument insgesamt mehrseitig lang wird." for i in range(1, 31)
    ]
    long_response = "Sehr geehrter Herr [MANDANT_01],\n\n" + "\n\n".join(paragraphs)
    writing_provider = FakeClaudeWritingProvider(response_text=long_response)
    service, _ = _service(writing_provider, local_llm_provider=None)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    assert "Max Mustermann" in result.draft_text
    for paragraph in paragraphs:
        assert paragraph in result.draft_text


def test_chat_and_editor_see_the_same_document_content(db_session: Session) -> None:
    """TEST 8 - Chat -> Editor muss denselben Dokumentinhalt verwenden:
    `ChatMessage.content` (Chat-Panel, Markdown) und `Draft.content`
    (Editor, zu HTML konvertiert) stammen aus DEMSELBEN `reconstructed_
    text` zum Erstellungszeitpunkt (app/chat/service.py::send_message setzt
    `content=result.draft_text`; app/drafting/service.py::_persist_draft
    konvertiert exakt denselben Text zu HTML) - kein zweites,
    divergierendes Dokumentmodell (Phase 10: EIN Dokumentmodell)."""
    matter = _matter(db_session, client_name="Max Mustermann")
    from app.models import Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandant Max Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Sehr geehrter Herr [MANDANT_01], hiermit legen wir Einspruch ein."
    )
    service, _ = _service(writing_provider, local_llm_provider=None)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    draft = db_session.query(Draft).filter_by(id=result.draft_id).first()
    # Der Editor-Inhalt (HTML) muss denselben Klartext enthalten wie der
    # Chat-Inhalt (result.draft_text, Quelle fuer ChatMessage.content) -
    # dieselbe rekonstruierte Aussage, keine abweichende zweite Fassung.
    assert "Max Mustermann" in draft.content
    assert "hiermit legen wir Einspruch ein" in draft.content
    assert draft.content_format == "html"
    for sentence_fragment in ("Max Mustermann", "hiermit legen wir Einspruch ein"):
        assert sentence_fragment in result.draft_text


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
    deterministischen Fehlschlag bewusst NICHT aufgerufen.

    purpose="improve_draft" (18.09., angepasst nach der dritten Eskalation
    von CHAT-01/formulate_draft-Fund - siehe app/drafting/service.py fuer
    die volle Begruendung): `require_full_placeholder_coverage` ist jetzt
    fuer ALLE `formulate_draft`/`chat_response`-Aufrufe deaktiviert, da
    `prepare_draft_context` deren Mappings identisch aus der gesamten Akte
    baut. Fuer die verbleibenden Zwecke (improve_draft, correct_draft, ...)
    gibt es dafuer noch KEINEN live-reproduzierten Gegenbeweis - dieser
    Test demonstriert daher weiterhin mit einem NICHT befreiten Zweck,
    dass der Fail-Closed-Mechanismus selbst (nicht nur die beiden
    Manipulations-/Leck-Pruefungen) noch grundsaetzlich funktioniert."""
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

    result = service.create_draft(matter.id, "improve_draft", db_session)

    assert result.success is False
    assert len(result.blocked_reasons) > 0
    assert db_session.query(Draft).count() == 0
    assert local_llm.structured_calls == []


def test_chat_response_missing_placeholder_is_not_blocked(db_session: Session) -> None:
    """CHAT-01 (15.09., Chat-Intelligence-Forensik): der reproduzierte
    Kernfall - EXAKT dieselbe Ausgangslage wie
    `test_missing_placeholder_in_response_fails_closed` (Aktendokument mit
    echtem Mandantennamen -> echtes Presidio-Mapping, volle Pipeline aktiv),
    aber mit purpose="chat_response" statt "formulate_draft": eine
    natuerliche Antwort ohne jeden Platzhalter darf jetzt NICHT mehr
    blockiert werden. Vorher wurde genau das blockiert - die direkte
    Ursache dafuer, dass eine normale Chat-Begruessung ("Hallo") nie
    beantwortet wurde."""
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
    writing_provider = FakeClaudeWritingProvider(response_text="Guten Tag, wie kann ich Ihnen helfen?")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "chat_response", db_session)

    assert result.success is True
    assert result.draft_text == "Guten Tag, wie kann ich Ihnen helfen?"
    assert db_session.query(Draft).count() == 1


def test_formulate_draft_missing_placeholder_is_not_blocked_when_replying_to_a_message(
    db_session: Session,
) -> None:
    """ECHTER FUND (18.09., Flow-Audit "Posteingang -> Antworten", live am
    echten Server reproduziert): dieselbe Ueberforderung wie CHAT-01, aber
    fuer einen ECHTEN Antwort-Entwurf (purpose="formulate_draft", NICHT
    chat_response) - "Antworten" auf eine Posteingang-Nachricht baut den
    Sachverhalt/die Mappings aus der GESAMTEN Akte, nicht nur der einen
    Nachricht; eine kurze, korrekte Antwort muss nicht jeden Akte-weiten
    Platzhalter woertlich enthalten. `message_id` ist das bereits
    bestehende Signal dafuer, dass dieser Entwurf aus EINER konkreten
    Nachricht/einem Dokument entstand (siehe app/drafting/service.py::
    _finish_non_streaming_stream fuer die volle Begruendung)."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import Document, Message

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    message = Message(matter_id=matter.id, direction="inbound", sender="Erika Mustermann")
    db_session.add(message)
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(response_text="Vielen Dank fuer Ihre Nachricht.")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        matter.id, "formulate_draft", db_session, message_id=message.id
    )

    assert result.success is True
    assert result.draft_text == "Vielen Dank fuer Ihre Nachricht."
    assert db_session.query(Draft).count() == 1


def test_formulate_draft_missing_placeholder_is_not_blocked_when_chat_triggered_without_message_id(
    db_session: Session,
) -> None:
    """Erweiterung desselben Fundes (18.09.): "Dokument analysieren"/
    "Schriftsatz-Entwurf erstellen" auf ein NICHT per E-Mail eingegangenes
    Dokument (z. B. ueber die "Dokument hochladen"-Funktion) hat KEIN
    `document.message_id` - ohne das zusaetzliche `chat_triggered`-Signal
    waere dieser Fall weiterhin betroffen gewesen, siehe app/drafting/
    service.py::_finish_non_streaming_stream fuer die volle Begruendung."""
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

    result = service.create_draft(
        matter.id, "formulate_draft", db_session, message_id=None, chat_triggered=True
    )

    assert result.success is True
    assert result.draft_text == "Vielen Dank fuer Ihre Nachricht."
    assert db_session.query(Draft).count() == 1


def test_formulate_draft_via_schriftsatz_generator_missing_placeholder_is_not_blocked(
    db_session: Session,
) -> None:
    """ECHTER FUND, dritte Eskalation (18.09., Owner-Direktive "CONTINUE
    AUTONOMOUS PRODUCT COMPLETION", Tiefen-E2E-Test Schriftsatz-Generator,
    live am echten Server reproduziert): der KANONISCHE Schriftsatz-
    Generator-Weg (message_id=None, chat_triggered=False - exakt dieser
    Fall galt bisher als der EINE, fuer den volle Abdeckung noch zwingend
    war) schlug live mit demselben Konsistenzfehler fehl wie die beiden
    zuvor behobenen Faelle. Ersetzt den frueheren, jetzt falsifizierten
    Test `test_formulate_draft_without_message_id_still_requires_full_coverage`
    (der GENAU dieses Verhalten noch als Pflicht behauptete) - siehe
    app/drafting/service.py fuer die volle Root-Cause-Begruendung
    (`prepare_draft_context` baut Mappings fuer JEDEN Zweck identisch aus
    der gesamten Akte, nicht nur fuer chat-getriggerte Faelle)."""
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

    result = service.create_draft(
        matter.id, "formulate_draft", db_session, message_id=None
    )

    assert result.success is True
    assert result.draft_text == "Vielen Dank fuer Ihre Nachricht."
    assert db_session.query(Draft).count() == 1


def test_empty_writing_response_is_blocked_not_persisted_as_empty_draft(
    db_session: Session,
) -> None:
    """ECHTER FUND (19.09., live am echten Server reproduziert, ZWEIMAL
    deterministisch mit identischer Akte/identischem Schriftsatz-Auftrag):
    ein Claude-Aufruf kann `max_tokens` komplett verbrauchen, ohne
    sichtbaren finalen Text zu liefern (`writing_result.text == ""`). VOR
    der `_RELAXED_COVERAGE_PURPOSES`-Lockerung wurde das als Nebeneffekt
    ueber `check_placeholders_present` erkannt (eine leere Antwort
    "enthaelt" trivialerweise keinen erwarteten Platzhalter) - mit der
    Lockerung fuer `formulate_draft` waere ein leerer Entwurf sonst still
    als `success=True` durchgereicht worden (Verstoss gegen §4 "REAL
    OBJECTS - NO FAKE UI"). Der neue, purpose-unabhaengige Mindestinhalt-
    Check in `_finish_non_streaming_stream` faengt das jetzt eigenstaendig
    ab, siehe app/drafting/service.py."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    writing_provider = FakeClaudeWritingProvider(response_text="")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert any("leer" in reason.lower() for reason in result.blocked_reasons)
    assert db_session.query(Draft).count() == 0


def test_whitespace_only_writing_response_is_also_blocked(db_session: Session) -> None:
    """Gegenprobe zum Mindestinhalt-Check: reine Leerraum-Antwort ist
    ebenso wenig ein echter Entwurf wie eine komplett leere - `.strip()`
    im Check muss auch diesen Fall abfangen."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    writing_provider = FakeClaudeWritingProvider(response_text="   \n\n  ")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert db_session.query(Draft).count() == 0


def test_chat_response_still_blocks_altered_placeholder(db_session: Session) -> None:
    """Die Manipulations-Pruefung bleibt fuer chat_response unveraendert
    Pflicht - CHAT-01 schaltet ausschliesslich die
    Vollstaendigkeitsforderung ab, nicht die tatsaechlich schuetzenden
    Pruefungen."""
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

    result = service.create_draft(matter.id, "chat_response", db_session)

    assert result.success is False
    assert db_session.query(Draft).count() == 0
    assert local_llm.structured_calls == []


def test_chat_response_still_blocks_leaked_original_value(db_session: Session) -> None:
    """Dieselbe Garantie fuer die dritte Stufe-1-Pruefung: ein geleakter
    Originalwert bleibt fuer chat_response ein Blocker.

    ECHTER FUND (UI-Live-Validierung "Zusammenfassen"-Aktion, 17.09.,
    reproduziert per DraftingService-Direktaufruf mit echter Presidio-
    Pseudonymisierung + echtem lokalem LLM): dieser exakte Blockfall - der
    schwerwiegendste der drei Stufe-1-Pruefungen - landete im Audit-Log
    bisher unter dem generischen `error_status="response_validation_failed"`
    UND wurde dem Anwalt als nichtssagendes "Die Anfrage wurde aus
    Datenschutzgruenden blockiert." angezeigt - nicht von einem beliebigen
    unbekannten Fehler unterscheidbar. Seit dem Fix (api_logger.py::
    _BLOCK_CATEGORIES) traegt sowohl das Audit-Log als auch die
    Anwalts-Meldung die spezifische Kategorie."""
    matter = _matter(db_session, client_name="Erika Mustermann")
    from app.models import ApiCallLog, Document

    db_session.add(
        Document(
            matter_id=matter.id,
            file_path="/tmp/x.pdf",
            extracted_text="Mandantin Erika Mustermann bittet um Rueckmeldung.",
        )
    )
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="Guten Tag, wir haben bereits mit Erika Mustermann telefoniert."
    )
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "chat_response", db_session)

    assert result.success is False
    assert db_session.query(Draft).count() == 0
    error_logs = db_session.query(ApiCallLog).filter_by(result_status="error").all()
    assert len(error_logs) == 1
    assert error_logs[0].error_status == "original_value_leaked"
    assert "Erika" not in (error_logs[0].error_status or "")
    assert "Mustermann" not in (error_logs[0].error_status or "")


def test_improve_draft_missing_placeholder_still_fails_closed_alongside_formulate_fix(
    db_session: Session,
) -> None:
    """Gegenprobe zum Fix (18.09., angepasst nach der dritten Eskalation -
    siehe app/drafting/service.py): `require_full_placeholder_coverage`
    ist jetzt fuer chat_response UND formulate_draft deaktiviert (beide
    live als Fehlalarm reproduziert), aber der Fail-Closed-Mechanismus
    selbst bleibt fuer einen NICHT befreiten Zweck (hier: improve_draft,
    fuer den es bislang keinen Gegenbeweis gibt) unveraendert aktiv -
    ersetzt den frueheren, jetzt falsifizierten Test
    `test_formulate_draft_missing_placeholder_still_fails_closed_alongside_chat_fix`."""
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
    writing_provider = FakeClaudeWritingProvider(response_text="Guten Tag, wie kann ich Ihnen helfen?")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "improve_draft", db_session)

    assert result.success is False
    assert db_session.query(Draft).count() == 0


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


def test_semantic_check_failure_gets_honest_category_not_privacy_violation(
    db_session: Session,
) -> None:
    """ECHTER FUND (05.10., Owner-Direktive "P1-BUGFIX: Schriftsatz
    unvollständig, Folgefragen blockiert, Datenschutzprüfung fehlerhaft",
    mit dem real konfigurierten lokalen Modell (qwen2.5:1.5b) reproduziert):
    ein Fund der rein qualitätsbezogenen Stufe 2 (lokales LLM,
    "AUSDRÜCKLICH KEINE juristische Bewertung") landete bisher im selben
    Audit-Log-Eimer wie ein echter Stufe-1-Datenschutzfund und wurde dem
    Anwalt mit genau derselben "Datenschutzgründen"-Formulierung gezeigt -
    obwohl diese Stufe gar keine Datenschutzentscheidung trifft. Die
    Fail-Closed-ENTSCHEIDUNG selbst bleibt unveraendert (der Entwurf wird
    weiterhin NICHT uebernommen) - nur `error_status`/die Nutzermeldung
    sind jetzt ehrlich von einem echten Stufe-1-Fund unterscheidbar."""
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
                    "type": "consistent_placeholder_usage",
                    "severity": "high",
                    "description": "The placeholders [KATEGORIE_XX] are not used consistently.",
                }
            ],
        }
    )
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    error_logs = db_session.query(ApiCallLog).filter_by(result_status="error").all()
    assert len(error_logs) == 1
    assert error_logs[0].error_status == "local_quality_check_uncertain"
    assert error_logs[0].error_status != "unknown_block_reason"
    assert error_logs[0].error_status != "response_validation_failed"
    assert any("lokale Qualitätsprüfung" in reason for reason in result.blocked_reasons)
    assert not any("Datenschutzgründen blockiert" in reason for reason in result.blocked_reasons)


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


def test_unlinked_general_chat_keeps_place_names_readable(db_session: Session) -> None:
    """ECHTER FUND (08.10.): "...in deutschland" erreichte Claude als
    "[ORT_01]" - Ende-zu-Ende ueber DraftingService im Akte-losen Chat."""
    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider=writing_provider)

    result = service.create_draft(
        None,
        "chat_response",
        db_session,
        attorney_anmerkungen="wieviele klempnerbetriebe gibt es ca. in deutschland",
        actor="test@example.invalid",
    )

    assert result.success is True
    payload = writing_provider.received_payloads[0]
    assert payload.anonymisierte_anwaltliche_anmerkungen == (
        "wieviele klempnerbetriebe gibt es ca. in deutschland"
    )


def test_unlinked_general_chat_followup_after_assistant_answer_is_not_blocked(
    db_session: Session,
) -> None:
    """ECHTER FUND (08.10.): "was kannst du" nach einer vorherigen Antwort
    wurde als Residual-PII blockiert - Ende-zu-Ende ueber DraftingService."""
    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider=writing_provider)

    result = service.create_draft(
        None,
        "chat_response",
        db_session,
        attorney_anmerkungen="was kannst du",
        gespraechsverlauf=[
            "Anwalt: hallo wer bist du",
            "Assistent: Ich bin der Arbeitsassistent von Lexono und helfe bei "
            "Fragen, Dokumenten und Entwürfen. " * 12,
        ],
        actor="test@example.invalid",
    )

    assert result.success is True
    assert result.blocked_reasons == []
    assert writing_provider.received_payloads[0].anonymisierte_anwaltliche_anmerkungen == "was kannst du"


# --- ECHTER FUND (08.10., Real-User-E2E im installierten Build): Mappings,
# die NUR aus fruehren Claude-Antworten der Historie stammen (Euro-Betraege,
# NER-Fehlalarme), liessen die lokalen LLM-Schichten laufen (~60 s statt
# ~12 s pro General-Chat-Folgefrage). ---

_AI_HISTORY_WITH_AMOUNTS = [
    "Anwalt: Wie hoch sind die Mietpreise?",
    "Assistent: Die Preise liegen zwischen 7,71 € und 18,58 € pro Quadratmeter. "
    "Der Miet-Check zeigt 12,72 € im Mittel.",
]


def test_general_chat_followup_with_mappings_only_from_ai_history_skips_local_llm(
    db_session: Session,
) -> None:
    writing_provider = FakeClaudeWritingProvider(response_text="Eine normale Antwort.")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        None,
        "chat_response",
        db_session,
        attorney_anmerkungen="was kannst du",
        gespraechsverlauf=_AI_HISTORY_WITH_AMOUNTS,
        actor="Testnutzer",
    )

    assert result.success is True
    assert local_llm.received_payloads == [], "Vorabanalyse haette uebersprungen werden muessen"
    assert local_llm.structured_calls == []
    # Geldbetraege sind sachverhaltsrelevant und bleiben unveraendert (kein Betrags-Platzhalter).
    history = " ".join(writing_provider.received_payloads[0].anonymisierter_gespraechsverlauf)
    assert "7,71" in history
    assert "[BETRAG_" not in history


def test_general_chat_with_a_new_person_typed_by_the_lawyer_still_runs_local_llm_despite_ai_history(
    db_session: Session,
) -> None:
    """Gegenprobe: eine NEU vom Anwalt getippte Person ist lokal-stammend -
    die volle Pipeline bleibt Pflicht, auch wenn Assistent-Historie vorhanden
    ist."""
    writing_provider = FakeClaudeWritingProvider(response_text="Notiert.")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        None,
        "chat_response",
        db_session,
        attorney_anmerkungen="Bitte notiere, dass auch Herr Klaus Andersen beteiligt ist.",
        gespraechsverlauf=_AI_HISTORY_WITH_AMOUNTS,
        actor="Testnutzer",
    )

    assert result.success is True
    assert len(local_llm.received_payloads) == 1, "volle Pipeline haette laufen muessen"


def test_value_typed_by_the_lawyer_and_repeated_in_ai_history_stays_relevant(
    db_session: Session,
) -> None:
    """Konservativ: taucht ein Wert auch in der Anwalt-Eingabe auf, bleibt
    er relevant, selbst wenn er ebenfalls in einer KI-Zeile steht."""
    writing_provider = FakeClaudeWritingProvider(response_text="Ok.")
    local_llm = FakeLocalLLMProvider()
    service, _ = _service(writing_provider, local_llm_provider=local_llm)

    result = service.create_draft(
        None,
        "chat_response",
        db_session,
        attorney_anmerkungen="Die Mieterin Martina Quellfeld hat widersprochen, bitte pruefen.",
        gespraechsverlauf=[
            "Anwalt: Wer ist die Gegenseite?",
            "Assistent: Das ist Martina Quellfeld, wohnhaft in Hamburg.",
        ],
        actor="Testnutzer",
    )

    assert result.success is True
    assert len(local_llm.received_payloads) == 1


def test_review_notes_block_is_kept_out_of_the_persisted_draft_but_stays_in_draft_text(
    db_session: Session,
) -> None:
    """ECHTER FUND (Real-E2E 08.10.): offene Pruefpunkte standen inline im
    kopierbaren Schriftsatz. Draft (Editor/Export) enthaelt jetzt nur das
    Schreiben; `draft_text` (Chat-Verlauf/Anzeige) behaelt den Hinweisblock,
    den die UI getrennt darstellt."""
    from app.drafting.review_notes import REVIEW_NOTES_HEADING

    matter = _matter(db_session, title="Testakte")
    response = (
        "Sehr geehrte Damen und Herren,\n\nwir bitten um Rueckmeldung.\n\n"
        f"## {REVIEW_NOTES_HEADING}\n\n- Aktenzeichen fehlt im Sachverhalt"
    )
    service, _ = _service(writing_provider=FakeClaudeWritingProvider(response_text=response))

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is True
    assert REVIEW_NOTES_HEADING in result.draft_text
    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert "wir bitten um Rueckmeldung" in persisted.content
    assert "Aktenzeichen fehlt" not in persisted.content
    assert "PRÜFPUNKTE" not in persisted.content


def test_ollama_failure_is_reported_honestly_not_as_a_privacy_block(db_session: Session) -> None:
    """ECHTER FUND (Real-E2E 08.10.): ein Ollama-Ladefehler erschien dem Anwalt als
    "aus Datenschutzgruenden blockiert". Ursache und Benutzertext muessen
    uebereinstimmen; nichts darf an die Cloud gehen."""
    from app.privacy.api_logger import friendly_block_message

    matter = _matter(db_session, title="Testakte")
    writing_provider = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider, local_llm_provider=FailingLocalLLMProvider())

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert writing_provider.received_payloads == []  # Claude wurde nie aufgerufen
    message = friendly_block_message(result.blocked_reasons)
    assert "Datenschutzgründen blockiert" not in message
    assert "lokale KI" in message and "nichts an die Cloud gesendet" in message
    assert "nicht um eine Datenschutz-Blockierung" in message


def test_failing_local_answer_check_is_also_reported_honestly() -> None:
    from app.privacy.api_logger import friendly_block_message

    reasons = ["Lokale Prüfung der Antwort (Ollama) nicht erreichbar - Entwurf wurde nicht übernommen."]

    message = friendly_block_message(reasons)

    assert "Datenschutzgründen blockiert" not in message
    assert "nicht um eine Datenschutz-Blockierung" in message


def test_failing_cloud_call_logs_exception_type_and_status_but_no_content(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Hardening (Real-E2E 08.10.): ein fehlgeschlagener Claude-Aufruf wurde ohne
    jede Diagnoseinformation verschluckt. Jetzt: Ausnahmetyp + HTTP-Status im Log,
    niemals die Nachricht oder Nutzdaten."""

    class _ApiError(Exception):
        status_code = 529

    class _FailingWritingProvider:
        def write(self, payload):
            raise _ApiError("Overloaded: enthaelt hier absichtlich Max Mustermann")

    matter = _matter(db_session, title="Testakte")
    service, _ = _service(_FailingWritingProvider())

    with caplog.at_level("WARNING", logger="lexono.drafting"):
        result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "_ApiError" in text and "529" in text
    assert "Max Mustermann" not in text and "Overloaded" not in text


def test_preamble_outside_the_letter_markers_is_not_persisted_in_the_draft(db_session: Session) -> None:
    """Hardening (Real-E2E 08.10.): Einleitungsabsatz vor einer Ueberarbeitung."""
    from app.drafting.review_notes import LETTER_END_MARKER, LETTER_START_MARKER

    matter = _matter(db_session, title="Testakte")
    response = (
        "Da die Frist relativ angegeben wird, formuliere ich sie ohne Datum.\n\n"
        f"{LETTER_START_MARKER}\nSehr geehrte Damen und Herren,\n\nwir bitten um Rueckmeldung.\n"
        f"{LETTER_END_MARKER}"
    )
    service, _ = _service(writing_provider=FakeClaudeWritingProvider(response_text=response))

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    persisted = db_session.query(Draft).filter_by(id=result.draft_id).first()
    assert "wir bitten um Rueckmeldung" in persisted.content
    assert "formuliere ich sie ohne Datum" not in persisted.content
    assert "===" not in persisted.content


def test_common_word_mapped_only_from_ai_history_does_not_block_the_local_summary(
    db_session: Session,
) -> None:
    """ECHTER FUND (Real-E2E 08.10., Fall A, reproduzierbar ab der zweiten Frage): das Wort
    "Mieters" wurde von der NER in einer frueheren KI-Antwort als Organisation erkannt
    ([ORGANISATION_03]); die lokale Zusammenfassung verwendet es normal und wurde als
    "nicht pseudonymisierter Wert" blockiert - noch vor dem Claude-Aufruf."""
    from app.privacy.detectors import DetectedSpan
    from app.privacy.pseudonymizer import Pseudonymizer
    from app.privacy.security_check import SecurityCheckService

    def ner(text: str) -> list[DetectedSpan]:
        spans, start = [], 0
        while (idx := text.find("Mieters", start)) != -1:
            spans.append(DetectedSpan(category="organisation", start=idx, end=idx + 7, value="Mieters"))
            start = idx + 7
        return spans

    gateway = ClaudePrivacyGateway(
        pseudonymizer=Pseudonymizer(ner_detector=ner), security_check=SecurityCheckService(ner_detector=ner)
    )
    writing_provider = FakeClaudeWritingProvider(response_text="Eine normale Antwort.")
    local_llm = FakeLocalLLMProvider(response_text="Die Zustimmung des Mieters wird verlangt.")
    from app.models import Document

    matter = _matter(db_session, title="Testakte")
    db_session.add(
        Document(
            matter=matter,
            file_path="/tmp/x.pdf",
            extracted_text="Ein Mieterhoehungsverlangen ohne erkennbare Namen.",
            classified_type="Sonstiges",
        )
    )
    db_session.commit()
    service = DraftingService(
        RuleBasedLocalAIProvider(),
        LegalResearchService(DocumentSearchService(FakeEmbeddingProvider()), min_score_for_sufficient=0.0),
        DocumentSearchService(FakeEmbeddingProvider()),
        gateway,
        writing_provider,
        model_name="claude-sonnet-5",
        local_llm_provider=local_llm,
    )

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Ist die Erhoehung rechnerisch nachvollziehbar?",
        gespraechsverlauf=[
            "Anwalt: Wer sind die Beteiligten?",
            "Assistent: Die Zustimmung des Mieters und des Mieters wird verlangt.",
        ],
        actor="Testnutzer",
    )

    assert result.success is True, result.blocked_reasons
    assert len(local_llm.received_payloads) == 1  # die lokale Vorabanalyse lief tatsaechlich
    assert len(writing_provider.received_payloads) == 1  # und Claude wurde erreicht


def test_firm_placeholders_in_the_letter_are_filled_from_the_firm_profile(
    db_session: Session,
) -> None:
    """Briefkopf/Unterzeichner kommen lokal aus dem Kanzlei-Profil, nicht vom Modell."""
    from app.firm_profile.service import get_firm_profile

    profile = get_firm_profile(db_session)
    profile.firm_name = "Kanzlei Beispiel (QA)"
    profile.signatory_name = "RA Test Beispiel"
    db_session.commit()
    writing_provider = FakeClaudeWritingProvider(
        response_text="[Kanzlei einsetzen]\n\nSehr geehrte Damen und Herren,\n\nMit freundlichen Grüßen\n\n[Unterzeichner einsetzen]"
    )
    service, _ = _service(writing_provider, local_llm_provider=FakeLocalLLMProvider())

    result = service.create_draft(
        None, "chat_response", db_session, attorney_anmerkungen="Schreibe einen kurzen Brief.", actor="Testnutzer"
    )

    assert result.success is True
    assert "Kanzlei Beispiel (QA)" in result.draft_text
    assert "RA Test Beispiel" in result.draft_text
    assert "einsetzen]" not in result.draft_text
    # Kanzleidaten gehen nie an die Cloud.
    sent = " ".join(
        str(v) for v in vars(writing_provider.received_payloads[0]).values()
    )
    assert "Kanzlei Beispiel (QA)" not in sent
