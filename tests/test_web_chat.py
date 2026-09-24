"""Tests für app/web/chat_router.py - die neue Chat-Startseite
(UI-Überarbeitung: "Login → Chat → zentrale Arbeitsoberfläche").

Gleiches Testmuster wie tests/test_web_schriftsatz.py: In-Memory-SQLite
über app.dependency_overrides, `get_drafting_service` wird in
app.web.chat_router direkt gemonkeypatcht. Deckt die im Auftrag
geforderten Szenarien TEST A-E ab."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.chat_router as chat_router_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.db.session import get_db
from app.drafting.service import DraftingService
from app.main import app
from app.models import (
    AuditEvent,
    ChatConversation,
    ChatMessage,
    Client,
    Draft,
    DraftSourceLink,
    Matter,
    Source,
)
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.auth_test_utils import create_test_user, extract_csrf, login, login_as_admin, seed_roles
from tests.fake_embedding_provider import FakeEmbeddingProvider


class FakeClaudeWritingProvider:
    def __init__(self, response_text: str = "Formulierte KI-Antwort.") -> None:
        self.response_text = response_text
        self.received_payloads: list[ClaudeRequestPayload] = []

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        self.received_payloads.append(payload)
        return ClaudeWritingResult(text=self.response_text, token_count=10)


def _working_drafting_service(writer: FakeClaudeWritingProvider | None = None) -> DraftingService:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        writer or FakeClaudeWritingProvider(),
        model_name="claude-sonnet-5",
    )


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service()
    )
    from app.config.settings import Settings

    fake_settings = Settings(chat_upload_storage_dir=str(tmp_path / "chat_uploads"))
    monkeypatch.setattr(chat_router_module, "get_settings", lambda: fake_settings)
    try:
        test_client = TestClient(app)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _csrf(client: TestClient) -> str:
    page = client.get("/dashboard/chat")
    return extract_csrf(page.text)


# ==========================================================================
# TEST A - LOGIN -> CHAT
# ==========================================================================


def test_login_redirects_to_chat_not_inbox(client: TestClient, db_session: Session) -> None:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")

    from tests.auth_test_utils import DEFAULT_TEST_PASSWORD

    response = client.post(
        "/dashboard/login",
        data={"email": "anwalt@kanzlei.test", "password": DEFAULT_TEST_PASSWORD, "next": "/dashboard/chat"},
        follow_redirects=False,
    )
    # Default-"next" im Loginformular ist bereits /dashboard/chat (siehe
    # app/web/auth_router.py) - hier explizit gesetzt, um den tatsaechlich
    # erwarteten Wert zu dokumentieren, nicht um ihn zu erzwingen.
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/chat"


def test_dashboard_root_redirects_to_chat(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    response = client.get("/dashboard", follow_redirects=False)
    # RedirectResponse ohne explizites status_code -> Starlette-Default 307
    # (unveraendertes Verhalten, vorher identisch fuer /dashboard/inbox).
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard/chat"


def test_chat_home_shows_empty_state_for_new_user(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert "Wie kann ich Sie heute unterstützen" in response.text


def test_chat_home_with_new_param_shows_empty_state_despite_existing_history(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (01.09.): "Neuen Chat starten"/das "+"-Icon zeigten bei
    bereits vorhandenem Unterhaltungsverlauf bisher faelschlich wieder die
    zuletzt aktive Unterhaltung, weil GET /dashboard/chat immer conversations[0]
    waehlte. `?new=1` erzwingt jetzt einen echten Leerzustand."""
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Antwort.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    csrf = _csrf(client)
    client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Erste Unterhaltung."},
        follow_redirects=True,
    )

    without_param = client.get("/dashboard/chat")
    assert "Erste Unterhaltung." in without_param.text

    with_new_param = client.get("/dashboard/chat?new=1")
    assert with_new_param.status_code == 200
    assert "chat-empty-state" in with_new_param.text
    assert "Wie kann ich Sie heute unterstützen" in with_new_param.text
    # Der Verlauf bleibt in der Unterhaltungsliste sichtbar (gewuenscht) -
    # nur das Hauptpanel muss den Leerzustand zeigen, keine Nachrichten.
    assert "chat-message--user" not in with_new_param.text


def test_send_message_creates_conversation_and_ai_reply(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Hier ist die KI-Antwort auf Ihre Frage.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Was ist der Stand der Akte?"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard/chat/")

    conversation = db_session.query(ChatConversation).first()
    assert conversation is not None
    messages = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Was ist der Stand der Akte?"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hier ist die KI-Antwort auf Ihre Frage."
    assert messages[1].blocked is False

    # Antwort tatsaechlich auf der Folgeseite sichtbar (echter Roundtrip,
    # kein reines Datenbank-Artefakt).
    follow_up = client.get(response.headers["location"])
    assert "Hier ist die KI-Antwort auf Ihre Frage." in follow_up.text


# ==========================================================================
# TEST B - DOKUMENT -> CHAT
# ==========================================================================


