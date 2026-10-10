"""Performance-Regressionstests fuer die Chat-Latenz-Untersuchung (13.09.,
LEXONO – PERFORMANCE ROOT-CAUSE & CHAT LATENCY FIX).

ECHTER FUND (real gemessen, siehe DECISIONS.md fuer die vollen Zahlen):
eine einfache Chat-Frage brauchte 4-5 Minuten. Der dominante Anteil war
`OllamaLocalLLMProvider.process()` (die verpflichtende lokale
Vorabanalyse, §65) - real gemessen ~124s, weil der Aufruf OHNE
`format`-JSON-Schema-Constraint lief (das "Thinking"-Verhalten von
`qwen3:8b` liess das Modell frei/unbegrenzt "nachdenken"). Mit
identischem Constraint (bereits vorher fuer `generate_structured()`
etabliert, jetzt auch fuer `process()` genutzt): ~14s statt ~124s, real
gemessen ~9x schneller, OHNE die Aufgabe zu aendern.

Bewusste Grenze dieser Tests: sie messen NICHT die tatsaechliche
Wanduhrzeit gegen ein echtes Ollama (das waere in CI weder deterministisch
noch reproduzierbar - haengt von Hardware/Modell/Auslastung ab und wurde
bereits REAL, live, mehrfach gemessen, siehe DECISIONS.md). Stattdessen
pruefen sie den MECHANISMUS, der die reale Beschleunigung traegt: dass
JEDE der 5 vom Auftrag genannten Anfrage-Arten den `format`-Schema-
Constraint tatsaechlich verwendet (statt still auf den alten,
unbegrenzten Pfad zurueckzufallen) - das ist die automatisierbare,
CI-taugliche Absicherung GEGEN genau die Regression, die urspruenglich
zur 4-5-Minuten-Latenz fuehrte.

P0-PERFORMANCE-FOLLOW-UP (13.09., zweiter Bottleneck behoben): TEST A/B
("Akte: Schnellentwurf 2026-09-13" bzw. eine Rueckfrage ohne echten Namen)
enthalten WEDER ein Aktendokument NOCH ein von Presidio erkanntes
PII-Vorkommen - fuer genau diese Klasse (siehe
`DraftingService._should_skip_llm_privacy_layers`) werden die
LLM-gestuetzten §65-Schritte jetzt bewusst UEBERSPRUNGEN (Presidio/
Pseudonymisierung und die deterministische Platzhalter-Integritaetspruefung
bleiben unveraendert Pflicht) - siehe DECISIONS.md fuer die volle,
evidenzbasierte Herleitung aus LEXONO_MASTER_PRODUCT.md §4. TEST C/D
(enthalten echte Namen -> Presidio erkennt PII -> mappings nicht leer)
und TEST E (expliziter Schreibauftrag) bleiben auf der VOLLEN Pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.drafting.service import DraftingService
from app.models import Client, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider


pytestmark = pytest.mark.usefixtures("always_run_local_summary")

class FakeClaudeWritingProvider:
    """Kein echter Claude-Aufruf noetig - diese Tests pruefen ausschliesslich
    den lokalen Vorabanalyse-Schritt, der VOR Claude laeuft. Gibt bewusst den
    pseudonymisierten Sachverhalt selbst als "Antwort" zurueck (statt eines
    freien Textes) - so enthaelt die Antwort automatisch GENAU dieselben
    Platzhalter wie das Mapping, und die nachgelagerte, hier nicht zu
    testende Platzhalter-Integritaetspruefung (app/drafting/
    response_validation.py) blockiert nicht faelschlich."""

    def __init__(self) -> None:
        self.received_payloads: list[ClaudeRequestPayload] = []

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        self.received_payloads.append(payload)
        text = "\n".join(
            [
                payload.anonymisierter_sachverhalt,
                payload.anonymisierte_anwaltliche_anmerkungen or "",
                *payload.anonymisierte_argumentationspunkte,
            ]
        )
        return ClaudeWritingResult(text=text, token_count=42)


class _RecordingFakeOllamaTransport:
    """Ersetzt `httpx.post`, sodass `OllamaLocalLLMProvider` real durchlaeuft
    (kein zweiter Test-Double auf `LocalLLMProvider`-Ebene) - jeder
    tatsaechlich gesendete Request wird aufgezeichnet, damit die Tests
    pruefen koennen, OB der `format`-Constraint verwendet wurde."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def __call__(self, url: str, *, json: dict, timeout: float):
        self.requests.append(json)
        # Realistische Ollama-Antwort: ein JSON-Objekt im "response"-Feld,
        # passend zum jeweils uebergebenen Schema.
        body = jsondump_response(json)
        return _FakeHttpxResponse(body)


