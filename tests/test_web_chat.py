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
from app.models import ChatConversation, ChatMessage, Client, Matter
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


def test_chat_empty_state_quick_actions_have_distinct_accent_colors(
    client: TestClient, db_session: Session
) -> None:
    """Weitere Akzentfarben (01.09., Referenzbild-Redesign): die vier
    Chat-Schnellaktionen sollen sich farblich unterscheiden statt alle
    dasselbe einfarbige Icon zu zeigen. Seit dem Referenzbild-Redesign
    bewusst OHNE Gruen - Gruen bleibt exklusiv die Markenfarbe (Logo/
    Sendebutton/aktive Chat-Navigation, siehe --brand-green in app.css),
    nicht fuer generische Schnellaktions-Icons wiederverwendet."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert "chat-quick-action__icon--blue" in response.text
    assert "chat-quick-action__icon--purple" in response.text
    assert "chat-quick-action__icon--orange" in response.text
    assert "chat-quick-action__icon--neutral" in response.text


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
    assert "mandant@beispielkanzlei.de" in workspace_response.text
    assert 'pii-highlight--email' in workspace_response.text
    assert "Pseudonymisierung" in workspace_response.text
    assert "Erkannte Mandantendaten" in workspace_response.text

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
