"""Tests für den in app/web/settings_router.py + settings.html eingebetteten
"Benutzer"-Tab (06.10., Owner-Direktive "SETTINGS -> BENUTZER").

Deckt NUR ab, was dieser Tab NEU hinzufügt (echte Benutzerliste/Status/
Letzte-Anmeldung-Ableitung/Rollen-&-Berechtigungen-Karte/Aktivitätsverlauf/
Sicherheits-Karte, eingebettet statt separat). Zugriffsschutz (Login-/
Admin-Pflicht für /dashboard/settings) UND die POST-Endpunkte selbst
(create_user/set_role/set_active/reset_password/force_logout, inkl. des
Letzter-Admin-Schutzes) sind bereits an anderer Stelle gründlich getestet
(tests/test_web_settings.py bzw. tests/test_auth_web.py) - hier bewusst
KEINE Duplikation, nur das, was speziell in diesem Tab neu ist.

Isolation: exakt dasselbe Muster wie tests/test_web_settings.py (`env_path`
biegt resolve_data_dir()/get_settings() auf tmp_path um, `db_session` ist
eine isolierte In-Memory-SQLite-DB)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models import AuditEvent, User
from app.models.base import Base
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".env"
    target.write_text(
        "APP_ENV=development\nSESSION_SECRET_KEY=test-secret-key-not-random-000000\n",
        encoding="utf-8",
    )
    return target


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


def _login_admin(client: TestClient, db_session: Session, email: str = "admin@kanzlei.test") -> User:
    roles = seed_roles(db_session)
    user = create_test_user(db_session, roles["admin"], email)
    login(client, email)
    return user


def _benutzer_tab(client: TestClient) -> str:
    response = client.get("/dashboard/settings?tab=benutzer")
    assert response.status_code == 200
    return response.text


# --- Echte Benutzerliste (kein Mock/Fake) ---


def test_benutzer_tab_lists_real_users_not_reference_names(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/real-data-only: die Referenz nennt "Max Mustermann"/"Anna Huber" als
    Designbeispiele - diese Namen dürfen NIRGENDS im gerenderten HTML
    auftauchen, nur die tatsächlich angelegten Testnutzer."""
    _login_admin(client, db_session)
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["anwalt"], "echter.anwalt@kanzlei.test")

    html = _benutzer_tab(client)
    assert "echter.anwalt@kanzlei.test" in html
    assert "admin@kanzlei.test" in html
    for fake_name in ("Max Mustermann", "Anna Huber", "Stefan König", "Laura Schmidt", "Tobias Becker", "Maria Klein"):
        assert fake_name not in html


def test_benutzer_tab_status_reflects_must_change_password(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/status: "Einladung ausstehend" ist NUR ein ehrlicher Status, wenn
    `must_change_password=True` tatsächlich gesetzt ist (create_user/
    reset_password setzen es immer) - kein erfundenes "Online"."""
    _login_admin(client, db_session)
    roles = seed_roles(db_session)
    pending_user = create_test_user(db_session, roles["mitarbeiter"], "neu@kanzlei.test")
    pending_user.must_change_password = True
    inactive_user = create_test_user(db_session, roles["anwalt"], "inaktiv@kanzlei.test")
    inactive_user.is_active = False
    db_session.commit()

    html = _benutzer_tab(client)
    assert "Einladung ausstehend" in html
    assert "Deaktiviert" in html
    assert "Online" not in html


def test_benutzer_tab_shows_never_logged_in_when_no_login_event_exists(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/inspect-Fund: `User` hat kein `last_login_at`-Feld - ohne ein
    passendes `login_succeeded`-AuditEvent darf NICHTS erfunden werden."""
    _login_admin(client, db_session)
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["anwalt"], "noch-nie@kanzlei.test")

    html = _benutzer_tab(client)
    assert "Noch nie angemeldet" in html