def test_document_attachment_flows_through_ocr_pseudonymization_to_chat(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetisches PDF anhängen -> Verarbeitung -> Dokument steht für den
    Chat zur Verfügung -> Canary-Beweis: der Mandantenname landet NIE im
    tatsächlich an die (gefakte) Cloud-KI gesendeten Payload."""
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Zusammenfassung erstellt.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    csrf = _csrf(client)

    marker_name = "Testfrau Mustermann"
    pdf_bytes = b"%PDF-1.4 Ein Test-PDF ohne echten Inhalt (kein OCR-Text-Layer)."

    response = client.post(
        "/dashboard/chat/send",
        data={
            "csrf_token": csrf,
            "conversation_id": "",
            "content": f"Bitte fasse das Dokument für Mandantin {marker_name} zusammen.",
        },
        files={"documents": ("beleg.pdf", pdf_bytes, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    conversation = db_session.query(ChatConversation).first()
    assert conversation is not None
    user_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="user")
        .first()
    )
    assert len(user_message.attached_documents) == 1
    document = user_message.attached_documents[0].document
    assert document.original_filename == "beleg.pdf"
    assert document.matter_id == conversation.matter_id

    # Canary: Originalname darf im tatsaechlich gesendeten Payload nicht
    # auftauchen.
    assert len(writer.received_payloads) == 1
    payload_json = writer.received_payloads[0].model_dump_json()
    assert marker_name not in payload_json


def _parse_sse_events(body: str) -> list[dict]:
    """Zerlegt einen `text/event-stream`-Antworttext in seine einzelnen
    JSON-Ereignisse (siehe app/web/chat_router.py::_sse_event - jedes
    Ereignis ist genau EINE "data: {json}"-Zeile, durch eine Leerzeile
    getrennt)."""
    import json

    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


def test_send_stream_creates_conversation_and_streams_ai_reply(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Streaming-Endpunkt (13.09., Streaming-Architekturentscheidung) - der
    hier konfigurierte Fake hat KEINE `write_stream`-Faehigkeit, liefert
    also den gepufferten Fallback (ein "start", ein "delta" mit dem
    vollstaendigen Text, ein "done") - deckt zugleich ab, dass der
    Datenbankzugriff waehrend des StreamingResponse-Generators (NACH
    Rueckgabe der Endpunkt-Funktion) tatsaechlich noch funktioniert (die
    `get_db`-Abhaengigkeit wird von FastAPI erst nach vollstaendigem
    Verbrauch des Response-Bodys geschlossen)."""
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Hier ist die gestreamte KI-Antwort.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Was ist der Stand der Akte?"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(response.text)
    assert events[0]["kind"] == "start"
    conversation_id = events[0]["conversation_id"]
    assert conversation_id

    delta_events = [e for e in events if e["kind"] == "delta"]
    done_events = [e for e in events if e["kind"] == "done"]
    assert "".join(e["text"] for e in delta_events) == "Hier ist die gestreamte KI-Antwort."
    assert len(done_events) == 1
    assert done_events[0]["content"] == "Hier ist die gestreamte KI-Antwort."
    assert done_events[0]["blocked"] is False
    assert done_events[0]["message_id"]

    # Echte Persistenz (nicht nur ein Response-Artefakt).
    conversation = db_session.query(ChatConversation).filter_by(id=conversation_id).first()
    assert conversation is not None
    messages = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    assert len(messages) == 2
    assert messages[1].content == "Hier ist die gestreamte KI-Antwort."


def test_send_stream_forwards_status_events_without_crashing(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 Performance-Feedback-Follow-up (17.09., siehe OPEN_ISSUES.md):
    `_finish_non_streaming_stream` liefert jetzt "status"-Zwischenereignisse
    (z. B. vor dem Claude-Aufruf, vor der Rekonstruktion) - der bestehende
    SSE-Router-Code nahm bisher an, JEDES Nicht-"delta"-Ereignis sei das
    abschliessende "done" (`assert message is not None`) und haette bei
    einem durchgereichten "status"-Ereignis (message=None) mit einem
    `AssertionError` abgestuerzt. Vor dem Fertigmelden real gefunden und in
    `_stream_chat_reply` (chat_router.py) mit einer eigenen
    `elif event.kind == "status"`-Verzweigung behoben - dieser Test haette
    VOR dem Fix mit HTTP 500 fehlgeschlagen."""
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Zusammenfassung des Dokuments.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Was steht in der Akte?"},
    )
    assert response.status_code == 200

    events = _parse_sse_events(response.text)
    status_events = [e for e in events if e["kind"] == "status"]
    # "claude" und "reconstruction" sind unbedingte Status-Ereignisse (siehe
    # DraftingService._STEP_STATUS_LABELS) - unabhaengig davon, ob ein
    # local_llm_provider konfiguriert ist.
    assert "Anfrage wird an Claude gesendet…" in [e["status"] for e in status_events]
    assert "Antwort wird zusammengesetzt…" in [e["status"] for e in status_events]
    # Status-Ereignisse tragen keinen Nachrichten-Content.
    for event in status_events:
        assert "message_id" not in event
    done_events = [e for e in events if e["kind"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["content"] == "Zusammenfassung des Dokuments."


def test_send_stream_empty_content_yields_error_event_without_persisting(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": csrf, "conversation_id": "", "content": ""},
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert len(events) == 1
    assert events[0]["kind"] == "done"
    assert events[0]["message_id"] is None
    assert events[0]["blocked"] is True
    assert db_session.query(ChatConversation).count() == 0


def test_rejects_disallowed_file_extension(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Analysiere das:"},
        files={"documents": ("script.exe", b"MZ", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(ChatConversation).count() == 0


# ==========================================================================
# TEST C - AUTHENTIFIZIERUNG
# ==========================================================================


def test_unauthenticated_cannot_view_chat(client: TestClient) -> None:
    response = client.get("/dashboard/chat", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_unauthenticated_cannot_send_message(client: TestClient) -> None:
    response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": "irrelevant", "conversation_id": "", "content": "Hallo"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_user_cannot_view_another_users_conversation(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    roles = seed_roles(db_session)
    owner = create_test_user(db_session, roles["anwalt"], "owner@kanzlei.test")

    from app.chat.service import ChatService

    chat_service = ChatService(tmp_path / "chat_uploads")
    conversation = chat_service.create_conversation(
        db_session, user=owner, matter_id=None, title="Privat", actor=owner.email
    )

    other = create_test_user(db_session, roles["anwalt"], "other@kanzlei.test")
    login(client, "other@kanzlei.test")

    response = client.get(f"/dashboard/chat/{conversation.id}")
    assert response.status_code == 403


def test_mitarbeiter_can_view_but_not_send(client: TestClient, db_session: Session) -> None:
    """Rollenpruefung (TEST E-verwandt): 'mitarbeiter' darf den Chat sehen
    (require_login), aber keine KI-Anfrage ausloesen (PERM_CLAUDE_CALL
    fehlt) - identische Rechte-Matrix wie beim Schriftsatz-Generator."""
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")

    get_response = client.get("/dashboard/chat")
    assert get_response.status_code == 200

    csrf = extract_csrf(get_response.text)
    post_response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Hallo"},
        follow_redirects=False,
    )
    assert post_response.status_code == 403


def test_send_message_without_csrf_token_is_rejected(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    response = client.post(
        "/dashboard/chat/send",
        data={"conversation_id": "", "content": "Hallo"},
        follow_redirects=False,
    )
    assert response.status_code == 422  # csrf_token ist Pflichtfeld (Form(...))


# ==========================================================================
# TEST D - PROVIDER NICHT KONFIGURIERT
# ==========================================================================


def test_provider_not_configured_shows_clear_message_no_secret_leak(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.web.service_factory import WritingProviderNotConfiguredError

    def _raise():
        raise WritingProviderNotConfiguredError("ANTHROPIC_API_KEY ist nicht konfiguriert.")

    login_as_admin(db_session, client)
    monkeypatch.setattr(chat_router_module, "get_drafting_service", _raise)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Hallo"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "nicht konfiguriert" in response.text
    # Kein Secret/Traceback im gerenderten HTML.
    assert "ANTHROPIC_API_KEY" not in response.text
    assert "Traceback" not in response.text

    conversation = db_session.query(ChatConversation).first()
    assistant_message = (
        db_session.query(ChatMessage).filter_by(conversation_id=conversation.id, role="assistant").first()
    )
    assert assistant_message.blocked is True


def test_chat_page_shows_banner_when_provider_not_configured(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.config.settings import Settings

    login_as_admin(db_session, client)
    fake_settings = Settings(
        chat_upload_storage_dir=str(tmp_path / "chat_uploads"), anthropic_api_key=None
    )
    monkeypatch.setattr(chat_router_module, "get_settings", lambda: fake_settings)

    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert "nicht konfiguriert" in response.text


def test_chat_page_shows_cloud_ki_verbunden_when_gateway_configured_without_dev_key(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Regression (Phase 3, §71): ein echter Produktivbetrieb hat NUR
    lexono_gateway_url gesetzt, keinen anthropic_api_key - die
    Statusanzeige prüfte bisher ausschließlich anthropic_api_key und
    zeigte in diesem (korrekt konfigurierten!) Fall fälschlich
    "nicht konfiguriert" an."""
    from app.config.settings import Settings

    login_as_admin(db_session, client)
    fake_settings = Settings(
        chat_upload_storage_dir=str(tmp_path / "chat_uploads"),
        anthropic_api_key=None,
        lexono_gateway_url="https://gateway.lexono.test",
        lexono_gateway_client_id="test-client",
        lexono_gateway_client_secret="lxg_secret_test",
    )
    monkeypatch.setattr(chat_router_module, "get_settings", lambda: fake_settings)
    # Seit Referenzbild (01.09.) steht der Status in der globalen Sidebar
    # (base.html), die bewusst NICHT chat_router_module.get_settings()
    # pro Anfrage neu aufruft, sondern denselben beim Start gesetzten
    # request.app.state.settings liest wie local_ai_status (siehe dortiger
    # Kommentar) - fuer diesen Test deshalb zusaetzlich direkt gesetzt.
    from app.main import app as main_app

    previous_settings = getattr(main_app.state, "settings", None)
    main_app.state.settings = fake_settings
    try:
        response = client.get("/dashboard/chat")
    finally:
        if previous_settings is not None:
            main_app.state.settings = previous_settings
        else:
            del main_app.state.settings

    assert response.status_code == 200
    # Seit Referenzbild (01.09.) steht der Status in der globalen Sidebar
    # statt im Chat-Panel-Header (chat.html hat keine eigene Kopie mehr).
    assert "Cloud-KI (Gateway)" in response.text
    assert 'sidebar__status-value--ok">Bereit' in response.text


def test_chat_page_shows_local_ai_checking_state_without_lifespan(
    client: TestClient, db_session: Session
) -> None:
    """Der TestClient hier durchläuft keinen Lifespan-Start (kein `with`-
    Block), `app.state.local_ai_status` ist also nie gesetzt - die Seite
    muss trotzdem sauber rendern (neutraler "wird geprüft"-Zustand statt
    eines AttributeError).

    `app.state` gehört zur EINEN, prozessweiten `app`-Instanz (siehe
    `from app.main import app`) - läuft an anderer Stelle in der Suite
    (z. B. tests/test_main_local_ai_startup_check.py) ein Test mit
    `with TestClient(app) as ...`, durchläuft dort der ECHTE Lifespan
    inkl. Local-AI-Startcheck und setzt `local_ai_status` dauerhaft für
    den Rest des Testprozesses - unabhängig von der Ausführungsreihenfolge
    wird das Attribut hier deshalb bewusst entfernt/zurückgesetzt statt
    seine Abwesenheit vorauszusetzen."""
    login_as_admin(db_session, client)
    from app.main import app as main_app

    had_attr = hasattr(main_app.state, "local_ai_status")
    if had_attr:
        previous_status = main_app.state.local_ai_status
        del main_app.state.local_ai_status
    try:
        response = client.get("/dashboard/chat")
    finally:
        if had_attr:
            main_app.state.local_ai_status = previous_status

    assert response.status_code == 200
    assert "Lokale KI" in response.text
    assert "wird geprüft…" in response.text


def test_chat_page_shows_local_ai_ready_state(
    client: TestClient, db_session: Session
) -> None:
    from app.local_ai.setup_orchestrator import LocalAiState, LocalAiStatus
    from app.main import app as main_app

    login_as_admin(db_session, client)
    main_app.state.local_ai_status = LocalAiStatus(
        state=LocalAiState.READY, configured_model="qwen2.5:1.5b"
    )
    try:
        response = client.get("/dashboard/chat")
    finally:
        del main_app.state.local_ai_status

    assert response.status_code == 200
    assert "Lokale KI" in response.text
    assert 'sidebar__status-value--ok">Bereit' in response.text


def test_chat_page_shows_local_ai_disabled_state(
    client: TestClient, db_session: Session
) -> None:
    from app.local_ai.setup_orchestrator import LocalAiState, LocalAiStatus
    from app.main import app as main_app

    login_as_admin(db_session, client)
    main_app.state.local_ai_status = LocalAiStatus(state=LocalAiState.DISABLED, configured_model=None)
    try:
        response = client.get("/dashboard/chat")
    finally:
        del main_app.state.local_ai_status

    assert response.status_code == 200
    assert "Lokale KI" in response.text
    assert "deaktiviert" in response.text


def test_chat_page_shows_local_ai_unreachable_state_as_warning(
    client: TestClient, db_session: Session
) -> None:
    from app.local_ai.setup_orchestrator import LocalAiState, LocalAiStatus
    from app.main import app as main_app

    login_as_admin(db_session, client)
    main_app.state.local_ai_status = LocalAiStatus(
        state=LocalAiState.RUNTIME_UNREACHABLE, configured_model="qwen2.5:1.5b"
    )
    try:
        response = client.get("/dashboard/chat")
    finally:
        del main_app.state.local_ai_status

    assert response.status_code == 200
    assert "Lokale KI" in response.text
    assert "nicht erreichbar" in response.text


# ==========================================================================
# TEST E - BESTEHENDE FUNKTIONEN BLEIBEN ERREICHBAR
# ==========================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/inbox",
        "/dashboard/tools/schriftsatz",
        "/dashboard/monitoring",
        "/dashboard/clients",
        "/dashboard/account",
    ],
)
def test_existing_pages_remain_reachable(client: TestClient, db_session: Session, path: str) -> None:
    login_as_admin(db_session, client)
    response = client.get(path)
    assert response.status_code == 200, f"{path} nicht mehr erreichbar (Status {response.status_code})"


