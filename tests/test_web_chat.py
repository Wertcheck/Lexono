"""Tests für app/web/chat_router.py - die neue Chat-Startseite
(UI-Überarbeitung: "Login → Chat → zentrale Arbeitsoberfläche").

Gleiches Testmuster wie tests/test_web_schriftsatz.py: In-Memory-SQLite
über app.dependency_overrides, `get_drafting_service` wird in
app.web.chat_router direkt gemonkeypatcht. Deckt die im Auftrag
geforderten Szenarien TEST A-E ab."""

from __future__ import annotations

from collections.abc import Iterator

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
    login_as_admin(db_session, client)
    response = client.get("/dashboard/chat")
    assert 'href="/dashboard/chat"' in response.text
