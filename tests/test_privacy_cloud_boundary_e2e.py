"""Pre-Cloud-Datenschutzgrenze Ende-zu-Ende mit kontrolliertem Transport (Folgezyklus 13.10.2026).

Echter Produktpfad: Upload eines synthetischen PDF ueber /dashboard/chat/send -> Extraktion -> ClaudePrivacyGateway
(Pseudonymisierung, Restrisiko-Scan, Final Payload Gate, Siegel) -> AnthropicClaudeWritingProvider -> ECHTES Anthropic-SDK.
Nur der HTTP-Transport des SDK ist ersetzt (httpx.MockTransport): er zeichnet den vollstaendigen HTTP-Request auf (URL,
Header, JSON-Body mit Systemanweisung, Nachrichten, Metadaten) und antwortet wie die Messages-API. Es verlaesst nichts den
Rechner. Das belegt die SDK-Payload, NICHT eine reale Uebertragung, TLS oder den Gateway-Pfad.
Nur synthetische Daten; der API-Schluessel ist ein Platzhalterwert."""

from __future__ import annotations

import json
from collections.abc import Iterator

import anthropic
import httpx2 as httpx  # vom Anthropic-SDK verwendete httpx-Variante (anthropic 1.5)
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.chat_router as chat_router_module
from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.db.session import get_db
from app.drafting.service import DraftingService
from app.main import app
from app.models import ChatMessage
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload, UnsealedPayloadError
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.auth_test_utils import extract_csrf, login_as_admin
from tests.fake_embedding_provider import FakeEmbeddingProvider

SYNTHETIC_KEY = "sk-ant-test-synthetic-0000"
# Synthetische Werte, die NIE im Cloud-Request stehen duerfen
PROTECTED = [
    "Karl Brenner", "Brenner", "Lindenallee 3", "30000 Beispielstadt", "DE89 3704 0044 0532 0130 00",
    "karl.brenner@example.org", "0151 23456789", "143/815/08154", "HH-AB 1234", "Odette Paschke",
]
DOCUMENT_LINES = [
    "Herrn Karl Brenner, Lindenallee 3, 30000 Beispielstadt",
    "E-Mail karl.brenner@example.org, Tel. 0151 23456789",
    "IBAN DE89 3704 0044 0532 0130 00, Steuernummer 143/815/08154",
    "Unfallgegner mit amtlichem Kennzeichen HH-AB 1234, Zeugin Odette Paschke.",
    "Im Muster steht [PERSON_01] als Lücke. Forderung 1.200,00 EUR nach § 286 BGB, Frist 14 Tage.",
]
ALLOWED_BODY_KEYS = {"model", "max_tokens", "thinking", "system", "messages", "tools"}


