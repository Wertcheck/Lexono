"""Tests für app/web/chat_router.py - die neue Chat-Startseite
(UI-Überarbeitung: "Login → Chat → zentrale Arbeitsoberfläche").

Gleiches Testmuster wie tests/test_web_schriftsatz.py: In-Memory-SQLite
über app.dependency_overrides, `get_drafting_service` wird in
app.web.chat_router direkt gemonkeypatcht. Deckt die im Auftrag
geforderten Szenarien TEST A-E ab."""

from __future__ import annotations

import tempfile
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
    ChatMessageDocument,
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
    # Hinweis (06.10., "CHAT & CHAT-HISTORY PROFESSIONAL UX PASS"): ein reiner
    # Substring-Check auf die ganze Seite reicht nicht mehr aus, denn
    # "Erste Unterhaltung." bleibt als Titel in der Unterhaltungsliste
    # sichtbar (gewuenscht, siehe oben) und "chat-message--user" taucht seit
    # der clientseitigen Sofort-Rendering-Funktion appendUserBubble() auch im
    # <script>-Block JEDER Chat-Seite als JS-String auf (nicht als
    # tatsaechlich gerendertes Element). Deshalb gezielt nur den Inhalt des
    # Haupt-Nachrichtenbereichs (id="chat-messages") pruefen.
    messages_panel_start = with_new_param.text.index('id="chat-messages"')
    messages_panel_end = with_new_param.text.index('id="chat-composer"')
    messages_panel_html = with_new_param.text[messages_panel_start:messages_panel_end]
    assert "chat-message--user" not in messages_panel_html
    assert "Erste Unterhaltung." not in messages_panel_html


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