def test_chat_appears_as_first_flat_nav_item(client: TestClient, db_session: Session) -> None:
    """"Chat" ist bewusst ein einfacher flacher Sidebar-Link OHNE
    Aufklapp-Unterpunkte (Nutzerkorrektur, 01.09.: eine zwischenzeitliche
    Aenderung hatte die Unterhaltungshistorie vertikal unter "Chat" in
    die Sidebar eingeblendet, wodurch die Sidebar bei laengerer Historie
    hoeher als das Fenster wurde und gescrollt werden musste - das
    veraendert seitdem NIE die Sidebar-Hoehe)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert 'href="/dashboard/chat"' in response.text
    assert 'class="sidebar__group-summary sidebar__group-summary--flat' in response.text


def test_chat_conversations_shown_as_separate_column_next_to_sidebar(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (01.09., Nutzerkorrektur): die Unterhaltungshistorie
    erscheint als EIGENE SPALTE RECHTS NEBEN der Haupt-Sidebar (wie im
    ürsprünglichen, funktionierenden Design), NICHT als Unterpunkte in
    der Haupt-Sidebar selbst - mit korrekter Aktiv-Markierung der gerade
    geoeffneten Unterhaltung."""
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Antwort.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    csrf = _csrf(client)
    send_response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Testnachricht fuer Spalte."},
        follow_redirects=True,
    )

    assert "chat-conversations" in send_response.text
    assert "Testnachricht fuer Spalte." in send_response.text
    assert "chat-conversations__item--active" in send_response.text


def test_chat_history_column_is_collapsed_by_default(
    client: TestClient, db_session: Session
) -> None:
    """Zweite Nutzerkorrektur (01.09.): die Spalte darf im Normalzustand
    KEINEN Platz einnehmen - sie wird zwar weiterhin serverseitig
    gerendert (fuer den Flyout-Inhalt), aber .chat-shell traegt bei
    jedem frischen Seitenaufruf NIE die Klasse "chat-shell--history-open"
    (CSS blendet sie dadurch standardmaessig auf Breite 0 aus)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert 'class="chat-shell' in response.text
    assert "chat-shell--history-open" not in response.text


def test_chat_sidebar_link_has_flyout_toggle_hook(
    client: TestClient, db_session: Session
) -> None:
    """Der "Chat"-Link in der Haupt-Sidebar braucht das id-Attribut, an
    dem app_sidebar.js den Klick-Toggle fuer das Flyout anhaengt (siehe
    app/web/static/js/app_sidebar.js)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert 'id="sidebar-chat-link"' in response.text
    assert 'aria-expanded="false"' in response.text


def test_app_sidebar_js_wires_chat_history_flyout_toggle() -> None:
    """Statische Verdrahtungspruefung: das JS muss existieren, das Klicks
    auf #sidebar-chat-link abfaengt und "chat-shell--history-open"
    umschaltet - Regressionsschutz, falls die Datei versehentlich
    ueberschrieben/gekuerzt wird."""
    js_path = Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "js" / "app_sidebar.js"
    content = js_path.read_text(encoding="utf-8")
    assert "sidebar-chat-link" in content
    assert "chat-shell--history-open" in content
    assert "preventDefault" in content


