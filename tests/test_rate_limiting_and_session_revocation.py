"""Tests für Prompt 29: Rate-Limiting beim Login + Session-Sofortwiderruf
bei Passwortänderung/Admin-Aktion.

Beide Punkte waren im Security Review (Prompt 27) als offene
Sicherheitsrisiken dokumentiert - dieser Prompt setzt die konkreten Fixes
um und beweist sie per Test.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.rate_limit import LoginRateLimiter, login_rate_limiter
from app.auth.security import hash_password
from app.auth.service import UserService
from app.db.session import get_db
from app.main import app
from app.models import Role, User
from app.models.base import Base

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None
    return match.group(1)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
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
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def user(db_session: Session) -> User:
    role = Role(name="Anwalt")
    db_session.add(role)
    db_session.commit()
    u = User(
        email="anwalt@kanzlei.test",
        role_id=role.id,
        is_active=True,
        password_hash=hash_password("UrsprungsPasswort123"),
        must_change_password=False,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, email: str, password: str):
    return client.post(
        "/dashboard/login",
        data={"email": email, "password": password, "next": "/dashboard/inbox"},
        follow_redirects=False,
    )


# ==========================================================================
# 1. Rate-Limiting (Unit-Ebene: LoginRateLimiter isoliert)
# ==========================================================================


def test_rate_limiter_allows_attempts_below_threshold() -> None:
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, lockout_seconds=900)
    for _ in range(4):
        limiter.record_failure("test@x.de")
    assert limiter.is_locked_out("test@x.de") is False


def test_rate_limiter_locks_out_after_threshold() -> None:
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, lockout_seconds=900)
    for _ in range(5):
        limiter.record_failure("test@x.de")
    assert limiter.is_locked_out("test@x.de") is True


def test_rate_limiter_success_clears_failures() -> None:
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, lockout_seconds=900)
    for _ in range(4):
        limiter.record_failure("test@x.de")
    limiter.record_success("test@x.de")
    for _ in range(4):
        limiter.record_failure("test@x.de")
    assert limiter.is_locked_out("test@x.de") is False


def test_rate_limiter_keys_are_independent() -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=900, lockout_seconds=900)
    for _ in range(3):
        limiter.record_failure("email:a@x.de")
    assert limiter.is_locked_out("email:a@x.de") is True
    assert limiter.is_locked_out("email:b@x.de") is False
    assert limiter.is_locked_out("ip:127.0.0.1") is False


# ==========================================================================
# 2. Rate-Limiting über den echten Login-Endpunkt
# ==========================================================================


def test_login_gets_locked_out_after_repeated_failures(
    client: TestClient, user: User
) -> None:
    for _ in range(5):
        response = _login(client, "anwalt@kanzlei.test", "falschesPasswort")
        assert response.status_code == 303
        assert "lexono_session" not in response.cookies

    # Der 6. Versuch - diesmal mit dem RICHTIGEN Passwort - wird trotzdem
    # blockiert, weil die Kontosperre bereits ausgelöst wurde.
    response = _login(client, "anwalt@kanzlei.test", "UrsprungsPasswort123")
    assert "lexono_session" not in response.cookies
    assert "Zu%20viele%20Fehlversuche" in response.headers["location"]


def test_login_not_locked_out_below_threshold(client: TestClient, user: User) -> None:
    for _ in range(4):
        _login(client, "anwalt@kanzlei.test", "falschesPasswort")

    response = _login(client, "anwalt@kanzlei.test", "UrsprungsPasswort123")
    assert "lexono_session" in response.cookies


def test_successful_login_resets_failure_count(client: TestClient, user: User) -> None:
    for _ in range(3):
        _login(client, "anwalt@kanzlei.test", "falschesPasswort")
    success = _login(client, "anwalt@kanzlei.test", "UrsprungsPasswort123")
    assert "lexono_session" in success.cookies

    for _ in range(3):
        _login(client, "anwalt@kanzlei.test", "falschesPasswort")
    response = _login(client, "anwalt@kanzlei.test", "UrsprungsPasswort123")
    assert "lexono_session" in response.cookies


def test_rate_limit_error_message_does_not_reveal_account_existence(
    client: TestClient,
) -> None:
    """Auch die Rate-Limit-Meldung selbst darf nicht verraten, ob ein
    Konto überhaupt existiert - dieselbe generische Meldung für ein
    existierendes wie ein nicht-existierendes Konto."""
    for _ in range(5):
        _login(client, "existiert-nicht@kanzlei.test", "irgendwas")
    response = _login(client, "existiert-nicht@kanzlei.test", "irgendwas")
    assert "Zu%20viele%20Fehlversuche" in response.headers["location"]


# ==========================================================================
# 3. Session-Sofortwiderruf bei Passwortänderung
# ==========================================================================


def test_password_change_invalidates_other_existing_sessions(
    client: TestClient, db_session: Session, user: User
) -> None:
    """Kernbeweis: eine (z. B. gestohlene) zweite Session desselben
    Nutzers wird durch eine Passwortänderung SOFORT ungültig - nicht erst
    nach Ablauf der 8 Stunden."""
    stolen_session_client = TestClient(app)
    app.dependency_overrides[get_db] = lambda: db_session
    login_response = _login(
        stolen_session_client, "anwalt@kanzlei.test", "UrsprungsPasswort123"
    )
    assert "lexono_session" in login_response.cookies

    still_valid = stolen_session_client.get("/dashboard/inbox")
    assert still_valid.status_code == 200

    UserService().change_password(
        db_session, user, "NeuesPasswort456", actor="anwalt@kanzlei.test"
    )

    after_change = stolen_session_client.get("/dashboard/inbox", follow_redirects=False)
    assert after_change.status_code == 303
    assert "/dashboard/login" in after_change.headers["location"]

    app.dependency_overrides.clear()


def test_new_login_after_password_change_works_normally(
    client: TestClient, db_session: Session, user: User
) -> None:
    UserService().change_password(
        db_session, user, "NeuesPasswort456", actor="anwalt@kanzlei.test"
    )
    response = _login(client, "anwalt@kanzlei.test", "NeuesPasswort456")
    assert "lexono_session" in response.cookies


def test_sessions_issued_after_password_change_remain_valid(
    client: TestClient, db_session: Session, user: User
) -> None:
    """Nur Sessions von VOR der Änderung werden ungültig - eine danach neu
    ausgestellte Session bleibt für ihre volle Gültigkeitsdauer nutzbar."""
    UserService().change_password(
        db_session, user, "NeuesPasswort456", actor="anwalt@kanzlei.test"
    )
    login_response = _login(client, "anwalt@kanzlei.test", "NeuesPasswort456")
    assert "lexono_session" in login_response.cookies

    still_works = client.get("/dashboard/inbox")
    assert still_works.status_code == 200


# ==========================================================================
# 4. Admin-Aktion "Sessions beenden" (ohne Passwortänderung)
# ==========================================================================


def test_admin_force_logout_invalidates_target_users_sessions(
    db_session: Session,
) -> None:
    role_admin = Role(name="Admin")
    role_anwalt = Role(name="Anwalt")
    db_session.add_all([role_admin, role_anwalt])
    db_session.commit()
    admin = User(
        email="admin@kanzlei.test",
        role_id=role_admin.id,
        is_active=True,
        password_hash=hash_password("AdminPasswort123"),
        must_change_password=False,
    )
    target = User(
        email="anwalt2@kanzlei.test",
        role_id=role_anwalt.id,
        is_active=True,
        password_hash=hash_password("ZielPasswort123"),
        must_change_password=False,
    )
    db_session.add_all([admin, target])
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    target_client = TestClient(app)
    login_response = _login(target_client, "anwalt2@kanzlei.test", "ZielPasswort123")
    assert "lexono_session" in login_response.cookies
    assert target_client.get("/dashboard/inbox").status_code == 200

    admin_client = TestClient(app)
    admin_login = _login(admin_client, "admin@kanzlei.test", "AdminPasswort123")
    assert "lexono_session" in admin_login.cookies
    users_page = admin_client.get("/dashboard/admin/users")
    csrf = _extract_csrf(users_page.text)

    force_logout_response = admin_client.post(
        f"/dashboard/admin/users/{target.id}/force-logout",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert force_logout_response.status_code == 303

    after_force_logout = target_client.get("/dashboard/inbox", follow_redirects=False)
    assert after_force_logout.status_code == 303
    assert "/dashboard/login" in after_force_logout.headers["location"]

    app.dependency_overrides.clear()


def test_deactivation_still_works_immediately_as_before(
    client: TestClient, db_session: Session, user: User
) -> None:
    """Regressionsschutz: die bereits vorher sofortige Wirkung von
    `is_active=False` bleibt unveraendert erhalten (Korrektur zur
    ursprünglichen Einschätzung im Security-Review-Bericht - Deaktivierung
    war nie verzögert, siehe SECURITY_REVIEW.md-Nachtrag)."""
    login_response = _login(client, "anwalt@kanzlei.test", "UrsprungsPasswort123")
    assert "lexono_session" in login_response.cookies
    assert client.get("/dashboard/inbox").status_code == 200

    UserService().set_active(db_session, user, False, actor="admin@kanzlei.test")

    after_deactivation = client.get("/dashboard/inbox", follow_redirects=False)
    assert after_deactivation.status_code == 303