def test_active_conversation_indicator_uses_brand_green_not_seal_green(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND behoben (07.10., Polish-Direktive "CHAT-HISTORY FINAL
    PRODUKTSTAND"): die linke Akzentlinie des aktiven Chats nutzte bisher
    `--seal-green` - trotz des Namens kein Gruenton (#101828 Navy im
    Hellmodus, #5b7fc4 Blaugrau im Dunkelmodus, siehe :root-Definition in
    app.css), inkonsistent mit dem bereits etablierten "aktiv = echtes
    Lexono-Gruen"-Muster der Hauptnavigation
    (`.sidebar__group-summary--active`, identische `inset 2px 0 0`-Linie,
    nutzt dort bereits `--brand-green`)."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/static/css/app.css")
    css = response.text
    start = css.index(".chat-conversations__item--active {")
    end = css.index("}", start)
    block = css[start:end]
    assert "box-shadow: inset 2px 0 0 var(--brand-green);" in block
    assert "--seal-green" not in block


def test_chat_history_no_longer_ships_dead_delete_button_css(
    client: TestClient, db_session: Session
) -> None:
    """Aufraeum-Fund (07.10., selbe Polish-Direktive): `.chat-conversations__
    delete-form`/`.chat-conversations__delete-btn` waren seit der Umstellung
    auf das Drei-Punkte-Menue (06.10.) nirgendwo mehr im Markup referenziert
    - totes CSS aus einer noch aelteren Loeschen-Variante (direkter
    Papierkorb-Button in der Zeile, vor dem Drei-Punkte-Menue)."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/static/css/app.css")
    css = response.text
    assert ".chat-conversations__delete-btn {" not in css
    assert ".chat-conversations__delete-form {" not in css


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


# --- "CHAT / COMPOSER / CHAT-HISTORY FINAL UX & STATE-CONSISTENCY PASS"
# (06.10.): Plus statt Buerooklammer, X waehrend Recording (strukturell
# bereits durch die bestehende Zeilen-Umschaltung geloest, siehe
# test_chat_composer_cancel_and_stop_buttons_have_distinct_labels oben),
# Single-Source-of-Truth-Leeren des Composers. ---


def test_chat_composer_attach_button_uses_plus_icon_not_clip(
    client: TestClient, db_session: Session
) -> None:
    """§12/§13 - der bisherige Buerooklammer-Button wird als Plus
    dargestellt, bleibt aber dieselbe Schaltflaeche (gleiche ID, gleiche
    bestehende Attachment-Funktion/Klick-Handler)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    normal_controls_start = response.text.index('id="chat-normal-controls"')
    attach_btn_end = response.text.index("</button>", normal_controls_start)
    attach_btn_markup = response.text[normal_controls_start:attach_btn_end]
    assert 'id="chat-attach-btn"' in attach_btn_markup
    # Plus-Icon-Pfad ("M12 5v14M5 12h14", siehe _icons.html plus()), nicht
    # mehr der Buerooklammer-Pfad.
    assert "M12 5v14M5 12h14" in attach_btn_markup


def test_chat_composer_attach_button_keeps_existing_upload_wiring(
    client: TestClient, db_session: Session
) -> None:
    """Die Plus-Umstellung ist eine reine Icon-/Label-Aenderung - der
    bestehende Klick-Handler (oeffnet den Datei-Dialog) und das
    File-Input-Element muessen unveraendert vorhanden bleiben."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert 'id="chat-file-input"' in response.text
    assert 'attachBtn.addEventListener("click", function () { fileInput.click(); });' in response.text


def test_chat_composer_textarea_has_autocomplete_off(
    client: TestClient, db_session: Session
) -> None:
    """Haertung (§8/§17): verhindert, dass der Browser bei einer Navigation
    unabhaengig vom servergerenderten HTML einen alten Wert in das benannte
    Formularfeld zurueckschreibt."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    textarea_start = response.text.index('id="chat-input"')
    textarea_tag_end = response.text.index(">", textarea_start)
    textarea_tag = response.text[textarea_start:textarea_tag_end]
    assert 'autocomplete="off"' in textarea_tag


def test_clear_composer_is_a_single_shared_function_used_by_streaming_send(
    client: TestClient, db_session: Session
) -> None:
    """§8 "Single Source of Truth fuer den Composer" - EINE Funktion statt
    mehrerer unabhaengiger `value = ""`-Stellen. Der Streaming-Sendepfad
    (der tatsaechliche Pfad in jeder modernen WebView2-/Chromium-Umgebung,
    siehe `supportsStreaming`) muss sie verwenden."""
    chat_html_path = (
        Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "chat.html"
    )
    content = chat_html_path.read_text(encoding="utf-8")
    assert "function clearComposer()" in content

    clear_fn_start = content.index("function clearComposer()")
    clear_fn_end = content.index("}", clear_fn_start)
    clear_fn_body = content[clear_fn_start:clear_fn_end]
    assert 'textarea.value = "";' in clear_fn_body

    streaming_fn_start = content.index("function runStreamingSend(")
    streaming_fn_end = content.index("\n      if (composer) {", streaming_fn_start)
    streaming_fn_body = content[streaming_fn_start:streaming_fn_end]
    assert "clearComposer();" in streaming_fn_body
    # Direkt im Streaming-Pfad darf NICHT zusaetzlich/unabhaengig ein
    # zweites `textarea.value = ""` auftauchen (genau EINE Stelle).
    assert streaming_fn_body.count('textarea.value = "";') == 0


def test_clear_composer_is_not_called_in_the_native_fallback_submit_handler(
    client: TestClient, db_session: Session
) -> None:
    """Gegenprobe, technisch begruendet: im klassischen Formular-Fallback
    (ohne `preventDefault()`) liest der Browser die Feldwerte beim
    eigentlichen nativen Submit erneut aus dem DOM - ein `clearComposer()`-
    Aufruf an dieser Stelle wuerde den Inhalt LOESCHEN, BEVOR er uebermittelt
    wird, und zu einer leer gesendeten Nachricht fuehren (derselbe
    Mechanismus wie beim bereits bestehenden `disabled`-Fund, hier aber
    ueber `value` statt `disabled`)."""
    chat_html_path = (
        Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "chat.html"
    )
    content = chat_html_path.read_text(encoding="utf-8")

    submit_handler_start = content.index('composer.addEventListener("submit"')
    submit_handler_end = content.index("});", submit_handler_start)
    submit_handler = content[submit_handler_start:submit_handler_end]
    assert "clearComposer()" not in submit_handler


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
# Dokumentvorschau im Chat (05.10., Owner-Direktive "ARCHITECTURE & PRODUCT
# FLOW PASS" §19-21): echtes Thumbnail-Rendering + Isolation.
# ==========================================================================


def _attach_document_to_new_conversation(
    db: Session, *, current_user_email: str, file_path: str
) -> tuple[ChatConversation, "chat_router_module.Document"]:
    """Legt direkt eine Konversation (mit der technisch noetigen
    Schnellentwurf-Platzhalterakte, siehe `_placeholder_conversation` oben)
    + ein angehaengtes Dokument an, ohne den Upload-Weg zu nutzen (der
    Chat-Upload selbst erlaubt nur PDF/DOCX, siehe
    `_ALLOWED_UPLOAD_EXTENSIONS` - die Thumbnail-Route muss aber
    unabhaengig davon fuer jedes von `determine_viewer_mode` unterstuetzte
    Format korrekt funktionieren, z. B. ein direkt als Mail-Anhang
    eingegangenes Bild)."""
    conversation = _placeholder_conversation(db, current_user_email)

    message = ChatMessage(conversation_id=conversation.id, role="user", content="Dokument.")
    db.add(message)
    db.commit()
    db.refresh(message)

    document = chat_router_module.Document(file_path=file_path, original_filename=Path(file_path).name)
    db.add(document)
    db.commit()
    db.refresh(document)

    link = ChatMessageDocument(message_id=message.id, document_id=document.id)
    db.add(link)
    db.commit()

    return conversation, document


def test_chat_document_thumbnail_renders_first_pdf_page(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    login_as_admin(db_session, client)

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Seite eins.")
    pdf_path = tmp_path / "anhang.pdf"
    doc.save(pdf_path)
    doc.close()

    conversation, document = _attach_document_to_new_conversation(
        db_session, current_user_email="admin@kanzlei.test", file_path=str(pdf_path)
    )

    response = client.get(f"/dashboard/chat/{conversation.id}/document/{document.id}/thumbnail.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_chat_document_thumbnail_serves_direct_image(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    login_as_admin(db_session, client)

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    pixmap = page.get_pixmap(dpi=40)
    png_path = tmp_path / "anhang.png"
    png_path.write_bytes(pixmap.tobytes("png"))
    doc.close()

    conversation, document = _attach_document_to_new_conversation(
        db_session, current_user_email="admin@kanzlei.test", file_path=str(png_path)
    )

    response = client.get(f"/dashboard/chat/{conversation.id}/document/{document.id}/thumbnail.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_chat_document_thumbnail_404_for_unsupported_format(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    login_as_admin(db_session, client)

    txt_path = tmp_path / "notiz.txt"
    txt_path.write_text("Reiner Text ohne Seiten-Rendering.", encoding="utf-8")

    conversation, document = _attach_document_to_new_conversation(
        db_session, current_user_email="admin@kanzlei.test", file_path=str(txt_path)
    )

    response = client.get(f"/dashboard/chat/{conversation.id}/document/{document.id}/thumbnail.png")
    assert response.status_code == 404


def test_chat_document_thumbnail_rejects_document_from_other_conversation(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    """Gleiche Isolationsgarantie wie `chat_document_view`
    (`ChatService.get_attached_document`): ein Dokument, das in einer
    anderen Konversation haengt, darf ueber die Thumbnail-Route nicht
    erreichbar sein - auch nicht ueber eine erratene, aber gueltige
    Dokument-ID."""
    login_as_admin(db_session, client)

    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    pdf_path = tmp_path / "fremd.pdf"
    doc.save(pdf_path)
    doc.close()

    _owning_conversation, document = _attach_document_to_new_conversation(
        db_session, current_user_email="admin@kanzlei.test", file_path=str(pdf_path)
    )

    other_conversation, _other_document = _attach_document_to_new_conversation(
        db_session, current_user_email="admin@kanzlei.test", file_path=str(pdf_path)
    )

    response = client.get(
        f"/dashboard/chat/{other_conversation.id}/document/{document.id}/thumbnail.png"
    )
    assert response.status_code == 404


# ==========================================================================
# Kontext-Isolation zwischen Unterhaltungen (05.10., Owner-Direktive
# "ARCHITECTURE & PRODUCT FLOW PASS" §10, TEST 11): Unterhaltung A mit
# explizitem Aktenkontext darf NICHT in eine neue, allgemeine Unterhaltung
# B durchsickern - weder als Gespraechsverlauf noch als Aktenbezug.
# ==========================================================================


def test_conversation_with_matter_context_does_not_leak_into_new_general_chat(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echter End-to-End-Weg (ueber die tatsaechliche `/send`-Route, nicht
    nur eine DB-Query) - prueft die an den KI-Provider gesendete Payload
    (`ClaudeRequestPayload.anonymisierter_gespraechsverlauf`/
    `anonymisierter_sachverhalt`) fuer eine BRANDNEUE allgemeine
    Unterhaltung B, NACHDEM zuvor eine Unterhaltung A mit echtem,
    vertraulichem Aktenkontext bedient wurde - mit derselben Fake-KI-
    Instanz, damit beide Aufrufe in `received_payloads` landen."""
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider()
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )

    confidential_client = Client(name="Vertraulich Mueller", client_number="K-ISO-1")
    confidential_matter = Matter(
        client=confidential_client, title="Geheimakte Mueller./.Schmidt", reference_number="A-ISO-1"
    )
    db_session.add_all([confidential_client, confidential_matter])
    db_session.commit()

    csrf_a = _csrf(client)
    response_a = client.post(
        "/dashboard/chat/send",
        data={
            "csrf_token": csrf_a,
            "conversation_id": "",
            "matter_id": confidential_matter.id,
            "content": "Vertraulicher Sachverhalt: Herr Mueller bestreitet die Kuendigungsfrist.",
        },
        follow_redirects=True,
    )
    assert response_a.status_code == 200

    # Unterhaltung B: brandneuer, ALLGEMEINER Chat (kein matter_id-Feld) -
    # genau das in §5/§6 der Direktive vorgeschriebene Standardverhalten.
    csrf_b = _csrf(client)
    response_b = client.post(
        "/dashboard/chat/send",
        data={
            "csrf_token": csrf_b,
            "conversation_id": "",
            "content": "Wie funktioniert eine Kuendigung wegen Eigenbedarfs?",
        },
        follow_redirects=True,
    )
    assert response_b.status_code == 200

    assert len(writer.received_payloads) == 2
    payload_a, payload_b = writer.received_payloads

    # Unterhaltung A hat (erwartungsgemaess) noch KEINE Historie (erste
    # Nachricht), aber den vertraulichen Sachverhalt im eigenen Payload.
    assert payload_a.anonymisierter_gespraechsverlauf == []

    # Der eigentliche Isolationsbeweis: Unterhaltung B ist komplett sauber -
    # weder Gespraechsverlauf noch Sachverhalt enthalten irgendeine Spur
    # aus Unterhaltung A (Mandantenname/Aktentitel/Inhalt).
    assert payload_b.anonymisierter_gespraechsverlauf == []
    assert "Mueller" not in payload_b.anonymisierter_sachverhalt
    assert "Kuendigungsfrist" not in payload_b.anonymisierter_sachverhalt
    assert "Geheimakte" not in payload_b.anonymisierter_sachverhalt

    # Auch auf DB-Ebene: zwei komplett getrennte Unterhaltungen/Akten.
    conversations = db_session.query(ChatConversation).order_by(ChatConversation.created_at).all()
    assert len(conversations) == 2
    conversation_a, conversation_b = conversations
    assert conversation_a.matter_id == confidential_matter.id
    assert conversation_b.matter_id != confidential_matter.id


# ==========================================================================
# Aufnahme-UX im Chat-Composer (06.10., Owner-Direktive "SPRACHEINGABE IM
# CHAT: VOLLSTAENDIGE RECORDING- UND SEND-UX") - reine Markup-
# Regressionstests: die eigentliche Zustandslogik (X verwirft, Stop
# transkribiert, Wellenform, Timer) ist JS-seitig im Browser und wird
# hier bewusst NICHT per Python-Unittest nachgebaut (keine neue
# JS-Testarchitektur, §16) - echter Ablauf wurde stattdessen live per CDP
# verifiziert (siehe Abschlussbericht). Diese Tests stellen nur sicher,
# dass die vom JS zwingend benoetigten Element-IDs/Klassen nicht durch
# eine spaetere Template-Aenderung versehentlich verschwinden.
# ==========================================================================


def test_chat_composer_renders_recording_row_markup(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    # Normale Steuerelemente jetzt in einem eigenen Umschalt-Container
    # (§4) - IDLE zeigt diesen, RECORDING die Aufnahme-Zeile darunter.
    assert 'id="chat-normal-controls"' in response.text
    assert 'id="chat-recording"' in response.text
    assert 'id="chat-recording-cancel"' in response.text
    assert 'id="chat-recording-stop"' in response.text
    assert 'id="chat-waveform-canvas"' in response.text
    assert 'id="chat-recording-time"' in response.text
    # Aufnahme-Zeile ist initial (IDLE) versteckt.
    assert '<div class="chat-composer__recording" id="chat-recording" hidden>' in response.text


def test_chat_composer_cancel_and_stop_buttons_have_distinct_labels(
    client: TestClient, db_session: Session
) -> None:
    """§6/§7 - X (verwerfen) und Stop/Rechteck (beenden) muessen fuer
    Screenreader UND optisch eindeutig unterscheidbar sein, nicht nur
    zwei gleich beschriftete Buttons."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert 'title="Aufnahme verwerfen"' in response.text
    assert 'aria-label="Aufnahme verwerfen"' in response.text
    assert 'title="Diktat beenden"' in response.text
    assert 'aria-label="Diktat beenden"' in response.text


def test_chat_composer_renders_transcribing_row_markup(
    client: TestClient, db_session: Session
) -> None:
    """06.10., Owner-Direktive "SPEECH COMPOSER FINAL UX PASS" §8/§9/§22 -
    TRANSCRIBING bekommt eine eigene Zeile INNERHALB des Composers (Status-
    Text + Spinner), separat von `#chat-recording` und von der alten
    Statuszeile `#chat-speech-status` (die fuer diesen Zustand nicht mehr
    verwendet wird, siehe `startTranscription()` in chat.html)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert 'id="chat-transcribing"' in response.text
    # Initial (IDLE) versteckt - wie die Aufnahme-Zeile.
    assert '<div class="chat-composer__transcribing" id="chat-transcribing" hidden>' in response.text
    assert '<span class="chat-composer__transcribing-label">Transkription läuft …</span>' in response.text
    assert 'class="chat-composer__transcribing-spinner"' in response.text
    # Die alte separate Statuszeile darf diesen Text nicht mehr selbst
    # setzen (die Zeichenkette darf nur als HTML-Inhalt der neuen
    # Composer-Zeile und ggf. in Kommentaren vorkommen, NICHT mehr als
    # aktiver `setSpeechStatus(...)`-Aufruf).
    assert 'setSpeechStatus("Transkription läuft …"' not in response.text


def test_chat_composer_waveform_uses_segmented_bars_not_single_line(
    client: TestClient, db_session: Session
) -> None:
    """06.10., Owner-Direktive "SPEECH COMPOSER FINAL UX PASS" §1/§3 -
    reiner Code-Gegenbeweis gegen die alte, als Problem gemeldete
    durchgehende Oszilloskop-Linie: `getByteFrequencyData()` (Spektrum,
    je Segment unterschiedliche Werte) statt der vorherigen
    `getByteTimeDomainData()` + einem einzigen `lineTo`-Pfad. Die
    eigentliche visuelle Pruefung (sieht es wie getrennte Balken statt
    einer Linie aus?) ist nur per echtem Rendering/CDP moeglich und wird
    dort verifiziert (siehe Abschlussbericht) - dieser Test haelt nur den
    Techniknachweis im Quellcode fest, damit er nicht versehentlich
    zurueckgebaut wird."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert "analyser.getByteFrequencyData(frequencyData)" in response.text
    assert "analyser.getByteTimeDomainData(" not in response.text
    # Mehrere getrennte Segmente statt eines einzigen Pfades: pro Balken
    # ein eigenes moveTo/lineTo-Paar in einer Schleife, nicht ein
    # durchgaengiger Pfad ueber alle Datenpunkte.
    assert "barLevels" in response.text
    assert "computeBarCount" in response.text


# ==========================================================================
# Diktat-UX: Waveform-Farben, Direkt-Senden-Sichtbarkeit, Send-Icon (06.10.,
# Owner-Direktive "DIKTAT-UX: WAVEFORM-FARBEN, DIREKT-SENDEN-SICHTBARKEIT,
# SEND-ICON") - wie bei der vorherigen Direktive oben: reine Markup-/
# Quellcode-Regressionstests, die eigentliche visuelle Pruefung (Farben,
# Balken-Interpolation) wurde live per CDP verifiziert.
# ==========================================================================


def test_chat_composer_waveform_no_longer_uses_error_red(
    client: TestClient, db_session: Session
) -> None:
    """Die Wellenform darf nicht mehr die Fehlerfarbe --wax-red nutzen -
    stattdessen die neuen, eigenstaendigen Lexono-Gruen-Token."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert '.getPropertyValue("--wax-red")' not in response.text
    assert '.getPropertyValue("--waveform-active")' in response.text
    assert '.getPropertyValue("--waveform-idle")' in response.text


def test_chat_composer_waveform_interpolates_per_bar_color(
    client: TestClient, db_session: Session
) -> None:
    """Jeder Balken wird einzeln zwischen Ruhig- und Aktiv-Farbe interpoliert
    (stufenlose Abstufung nach Ausschlagsstaerke, nicht nur ein binaerer
    Farbwechsel)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert "function waveformBarColor(" in response.text
    assert "function hexToRgb(" in response.text


def test_chat_composer_send_button_is_sibling_not_nested_in_normal_controls(
    client: TestClient, db_session: Session
) -> None:
    """"Direkt senden" muss ausserhalb von `#chat-normal-controls` liegen,
    sonst wuerde es zusammen mit den anderen Idle-Steuerelementen waehrend
    RECORDING/TRANSCRIBING versteckt (`setMicState()` toggelt `hidden` auf
    dem gesamten Container)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    normal_controls_start = response.text.index('id="chat-normal-controls"')
    normal_controls_end = response.text.index("</div>", normal_controls_start)
    normal_controls_html = response.text[normal_controls_start:normal_controls_end]
    assert 'id="chat-send-btn"' not in normal_controls_html


def test_chat_composer_send_button_hidden_only_during_transcribing(
    client: TestClient, db_session: Session
) -> None:
    """"Diktat beenden" darf "Direkt senden" nicht verdraengen: der
    Senden-Button wird in `setMicState()` nur fuer TRANSCRIBING ausgeblendet,
    in IDLE und RECORDING bleibt er sichtbar."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert 'sendBtn.hidden = state === "transcribing"' in response.text


def test_chat_composer_send_button_uses_modern_arrow_icon(
    client: TestClient, db_session: Session
) -> None:
    """Der Senden-Button nutzt das neue, eigenstaendige Pfeil-Icon
    (`send_arrow`), nicht mehr das alte, an anderer Stelle weiterhin
    genutzte Papierflieger-Icon (`send`)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    send_btn_start = response.text.index('id="chat-send-btn"')
    send_btn_end = response.text.index("</button>", send_btn_start)
    send_btn_html = response.text[send_btn_start:send_btn_end]
    # Neues Icon: Schaft + Chevron-Spitze.
    assert 'd="M12 19V6.5"' in send_btn_html
    # Altes Icon (Papierflieger-Umriss) darf hier nicht mehr vorkommen.
    assert "M20.5 3.5 3 10.2l7 2.8 2.8 7 7.7-16.5Z" not in send_btn_html


# ==========================================================================
# Lokale Spracheingabe (05.10., Owner-Direktive "ARCHITECTURE & PRODUCT
# FLOW PASS" §22-27): die Route selbst, inkl. Auth/CSRF/Fehlerabbildung.
# Die eigentliche Transkriptionslogik wird separat in
# tests/test_chat_speech.py getestet - hier geht es nur um die
# Web-Schicht (Auth/CSRF/HTTP-Statuscodes/JSON-Form).
# ==========================================================================


def test_speech_transcribe_returns_recognized_text(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)

    monkeypatch.setattr(
        chat_router_module, "transcribe_audio_bytes", lambda audio_bytes, **kw: "Bitte prüfen Sie die Frist."
    )

    response = client.post(
        "/dashboard/chat/speech/transcribe",
        data={"csrf_token": csrf},
        files={"audio": ("aufnahme.webm", b"\x00" * 100, "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "Bitte prüfen Sie die Frist."}


def test_speech_transcribe_requires_login(client: TestClient) -> None:
    response = client.post(
        "/dashboard/chat/speech/transcribe",
        data={"csrf_token": "egal"},
        files={"audio": ("aufnahme.webm", b"\x00" * 100, "audio/webm")},
        follow_redirects=False,
    )
    assert response.status_code in (303, 401)


def test_speech_transcribe_rejects_invalid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    _csrf(client)  # Session aktiv, Token bewusst NICHT verwendet.

    response = client.post(
        "/dashboard/chat/speech/transcribe",
        data={"csrf_token": "falsches-token"},
        files={"audio": ("aufnahme.webm", b"\x00" * 100, "audio/webm")},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("error_cls_name", "expected_status", "expected_error_key"),
    [
        ("SpeechEmptyRecordingError", 422, "empty_recording"),
        ("SpeechTooLargeError", 413, "too_large"),
        ("SpeechDecodeError", 422, "decode_failed"),
        ("SpeechModelUnavailableError", 503, "model_unavailable"),
    ],
)
def test_speech_transcribe_maps_errors_to_honest_json_responses(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    error_cls_name: str,
    expected_status: int,
    expected_error_key: str,
) -> None:
    """§26 (Fehlerzustaende) + CLAUDE.md "Keine Fake-Vollstaendigkeit": bei
    jedem bekannten Fehlerfall ein ehrlicher Status/eine ehrliche Meldung,
    nie ein stiller 200er mit leerem Text."""
    from app.chat import speech as speech_module

    error_cls = getattr(speech_module, error_cls_name)

    def _raise(audio_bytes: bytes, **kwargs: object) -> str:
        raise error_cls("Testfehler")

    login_as_admin(db_session, client)
    csrf = _csrf(client)
    monkeypatch.setattr(chat_router_module, "transcribe_audio_bytes", _raise)

    response = client.post(
        "/dashboard/chat/speech/transcribe",
        data={"csrf_token": csrf},
        files={"audio": ("aufnahme.webm", b"\x00" * 100, "audio/webm")},
    )
    assert response.status_code == expected_status
    assert response.json()["error"] == expected_error_key


def test_speech_transcribe_never_writes_a_permanent_file(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§24 "keine unnoetige Audiospeicherung" auf Web-Schicht-Ebene: ruft
    tatsaechlich die echte Transkriptionsfunktion auf (mit einem Fake-
    Modell, siehe tests/test_chat_speech.py fuer das Fake) und prueft, dass
    danach keine Datei im tmp-Verzeichnis uebrig bleibt."""
    from app.chat import speech as speech_module

    class _FakeSegment:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeInfo:
        duration = 2.0

    class _FakeModel:
        def transcribe(self, path: str, *, language: str, vad_filter: bool):
            assert Path(path).exists()
            return (iter([_FakeSegment("Erkannter Text.")]), _FakeInfo())

    speech_module._model = _FakeModel()
    speech_module._model_load_error = None
    try:
        login_as_admin(db_session, client)
        csrf = _csrf(client)

        before = set(Path(tempfile.gettempdir()).glob("tmp*"))
        response = client.post(
            "/dashboard/chat/speech/transcribe",
            data={"csrf_token": csrf},
            files={"audio": ("aufnahme.webm", b"\x00" * 2000, "audio/webm")},
        )
        after = set(Path(tempfile.gettempdir()).glob("tmp*"))

        assert response.status_code == 200
        assert response.json() == {"text": "Erkannter Text."}
        assert after - before == set()
    finally:
        speech_module._model = None
        speech_module._model_load_error = None


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


def _placeholder_conversation(db: Session, current_user_email: str, *, user_message: str = "Allgemeine Frage") -> ChatConversation:
    """Eine Unterhaltung OHNE bewusste Aktenauswahl - wie sie
    `create_quick_matter` (app/drafting/quick_matter.py) fuer jeden neuen
    Chat ohne Kontext anlegt: Sammel-Mandant "Ohne Mandantenzuordnung"."""
    from app.models import User

    user = db.query(User).filter_by(email=current_user_email).first()
    client_row = Client(name="Ohne Mandantenzuordnung")
    matter = Matter(client=client_row, title="Schnellentwurf 2026-10-05")
    db.add_all([client_row, matter])
    db.commit()
    conversation = ChatConversation(matter_id=matter.id, user_id=user.id, title=user_message)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def test_chat_header_title_truncates_long_matter_title_with_ellipsis(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND behoben (07.10., Polish-Direktive "CHAT-HISTORY FINAL
    PRODUKTSTAND"): `text-overflow: ellipsis` direkt auf `.chat-panel__
    header-title` (einem `display:flex`-Container mit Icon/Tag als
    Geschwister-Flex-Items) griff live nie zuverlaessig - eine sehr lange
    Aktenbezeichnung wurde hart am Fensterrand abgeschnitten statt sauber
    zu ellipsen. Jeder Textzweig steht jetzt in einem eigenen
    `.chat-panel__header-title-text`-Span (siehe chat.html) - dieser Test
    verankert, dass der Aktentitel-Zweig tatsaechlich in diesem Span
    steht, nicht mehr als nackter Text direkt im Flex-Container."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get(f"/dashboard/chat/{conversation.id}")

    title_marker = '<div class="chat-panel__header-title">'
    start = response.text.index(title_marker) + len(title_marker)
    end = response.text.index("</div>", start)
    title_block = response.text[start:end]
    assert '<span class="chat-panel__header-title-text">' in title_block
    assert conversation.matter.title in title_block


def test_chat_header_title_text_span_has_working_ellipsis_css(
    client: TestClient, db_session: Session
) -> None:
    """Gegenprobe zum obigen Markup-Test: die eigentliche Kuerzung passiert
    im CSS - `.chat-panel__header-title-text` braucht `min-width: 0`
    (sonst verweigert sich der Flex-Item dem Schrumpfen unter die eigene
    Inhaltsbreite, egal wie das Markup aussieht) UND
    `text-overflow: ellipsis`."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/static/css/app.css")
    css = response.text
    start = css.index(".chat-panel__header-title-text {")
    end = css.index("}", start)
    block = css[start:end]
    assert "min-width: 0;" in block
    assert "text-overflow: ellipsis;" in block
    assert "white-space: nowrap;" in block


def test_chat_without_explicit_matter_hides_akte_breadcrumb(
    client: TestClient, db_session: Session
) -> None:
    """05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §5-9 -
    ein allgemeiner Chat (keine bewusste Aktenauswahl, nur die technisch
    noetige Schnellentwurf-Platzhalterakte im Hintergrund) darf NICHT wie
    Akte-Arbeit wirken: keine "Schnellentwurf ..."-Akte im Breadcrumb/
    Titel, stattdessen der natuerliche Unterhaltungstitel."""
    login_as_admin(db_session, client)
    conversation = _placeholder_conversation(
        db_session, "admin@kanzlei.test", user_message="Was ist der Unterschied zwischen Besitz und Eigentum?"
    )

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "Schnellentwurf 2026-10-05" not in response.text
    assert conversation.title in response.text
    assert f'href="/dashboard/matters/{conversation.matter_id}"' not in response.text


def test_chat_without_explicit_matter_offers_add_akte_not_change_akte(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _placeholder_conversation(db_session, "admin@kanzlei.test")
    # `other_matters` muss nicht-leer sein, damit das Popover ueberhaupt
    # gerendert wird (siehe chat.html: "{% if not viewing_document and
    # other_matters %}").
    other_client = Client(name="Anderer Mandant")
    other_matter = Matter(client=other_client, title="Andere Akte")
    db_session.add_all([other_client, other_matter])
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "Akte hinzufügen" in response.text
    assert "Akte ändern" not in response.text


def test_chat_with_explicit_matter_shows_matter_title_and_change_label(
    client: TestClient, db_session: Session
) -> None:
    """Gegenprobe: eine bewusst gewaehlte/verknuepfte Akte bleibt
    unveraendert sichtbar, inkl. "Akte ändern" (nicht "hinzufügen")."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    other_client = Client(name="Anderer Mandant")
    other_matter = Matter(client=other_client, title="Andere Akte")
    db_session.add_all([other_client, other_matter])
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert conversation.matter.title in response.text
    assert "Akte ändern" in response.text
    assert "Akte hinzufügen" not in response.text


def test_chat_with_explicit_matter_offers_remove_context_button(
    client: TestClient, db_session: Session
) -> None:
    """05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §9/TEST 4
    - eine bewusst verknuepfte Akte zeigt eine eigene "Kontext entfernen"-
    Aktion (nicht nur "Akte ändern" gegen eine ANDERE Akte)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "Kontext entfernen" in response.text
    assert f'/dashboard/chat/{conversation.id}/link-matter' in response.text


def test_general_chat_does_not_offer_remove_context_button(
    client: TestClient, db_session: Session
) -> None:
    """Gegenprobe: ein bereits allgemeiner Chat hat nichts zu entfernen."""
    login_as_admin(db_session, client)
    conversation = _placeholder_conversation(db_session, "admin@kanzlei.test")

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "Kontext entfernen" not in response.text


def test_link_matter_with_empty_matter_id_removes_context(
    client: TestClient, db_session: Session
) -> None:
    """TEST 4 (§28): "Akte aktiv -> Kontext entfernen -> Frage -> wieder
    allgemeiner Chat." Leeres `matter_id` loest die Unterhaltung von der
    bisherigen (echten) Akte und haengt sie an eine frische Schnellentwurf-
    Platzhalterakte - danach verhaelt sich die Unterhaltung wie ein
    brandneuer allgemeiner Chat (kein Aktentitel im Breadcrumb/Header,
    "Akte hinzufügen" statt "Akte ändern")."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    original_matter_id = conversation.matter_id
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{conversation.id}/link-matter",
        data={"csrf_token": csrf, "matter_id": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(conversation)
    assert conversation.matter_id != original_matter_id
    assert conversation.matter.client.name == "Ohne Mandantenzuordnung"

    follow_up = client.get(f"/dashboard/chat/{conversation.id}")
    # "Erste Akte" darf nur noch als AUSWAHLOPTION im "Akte hinzufügen"-
    # Dropdown auftauchen (wieder anhängbar), NICHT mehr als aktiver
    # Breadcrumb-Link (der immer auf die AKTUELLE `matter_id` zeigt).
    assert f"/dashboard/matters/{original_matter_id}" not in follow_up.text
    assert "Akte hinzufügen" in follow_up.text
    assert "Kontext entfernen" not in follow_up.text


def test_conversation_list_hides_placeholder_matter_meta(
    client: TestClient, db_session: Session
) -> None:
    """05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §5-9 -
    die Unterhaltungsliste in der linken Spalte zeigt fuer eine Schnell-
    entwurf-Platzhalterakte keine Akte-Metazeile (nur den natuerlichen
    Unterhaltungstitel), fuer eine echte Akte weiterhin schon."""
    login_as_admin(db_session, client)
    general = _placeholder_conversation(
        db_session, "admin@kanzlei.test", user_message="Allgemeine Rechtsfrage"
    )
    with_matter = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get(f"/dashboard/chat/{general.id}")

    assert "Schnellentwurf 2026-10-05" not in response.text
    assert with_matter.matter.title in response.text


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


def test_chat_history_row_menu_closes_on_escape_key(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND behoben (07.10., erneuter QA-Durchlauf "CHAT-VERWALTUNG
    PRODUKTIONSREIF"): live per CDP reproduziert, dass Escape das
    geoeffnete Drei-Punkte-Menue bzw. "Akte zuordnen"-Popover bisher NICHT
    schloss - nur der Umbenennen-Inline-Input und das Datenschutz-Popover
    hatten einen eigenen Escape-Handler. Kein End-to-End-Browsertest hier
    (reines JS-Verhalten) - prueft stattdessen, dass der global registrierte
    `keydown`-Handler fuer "Escape" tatsaechlich im ausgelieferten Markup
    vorhanden ist und dieselbe Schliess-Funktion wie der Outside-Click
    verwendet (kein zweiter, abweichender Mechanismus)."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/chat")

    html = response.text
    assert "function closeAllChatRowMenus()" in html
    assert '"click", closeAllChatRowMenus' in html
    assert 'if (evt.key === "Escape") { closeAllChatRowMenus(); }' in html


def test_chat_history_row_menu_offers_akte_zuordnen(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND behoben (07.10., Owner-Direktive "CHAT-HISTORY-
    MANAGEMENT ERWEITERN" §2/§4): das Drei-Punkte-Menue einer Unterhaltung
    in der Liste bot bisher nur "Umbenennen"/"Löschen" - eine Akte liess
    sich nur ueber die separate Kopfzeilen-Karte der GERADE AKTIVEN
    Konversation zuordnen, nicht pro Zeile fuer JEDE Unterhaltung in der
    Liste. Wiederverwendet denselben bestehenden `/link-matter`-Endpunkt,
    kein neuer Codepfad."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    other_client = Client(name="Andere Mandantin", client_number="K-ROW")
    other_matter = Matter(client=other_client, title="Zielakte Zeile", reference_number="A-ROW")
    db_session.add_all([other_client, other_matter])
    db_session.commit()

    response = client.get("/dashboard/chat")

    assert "chat-conversations__matter-trigger" in response.text
    assert "Akte zuordnen" in response.text
    assert f'action="/dashboard/chat/{conversation.id}/link-matter"' in response.text
    assert f'value="{other_matter.id}"' in response.text


def test_chat_history_row_matter_popover_excludes_own_current_matter(
    client: TestClient, db_session: Session
) -> None:
    """Dieselbe Regel wie bei der Aktenbezug-Karte der aktiven Konversation
    (siehe `other_matters` in app/web/chat_router.py): die eigene,
    bereits zugeordnete Akte einer Zeile darf in deren EIGENEM Popover
    nicht nochmal als Ziel erscheinen (waere ein wirkungsloser No-Op)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get("/dashboard/chat")

    assert f'value="{conversation.matter_id}"' not in response.text


def test_chat_history_row_matter_popover_excludes_placeholder_quick_matters(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (live beim ersten Testlauf reproduziert): automatisch
    angelegte Schnellentwurf-Platzhalterakten (PLACEHOLDER_CLIENT_NAME)
    tauchten zunaechst als waehlbares Zuordnungsziel auf - widerspricht
    der an anderer Stelle im selben Template bereits etablierten Regel,
    dass ein allgemeiner Chat/seine Platzhalterakte nicht wie echte
    Akte-Arbeit wirken/sichtbar werden soll."""
    login_as_admin(db_session, client)
    placeholder_conversation = _placeholder_conversation(
        db_session, "admin@kanzlei.test", user_message="Allgemeine Frage"
    )
    # Eine zweite, echte Unterhaltung mit echter Akte, damit das Popover
    # der Platzhalter-Unterhaltung getestet werden kann, ohne dass deren
    # EIGENE Akte (sowieso ausgeschlossen) die Aussage verwaessert.
    _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get(f"/dashboard/chat/{placeholder_conversation.id}")

    assert "Schnellentwurf" not in response.text


def test_chat_history_row_matter_popover_offers_remove_for_explicit_matter(
    client: TestClient, db_session: Session
) -> None:
    """Direktive §4: "Wenn ein Chat bereits einer Akte zugeordnet ist, soll
    das Menü außerdem eine sinnvolle Möglichkeit bieten, die Zuordnung zu
    ändern bzw. zu entfernen." - nur sichtbar, wenn tatsaechlich ein
    bewusster Aktenkontext aktiv ist (echte Akte, keine Platzhalterakte)."""
    login_as_admin(db_session, client)
    with_matter = _active_conversation(db_session, "admin@kanzlei.test")
    placeholder_conversation = _placeholder_conversation(
        db_session, "admin@kanzlei.test", user_message="Allgemeine Frage"
    )

    response = client.get("/dashboard/chat")

    assert f'id="chat-matter-remove-{with_matter.id}"' in response.text
    assert f'id="chat-matter-remove-{placeholder_conversation.id}"' not in response.text


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
    """ECHTER FUND behoben (07.10., Owner-Direktive "CHAT-HISTORY-
    MANAGEMENT ERWEITERN" §5): ein einzelner Chat liess sich bisher nur
    ueber ein klassisches Formular ohne jede Bestaetigung loeschen (ein
    Klick loeschte sofort, kein `window.confirm` o. ae.) - UND ein
    (zufaelliger) Klick auf "Loeschen" bei einer NICHT aktiven
    Unterhaltung riss den gerade geoeffneten aktiven Chat per vollem
    Seiten-Redirect weg, obwohl dieser gar nicht geloescht wurde. Jetzt
    ein JS-Button (kein `<form action=".../delete">` mehr), der denselben,
    bereits bestehenden `/bulk-delete`-Endpunkt mit einer einzelnen ID
    aufruft (siehe chat.html) - inkl. Bestaetigungsdialog und korrekter
    Behandlung des aktiven Chats (siehe JS-Kommentar dort)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get("/dashboard/chat")

    assert "chat-conversations__delete-trigger" in response.text
    assert f'data-title="{conversation.title}"' in response.text
    # Kein klassisches, bestaetigungsloses Formular mehr fuer die
    # Einzel-Loeschung.
    assert f'action="/dashboard/chat/{conversation.id}/delete"' not in response.text


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

    # 04.10., Dokumenten-Editor: zeigt jetzt auf den neuen Rich-Text-Editor
    # (app/web/draft_editor_router.py), nicht mehr auf den reinen Viewer
    # (draft_detail.html).
    #
    # 06.10., Owner-Direktive "Dokument-Upload -> Schriftsatz -> Dokument-
    # Panel -> Editor": ein echter Schriftsatz erscheint jetzt als
    # eigenstaendiges Dokument-Panel (".chat-document-panel") statt als
    # normale Flieszext-Antwort - der Editor-Link lebt jetzt im
    # Panel-Footer ("Editor öffnen" statt vorher "Vollständigen Editor
    # öffnen", gleiche Zielseite).
    assert "chat-document-panel" in response.text
    assert f'href="/dashboard/drafts/{draft.id}/edit"' in response.text
    assert "Editor öffnen" in response.text
    # Ohne gesetztes Draft.subject wird der Titel aus `ChatMessage.content`
    # abgeleitet (siehe app/web/chat_router.py::_draft_panel_title) - NICHT
    # aus `Draft.content` (das ist bei einem echten, ueber den Chat erzeugten
    # Schriftsatz bereits zu HTML konvertiert, siehe dortiger Docstring für
    # den live im Browser gefundenen Fund) - keine erfundene Ueberschrift.
    assert "Hier ist der Entwurf." in response.text


def test_assistant_message_with_chat_reference_draft_hides_open_editor_link(
    client: TestClient, db_session: Session
) -> None:
    """05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §11/§12 -
    eine normale Chat-Antwort ohne Schriftsatz-Intent persistiert zwar eine
    Draft-Zeile (status="chat_reference", siehe app/drafting/service.py::
    _persist_draft - Traeger fuer "Quellen & Verweise"), bietet dafuer aber
    KEINEN Editor an. Gegenstueck zu
    test_assistant_message_with_draft_shows_open_editor_link."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    draft = Draft(
        matter_id=conversation.matter_id, content="<p>Antworttext.</p>",
        version=1, status="chat_reference", content_format="html",
    )
    db_session.add(draft)
    db_session.commit()
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant", content="Antworttext.", draft_id=draft.id
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "chat-document-panel" not in response.text
    assert f'href="/dashboard/drafts/{draft.id}/edit"' not in response.text
    assert "Editor öffnen" not in response.text


def test_document_panel_title_has_no_raw_html_for_a_real_html_draft(
    client: TestClient, db_session: Session
) -> None:
    """Regression (06.10., live im Browser mit einem echten, ueber den Chat
    erzeugten Schriftsatz gefunden): JEDER echte, ueber DraftingService
    erzeugte Schriftsatz hat `content_format == "html"` und `Draft.content`
    als bereits konvertiertes HTML (siehe app/drafting/service.py::
    _persist_draft) - ein erster Versuch leitete den Panel-Titel faelschlich
    aus `Draft.content` ab und zeigte dadurch rohe `<p>`-Tags im Titel. Der
    Titel muss stattdessen aus `ChatMessage.content` (immer Klartext/
    Markdown, nie HTML) abgeleitet werden."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    draft = Draft(
        matter_id=conversation.matter_id,
        content="<p>Sehr geehrte Damen und Herren,</p><p>hiermit legen wir Einspruch ein.</p>",
        version=1, status="draft", content_format="html",
    )
    db_session.add(draft)
    db_session.commit()
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant",
        content="Sehr geehrte Damen und Herren,\n\nhiermit legen wir Einspruch ein.",
        draft_id=draft.id,
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    title_start = response.text.find('chat-document-panel__title">') + len('chat-document-panel__title">')
    title_end = response.text.find("</div>", title_start)
    title_section = response.text[title_start:title_end]
    assert "<p>" not in title_section
    assert "Sehr geehrte Damen und Herren" in title_section


def test_document_panel_prefers_real_draft_subject_over_derived_title(
    client: TestClient, db_session: Session
) -> None:
    """Der Panel-Titel verwendet das echte `Draft.subject`-Feld (vom Editor
    gepflegt), WENN es gesetzt ist - keine erfundene/abgeleitete
    Ueberschrift, wenn bereits ein echter Betreff vorliegt (siehe
    app/web/chat_router.py::_draft_panel_title)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    draft = Draft(
        matter_id=conversation.matter_id, content="Sehr geehrte Damen und Herren, ...",
        version=1, status="draft", subject="Klageschrift wegen Schadensersatz",
    )
    db_session.add(draft)
    db_session.commit()
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant", content="Entwurf.", draft_id=draft.id
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert "Klageschrift wegen Schadensersatz" in response.text


def test_document_panel_copy_button_targets_full_draft_content(
    client: TestClient, db_session: Session
) -> None:
    """Der Kopieren-Button im Panel-Header muss auf denselben Container
    zeigen, der den VOLLSTAENDIGEN Entwurfstext enthaelt (kein Download,
    keine neue Clipboard-Implementierung - derselbe bestehende
    ".chat-message__copy-btn"/"data-copy-target"-Mechanismus wie bei einer
    normalen Chat-Antwort)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    long_content = "Sehr geehrte Damen und Herren, " + ("dies ist ein langer Schriftsatz. " * 50)
    draft = Draft(matter_id=conversation.matter_id, content=long_content, version=1, status="draft")
    db_session.add(draft)
    db_session.commit()
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant", content=long_content, draft_id=draft.id
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert f'data-copy-target="chat-message-text-{message.id}"' in response.text
    assert f'id="chat-message-text-{message.id}"' in response.text
    # /ux-long-content: der VOLLSTAENDIGE Text bleibt im Datenmodell/DOM
    # erhalten - nur die sichtbare Hoehe wird per CSS begrenzt
    # (".chat-document-panel__content"), keine Kuerzung im Markup.
    assert long_content.strip() in response.text
    assert "chat-document-panel__content" in response.text


def test_document_panel_appears_only_once_per_schriftsatz_message(
    client: TestClient, db_session: Session
) -> None:
    """Regression: eine Schriftsatz-Nachricht zeigt NICHT zusaetzlich auch
    noch den normalen ".chat-message__text"-Flieszextblock - das Panel
    ersetzt ihn vollstaendig, keine doppelte Darstellung desselben
    Inhalts."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    draft = Draft(matter_id=conversation.matter_id, content="Entwurfstext", version=1, status="draft")
    db_session.add(draft)
    db_session.commit()
    message = ChatMessage(
        conversation_id=conversation.id, role="assistant", content="Entwurfstext", draft_id=draft.id
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    # Der Inhalt darf nur EINMAL im Markup erscheinen (im Panel) - keine
    # zusaetzliche, doppelte Darstellung ueber den normalen Flieszext-Block.
    assert response.text.count(f'id="chat-message-text-{message.id}"') == 1
    assert response.text.count("chat-document-panel\"") == 1


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


# --- Chat umbenennen (06.10., Owner-Direktive "CHAT & CHAT-HISTORY
# PROFESSIONAL UX PASS" §13-§17). Kein neues Datenmodell/keine Migration -
# `ChatConversation.title` ist bereits ein freies String(200)-Feld, das
# ausserhalb von `_derive_title()` (nur bei Erstellung) nie wieder
# ueberschrieben wird (siehe app/chat/service.py). Gleicher IDOR-Schutz wie
# bei delete/link-matter (`_require_own_conversation`). ---


def test_rename_conversation_updates_title(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{conversation.id}/rename",
        data={"csrf_token": csrf, "title": "Neuer Titel"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "title": "Neuer Titel"}
    db_session.refresh(conversation)
    assert conversation.title == "Neuer Titel"


def test_rename_conversation_works_for_the_active_conversation_without_new_id(
    client: TestClient, db_session: Session
) -> None:
    """Die Unterhaltungs-ID (und damit die konkrete Konversation) darf sich
    durch das Umbenennen NICHT aendern - kein neuer Chat, keine Navigation
    notwendig (Direktive §15)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)

    client.get(f"/dashboard/chat/{conversation_id}")
    client.post(
        f"/dashboard/chat/{conversation_id}/rename",
        data={"csrf_token": csrf, "title": "Umbenannt waehrend aktiv"},
    )

    still_open = client.get(f"/dashboard/chat/{conversation_id}")
    assert still_open.status_code == 200
    assert "Umbenannt waehrend aktiv" in still_open.text
    assert db_session.get(ChatConversation, conversation_id) is not None


def test_rename_conversation_trims_whitespace(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{conversation.id}/rename",
        data={"csrf_token": csrf, "title": "   Getrimmt   "},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Getrimmt"
    db_session.refresh(conversation)
    assert conversation.title == "Getrimmt"


def test_rename_conversation_rejects_empty_title(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    original_title = conversation.title
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{conversation.id}/rename",
        data={"csrf_token": csrf, "title": "   "},
    )

    assert response.status_code == 422
    db_session.refresh(conversation)
    assert conversation.title == original_title


def test_rename_conversation_persists_across_reload(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)

    client.post(
        f"/dashboard/chat/{conversation_id}/rename",
        data={"csrf_token": csrf, "title": "Haelt nach Reload"},
    )

    overview = client.get("/dashboard/chat")
    reopened = client.get(f"/dashboard/chat/{conversation_id}")
    assert "Haelt nach Reload" in overview.text
    assert "Haelt nach Reload" in reopened.text


def test_rename_conversation_survives_further_messages_and_is_not_auto_overwritten(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direktive §16: ein manuell gesetzter Titel darf durch die
    automatische Titel-Generierung (`_derive_title`, nur bei Erstellung
    aktiv) NIEMALS im Nachhinein stillschweigend ueberschrieben werden -
    hier verifiziert durch eine weitere Nachricht NACH dem Umbenennen."""
    login_as_admin(db_session, client)
    writer = FakeClaudeWritingProvider("Antwort.")
    monkeypatch.setattr(
        chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer)
    )
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)

    client.post(
        f"/dashboard/chat/{conversation_id}/rename",
        data={"csrf_token": csrf, "title": "Mein eigener Titel"},
    )
    client.post(
        "/dashboard/chat/send",
        data={
            "csrf_token": csrf,
            "conversation_id": conversation_id,
            "content": "Eine weitere, ganz andere Nachricht.",
        },
        follow_redirects=True,
    )

    db_session.refresh(conversation)
    assert conversation.title == "Mein eigener Titel"


def test_rename_conversation_requires_csrf(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    original_title = conversation.title

    response = client.post(
        f"/dashboard/chat/{conversation.id}/rename",
        data={"csrf_token": "falscher-erratener-token", "title": "Sollte nicht klappen"},
    )

    assert response.status_code == 403
    db_session.refresh(conversation)
    assert conversation.title == original_title


def test_rename_conversation_rejects_another_users_conversation(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    roles = seed_roles(db_session)
    other_user = create_test_user(db_session, roles["anwalt"], "andere-rename@kanzlei.test")
    other_client_row = Client(name="X", client_number="K-REN")
    matter = Matter(client=other_client_row, title="Fremde Akte", reference_number="A-REN")
    db_session.add_all([other_client_row, matter])
    db_session.commit()
    foreign_conversation = ChatConversation(matter_id=matter.id, user_id=other_user.id, title="Fremd")
    db_session.add(foreign_conversation)
    db_session.commit()
    foreign_id = foreign_conversation.id
    csrf = _csrf(client)

    response = client.post(
        f"/dashboard/chat/{foreign_id}/rename",
        data={"csrf_token": csrf, "title": "Uebernommen"},
    )

    assert response.status_code == 403
    db_session.refresh(foreign_conversation)
    assert foreign_conversation.title == "Fremd"


def test_rename_conversation_on_unknown_conversation_returns_404(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/does-not-exist/rename",
        data={"csrf_token": csrf, "title": "Egal"},
    )

    assert response.status_code == 404


def test_rename_conversation_writes_an_audit_event(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id
    csrf = _csrf(client)

    client.post(
        f"/dashboard/chat/{conversation_id}/rename",
        data={"csrf_token": csrf, "title": "Mit Audit-Eintrag"},
    )

    event = (
        db_session.query(AuditEvent)
        .filter_by(
            entity_type="ChatConversation",
            entity_id=conversation_id,
            event_type="chat_conversation_renamed",
        )
        .first()
    )
    assert event is not None


def test_chat_history_list_offers_rename_action_for_each_conversation(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get("/dashboard/chat")

    assert "chat-conversations__rename-trigger" in response.text
    assert f'data-conversation-id="{conversation.id}"' in response.text


# --- Mehrfachauswahl/Sammelloeschung (06.10., Owner-Direktive "CHAT &
# CHAT-HISTORY PROFESSIONAL UX PASS" §18-§22). Wiederverwendet dieselbe
# Kaskade wie die Einzelloeschung (cascade="all, delete-orphan" auf
# `ChatConversation.messages`) - eine Transaktion, ein Commit. Sicherheit:
# NIE auf clientseitige Checkboxen verlassen - die Server-Query filtert
# zusaetzlich zu den IDs auch auf `user_id == current_user.id`, fremde/
# ungueltige IDs werden dadurch aus dem Ergebnis stillschweigend
# ausgeschlossen statt den gesamten Batch abzulehnen. ---


def test_bulk_delete_removes_all_selected_conversations(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    first = _active_conversation(db_session, "admin@kanzlei.test")
    second = _placeholder_conversation(db_session, "admin@kanzlei.test", user_message="Zweiter Chat")
    third = _placeholder_conversation(db_session, "admin@kanzlei.test", user_message="Dritter Chat")
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/bulk-delete",
        data={"csrf_token": csrf, "conversation_ids": [first.id, second.id]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert sorted(body["deleted_ids"]) == sorted([first.id, second.id])
    assert db_session.get(ChatConversation, first.id) is None
    assert db_session.get(ChatConversation, second.id) is None
    assert db_session.get(ChatConversation, third.id) is not None


def test_bulk_delete_cascades_messages_for_every_selected_conversation(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    first = _active_conversation(db_session, "admin@kanzlei.test")
    second = _placeholder_conversation(db_session, "admin@kanzlei.test", user_message="Zweiter Chat")
    message_a = ChatMessage(conversation_id=first.id, role="user", content="Hallo A")
    message_b = ChatMessage(conversation_id=second.id, role="user", content="Hallo B")
    db_session.add_all([message_a, message_b])
    db_session.commit()
    message_a_id, message_b_id = message_a.id, message_b.id
    csrf = _csrf(client)

    client.post(
        "/dashboard/chat/bulk-delete",
        data={"csrf_token": csrf, "conversation_ids": [first.id, second.id]},
    )

    assert db_session.get(ChatMessage, message_a_id) is None
    assert db_session.get(ChatMessage, message_b_id) is None


def test_bulk_delete_with_nothing_selected_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/bulk-delete",
        data={"csrf_token": csrf, "conversation_ids": []},
    )

    assert response.status_code == 422


def test_bulk_delete_requires_csrf(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    conversation_id = conversation.id

    response = client.post(
        "/dashboard/chat/bulk-delete",
        data={"csrf_token": "falscher-erratener-token", "conversation_ids": [conversation_id]},
    )

    assert response.status_code == 403
    assert db_session.get(ChatConversation, conversation_id) is not None


def test_bulk_delete_never_deletes_another_users_conversation(
    client: TestClient, db_session: Session
) -> None:
    """NIEMALS ausschliesslich auf UI-Checkboxen vertrauen (Direktive §21):
    eine fremde Konversations-ID in der Auswahl darf weder diese fremde
    Konversation loeschen, noch den gesamten Batch zum Scheitern bringen -
    die eigenen, gueltigen IDs werden trotzdem geloescht."""
    login_as_admin(db_session, client)
    roles = seed_roles(db_session)
    other_user = create_test_user(db_session, roles["anwalt"], "andere-bulk@kanzlei.test")
    other_client_row = Client(name="X", client_number="K-BULK")
    matter = Matter(client=other_client_row, title="Fremde Akte", reference_number="A-BULK")
    db_session.add_all([other_client_row, matter])
    db_session.commit()
    foreign_conversation = ChatConversation(matter_id=matter.id, user_id=other_user.id, title="Fremd")
    db_session.add(foreign_conversation)
    db_session.commit()
    foreign_id = foreign_conversation.id

    own_conversation = _active_conversation(db_session, "admin@kanzlei.test")
    own_id = own_conversation.id
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/bulk-delete",
        data={"csrf_token": csrf, "conversation_ids": [own_id, foreign_id]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_ids"] == [own_id]
    assert db_session.get(ChatConversation, own_id) is None
    assert db_session.get(ChatConversation, foreign_id) is not None


def test_bulk_delete_ignores_unknown_conversation_ids(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    own_conversation = _active_conversation(db_session, "admin@kanzlei.test")
    own_id = own_conversation.id
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/chat/bulk-delete",
        data={"csrf_token": csrf, "conversation_ids": [own_id, "does-not-exist"]},
    )

    assert response.status_code == 200
    assert response.json()["deleted_ids"] == [own_id]
    assert db_session.get(ChatConversation, own_id) is None


def test_bulk_delete_writes_an_audit_event_per_conversation(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    first = _active_conversation(db_session, "admin@kanzlei.test")
    second = _placeholder_conversation(db_session, "admin@kanzlei.test", user_message="Zweiter Chat")
    csrf = _csrf(client)

    client.post(
        "/dashboard/chat/bulk-delete",
        data={"csrf_token": csrf, "conversation_ids": [first.id, second.id]},
    )

    events = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "ChatConversation",
            AuditEvent.entity_id.in_([first.id, second.id]),
            AuditEvent.event_type == "chat_conversation_deleted",
        )
        .all()
    )
    assert len(events) == 2


def test_chat_history_list_offers_selection_toggle_when_multiple_conversations_exist(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    _active_conversation(db_session, "admin@kanzlei.test")
    _placeholder_conversation(db_session, "admin@kanzlei.test", user_message="Zweiter Chat")

    response = client.get("/dashboard/chat")

    # Attribut-Form statt reinem Substring: "chat-selection-toggle" tritt
    # auch im <script>-Block als JS-String (getElementById(...)) auf jeder
    # Chat-Seite auf - erst `id="..."` identifiziert das echte, gerenderte
    # Element eindeutig.
    assert 'id="chat-selection-toggle"' in response.text
    assert 'id="chat-selection-bar"' in response.text


def test_chat_history_list_hides_selection_toggle_with_a_single_conversation(
    client: TestClient, db_session: Session
) -> None:
    """Eine Mehrfachauswahl ergibt bei genau einer Unterhaltung keinen Sinn
    (nichts, was man sinnvoll "mehrfach" waehlen koennte)."""
    login_as_admin(db_session, client)
    _active_conversation(db_session, "admin@kanzlei.test")

    response = client.get("/dashboard/chat")

    assert 'id="chat-selection-toggle"' not in response.text


# ==========================================================================
# Chat UI Structural + Visual Overhaul (07.10., Owner-Direktive "CHAT UI
# STRUCTURAL + VISUAL OVERHAUL") - zentrale Chat-Content-Spalte, Message
# Bubbles. Reine Markup-/Quellcode-Regressionstests (gleiche Philosophie wie
# die Diktat-UX-Tests oben) - die eigentliche visuelle Pruefung (Breiten-
# Abgleich in Pixeln, Farben) wurde live per CDP verifiziert.
# ==========================================================================


def test_chat_message_and_composer_share_the_same_width_token(
    client: TestClient, db_session: Session
) -> None:
    """Zentrale Regel "CHAT CONTENT WIDTH = COMPOSER WIDTH": beide
    Container muessen dieselbe CSS-Variable referenzieren, nicht nur
    zufaellig denselben Pixelwert an zwei unabhaengigen Stellen."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/static/css/app.css")
    assert response.status_code == 200
    css = response.text
    assert "--chat-content-width:" in css
    panel_messages_start = css.index(".chat-panel__messages {")
    panel_messages_end = css.index("}", panel_messages_start)
    assert "var(--chat-content-width)" in css[panel_messages_start:panel_messages_end]
    composer_start = css.index(".chat-composer {")
    composer_end = css.index("}", composer_start)
    assert "var(--chat-content-width)" in css[composer_start:composer_end]


def test_chat_message_bubbles_render_for_user_and_assistant(
    client: TestClient, db_session: Session
) -> None:
    """Beide Rollen muessen im Markup unterscheidbar sein (fuer
    rollenspezifisches CSS: rechtsbuendige User-Sprechblase vs.
    linksbuendiges Assistant-Panel)."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    db_session.add_all(
        [
            ChatMessage(conversation_id=conversation.id, role="user", content="Eine Frage."),
            ChatMessage(conversation_id=conversation.id, role="assistant", content="Eine Antwort."),
        ]
    )
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert response.status_code == 200
    assert "chat-message--user" in response.text
    assert "chat-message--assistant" in response.text


def test_chat_document_panel_is_isolated_from_the_dark_theme(
    client: TestClient, db_session: Session
) -> None:
    """Dokument-Schutz (/document-protection): der inline im Chat gezeigte
    Schriftsatz-Panel muss in beiden Themes eine helle Papierflaeche
    bleiben - lokale Token-Neuverankerung analog zu `.document-page`,
    echter, bei dieser Direktive gefundener Vorbestand-Fund."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/static/css/app.css")
    assert response.status_code == 200
    css = response.text
    panel_start = css.index(".chat-document-panel {")
    panel_end = css.index("}", panel_start)
    panel_block = css[panel_start:panel_end]
    assert "--paper-000: #ffffff;" in panel_block
    assert "--ink-900: #0f172a;" in panel_block


def test_chat_document_panel_content_declares_its_own_text_color(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (07.10., Owner-Direktive "SCHRIFTSATZ-/DRAFTING-BLOCK
    DARSTELLUNGSFEHLER"): `.chat-document-panel__content` traegt - anders
    als der normale Chat-Antwort-Zweig - NICHT die Basisklasse
    `.chat-message__text` (siehe chat.html `is_schriftsatz`-Verzweigung),
    weil er eine eigene Kartenoptik statt der Sprechblasen-Polsterung
    braucht. Dadurch fehlte ihm auch deren `color: var(--ink-900)`-
    Deklaration: die Textfarbe wurde rein von einem Vorfahren AUSSERHALB
    von `.chat-document-panel` geerbt, wo die lokale Token-Neuverankerung
    (siehe `.chat-document-panel`, obiger Test) noch nicht griff - live mit
    einem echten, ueber die Cloud-API erzeugten Schriftsatz reproduziert:
    im Dark Mode war die berechnete Textfarbe `rgb(241, 245, 249)` (der
    globale, fuer dunklen Chat-Hintergrund gedachte Theme-Wert von
    `--ink-900`) auf weiterhin weisser Papierflaeche - nahezu unlesbar.
    Ueberschriften/Zitate/Links blieben lesbar, weil sie bereits eigene,
    explizite `var(...)`-Deklarationen haben, die den lokal neu
    verankerten Tokenwert korrekt erneut auslesen; normale Absaetze/
    Listen/Fettschrift ohne eigene Farbregel nicht. Regressionsschutz:
    `.chat-document-panel__content` muss die lokal neu verankerte
    `--ink-900`-Variable selbst erneut referenzieren, statt sich auf reine
    Vererbung von aussen zu verlassen."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/static/css/app.css")
    assert response.status_code == 200
    css = response.text
    content_start = css.index(".chat-document-panel__content {")
    content_end = css.index("}", content_start)
    content_block = css[content_start:content_end]
    assert "color: var(--ink-900);" in content_block


def test_thinking_indicator_dots_use_a_dedicated_class_not_a_bare_span_selector(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (07.10., Owner-Direktive "CHAT UI/UX + INTENT ROOT-CAUSE
    PASS", Referenz-Screenshot 44): `.chat-thinking-indicator span` traf
    bisher per Element-Selektor AUCH `.chat-thinking-indicator__status`
    (ebenfalls ein `<span>`) und zwang dessen Statustext in eine
    6x6px-Box - jedes Zeichen brach dadurch in eine eigene Zeile um.
    Regressionsschutz: die CSS-Datei darf den bare-`span`-Selektor nicht
    mehr enthalten, UND das HTML/JS muss die neue, dedizierte
    `__dot`-Klasse tatsaechlich verwenden."""
    login_as_admin(db_session, client)
    css_response = client.get("/dashboard/static/css/app.css")
    assert css_response.status_code == 200
    assert ".chat-thinking-indicator span {" not in css_response.text
    assert ".chat-thinking-indicator__dot {" in css_response.text

    chat_response = client.get("/dashboard/chat")
    assert chat_response.status_code == 200
    assert "chat-thinking-indicator__dot" in chat_response.text


def test_permanent_privacy_hint_below_composer_is_replaced_by_an_icon_popover(
    client: TestClient, db_session: Session
) -> None:
    """§9/§10 der Owner-Direktive "CHAT UI/UX + INTENT ROOT-CAUSE PASS"
    (07.10.): der bisher dauerhaft unter JEDER Unterhaltung sichtbare
    Fliesstext muss verschwunden sein, ersetzt durch ein dezentes
    Datenschutz-Icon mit Popover - das Popover deckt ausdruecklich
    allgemein personenbezogene/sensible Daten ab, nicht nur
    Dokumentinhalte, und behauptet KEINE absolute "immer"-Garantie."""
    login_as_admin(db_session, client)
    conversation = _active_conversation(db_session, "admin@kanzlei.test")
    db_session.add(ChatMessage(conversation_id=conversation.id, role="user", content="Hallo."))
    db_session.commit()

    response = client.get(f"/dashboard/chat/{conversation.id}")

    assert response.status_code == 200
    assert "chat-composer__hint" not in response.text
    assert "Dokumentinhalte werden vor jeder KI-Anfrage lokal pseudonymisiert -" not in response.text
    assert "chat-composer__privacy-btn" in response.text
    assert "chat-privacy-popover" in response.text
    assert "personenbezogene" in response.text


def test_scroll_to_bottom_button_present_in_composer(
    client: TestClient, db_session: Session
) -> None:
    """§8 der Direktive: ein schwebender Scroll-Button muss im Markup
    vorhanden sein (Sichtbarkeit selbst ist reines Laufzeitverhalten,
    hier nur die strukturelle Voraussetzung dafuer geprueft)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert response.status_code == 200
    assert 'id="chat-scroll-to-bottom"' in response.text


def test_scroll_to_bottom_button_has_a_hidden_attribute_display_override(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (07.10., live per CDP reproduziert): `.chat-scroll-to-
    bottom` deklariert sein eigenes `display: flex` - ein Autoren-Stylesheet
    mit eigener `display`-Deklaration hat IMMER Vorrang vor der UA-
    Standardregel `[hidden]{display:none}`, unabhaengig von Spezifitaet
    (Autoren- schlagen User-Agent-Stylesheets in der Kaskade). Ohne eine
    explizite `.chat-scroll-to-bottom[hidden]{display:none}`-Regel blieb
    der Button dadurch permanent sichtbar, obwohl JS das `hidden`-Attribut
    korrekt setzte - Regressionsschutz dafuer."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/static/css/app.css")
    assert response.status_code == 200
    css = response.text
    assert ".chat-scroll-to-bottom[hidden] {" in css
    rule_start = css.index(".chat-scroll-to-bottom[hidden] {")
    rule_end = css.index("}", rule_start)
    assert "display: none;" in css[rule_start:rule_end]


def test_chat_panel_messages_scrollbar_is_hidden_but_overflow_stays_scrollable(
    client: TestClient, db_session: Session
) -> None:
    """§7: nur die SICHTBARE Scrollbar darf verschwinden - `overflow-y:
    auto` (= weiterhin scrollbar per Maus/Trackpad/Touch) muss erhalten
    bleiben, `overflow:hidden` ist explizit verboten (wuerde das Scrollen
    selbst deaktivieren)."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/static/css/app.css")
    assert response.status_code == 200
    css = response.text
    start = css.index(".chat-panel__messages {")
    end = css.index("}", start)
    block = css[start:end]
    assert "overflow-y: auto;" in block
    assert "overflow: hidden" not in block
    assert "scrollbar-width: none;" in block
    assert ".chat-panel__messages::-webkit-scrollbar {\n  display: none;\n}" in css
