"""Integrationstests für Login/Rollen/Berechtigungen über das Dashboard
und die JSON-API (Prompt 26).

Deckt aus der geforderten Testliste ab: #1-16 (siehe einzelne
Testfunktionen, jeweils mit der Nummer im Docstring referenziert).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.drafts_router as drafts_router_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.auth.security import hash_password
from app.db.session import get_db
from app.drafting.service import DraftingService
from app.main import app
from app.models import AttorneyInstruction, Client, Draft, Matter, OutboxEntry, Role, User
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None, "Kein csrf_token-Feld im HTML gefunden"
    return match.group(1)


class FakeClaudeWritingProvider:
    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        return ClaudeWritingResult(text="Neu generierte Antwort.", token_count=10)


def _working_drafting_service() -> DraftingService:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        FakeClaudeWritingProvider(),
        model_name="claude-sonnet-5",
    )


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    """Der Login-Rate-Limiter (Prompt 29) ist ein Prozess-Singleton -
    ohne Reset würden fehlgeschlagene Logins aus früheren Tests sich
    über die gesamte Testsuite hinweg aufsummieren und irgendwann
    spätere, eigentlich korrekte Logins fälschlich sperren."""
    from app.auth.rate_limit import login_rate_limiter

    login_rate_limiter.reset()
    yield
    login_rate_limiter.reset()


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
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        # cookies=True Verhalten ist Default bei TestClient - die
        # Session-Cookie aus dem Login-Response wird automatisch fuer
        # Folgeanfragen mitgeschickt (wie ein echter Browser).
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def roles(db_session: Session) -> dict[str, Role]:
    admin = Role(name="Admin")
    anwalt = Role(name="Anwalt")
    mitarbeiter = Role(name="Mitarbeiter")
    db_session.add_all([admin, anwalt, mitarbeiter])
    db_session.commit()
    return {"admin": admin, "anwalt": anwalt, "mitarbeiter": mitarbeiter}


def _make_user(db: Session, role: Role, email: str, password: str = "TestPasswort123") -> User:
    user = User(
        email=email,
        role_id=role.id,
        is_active=True,
        password_hash=hash_password(password),
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture()
def users(db_session: Session, roles: dict[str, Role]) -> dict[str, User]:
    return {
        "admin": _make_user(db_session, roles["admin"], "admin@kanzlei.test"),
        "anwalt": _make_user(db_session, roles["anwalt"], "anwalt@kanzlei.test"),
        "mitarbeiter": _make_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test"),
    }


@pytest.fixture()
def seeded_draft(db_session: Session) -> dict[str, str]:
    client_ = Client(name="Synthetischer Testmandant GmbH")
    matter = Matter(client=client_, title="Einspruch Steuerbescheid 2025")
    draft = Draft(matter=matter, content="Testinhalt.")
    db_session.add_all([client_, matter, draft])
    db_session.commit()
    return {"matter_id": matter.id, "draft_id": draft.id}


def _login(client: TestClient, email: str, password: str = "TestPasswort123") -> None:
    response = client.post(
        "/dashboard/login",
        data={"email": email, "password": password, "next": "/dashboard/inbox"},
        follow_redirects=False,
    )
    assert response.status_code == 303, f"Login fehlgeschlagen: {response.headers}"


# --- Eigene Fenster-Titelleiste auch VOR dem Login (Masterprompt V2,
#     Task #61) ---


def test_login_page_activates_custom_titlebar_script_before_login(
    client: TestClient,
) -> None:
    """Regressionstest fuer einen real gefundenen Bug (01.09.): das
    gebuendelte Fenster startet auf /dashboard/login, wo current_user noch
    None ist. Das Titelleisten-Aktivierungsskript lag zunaechst faelschlich
    INNERHALB von "{% if current_user %}" in base.html - dadurch blieb die
    per frameless=True bereits chromfreie Titelleiste auf der allerersten
    Seite dauerhaft unsichtbar (kein X, kein Minimieren, kein Verschieben -
    einzig per Task-Manager beendbar, real am echten Fenster beobachtet).
    Dieser Test stellt sicher, dass sowohl das Titelleisten-Markup ALS AUCH
    das Aktivierungsskript (nicht nur eines von beiden) bereits auf der
    unauthentifizierten Login-Seite im HTML vorhanden sind."""
    response = client.get("/dashboard/login")
    assert response.status_code == 200
    assert 'id="app-titlebar-close"' in response.text
    assert 'id="app-titlebar-minimize"' in response.text
    assert '<script src="/dashboard/static/js/app_titlebar.js"></script>' in response.text

    script_response = client.get("/dashboard/static/js/app_titlebar.js")
    assert script_response.status_code == 200
    assert "activateCustomTitlebar" in script_response.text
    assert "close_window" in script_response.text


# --- #1 Login erfolgreich ---


def test_login_successful(client: TestClient, users: dict) -> None:
    response = client.post(
        "/dashboard/login",
        data={"email": "anwalt@kanzlei.test", "password": "TestPasswort123", "next": "/dashboard/inbox"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/inbox"
    assert "kanzlei_ai_session" in response.cookies


# --- #2 Login mit falschem Passwort ---


def test_login_with_wrong_password(client: TestClient, users: dict) -> None:
    response = client.post(
        "/dashboard/login",
        data={"email": "anwalt@kanzlei.test", "password": "FalschesPasswort", "next": "/dashboard/inbox"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "kanzlei_ai_session" not in response.cookies

    # Und tatsaechlich weiterhin nicht angemeldet:
    protected = client.get("/dashboard/inbox", follow_redirects=False)
    assert protected.status_code == 303
    assert "/dashboard/login" in protected.headers["location"]


# --- #3 Nicht authentifizierter Zugriff wird verweigert ---


def test_unauthenticated_access_denied(client: TestClient) -> None:
    response = client.get("/dashboard/inbox", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_unauthenticated_api_access_denied(client: TestClient) -> None:
    response = client.get("/api/matters")
    assert response.status_code == 401


# --- #4 Mitarbeiter kann Anmerkung speichern ---


def test_mitarbeiter_can_save_instruction(
    client: TestClient, db_session: Session, users: dict, seeded_draft: dict
) -> None:
    _login(client, "mitarbeiter@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/instructions",
        data={"instruction_text": "Auf Punkt 3 eingehen.", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    instructions = db_session.query(AttorneyInstruction).all()
    assert len(instructions) == 1


# --- #5 Mitarbeiter kann keinen Claude-Call auslösen ---


def test_mitarbeiter_cannot_trigger_claude_call(
    client: TestClient, users: dict, seeded_draft: dict
) -> None:
    _login(client, "mitarbeiter@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/regenerate",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 403


def test_mitarbeiter_cannot_apply_instruction(
    client: TestClient, users: dict, seeded_draft: dict
) -> None:
    _login(client, "mitarbeiter@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/instructions/apply",
        data={"instruction_text": "Test.", "csrf_token": csrf},
    )
    assert response.status_code == 403


# --- #6 Mitarbeiter kann nicht freigeben ---


def test_mitarbeiter_cannot_approve(client: TestClient, users: dict, seeded_draft: dict) -> None:
    _login(client, "mitarbeiter@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/approve",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 403


# --- #7 Mitarbeiter kann nicht zurückweisen ---


def test_mitarbeiter_cannot_reject(client: TestClient, users: dict, seeded_draft: dict) -> None:
    _login(client, "mitarbeiter@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/reject",
        data={"comment": "Testgrund.", "csrf_token": csrf},
    )
    assert response.status_code == 403


# --- #8 Mitarbeiter kann nicht als versendet markieren ---


def test_mitarbeiter_cannot_mark_sent(
    client: TestClient, db_session: Session, users: dict, seeded_draft: dict
) -> None:
    entry = OutboxEntry(matter_id=seeded_draft["matter_id"], draft_id=seeded_draft["draft_id"])
    db_session.add(entry)
    db_session.commit()

    _login(client, "mitarbeiter@kanzlei.test")
    outbox_page = client.get("/dashboard/outbox")
    # Bewusst der echte Regex-Treffer als Bedingung, NICHT eine naive
    # Substring-Suche nach "csrf_token" - seit Schritt 3 (PIN-Lock,
    # base.html) enthält JEDE angemeldete Seite dieses Wort bereits im
    # eingebetteten Inaktivitäts-Skript, unabhängig davon, ob die
    # jeweilige Seite selbst ein echtes CSRF-Formularfeld für DIESE Aktion
    # rendert.
    csrf_match = _CSRF_RE.search(outbox_page.text)
    csrf = csrf_match.group(1) if csrf_match else None
    # Mitarbeiter sieht in der Ansicht gar kein CSRF-Feld fuer diese
    # Aktion (Button ausgeblendet), daher direkt mit leerem Platzhalter -
    # der servereitige Check muss trotzdem (unabhängig vom UI) greifen.
    response = client.post(
        f"/dashboard/outbox/{entry.id}/mark-sent",
        data={"csrf_token": csrf or "irrelevant"},
    )
    assert response.status_code == 403


# --- #9 Anwalt kann freigeben ---


def test_anwalt_can_approve(
    client: TestClient, db_session: Session, users: dict, seeded_draft: dict
) -> None:
    _login(client, "anwalt@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/approve",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    draft = db_session.get(Draft, seeded_draft["draft_id"])
    assert draft.status == "approved"


# --- #10 Anwalt kann zurückweisen ---


def test_anwalt_can_reject(
    client: TestClient, db_session: Session, users: dict, seeded_draft: dict
) -> None:
    _login(client, "anwalt@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/reject",
        data={"comment": "Nicht ausreichend begründet.", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    draft = db_session.get(Draft, seeded_draft["draft_id"])
    assert draft.status == "rejected"


# --- #11 Anwalt kann Claude-Neugenerierung auslösen ---


def test_anwalt_can_trigger_claude_regeneration(
    client: TestClient,
    db_session: Session,
    users: dict,
    seeded_draft: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        drafts_router_module, "get_drafting_service", lambda: _working_drafting_service()
    )
    _login(client, "anwalt@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/regenerate",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    new_draft_id = response.headers["location"].rsplit("/", 1)[-1]
    new_draft = db_session.get(Draft, new_draft_id)
    assert new_draft.content == "Neu generierte Antwort."


# --- #12 Admin kann Nutzer verwalten ---


def test_admin_can_manage_users(client: TestClient, db_session: Session, users: dict) -> None:
    _login(client, "admin@kanzlei.test")
    users_page = client.get("/dashboard/admin/users")
    assert users_page.status_code == 200
    csrf = _extract_csrf(users_page.text)

    response = client.post(
        "/dashboard/admin/users",
        data={"email": "neu@kanzlei.test", "role_name": "Mitarbeiter", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    created = db_session.query(User).filter_by(email="neu@kanzlei.test").first()
    assert created is not None
    assert created.must_change_password is True


# --- #13 Mitarbeiter kann keine Nutzer verwalten ---


def test_mitarbeiter_cannot_view_user_management(client: TestClient, users: dict) -> None:
    _login(client, "mitarbeiter@kanzlei.test")
    response = client.get("/dashboard/admin/users")
    assert response.status_code == 403


def test_mitarbeiter_cannot_create_user(client: TestClient, users: dict) -> None:
    _login(client, "mitarbeiter@kanzlei.test")
    # Selbst mit irgendeinem CSRF-Token - die Rollenprüfung greift zuerst
    # bzw. unabhängig davon, der eigentliche Test ist die Rollensperre.
    detail = client.get("/dashboard/inbox")
    # Siehe Kommentar bei test_mitarbeiter_cannot_mark_sent: echter
    # Regex-Treffer statt naiver Substring-Suche.
    csrf_match = _CSRF_RE.search(detail.text)
    csrf = csrf_match.group(1) if csrf_match else "x"
    response = client.post(
        "/dashboard/admin/users",
        data={"email": "hack@kanzlei.test", "role_name": "Admin", "csrf_token": csrf},
    )
    assert response.status_code == 403


# --- #14 Direkter API-/Endpunkt-Aufruf durch Mitarbeiter wird serverseitig verweigert ---


def test_no_unprotected_api_path_exists_for_restricted_actions(client: TestClient) -> None:
    """Strukturelle Absicherung: es darf KEINEN /api/-Endpunkt geben, über
    den Freigeben/Zurückweisen/Neugenerieren/Versandmarkierung/Nutzer-
    verwaltung an der Dashboard-Rollenprüfung vorbei erreichbar wären."""
    schema = client.get("/openapi.json").json()
    api_paths = [p for p in schema["paths"] if p.startswith("/api/")]
    for path in api_paths:
        methods = schema["paths"][path].keys()
        assert "post" not in methods
        assert "put" not in methods
        assert "delete" not in methods
        assert "patch" not in methods


def test_mitarbeiter_direct_post_to_approve_endpoint_denied_even_with_valid_csrf(
    client: TestClient, users: dict, seeded_draft: dict
) -> None:
    """Simuliert einen direkten, nicht über einen UI-Button ausgelösten
    POST-Aufruf (z. B. curl/Skript) mit korrektem CSRF-Token - die
    Rollenprüfung muss trotzdem greifen. Ein ausgeblendeter Button ist
    KEINE Berechtigungsprüfung (Vorgabe des Anwalts)."""
    _login(client, "mitarbeiter@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)  # gueltiges Token, Button war evtl. gar nicht sichtbar

    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/approve",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 403


# --- #15 Actor stammt aus Session, nicht aus einem Request-Feld ---


def test_actor_comes_from_session_not_from_spoofed_field(
    client: TestClient, db_session: Session, users: dict, seeded_draft: dict
) -> None:
    """Selbst wenn ein manipuliertes Formular versucht, einen anderen
    Actor mitzuschicken (z. B. ein 'actor'-Feld, das es in den Routen gar
    nicht mehr gibt), bleibt der tatsächliche Actor der angemeldete
    Nutzer - das Feld existiert serverseitig schlicht nicht mehr."""
    _login(client, "mitarbeiter@kanzlei.test")
    detail = client.get(f"/dashboard/drafts/{seeded_draft['draft_id']}")
    csrf = _extract_csrf(detail.text)

    client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/instructions",
        data={
            "instruction_text": "Testanmerkung.",
            "csrf_token": csrf,
            "actor": "anwalt@kanzlei.test",  # Spoofing-Versuch
        },
    )

    instruction = db_session.query(AttorneyInstruction).first()
    assert instruction.actor == "mitarbeiter@kanzlei.test"
    assert instruction.actor != "anwalt@kanzlei.test"


# --- #16 Logout invalidiert die Session ---


def test_logout_invalidates_session(client: TestClient, users: dict) -> None:
    _login(client, "anwalt@kanzlei.test")
    still_in = client.get("/dashboard/inbox")
    assert still_in.status_code == 200

    logout_response = client.post("/dashboard/logout", follow_redirects=False)
    assert logout_response.status_code == 303

    after_logout = client.get("/dashboard/inbox", follow_redirects=False)
    assert after_logout.status_code == 303
    assert "/dashboard/login" in after_logout.headers["location"]


# --- CSRF-Schutz (ergänzend zur geforderten Liste) ---


def test_post_without_csrf_token_is_rejected(client: TestClient, users: dict, seeded_draft: dict) -> None:
    _login(client, "anwalt@kanzlei.test")
    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/approve", data={}
    )
    assert response.status_code == 422  # Form-Feld csrf_token fehlt komplett


def test_post_with_wrong_csrf_token_is_rejected(
    client: TestClient, users: dict, seeded_draft: dict
) -> None:
    _login(client, "anwalt@kanzlei.test")
    response = client.post(
        f"/dashboard/drafts/{seeded_draft['draft_id']}/approve",
        data={"csrf_token": "falsches-token"},
    )
    assert response.status_code == 403