def jsondump_response(request_json: dict) -> dict:
    schema = request_json.get("format") or {}
    if "zusammenfassung" in schema.get("properties", {}):
        return {"response": json.dumps({"zusammenfassung": "Kurzfassung des Sachverhalts."})}
    return {"response": json.dumps({"passed": True, "issues": []})}


class _FakeHttpxResponse:
    def __init__(self, json_data: dict) -> None:
        self._json_data = json_data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._json_data


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
    client = Client(name="Max Mustermann")
    matter = Matter(client=client, title="Testakte")
    db.add_all([client, matter])
    db.commit()
    return matter


def _service(local_llm_provider, writing_provider) -> DraftingService:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return DraftingService(
        RuleBasedLocalAIProvider(),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        writing_provider,
        model_name="claude-sonnet-5",
        local_llm_provider=local_llm_provider,
    )


def _run_scenario(
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
    *,
    purpose: str,
    sachverhalt: str,
    attach_document: bool = False,
) -> dict:
    transport = _RecordingFakeOllamaTransport()
    monkeypatch.setattr(httpx, "post", transport)
    provider = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:8b")
    matter = _matter(db)
    if attach_document:
        _attach_document(db, matter)
    service = _service(provider, FakeClaudeWritingProvider())

    result = service.create_draft(
        matter.id, purpose, db, attorney_anmerkungen=sachverhalt, actor="Testnutzer"
    )

    assert result.success, result.blocked_reasons
    assert len(transport.requests) >= 1, "lokale Vorabanalyse wurde nicht aufgerufen"
    return transport.requests[0]


def _attach_document(db: Session, matter: Matter) -> None:
    """Haengt ein echtes Aktendokument an - macht
    `preparation.has_document_context=True`, unabhaengig davon, ob
    Presidio darin PII findet (siehe `_should_skip_llm_privacy_layers`:
    ein Dokument allein verhindert bereits das Ueberspringen)."""
    from app.models import Document

    doc = Document(
        matter_id=matter.id,
        file_path="synthetic.txt",
        original_filename="dokument.txt",
        content_hash="synthetic-hash",
        mime_type="text/plain",
        extracted_text="Ein synthetischer Dokumenttext ohne echten Namen.",
        classified_type="Sonstiges",
    )
    db.add(doc)
    db.commit()


# --- TEST A: Normal chat, cold (erste Nachricht einer neuen Konversation) ---
# P0-Performance-Follow-up (13.09.): kein Dokument, kein von Presidio
# erkanntes PII -> die LLM-gestuetzten §65-Schritte werden jetzt bewusst
# uebersprungen (siehe Moduldocstring). Presidio/Pseudonymisierung selbst
# bleibt unveraendert Pflicht (siehe test_..._presidio_still_runs unten).


def test_scenario_a_normal_chat_cold_skips_llm_privacy_layers(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _RecordingFakeOllamaTransport()
    monkeypatch.setattr(httpx, "post", transport)
    provider = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:8b")
    matter = _matter(db_session)
    service = _service(provider, FakeClaudeWritingProvider())

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Akte: Schnellentwurf 2026-09-13",
        actor="Testnutzer",
    )

    assert result.success, result.blocked_reasons
    assert transport.requests == [], (
        "TEST A (normaler Chat, kein Dokument/PII): lokale KI haette "
        "uebersprungen werden muessen"
    )


# --- TEST B: Normal chat, warm (Folgenachricht in bestehender Konversation) ---


def test_scenario_b_normal_chat_warm_skips_llm_privacy_layers(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _RecordingFakeOllamaTransport()
    monkeypatch.setattr(httpx, "post", transport)
    provider = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:8b")
    matter = _matter(db_session)
    service = _service(provider, FakeClaudeWritingProvider())

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Und was bedeutet das konkret fuer die Kuendigungsfrist?",
        actor="Testnutzer",
    )

    assert result.success, result.blocked_reasons
    assert transport.requests == [], (
        "TEST B (normaler Chat, warm, kein Dokument/PII): lokale KI haette "
        "uebersprungen werden muessen"
    )


# --- TEST C: Dokumenten-Zusammenfassung ---