def test_composer_submit_handler_never_disables_the_textarea() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): der Composer setzte im
    "submit"-Event-Handler `textarea.disabled = true`, um Tipp-Eingaben
    waehrend des Ladezustands zu verhindern. Per HTML-Spezifikation werden
    DEAKTIVIERTE Formularfelder aber von der eigentlichen Formular-
    uebermittlung ausgeschlossen - der Server erhielt dadurch IMMER einen
    leeren `content`-Wert, unabhaengig vom sichtbar eingegebenen Text
    ("Bitte eine Nachricht eingeben." trotz Eingabe). Real reproduziert:
    Chat ohne Dokumentanhang lieferte nie eine Antwort, mit Anhang blieb
    es unbemerkt (siehe app/web/chat_router.py::send_message - dort
    ersetzt ein serverseitiger Default-Text den leeren Inhalt NUR, wenn
    ein Dokument angehaengt ist).

    Statischer Verdrahtungsschutz statt eines Browser-Tests (kein JS-
    Test-Harness vorhanden, siehe test_app_sidebar_js_wires_...
    unmittelbar oberhalb fuer dasselbe Muster) - prueft direkt am
    Template-Quelltext, dass die Textarea im Ladezustand NICHT mehr per
    `disabled` deaktiviert wird, sondern per `readOnly` (verhindert weitere
    Eingaben genauso, wird aber weiterhin mituebermittelt).

    Seit der Streaming-Einfuehrung (13.09., siehe DECISIONS.md) ruft der
    "submit"-Handler diese Logik ueber `setLoadingState()` auf (gemeinsam
    genutzt vom Streaming- UND vom klassischen Fallback-Pfad) statt sie
    selbst zu enthalten - dieselbe Garantie, nur an EINER Stelle statt
    dupliziert."""
    chat_html_path = (
        Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "chat.html"
    )
    content = chat_html_path.read_text(encoding="utf-8")
    handler_start = content.index("function setLoadingState()")
    handler_end = content.index("}", content.index("function setLoadingState()"))
    loading_state_fn = content[handler_start:handler_end]
    assert "textarea.disabled = true" not in loading_state_fn
    assert "textarea.readOnly = true" in loading_state_fn

    submit_handler_start = content.index('composer.addEventListener("submit"')
    submit_handler_end = content.index("});", submit_handler_start)
    submit_handler = content[submit_handler_start:submit_handler_end]
    assert "textarea.disabled = true" not in submit_handler
    assert "setLoadingState()" in submit_handler


# ==========================================================================
# Phase 2 (visuelle/UX-Fertigstellung): mehrere Nachrichten,
# Konversationswechsel, Dokumentstatus-Anzeige, Kopfbereich
# ==========================================================================


def test_multiple_messages_appear_in_correct_order(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Antwort eins.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    # Bewusst EIN Wort pro Nachricht statt "Erste Frage"/"Zweite Frage" -
    # zwei aufeinanderfolgende grossgeschriebene Woerter wuerden von der
    # bestehenden PII-Heuristik als moeglicher unerkannter Name geblockt
    # (siehe app/privacy/security_check.py, an mehreren Stellen dieser
    # Session bereits beobachtet) und damit die KI-Antwort selbst blockieren -
    # fuer DIESEN Test irrelevant, es geht nur um Nachrichten-Reihenfolge.
    csrf = _csrf(client)
    first = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Erstfrage"},
        follow_redirects=False,
    )
    conversation_url = first.headers["location"]

    writer.response_text = "Antwort zwei."
    page = client.get(conversation_url)
    csrf2 = extract_csrf(page.text)
    client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf2, "conversation_id": conversation_url.rsplit("/", 1)[-1], "content": "Zweitfrage"},
        follow_redirects=False,
    )

    final_page = client.get(conversation_url)
    text = final_page.text
    assert text.index("Erstfrage") < text.index("Antwort eins.") < text.index("Zweitfrage") < text.index(
        "Antwort zwei."
    )


def test_switching_between_conversations_shows_correct_messages(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service()
    )
    csrf = _csrf(client)
    r1 = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Unterhaltung Eins Inhalt"},
        follow_redirects=False,
    )
    csrf = _csrf(client)
    r2 = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Unterhaltung Zwei Inhalt"},
        follow_redirects=False,
    )

    def _messages_panel(html: str) -> str:
        start = html.index('id="chat-messages"')
        end = html.index('<form method="post" action="/dashboard/chat/send"')
        return html[start:end]

    page1 = _messages_panel(client.get(r1.headers["location"]).text)
    assert "Unterhaltung Eins Inhalt" in page1
    assert "Unterhaltung Zwei Inhalt" not in page1

    full_page2 = client.get(r2.headers["location"]).text
    page2 = _messages_panel(full_page2)
    assert "Unterhaltung Zwei Inhalt" in page2
    assert "Unterhaltung Eins Inhalt" not in page2

    # Beide Unterhaltungen bleiben in der Konversationsliste sichtbar
    # (Auftrag Abschnitt 6) - unabhaengig davon, welche gerade aktiv ist.
    assert 'href="' + r1.headers["location"] + '"' in full_page2


def test_document_status_badge_shows_processed_for_text_pdf(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Dokument mit Text-Layer wird direkt extrahiert (ocr_status
    'not_needed') - die Chat-UI muss das als 'Verarbeitet' anzeigen, nicht
    als technischen Rohstatus."""
    login_as_admin(db_session, client)
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service()
    )
    csrf = _csrf(client)

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Echter Text-Layer fuer den Status-Test.")
    pdf_bytes = doc.tobytes()
    doc.close()

    response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Bitte pruefen."},
        files={"documents": ("textdokument.pdf", pdf_bytes, "application/pdf")},
        follow_redirects=True,
    )
    assert "Verarbeitet" in response.text
    assert "textdokument.pdf" in response.text


def test_chat_page_shows_ai_status_in_sidebar(client: TestClient, db_session: Session) -> None:
    """Seit dem Referenzbild-Redesign (01.09.) steht der KI-Status nicht
    mehr im Chat-Panel-Header, sondern durchgaengig in der globalen
    Sidebar (base.html) - auch auf der Chat-Seite selbst."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert "sidebar__status-panel" in response.text
    assert "Cloud-KI (Gateway)" in response.text


def test_chat_page_includes_thinking_indicator_for_ai_loading_state(
    client: TestClient, db_session: Session
) -> None:
    """KI-Ladezustand (Masterprompt-Vorgabe): eine echte lokale+Cloud-KI-
    Antwort kann 15-30+ Sekunden dauern (siehe ARCHITECTURE.md §71 fuer
    reale gemessene Zeiten) - ein rein abgedunkelter Sendebutton reicht
    dafuer nicht als Feedback. Prueft nur, dass die client-seitige Logik
    (Sprechblase mit Puls-Animation, per JS beim Absenden eingefuegt) im
    ausgelieferten HTML vorhanden ist - das tatsaechliche Verhalten im
    Browser ist nur per Browser-Tool/menschlicher Pruefung verifizierbar."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert "chat-message--thinking" in response.text
    assert "chat-thinking-indicator" in response.text