class RecordingTransport:
    """Zeichnet jeden HTTP-Request des SDK auf und antwortet wie die Messages-API (Echo der Platzhalter)."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.fail_with = fail_with

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        body = json.loads(request.content)
        prompt = "".join(block.get("text", "") for block in body["messages"][0]["content"])
        import re

        placeholders = list(dict.fromkeys(re.findall(r"\[[A-Z][A-Z_]*_\d{2}\]", prompt)))
        reply = "Zusammenfassung: " + ", ".join(placeholders) + "."
        return httpx.Response(200, json={
            "id": "msg_test_0001", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })


def _provider(transport: RecordingTransport) -> AnthropicClaudeWritingProvider:
    provider = AnthropicClaudeWritingProvider(api_key=SYNTHETIC_KEY, model="claude-sonnet-5", max_tokens=1024)
    # Nur der Transport ist ersetzt - Request-Aufbau, Serialisierung und Header stammen aus dem echten SDK.
    provider._client = anthropic.Anthropic(
        api_key=SYNTHETIC_KEY, base_url="https://api.anthropic.com", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    return provider


def _drafting_service(provider) -> DraftingService:
    search = DocumentSearchService(FakeEmbeddingProvider())
    return DraftingService(
        RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0), search,
        ClaudePrivacyGateway(), provider, model_name="claude-sonnet-5",
    )


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    from app.config.settings import Settings

    monkeypatch.setattr(chat_router_module, "get_settings", lambda: Settings(chat_upload_storage_dir=str(tmp_path / "up")))
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in DOCUMENT_LINES:
        page.insert_text((40, y), line, fontsize=10)
        y += 18
    data = doc.tobytes()
    doc.close()
    return data


def _send(client, db_session, transport, monkeypatch, *, content="Fasse das Dokument zusammen. Was gilt für Herrn Brenner?"):
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _drafting_service(_provider(transport)))
    login_as_admin(db_session, client)
    csrf = extract_csrf(client.get("/dashboard/chat").text)
    # §57: (1) nur hochladen - nichts verlaesst die Maschine; (2) die Frage bewusst senden
    upload = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": content},
        files={"documents": ("synthetisch.pdf", _pdf(), "application/pdf")},
        follow_redirects=False,
    )
    assert upload.status_code == 303 and transport.requests == []
    conversation_id = upload.headers["location"].split("/")[3].split("?")[0]
    return client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": conversation_id, "content": content},
        follow_redirects=True,
    )


def _assistant_text(db_session) -> str:
    message = db_session.query(ChatMessage).filter(ChatMessage.role == "assistant").order_by(ChatMessage.id.desc()).first()
    return message.content if message else ""


def test_full_sdk_request_contains_no_protected_value(client, db_session, monkeypatch) -> None:
    transport = RecordingTransport()
    _send(client, db_session, transport, monkeypatch)
    assert len(transport.requests) == 1, "genau ein Cloud-Request erwartet"
    request = transport.requests[0]
    assert request.method == "POST" and str(request.url) == "https://api.anthropic.com/v1/messages"
    raw = request.content.decode("utf-8")
    headers = "\n".join(f"{k}: {v}" for k, v in request.headers.items())
    for value in PROTECTED:
        assert value not in raw, f"{value!r} im SDK-Request-Body"
        assert value not in headers, f"{value!r} in einem Header"
    body = json.loads(raw)
    assert set(body) <= ALLOWED_BODY_KEYS, f"unerwartete Felder im Request: {set(body) - ALLOWED_BODY_KEYS}"
    assert "metadata" not in body
    assert request.headers["x-api-key"] == SYNTHETIC_KEY
    prompt = "".join(block.get("text", "") for block in body["messages"][0]["content"])
    # Fachinhalt bleibt lesbar, PII erscheint als Platzhalter, der woertliche Platzhalter ist eine Textmarke
    assert "1.200,00 EUR" in prompt and "§ 286 BGB" in prompt
    assert "[PERSON_" in prompt and "[TEXTMARKE_" in prompt
    system_text = "".join(block["text"] for block in body["system"])
    for value in PROTECTED:
        assert value not in system_text


def test_answer_is_restored_locally_without_foreign_values(client, db_session, monkeypatch) -> None:
    transport = RecordingTransport()
    _send(client, db_session, transport, monkeypatch)
    answer = _assistant_text(db_session)
    assert "Karl Brenner" in answer  # lokal zurueckgefuehrt
    assert "[PERSON_01]" in answer  # woertlicher Platzhalter aus dem Dokument bleibt woertlich
    assert "TEXTMARKE" not in answer and "[Angabe einsetzen]" not in answer


def test_pseudonymization_error_blocks_before_any_request(client, db_session, monkeypatch) -> None:
    transport = RecordingTransport()

    def broken_ner(text: str):
        raise RuntimeError("NER-Ausfall (synthetisch)")

    import app.privacy.presidio_ner as presidio_ner

    monkeypatch.setattr(presidio_ner, "detect_presidio_entities", broken_ner)
    monkeypatch.setattr(ClaudePrivacyGateway, "__init__", _gateway_init_with(broken_ner))
    response = _send(client, db_session, transport, monkeypatch)
    assert transport.requests == [], "bei Fehler in der Pseudonymisierung darf kein Request entstehen"
    assert response.status_code in (200, 500)  # Fehleranzeige ist zulaessig, ein Versand nicht


def _gateway_init_with(ner):
    original = ClaudePrivacyGateway.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.pseudonymizer.ner_detector = ner
        self.security_check.ner_detector = ner

    return init


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("synthetischer Timeout"), httpx.ConnectError("keine Verbindung")])
def test_transport_errors_do_not_open_another_path(client, db_session, monkeypatch, error) -> None:
    transport = RecordingTransport(fail_with=error)
    response = _send(client, db_session, transport, monkeypatch)
    assert response.status_code == 200
    assert len(transport.requests) == 1  # max_retries=0: genau der versiegelte Versuch, kein Ausweichpfad
    raw = transport.requests[0].content.decode("utf-8")
    for value in PROTECTED:
        assert value not in raw


def test_unsealed_or_modified_payload_never_reaches_the_transport() -> None:
    transport = RecordingTransport()
    provider = _provider(transport)
    unsealed = ClaudeRequestPayload(schreibauftrag="chat_response", anonymisierter_sachverhalt="Herr Karl Brenner")
    with pytest.raises(UnsealedPayloadError):
        provider.write(unsealed)
    gateway = ClaudePrivacyGateway()
    result = gateway.prepare_request(purpose="chat_response", sachverhalt="Herr Karl Brenner schuldet 500 EUR.",
                                     anwaltliche_anmerkungen="Bitte prüfen.", review_mode="mask")
    assert result.allowed
    result.payload.anonymisierter_sachverhalt = "Herr Karl Brenner schuldet 500 EUR."  # Veraenderung nach dem Siegel
    with pytest.raises(UnsealedPayloadError):
        provider.write(result.payload)
    with pytest.raises(UnsealedPayloadError):
        next(provider.write_stream(result.payload))
    assert transport.requests == []


def _sse(text: str) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {"id": "msg_s", "type": "message", "role": "assistant",
                           "model": "claude-sonnet-5", "content": [], "stop_reason": None, "stop_sequence": None,
                           "usage": {"input_tokens": 5, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 3}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode("utf-8")


def test_streaming_sdk_request_contains_no_protected_value_and_restores() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        prompt = json.loads(request.content)["messages"][0]["content"]
        import re

        found = re.findall(r"\[PERSON_\d{2}\]", "".join(block.get("text", "") for block in prompt))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(f"Antwort an {found[0]}."))

    provider = AnthropicClaudeWritingProvider(api_key=SYNTHETIC_KEY, model="claude-sonnet-5")
    provider._client = anthropic.Anthropic(api_key=SYNTHETIC_KEY, base_url="https://api.anthropic.com", max_retries=0,
                                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    gateway = ClaudePrivacyGateway()
    result = gateway.prepare_request(purpose="chat_response", sachverhalt="\n".join(DOCUMENT_LINES),
                                     anwaltliche_anmerkungen="Was gilt für Herrn Brenner?", review_mode="mask")
    assert result.allowed, result.reasons
    stream = provider.write_stream(result.payload)
    chunks = []
    try:
        while True:
            chunks.append(next(stream))
    except StopIteration:
        pass
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body.get("stream") is True and set(body) - {"stream"} <= ALLOWED_BODY_KEYS
    raw = requests[0].content.decode("utf-8")
    for value in PROTECTED:
        assert value not in raw, value
    assert "Karl Brenner" in gateway.reconstruct_response("".join(chunks), result.mappings)


def test_subclass_with_copied_seal_is_refused() -> None:
    """Review L5: eine Unterklasse mit ueberschriebener Serialisierung und kopiertem Siegel versandte Klartext."""
    transport = RecordingTransport()
    provider = _provider(transport)
    gateway = ClaudePrivacyGateway()
    result = gateway.prepare_request(purpose="chat_response", sachverhalt="Herr Karl Brenner schuldet 500 EUR.",
                                     anwaltliche_anmerkungen="Bitte prüfen.", review_mode="mask")

    class Forged(ClaudeRequestPayload):
        def model_dump_json(self, **kwargs):  # noqa: ANN003
            return result.payload.model_dump_json(**kwargs)

    forged = Forged(**{**result.payload.model_dump(), "anonymisierter_sachverhalt": "Herr Karl Brenner schuldet 500 EUR."})
    forged._gateway_seal = result.payload._gateway_seal
    with pytest.raises(UnsealedPayloadError):
        provider.write(forged)
    assert transport.requests == []


def test_invalid_placeholders_in_the_answer_do_not_restore_foreign_values(client, db_session, monkeypatch) -> None:
    class ManipulatingTransport(RecordingTransport):
        def __call__(self, request):
            self.requests.append(request)
            body = json.loads(request.content)
            return httpx.Response(200, json={
                "id": "msg_test_0002", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": "Antwort an [PERSON_99] und [person_01] und PERSON_01."}],
                "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
            })

    transport = ManipulatingTransport()
    _send(client, db_session, transport, monkeypatch)
    answer = _assistant_text(db_session)
    for value in PROTECTED:  # erfundene/veraenderte Platzhalter werden nie zu einem echten Wert
        assert value not in answer, (value, answer)