def test_scenario_c_document_summary_uses_schema_constraint(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _run_scenario(
        monkeypatch,
        db_session,
        purpose="chat_response",
        sachverhalt=(
            "Bitte fasse folgendes Dokument zusammen: Mietvertrag zwischen "
            "Herrn Thomas Berger und Frau Sabine Krueger vom 01.01.2020, "
            "monatliche Miete 850 EUR, Kuendigungsfrist drei Monate zum "
            "Quartalsende."
        ),
    )
    assert "format" in request, "TEST C (Dokumenten-Zusammenfassung): format-Constraint fehlt"


# --- TEST D: Komplexe Dokumentenanalyse (mehrere Absaetze/Argumentationspunkte) ---


def test_scenario_d_complex_document_analysis_uses_schema_constraint(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sachverhalt = "\n\n".join(
        [
            "Mandant Herr Klaus Fischer erhielt am 02.03.2026 einen Steuerbescheid.",
            "Nachzahlung in Hoehe von 4250 EUR fuer das Steuerjahr 2024.",
            "Einspruchsfrist laeuft am 02.04.2026 ab.",
            "Streitig ist die Nichtanerkennung von Werbungskosten fuer ein "
            "Homeoffice in Hoehe von 1800 EUR.",
        ]
    )
    request = _run_scenario(
        monkeypatch, db_session, purpose="chat_response", sachverhalt=sachverhalt
    )
    assert "format" in request, "TEST D (komplexe Dokumentenanalyse): format-Constraint fehlt"


# --- TEST E: Drafting (explizite Schriftsatz-Erstellung) ---


def test_scenario_e_drafting_uses_schema_constraint(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _run_scenario(
        monkeypatch,
        db_session,
        purpose="formulate_draft",
        sachverhalt="Mandant Herr Klaus Fischer bittet um ein Antwortschreiben an Frau Petra Wolff.",
    )
    assert "format" in request, "TEST E (Drafting): format-Constraint fehlt"


def test_local_llm_pre_analysis_runs_exactly_when_sensitive_context_exists(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-Performance-Follow-up (13.09.): ersetzt den frueheren, jetzt
    ueberholten 'niemals uebersprungen'-Test - lokale KI wird bewusst NUR
    uebersprungen, wenn ALLE drei Bedingungen zutreffen (chat_response +
    kein Dokument + keine erkannte PII, siehe
    `_should_skip_llm_privacy_layers`). Deckt beide Richtungen ab: echte
    Namen (Presidio erkennt PII) UND ein angehaengtes Dokument erzwingen
    weiterhin die volle Pipeline, auch bei purpose='chat_response'."""
    scenarios = [
        ("chat_response", "Bitte fasse dieses Dokument fuer Herrn Klaus Fischer zusammen.", False, True),
        ("chat_response", "Mandant Herr Klaus Fischer erhielt einen Bescheid ueber 4250 EUR.", False, True),
        ("formulate_draft", "Mandant Herr Klaus Fischer bittet um ein Antwortschreiben.", False, True),
        ("chat_response", "Und was bedeutet das konkret? Keine Namen hier.", True, True),
    ]
    for purpose, sachverhalt, attach_document, expect_local_ai_call in scenarios:
        transport = _RecordingFakeOllamaTransport()
        monkeypatch.setattr(httpx, "post", transport)
        provider = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:8b")
        matter = _matter(db_session)
        if attach_document:
            _attach_document(db_session, matter)
        service = _service(provider, FakeClaudeWritingProvider())
        result = service.create_draft(
            matter.id, purpose, db_session, attorney_anmerkungen=sachverhalt, actor="Testnutzer"
        )
        assert result.success, (purpose, sachverhalt, result.blocked_reasons)
        if expect_local_ai_call:
            assert len(transport.requests) >= 1, (
                f"lokale KI haette fuer '{sachverhalt}' NICHT uebersprungen werden duerfen"
            )
        else:
            assert transport.requests == [], (
                f"lokale KI haette fuer '{sachverhalt}' uebersprungen werden muessen"
            )


def test_presidio_pseudonymization_and_stage_one_validation_never_skipped(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressionssicherung: auch wenn die LLM-gestuetzten §65-Schritte
    fuer eine einfache Chat-Nachricht uebersprungen werden, bleiben
    Presidio/Pseudonymisierung (Gateway) und die deterministische
    Platzhalter-Integritaetspruefung (Stufe 1) unveraendert Pflicht - eine
    Anfrage MIT echtem PII darf niemals unpseudonymisiert bei Claude
    ankommen, unabhaengig vom Skip-Mechanismus."""
    transport = _RecordingFakeOllamaTransport()
    monkeypatch.setattr(httpx, "post", transport)
    provider = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:8b")
    matter = _matter(db_session)
    writer = FakeClaudeWritingProvider()
    service = _service(provider, writer)

    result = service.create_draft(
        matter.id,
        "chat_response",
        db_session,
        attorney_anmerkungen="Was denkt Herr Klaus Fischer ueber diesen Fall?",
        actor="Testnutzer",
    )

    assert result.success, result.blocked_reasons
    sent_payload = writer.received_payloads[0]
    assert "Klaus Fischer" not in sent_payload.anonymisierte_anwaltliche_anmerkungen
    assert "[PERSON_01]" in sent_payload.anonymisierte_anwaltliche_anmerkungen
    # Echte PII erkannt -> mappings nicht leer -> lokale KI bleibt Pflicht.
    assert len(transport.requests) >= 1