def test_chat_page_wires_up_streaming_status_events_into_the_thinking_bubble(
    client: TestClient, db_session: Session
) -> None:
    """P1 Performance-Feedback-Follow-up (17.09., siehe OPEN_ISSUES.md):
    fuer JEDEN Aufruf mit Dokument-/Aktenkontext lieferte die "denkt
    nach"-Sprechblase bisher 80-105+ Sekunden lang KEIN sichtbares
    Lebenszeichen - `DraftingService`/`ChatService` senden jetzt ein neues
    SSE-`kind="status"`-Ereignis (fester, inhaltsfreier Fortschritts-Text)
    VOR dem abschliessenden Ergebnis. Prueft nur, dass die client-seitige
    Anbindung (Status-Slot im Markup + JS-Handler dafuer) im ausgelieferten
    HTML vorhanden ist - identisches Prinzip wie der bestehende Test fuer
    die Sprechblase selbst."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert "chat-thinking-indicator__status" in response.text
    assert 'payload.kind === "status"' in response.text
    assert "updateThinkingStatus" in response.text


def test_chat_empty_state_quick_actions_have_distinct_accent_colors(
    client: TestClient, db_session: Session
) -> None:
    """Akzentfarben der vier Chat-Schnellaktionen (aktualisiert 14.09.,
    echter Screenshot-Abgleich der laufenden Anwendung gegen
    assets/ux-ui/05_chat_startseite.png im Rahmen des Overnight-UI/UX-
    Audits): die Referenz zeigt EXPLIZIT vier unterschiedlich (inkl.
    Gruen) getoente Karten statt einer neutralen vierten Karte - die
    fruehere "bewusst ohne Gruen"-Entscheidung (01.09.) galt fuer eine
    inzwischen durch die aktuelle Referenz ueberholte Kartenvariante
    (kompakte Pillen statt der jetzt wieder vertikalen, farbig getoenten
    Karten) und wird hiermit korrigiert - Gruen bleibt weiterhin auch
    die Markenfarbe (Logo/Sendebutton/aktive Chat-Navigation), wird hier
    aber zusaetzlich fuer GENAU eine von vier gleichwertigen
    Schnellaktionen verwendet, identisch zur Referenz."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert "chat-quick-action__icon--green" in response.text
    assert "chat-quick-action__icon--blue" in response.text
    assert "chat-quick-action__icon--purple" in response.text
    assert "chat-quick-action__icon--orange" in response.text


# --- Dokument-Workspace (Masterprompt V2, Task #62) ---------------------


def test_document_workspace_shows_highlighted_pii_and_context_panel(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Voller Weg: Dokument mit erkennbarer PII (E-Mail) hochladen ->
    Anhangs-Chip im Chat ist ein Link -> Dokument-Workspace zeigt die
    E-Mail hervorgehoben UND in der rechten Kontextleiste."""
    login_as_admin(db_session, client)
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service()
    )
    csrf = _csrf(client)

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Bitte kontaktieren Sie mandant@beispielkanzlei.de zeitnah.")
    pdf_bytes = doc.tobytes()
    doc.close()

    upload_response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Bitte pruefen."},
        files={"documents": ("mandantenbrief.pdf", pdf_bytes, "application/pdf")},
        follow_redirects=True,
    )
    assert "chat-attachment-chip--link" in upload_response.text

    conversation = db_session.query(ChatConversation).first()
    document = db_session.query(chat_router_module.Document).first()
    assert conversation is not None
    assert document is not None

    workspace_response = client.get(
        f"/dashboard/chat/{conversation.id}/document/{document.id}"
    )
    assert workspace_response.status_code == 200
    assert "chat-shell--document-view" in workspace_response.text
    assert "mandantenbrief.pdf" in workspace_response.text
    # "Herunterladen" (16.09., UI/UX-Sweep) - derselbe Download-Endpunkt
    # wie in matter_document.html, siehe tests/test_web_matters.py fuer
    # die eigentlichen Download-Verhaltenstests.
    assert (
        f"/dashboard/matters/{conversation.matter_id}/document/{document.id}/download"
        in workspace_response.text
    )
    assert "mandant@beispielkanzlei.de" in workspace_response.text
    assert 'pii-highlight--email' in workspace_response.text
    assert "Pseudonymisierung" in workspace_response.text
    assert "Erkannte Mandantendaten" in workspace_response.text
    # KI-Analyse-Transparenzkarte (Phase 5, 13.09.).
    assert "chat-transparency-card" in workspace_response.text
    assert "Lokal analysiert." in workspace_response.text

    # Schnellaktionen im Dokument-Workspace (Referenzbild "Antwort
    # entwerfen"/"Fristen & Risiken pruefen"/"Zusammenfassung erstellen")
    # nutzen denselben Prefill-Mechanismus wie die leeren Chat-Quick-Actions
    # und referenzieren den echten Dateinamen - keine neue Sende-Logik.
    assert "Schnellaktionen" in workspace_response.text
    assert "Antwort entwerfen" in workspace_response.text
    assert "Fristen &amp; Risiken pruefen" in workspace_response.text
    assert "Zusammenfassung erstellen" in workspace_response.text
    assert "mandantenbrief.pdf" in workspace_response.text.split("Schnellaktionen", 1)[1].split(
        "Erkannte Mandantendaten", 1
    )[0]


def test_document_workspace_rejects_document_from_other_conversation(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service()
    )
    csrf = _csrf(client)

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Vertraulicher Inhalt.")
    pdf_bytes = doc.tobytes()
    doc.close()

    client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Erste Unterhaltung."},
        files={"documents": ("erste.pdf", pdf_bytes, "application/pdf")},
        follow_redirects=True,
    )
    csrf2 = _csrf(client)
    client.post(
        "/dashboard/chat/send",
        data={"csrf_token": csrf2, "conversation_id": "", "content": "Zweite Unterhaltung ohne Dokument."},
        follow_redirects=True,
    )

    conversations = db_session.query(ChatConversation).order_by(ChatConversation.created_at).all()
    document = db_session.query(chat_router_module.Document).first()
    assert len(conversations) == 2
    assert document is not None
    other_conversation = conversations[1]

    response = client.get(
        f"/dashboard/chat/{other_conversation.id}/document/{document.id}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "nicht%20gefunden" in response.headers["location"]


# ==========================================================================
# UI/UX-Ueberarbeitung, Phase 4 (13.09.): Breadcrumb, Aktenbezug, Kopieren/
# Vollstaendigen-Editor-Link, echte Quellen-Karte.
# ==========================================================================


def _active_conversation(db: Session, current_user_email: str) -> ChatConversation:
    from app.models import User

    user = db.query(User).filter_by(email=current_user_email).first()
    client_row = Client(name="Max Mustermann", client_number="K-1")
    matter = Matter(client=client_row, title="Erste Akte", reference_number="A-1")
    db.add_all([client_row, matter])
    db.commit()
    conversation = ChatConversation(matter_id=matter.id, user_id=user.id, title="Testchat")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def test_chat_shows_breadcrumb_with_matter_title(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "chat-breadcrumb" in response.text
    assert conversation.matter.title in response.text
    # ECHTER FUND (17.09., Owner-Direktive §5/§7 "Chat bleibt die zentrale
    # Arbeitsoberflaeche ... weiterarbeiten"): der Aktenname im Breadcrumb
    # war reiner Text ohne Verweis auf die laengst existierende
    # Aktendetailseite (Dokumente/Aufgaben & Fristen/Kommunikation) - jetzt
    # ein echter Link.
    assert f'href="/dashboard/matters/{conversation.matter_id}"' in response.text


def test_chat_header_offers_to_relink_conversation_to_another_matter(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    other_client = Client(name="Andere Mandantin", client_number="K-2")
    other_matter = Matter(client=other_client, title="Zweite Akte", reference_number="A-2")
    db_session.add_all([other_client, other_matter])
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "chat-matter-link" in response.text
    assert f'value="{other_matter.id}"' in response.text
    # Die AKTUELLE Akte darf nicht als Ziel zur Auswahl stehen.
    assert f'value="{conversation.matter_id}"' not in response.text


def test_link_matter_endpoint_updates_conversation(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    other_client = Client(name="Andere Mandantin", client_number="K-3")
    other_matter = Matter(client=other_client, title="Zielakte", reference_number="A-3")
    db_session.add_all([other_client, other_matter])
    db_session.commit()
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{conversation.id}/link-matter",
        data={"csrf_token": csrf, "matter_id": other_matter.id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(conversation)
    assert conversation.matter_id == other_matter.id


def test_link_matter_endpoint_rejects_wrong_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (14.09., Sicherheits-Review): dieser Endpunkt nutzte
    bisher `Depends(require_login)` statt `Depends(require_role())` -
    dadurch wurde ein mitgesendeter `csrf_token` NIE tatsächlich mit dem
    der Sitzung abgeglichen (nur `require_role()` erzwingt das, siehe
    app/auth/permissions.py). Ein falscher/erratener Token MUSS jetzt
    abgelehnt werden - vorher hätte diese Anfrage die Akte trotzdem
    umgehängt."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    other_client = Client(name="Andere Mandantin", client_number="K-CSRF")
    other_matter = Matter(client=other_client, title="Zielakte", reference_number="A-CSRF")
    db_session.add_all([other_client, other_matter])
    db_session.commit()

    response = client.post(
        f"/dashboard/chat/{conversation.id}/link-matter",
        data={"csrf_token": "falscher-erratener-token", "matter_id": other_matter.id},
        follow_redirects=False,
    )

    assert response.status_code == 403
    db_session.refresh(conversation)
    assert conversation.matter_id != other_matter.id


def test_link_matter_endpoint_writes_audit_event(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    other_client = Client(name="Andere Mandantin", client_number="K-4")
    other_matter = Matter(client=other_client, title="Zielakte", reference_number="A-4")
    db_session.add_all([other_client, other_matter])
    db_session.commit()
    csrf = _csrf(client)

    client.post(
        f"/dashboard/chat/{conversation.id}/link-matter",
        data={"csrf_token": csrf, "matter_id": other_matter.id},
    )

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="ChatConversation", entity_id=conversation.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "chat_relinked_to_matter"


def test_link_matter_endpoint_rejects_another_users_conversation(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    roles = seed_roles(db_session)
    other_user = create_test_user(db_session, roles["anwalt"], "andere@kanzlei.test")
    other_client_row = Client(name="X", client_number="K-5")
    matter = Matter(client=other_client_row, title="Fremde Akte", reference_number="A-5")
    db_session.add_all([other_client_row, matter])
    db_session.commit()
    foreign_conversation = ChatConversation(matter_id=matter.id, user_id=other_user.id, title="Fremd")
    db_session.add(foreign_conversation)
    db_session.commit()

    target_client = Client(name="Y", client_number="K-6")
    target_matter = Matter(client=target_client, title="Zielakte", reference_number="A-6")
    db_session.add_all([target_client, target_matter])
    db_session.commit()
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{foreign_conversation.id}/link-matter",
        data={"csrf_token": csrf, "matter_id": target_matter.id},
    )

    assert response.status_code == 403
    db_session.refresh(foreign_conversation)
    assert foreign_conversation.matter_id == matter.id


# --- Unterhaltung loeschen (20.09., Owner-Direktive "WORKSTREAM A — CHATS
# LOESCHBAR"). Voller Lifecycle: erstellen -> verwenden -> loeschen ->
# Liste aktualisiert -> nicht mehr oeffenbar; IDOR-Schutz; verknuepfte
# Draft/Document-Zeilen bleiben unberuehrt. ---


def test_delete_conversation_removes_it_and_redirects_to_chat_overview(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{conversation_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/chat"
    assert db_session.get(ChatConversation, conversation_id) is None


def test_deleted_conversation_disappears_from_history_list_and_cannot_be_reopened(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)

    client.post(f"/dashboard/chat/{conversation_id}/delete", data={"csrf_token": csrf})

    overview = client.get("/dashboard/chat")
    reopen_attempt = client.get(f"/dashboard/chat/{conversation_id}")

    assert "Testchat" not in overview.text
    assert reopen_attempt.status_code == 404


def test_delete_conversation_cascades_messages_but_keeps_matter_and_documents(
    client: TestClient, db_session: Session
) -> None:
    """Direktive: "Wenn ein Chat einer Akte zugeordnet ist: NICHT die Akte
    löschen. NICHT automatisch Dokumente löschen." - `ChatMessage`/
    `ChatMessageDocument` cascaden (Arbeitsverlauf), `Matter` und
    `Document` bleiben vollstaendig unberuehrt."""
    from app.models import ChatMessageDocument, Document

    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    matter_id = conversation.matter_id
    document = Document(
        matter_id=matter_id, file_path="/tmp/anhang.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    message = ChatMessage(conversation_id=conversation.id, role="user", content="Hallo")
    db_session.add(message)
    db_session.commit()
    link = ChatMessageDocument(message_id=message.id, document_id=document.id)
    db_session.add(link)
    db_session.commit()
    message_id = message.id
    link_id = link.id
    document_id = document.id
    csrf = _csrf(client)

    client.post(f"/dashboard/chat/{conversation.id}/delete", data={"csrf_token": csrf})

    assert db_session.get(ChatMessage, message_id) is None
    assert db_session.get(ChatMessageDocument, link_id) is None
    assert db_session.get(Document, document_id) is not None
    assert db_session.get(Matter, matter_id) is not None


def test_delete_conversation_keeps_the_draft_it_produced(
    client: TestClient, db_session: Session
) -> None:
    """Ein aus einer Unterhaltung erzeugter Schriftsatz-Entwurf ist das
    eigentliche Arbeitsergebnis - er darf beim Loeschen des Chats selbst
    NICHT verloren gehen."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    draft = Draft(matter_id=conversation.matter_id, content="Entwurfsinhalt")
    db_session.add(draft)
    db_session.commit()
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant", content="Antwort", draft_id=draft.id
    )
    db_session.add(message)
    db_session.commit()
    draft_id = draft.id
    csrf = _csrf(client)

    client.post(f"/dashboard/chat/{conversation.id}/delete", data={"csrf_token": csrf})

    assert db_session.get(Draft, draft_id) is not None


