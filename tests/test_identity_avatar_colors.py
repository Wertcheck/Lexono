"""Tests für die Nutzer-/Lexono-Identitätsfarben im UI (07.10., Owner-
Direktive "LEXONO/USER-IDENTITAET IM UI KONSISTENT").

ECHTER FUND (vor dieser Direktive reproduziert): `--seal-green` ist trotz
des Namens KEIN Grünton (#101828 Navy im Hellmodus, #5b7fc4 Blaugrau im
Dunkelmodus, siehe :root-Definition in app.css) - wurde aber sowohl für
den Lexono-KI-Chat-Avatar als auch für den kleinen Sidebar-Profil-Avatar
des NUTZERS verwendet. `--brand-green` (das echte Lexono-Grün) wurde
umgekehrt sowohl für den Chat-Avatar des Nutzers als auch für den großen
Profil-Avatar in den Einstellungen verwendet - dort optisch nicht mehr
von Lexono selbst unterscheidbar.

Verbindliche Regel seither: Lexono/KI verwendet ÜBERALL ausschließlich
`--brand-green`, der angemeldete Nutzer bekommt eine deterministische,
bewusst NICHT grüne Akzentfarbe (`app/web/templates/_identity.html`).

ECHTER ARCHITEKTUR-FUND (ebenfalls diese Direktive): die Farbzuordnung
wurde zunächst als Python-Jinja-Global geplant (wie das bereits
bestehende, aber projektweit ungenutzte `avatar_color` in
app/web/router.py) - live reproduziert, dass das NICHT funktioniert,
sobald eine Seite über einen ANDEREN der ~24 unabhängigen
`Jinja2Templates`-Router-Instanzen gerendert wird (`jinja2.exceptions.
UndefinedError: 'avatar_color' is undefined` z. B. auf /dashboard/matters).
Deshalb als Makro (`_identity.html`) umgesetzt - funktioniert über den
gemeinsamen Dateisystem-Loader hinweg, unabhängig vom rendernden Router."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models.base import Base
from tests.auth_test_utils import login_as_admin


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
        test_client = TestClient(app)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def test_sidebar_avatar_is_rendered_by_a_page_from_a_different_router(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND: `base.html` (Sidebar-Fuss-Avatar) muss ueber JEDEN der
    ~24 unabhaengigen Router-Jinja2Templates-Instanzen funktionieren, nicht
    nur ueber app/web/router.py selbst - /dashboard/matters (matters_
    router.py) ist eine komplett unabhaengige Instanz und reproduzierte
    vor dem Makro-Fix zuverlaessig einen 500er."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/matters")

    assert response.status_code == 200
    assert "identity-avatar--" in response.text


def test_user_sidebar_avatar_never_uses_the_lexono_green_class(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)

    response = client.get("/dashboard/matters")

    assert "identity-avatar--green" not in response.text


def test_user_identity_color_is_consistent_across_sidebar_chat_and_settings(
    client: TestClient, db_session: Session
) -> None:
    """Derselbe angemeldete Nutzer muss ueberall dieselbe Identitaetsfarbe
    bekommen - EINE Quelle der Wahrheit (_identity.html::
    user_avatar_color), kein unabhaengiger zweiter Farb-Code pro Seite."""
    import re

    login_as_admin(db_session, client)

    colors_seen = set()
    for path in ("/dashboard/matters", "/dashboard/chat", "/dashboard/settings"):
        response = client.get(path)
        assert response.status_code == 200
        matches = re.findall(r"identity-avatar--(\w+)", response.text)
        assert matches, f"keine identity-avatar--Klasse auf {path} gefunden"
        colors_seen.update(matches)

    assert len(colors_seen) == 1, f"Nutzer sah unterschiedliche Farben: {colors_seen}"


def test_different_users_can_get_different_identity_colors(
    client: TestClient, db_session: Session
) -> None:
    """Keine Garantie auf PERFEKTE Verteilung, aber die Funktion darf nicht
    konstant denselben Wert liefern - mit genuegend unterschiedlichen
    E-Mail-Adressen muss mindestens eine zweite Farbe auftauchen."""
    import re

    seen_colors: set[str] = set()
    emails = [
        "anna@kanzlei.test",
        "max.mustermann@kanzlei.de",
        "erika@lexono.test",
        "thomas.weber@kanzlei-partner.de",
        "julia.schmidt@beispiel-kanzlei.de",
    ]
    for email in emails:
        login_as_admin(db_session, client, email=email)
        response = client.get("/dashboard/chat")
        assert response.status_code == 200
        match = re.search(r'sidebar__profile-avatar identity-avatar--(\w+)"', response.text)
        assert match, f"kein Sidebar-Avatar mit Identitaetsfarbe fuer {email} gefunden"
        seen_colors.add(match.group(1))
        client.cookies.clear()

    assert len(seen_colors) >= 2, f"alle Nutzer bekamen dieselbe Farbe: {seen_colors}"
    assert "green" not in seen_colors


def test_lexono_assistant_avatar_css_uses_brand_green_not_seal_green(
    client: TestClient, db_session: Session
) -> None:
    """Der Lexono-KI-Chat-Avatar muss IMMER `--brand-green` verwenden (das
    echte, aus dem Logo gemessene Gruen) - NICHT `--seal-green` (trotz des
    Namens kein Gruenton, siehe :root-Definition: #101828 Navy im
    Hellmodus, #5b7fc4 Blaugrau im Dunkelmodus - der real gemeldete Fehler
    "Lexono teilweise schwarz/blau")."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/static/css/app.css")
    assert response.status_code == 200
    css = response.text

    start = css.index(".chat-message--assistant .chat-message__avatar {")
    end = css.index("}", start)
    block = css[start:end]
    assert "var(--brand-green)" in block
    assert "var(--seal-green)" not in block


def test_user_chat_avatar_css_no_longer_hardcodes_brand_green(
    client: TestClient, db_session: Session
) -> None:
    """Gegenprobe: der Nutzer-Chat-Avatar darf `--brand-green` (Lexonos
    reservierte Farbe) nicht mehr als feste Basisfarbe tragen - die
    tatsaechliche Farbe kommt ausschliesslich aus der
    `.identity-avatar--*`-Modifier-Klasse."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/static/css/app.css")
    css = response.text

    start = css.index(".chat-message--user .chat-message__avatar {")
    end = css.index("}", start)
    block = css[start:end]
    assert "var(--brand-green)" not in block


def test_identity_avatar_color_palette_excludes_green(
    client: TestClient, db_session: Session
) -> None:
    """Die komplette `.identity-avatar--*`-Palette darf keinen gruenen
    Eintrag enthalten - Gruen bleibt strukturell Lexono vorbehalten."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/static/css/app.css")
    css = response.text

    assert ".identity-avatar--green" not in css
    for color in ("blue", "purple", "orange", "amber"):
        assert f".identity-avatar--{color}" in css