def test_benutzer_tab_derives_last_login_from_audit_event(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Der tatsächliche Login des Admins in `_login_admin` erzeugt bereits
    ein echtes `login_succeeded`-AuditEvent (siehe AuthService.authenticate) -
    "Letzte Anmeldung" muss dessen echtes Datum zeigen, nicht "Noch nie"."""
    admin = _login_admin(client, db_session)
    login_event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.entity_type == "User", AuditEvent.event_type == "login_succeeded")
        .filter(AuditEvent.entity_id == admin.id)
        .first()
    )
    assert login_event is not None

    html = _benutzer_tab(client)
    assert login_event.created_at.strftime("%d.%m.%Y") in html


# --- Rollen & Berechtigungen: nur echte, serverseitig durchgesetzte Rechte ---


def test_benutzer_tab_role_permissions_match_permission_matrix(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/permissions: die UI darf NUR Berechtigungen zeigen, die
    PERMISSION_MATRIX tatsächlich kennt - Mitarbeiter darf laut Matrix
    keine Nutzerverwaltung, Admin schon."""
    _login_admin(client, db_session)
    html = _benutzer_tab(client)
    assert "Benutzer- &amp; Rollenverwaltung" in html

    assert "Rollen &amp; Berechtigungen" in html
    role_card_start = html.index("Rollen &amp; Berechtigungen")
    # Nicht nach dem bloßen Teilstring "invite-user-card" suchen: der
    # "+ Benutzer einladen"-Anker (href="#invite-user-card") liegt bereits
    # VOR dieser Karte in der Hauptspalte - gesucht ist das Karten-Element
    # selbst (id="invite-user-card").
    role_card_end = html.index('id="invite-user-card"')
    role_card_block = html[role_card_start:role_card_end]
    # "Mitarbeiter" ist laut _ROLE_DISPLAY_ORDER die LETZTE Rolle in dieser
    # Karte - alles ab ihrer Ueberschrift bis zum Kartenende gehoert zu
    # ihrer eigenen Berechtigungsliste (laut PERMISSION_MATRIX OHNE
    # user:manage).
    mitarbeiter_block = role_card_block[role_card_block.index("Mitarbeiter") :]
    assert "Benutzer- &amp; Rollenverwaltung" not in mitarbeiter_block


def test_benutzer_tab_reference_roles_not_fabricated(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/roles: die Referenzbild-Rollen (Rechtsanwalt/Referendar/Assistenz/
    Sekretariat) existieren in dieser Codebasis nicht und dürfen NICHT
    automatisch übernommen werden - nur Admin/Anwalt/Mitarbeiter."""
    _login_admin(client, db_session)
    html = _benutzer_tab(client)
    for fake_role in ("Rechtsanwalt", "Referendar", "Assistenz", "Sekretariat"):
        assert fake_role not in html


# --- Sicherheit: nur real existierende Mechanismen, kein Vortäuschen ---


def test_benutzer_tab_shows_honest_2fa_unavailable_status(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/security-settings: 2FA existiert in dieser Codebasis nirgends
    (bestätigt per Grep) - KEIN wirkungsloser Toggle, ehrliches "Nicht
    verfügbar"."""
    _login_admin(client, db_session)
    html = _benutzer_tab(client)
    assert "Zwei-Faktor-Authentifizierung" in html
    assert "Nicht verfügbar" in html


def test_benutzer_tab_shows_real_inactivity_lock_minutes(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Die Inaktivitäts-Sperrminuten stammen aus `Settings.
    pin_lock_inactivity_minutes` (Default 10, app/config/settings.py),
    NICHT aus den referenzbildtypischen 60 Minuten."""
    _login_admin(client, db_session)
    html = _benutzer_tab(client)
    assert "Nach 10 Minuten Inaktivität" in html


# --- Keine Secrets im UI ---


def test_benutzer_tab_never_exposes_password_or_pin_hashes(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/security-invariants #6: KEINE Passwort-/PIN-Hashes im Frontend."""
    admin = _login_admin(client, db_session)
    html = _benutzer_tab(client)
    assert admin.password_hash not in html
    if admin.pin_hash:
        assert admin.pin_hash not in html


# --- Einladen (bestehender create_user-Flow, jetzt eingebettet) ---


def test_invite_user_via_embedded_form_shows_one_time_password_banner(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/invite: nutzt den bereits bestehenden Flow (UserService.create_user)
    - erzeugt einen echten Account + Einmal-Passwort-Banner, KEINE direkte
    Aktivierung ohne Passwortänderung beim ersten Login."""
    _login_admin(client, db_session)
    page = client.get("/dashboard/settings?tab=benutzer")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/admin/users",
        data={"email": "eingeladen@kanzlei.test", "role_name": "Mitarbeiter", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "tab=benutzer" in response.headers["location"]

    created = db_session.query(User).filter_by(email="eingeladen@kanzlei.test").first()
    assert created is not None
    assert created.must_change_password is True

    followed = client.get(response.headers["location"])
    assert "Wird nur EINMALIG angezeigt" in followed.text
    assert "eingeladen@kanzlei.test" in followed.text


def test_invite_user_with_unknown_role_is_rejected(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/negative-tests #4 (Rolle manipuliert): ein nicht existierender
    Rollenname darf keinen Nutzer anlegen und nicht abstürzen."""
    _login_admin(client, db_session)
    page = client.get("/dashboard/settings?tab=benutzer")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/admin/users",
        data={"email": "hack@kanzlei.test", "role_name": "Superadmin", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(User).filter_by(email="hack@kanzlei.test").first() is None


# --- Alte Standalone-Seite leitet jetzt hierher um (keine zweite UI) ---


def test_old_standalone_users_page_redirects_to_settings_tab(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    response = client.get("/dashboard/admin/users", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/settings?tab=benutzer"