def test_delete_conversation_requires_csrf(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id

    response = client.post(
        f"/dashboard/chat/{conversation_id}/delete",
        data={"csrf_token": "falscher-erratener-token"},
    )

    assert response.status_code == 403
    assert db_session.get(ChatConversation, conversation_id) is not None


def test_delete_conversation_rejects_another_users_conversation(
    client: TestClient, db_session: Session
) -> None:
    """IDOR-Schutz: eine gueltige Konversations-ID darf nicht ueber ein
    fremdes Nutzerkonto geloescht werden - identischer Schutz wie
    `link-matter` (`_require_own_conversation`)."""
    login_as_admin(db_session, client)
    roles = seed_roles(db_session)
    other_user = create_test_user(db_session, roles["anwalt"], "andere@kanzlei.test")
    other_client_row = Client(name="X", client_number="K-DEL")
    matter = Matter(client=other_client_row, title="Fremde Akte", reference_number="A-DEL")
    db_session.add_all([other_client_row, matter])
    db_session.commit()
    foreign_conversation = ChatConversation(matter_id=matter.id, user_id=other_user.id, title="Fremd")
    db_session.add(foreign_conversation)
    db_session.commit()
    foreign_id = foreign_conversation.id
    csrf = _csrf(client)

    response = client.post(f"/dashboard/chat/{foreign_id}/delete", data={"csrf_token": csrf})

    assert response.status_code == 403
    assert db_session.get(ChatConversation, foreign_id) is not None


def test_delete_conversation_writes_an_audit_event(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)

    client.post(f"/dashboard/chat/{conversation_id}/delete", data={"csrf_token": csrf})

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="ChatConversation", entity_id=conversation_id, event_type="chat_conversation_deleted")
        .first()
    )
    assert event is not None


def test_deleting_an_already_deleted_conversation_returns_404(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)
    client.post(f"/dashboard/chat/{conversation_id}/delete", data={"csrf_token": csrf})

    second_attempt = client.post(
        f"/dashboard/chat/{conversation_id}/delete", data={"csrf_token": csrf}
    )

    assert second_attempt.status_code == 404


def test_chat_history_list_shows_delete_button_for_each_conversation(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get("/dashboard/chat")

    assert f'action="/dashboard/chat/{conversation.id}/delete"' in response.text


def test_assistant_message_shows_copy_button_and_timestamp(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    message = ChatMessage(conversation_id=conversation.id, role="assistant", content="Eine echte Antwort.")
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "chat-message__copy-btn" in response.text
    assert "chat-message__timestamp" in response.text


def test_assistant_message_with_draft_shows_open_editor_link(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    draft = Draft(matter_id=conversation.matter_id, content="Entwurfstext", version=1, status="draft")
    db_session.add(draft)
    db_session.commit()
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant", content="Hier ist der Entwurf.", draft_id=draft.id
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert f'href="/dashboard/drafts/{draft.id}"' in response.text
    assert "Vollständigen Editor öffnen" in response.text


def test_assistant_message_shows_real_sources_used_for_its_draft(
    client: TestClient, db_session: Session
) -> None:
    """Die Quellen-Karte darf NUR bereits real persistierte
    DraftSourceLink-Zeilen anzeigen - niemals erfunden (siehe
    app/web/chat_router.py::_gather_message_sources)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    draft = Draft(matter_id=conversation.matter_id, content="Entwurfstext", version=1, status="draft")
    db_session.add(draft)
    db_session.commit()
    source = Source(title="§ 558 BGB", source_type="Gesetz", reference="BGB", url="https://example.test/558")
    db_session.add(source)
    db_session.commit()
    db_session.add(DraftSourceLink(draft_id=draft.id, source_id=source.id))
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant", content="Antwort mit Quelle.", draft_id=draft.id
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "chat-sources-card" in response.text
    assert "§ 558 BGB" in response.text
    assert 'href="https://example.test/558"' in response.text


def test_message_without_sources_shows_no_sources_card(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    message = ChatMessage(conversation_id=conversation.id, role="assistant", content="Antwort ohne Quelle.")
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "chat-sources-card" not in response.text


# --- Chat MIT einer bestimmten Akte starten (14.09., Gold-Workflow) -------
# ECHTER FUND beim UI-Durchgang: die Aktendetailseite musste woertlich
# einraeumen, eine Unterhaltung "direkt dieser Akte zuzuordnen" sei "noch
# nicht moeglich" - ein neuer Chat legte immer eine eigene, leere
# Schnellakte an (`matter_id=None` fest verdrahtet). Der Anwalt stand damit
# in einer vollstaendig gefuellten Akte (Dokumente, Frist, Kommunikation)
# und konnte genau mit dieser NICHT weiterarbeiten. Die Faehigkeit gab es
# im Service laengst - sie war nur nie mit der Weboberflaeche verbunden.


def _matter_for_chat(db_session: Session, title: str = "Einspruch 2025") -> Matter:
    client = Client(name="Kontextmandantin", client_number="K-CTX")
    matter = Matter(client=client, title=title, reference_number="CTX-1")
    db_session.add_all([client, matter])
    db_session.commit()
    return matter


def test_chat_page_accepts_a_matter_to_start_with(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter_for_chat(db_session)
    login_as_admin(db_session, client)

    response = client.get(f"/dashboard/chat?new=1&matter={matter.id}")

    assert response.status_code == 200
    # Die Akte wird im Formular vorgemerkt - der Datensatz entsteht weiterhin
    # erst mit der ersten Nachricht.
    assert matter.id in response.text


def test_chat_page_rejects_unknown_matter(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat?new=1&matter=gibt-es-nicht")
    assert response.status_code == 404


def test_new_conversation_is_bound_to_the_selected_matter(
    client: TestClient, db_session: Session
) -> None:
    """Kern des Fixes: die neue Unterhaltung haengt an der GEWAEHLTEN Akte -
    nicht an einer frisch angelegten leeren Schnellakte."""
    matter = _matter_for_chat(db_session)
    login_as_admin(db_session, client)
    matters_before = db_session.query(Matter).count()

    response = client.post(
        "/dashboard/chat/send",
        data={
            "content": "Bitte den Steuerbescheid dieser Akte zusammenfassen.",
            "conversation_id": "",
            "matter_id": matter.id,
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    conversation = db_session.query(ChatConversation).one()
    assert conversation.matter_id == matter.id
    # Es darf KEINE zusaetzliche Schnellakte entstanden sein.
    assert db_session.query(Matter).count() == matters_before


def test_new_conversation_without_matter_keeps_previous_behaviour(
    client: TestClient, db_session: Session
) -> None:
    """Rueckwaertskompatibilitaet: ohne Akte bleibt es beim bisherigen
    Verhalten (automatische Schnellakte)."""
    login_as_admin(db_session, client)

    client.post(
        "/dashboard/chat/send",
        data={
            "content": "Allgemeine Frage",
            "conversation_id": "",
            "matter_id": "",
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )

    conversation = db_session.query(ChatConversation).one()
    assert conversation.matter_id is not None


# ==========================================================================
# "Zusammenfassen"/"Antworten" aus dem Posteingang (16.09., UI/UX-Sweep -
# Referenz `04_posteingang_nachricht_detail.png`). Beide starten eine neue
# Chat-Unterhaltung fuer die Akte der Nachricht ueber denselben, bereits
# getesteten `ChatService`/`DraftingService`-Weg wie /dashboard/chat/send -
# hier wird nur die NEUE Einstiegsroute (`/dashboard/chat/from-message/
# {id}`) selbst getestet, nicht die dahinterliegende Pipeline erneut.
# ==========================================================================

from app.models import Message  # noqa: E402


def _matter_with_message(
    db_session: Session, *, matter_id: bool = True, body: str = "Testinhalt der Nachricht."
) -> tuple[Matter, Message]:
    mandant = Client(name="Posteingang-Testmandant", client_number="K-MSG")
    matter = Matter(client=mandant, title="Akte aus dem Posteingang", reference_number="A-MSG")
    db_session.add_all([mandant, matter])
    db_session.flush()
    message = Message(
        matter_id=matter.id if matter_id else None,
        direction="inbound",
        sender="mandant@example-testdomain.invalid",
        subject="Testbetreff",
        body_text=body,
    )
    db_session.add(message)
    db_session.commit()
    return matter, message


def test_summarize_action_starts_conversation_bound_to_messages_matter(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    matter, message = _matter_with_message(db_session)
    writer = FakeClaudeWritingProvider("Kurze Zusammenfassung.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )

    response = client.post(
        f"/dashboard/chat/from-message/{message.id}",
        data={"csrf_token": _csrf(client), "action": "summarize"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    conversation = db_session.query(ChatConversation).one()
    assert conversation.matter_id == matter.id
    assert response.headers["location"] == f"/dashboard/chat/{conversation.id}"
    user_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="user")
        .one()
    )
    assert "Fasse diese eingehende Nachricht zusammen" in user_message.content
    assert message.subject in user_message.content
    assert message.body_text in user_message.content
    ai_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="assistant")
        .one()
    )
    # Wie jede Chat-Antwort erzeugt auch "Zusammenfassen" einen `Draft`-
    # Datensatz (DraftingService.create_draft persistiert das Ergebnis
    # IMMER, unabhaengig vom `purpose` - reine Chat-Antworten sind keine
    # Ausnahme, siehe app/drafting/service.py). Der eigentliche
    # inhaltliche Unterschied zu "Antworten" liegt im `purpose`
    # (_PURPOSE_CHAT vs. _PURPOSE_DRAFT), nicht darin, OB ein Draft
    # entsteht.
    assert ai_message.draft_id is not None
    # ECHTER FUND (17.09., Overnight-Direktive §6/§7 "Workflows
    # verbinden"): `Draft.message_id` unterstuetzte dieses Feld bereits
    # laenger (app/drafting/versioning.py), wurde aber von KEINEM Aufrufer
    # tatsaechlich gesetzt - das "Original links"-Panel in
    # draft_detail.html konnte dadurch strukturell nie eine
    # Ursprungsnachricht anzeigen. Jetzt verdrahtet.
    draft = db_session.query(Draft).filter_by(id=ai_message.draft_id).one()
    assert draft.message_id == message.id


def test_draft_reply_action_produces_a_real_editable_draft_not_a_send(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zentrale Anforderung der Owner-Klarstellung (16.09.): "Antworten"
    erzeugt einen echten, im Editor pruef-/bearbeitbaren ENTWURF - es gibt
    in dieser Pipeline schlicht keine Versandfunktion, die aufgerufen
    werden koennte."""
    login_as_admin(db_session, client)
    matter, message = _matter_with_message(db_session)
    writer = FakeClaudeWritingProvider("Sehr geehrte Damen und Herren, ...")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )

    response = client.post(
        f"/dashboard/chat/from-message/{message.id}",
        data={"csrf_token": _csrf(client), "action": "draft_reply"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    conversation = db_session.query(ChatConversation).one()
    user_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="user")
        .one()
    )
    assert f'Antwort an {message.sender}' in user_message.content
    ai_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="assistant")
        .one()
    )
    assert ai_message.draft_id is not None
    draft = db_session.query(Draft).filter_by(id=ai_message.draft_id).one()
    assert draft.matter_id == matter.id
    assert draft.status == "draft"
    assert draft.message_id == message.id


def test_action_on_message_without_matter_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    """Aktenisolation: ohne zugeordnete Akte darf keine Chat-Unterhaltung
    entstehen - die UI zeigt die Buttons dafuer gar nicht erst an (siehe
    partials/message_detail.html), dieser Test sichert den eigentlich
    schuetzenden Server-Check gegen einen manipulierten Request ab."""
    login_as_admin(db_session, client)
    _matter, message = _matter_with_message(db_session, matter_id=False)

    response = client.post(
        f"/dashboard/chat/from-message/{message.id}",
        data={"csrf_token": _csrf(client), "action": "summarize"},
    )

    assert response.status_code == 400
    assert db_session.query(ChatConversation).count() == 0


def test_unknown_action_is_rejected(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    _matter, message = _matter_with_message(db_session)

    response = client.post(
        f"/dashboard/chat/from-message/{message.id}",
        data={"csrf_token": _csrf(client), "action": "delete-everything"},
    )

    assert response.status_code == 400
    assert db_session.query(ChatConversation).count() == 0


def test_action_on_unknown_message_returns_404(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)

    response = client.post(
        "/dashboard/chat/from-message/does-not-exist",
        data={"csrf_token": _csrf(client), "action": "summarize"},
    )

    assert response.status_code == 404


# ==========================================================================
# KI-Aktionen aus der Akte-Dokumentansicht (16.09., UI/UX-Sweep - Referenz
# `28_dokument_vorschau_export.png`). Identisches Muster/dieselbe Test-
# Abdeckung wie die Posteingang-Aktionen oben, nur mit einem `Document`
# statt einer `Message` als Quelle.
# ==========================================================================

from app.models import Document  # noqa: E402


def _matter_with_document(db_session: Session, *, filename: str = "Anhang.pdf") -> tuple[Matter, Document]:
    mandant = Client(name="Dokument-Testmandant", client_number="K-DOC")
    matter = Matter(client=mandant, title="Akte mit Dokument", reference_number="A-DOC")
    db_session.add_all([mandant, matter])
    db_session.flush()
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/anhang.pdf",
        original_filename=filename,
        extracted_text="Testinhalt des Dokuments.",
    )
    db_session.add(document)
    db_session.commit()
    return matter, document


def test_document_analyze_action_starts_conversation_bound_to_matter(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    matter, document = _matter_with_document(db_session)
    writer = FakeClaudeWritingProvider("Analyse-Ergebnis.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )

    response = client.post(
        f"/dashboard/chat/from-document/{document.id}",
        data={"csrf_token": _csrf(client), "action": "analyze"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    conversation = db_session.query(ChatConversation).one()
    assert conversation.matter_id == matter.id
    assert response.headers["location"] == f"/dashboard/chat/{conversation.id}"
    user_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="user")
        .one()
    )
    assert document.original_filename in user_message.content
    assert "analysiere" in user_message.content.lower()


def test_document_draft_reply_action_triggers_a_real_draft(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    matter, document = _matter_with_document(db_session)
    writer = FakeClaudeWritingProvider("Sehr geehrte Damen und Herren, ...")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )

    response = client.post(
        f"/dashboard/chat/from-document/{document.id}",
        data={"csrf_token": _csrf(client), "action": "draft_reply"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    conversation = db_session.query(ChatConversation).one()
    ai_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="assistant")
        .one()
    )
    assert ai_message.draft_id is not None
    draft = db_session.query(Draft).filter_by(id=ai_message.draft_id).one()
    assert draft.matter_id == matter.id
    # Dieses Dokument ist ein eigenstaendiger Upload OHNE Nachrichtenbezug
    # (`document.message_id is None`, siehe `_matter_with_document`) - der
    # Entwurf darf hier ehrlich KEINE Ursprungsnachricht behaupten.
    assert draft.message_id is None


def test_document_draft_reply_links_the_drafts_source_message_when_document_is_a_mail_attachment(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ECHTER FUND (17.09., Overnight-Direktive §6/§7): Gegenprobe zu oben -
    ist das Dokument tatsaechlich ein Mail-Anhang (`document.message_id`
    gesetzt), muss der daraus erstellte Entwurf DENSELBEN Ursprungsbezug
    tragen wie "Antworten" direkt aus dem Posteingang - identischer
    "Original links"-Kontext in draft_detail.html, unabhaengig davon, ob
    der Anwalt ueber die Nachricht oder ueber das Dokument selbst
    eingestiegen ist."""
    login_as_admin(db_session, client)
    matter, document = _matter_with_document(db_session)
    source_message = Message(
        matter_id=matter.id,
        direction="inbound",
        sender="mandant@example-testdomain.invalid",
        subject="Mail mit Anhang",
        body_text="Siehe Anhang.",
    )
    db_session.add(source_message)
    db_session.flush()
    document.message_id = source_message.id
    db_session.commit()
    writer = FakeClaudeWritingProvider("Sehr geehrte Damen und Herren, ...")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )

    response = client.post(
        f"/dashboard/chat/from-document/{document.id}",
        data={"csrf_token": _csrf(client), "action": "draft_reply"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    conversation = db_session.query(ChatConversation).one()
    ai_message = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="assistant")
        .one()
    )
    draft = db_session.query(Draft).filter_by(id=ai_message.draft_id).one()
    assert draft.message_id == source_message.id


def test_document_action_on_document_without_matter_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    """Aktenisolation: ein Dokument ohne Akte darf keine Chat-Unterhaltung
    starten - dieselbe Absicherung wie bei Posteingang-Nachrichten."""
    login_as_admin(db_session, client)
    document = Document(
        matter_id=None, file_path="/tmp/x.pdf", original_filename="x.pdf"
    )
    db_session.add(document)
    db_session.commit()

    response = client.post(
        f"/dashboard/chat/from-document/{document.id}",
        data={"csrf_token": _csrf(client), "action": "analyze"},
    )

    assert response.status_code == 400
    assert db_session.query(ChatConversation).count() == 0


def test_document_action_unknown_action_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    _matter, document = _matter_with_document(db_session)

    response = client.post(
        f"/dashboard/chat/from-document/{document.id}",
        data={"csrf_token": _csrf(client), "action": "delete-everything"},
    )

    assert response.status_code == 400
    assert db_session.query(ChatConversation).count() == 0


def test_document_action_on_unknown_document_returns_404(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)

    response = client.post(
        "/dashboard/chat/from-document/does-not-exist",
        data={"csrf_token": _csrf(client), "action": "analyze"},
    )

    assert response.status_code == 404
