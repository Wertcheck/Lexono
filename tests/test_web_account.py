"""Web-Layer-Tests für den Profil-/Einstellungen-Bereich (Prompt 49).

Deckt ab: alle drei echten Konto-Seiten (Übersicht, Mein Konto, Anonymisierung
& Datenschutz) sind erreichbar, zeigen die erwarteten Inhalte, verweigern
unauthentifizierten Zugriff, und die Nutzerverwaltungs-Kachel auf der
Übersicht ist rollenabhängig (nur Admin) sichtbar - sowie der wichtigste
Sicherheits-/Ehrlichkeits-Punkt dieses Prompts: die Datenschutz-Seite
behauptet KEINE rechtliche Konformität und bietet KEINEN (fälschlich
abschaltbar wirkenden) Schalter für die immer aktive Pseudonymisierung.
"""

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
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles


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
def admin_client(db_session: Session, client: TestClient) -> TestClient:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    return client


@pytest.fixture()
def mitarbeiter_client(db_session: Session, client: TestClient) -> TestClient:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")
    return client


# --- Übersicht ---


def test_account_overview_returns_200(admin_client: TestClient) -> None:
    """UI/UX-Ueberarbeitung (13.09.): Titel ist jetzt schlicht
    "Einstellungen" - "Mein Profil" ist ein eigener Menuepunkt geworden
    (siehe base.html-Profilmenue), diese Seite ist die reine
    Einstellungen-Uebersicht."""
    response = admin_client.get("/dashboard/account")
    assert response.status_code == 200
    assert "<title>Einstellungen" in response.text
    assert '<h1 class="topbar__title">Einstellungen</h1>' in response.text


def test_account_overview_shows_admin_only_card_for_admin(admin_client: TestClient) -> None:
    """Verlinkt seit der Owner-Direktive "SETTINGS -> BENUTZER" (06.10.)
    direkt auf den eingebetteten "Benutzer"-Tab in den Einstellungen statt
    auf die inzwischen entfernte eigenstaendige Seite /dashboard/admin/users
    (deren POST-Endpunkte unveraendert bestehen, siehe users_router.py)."""
    response = admin_client.get("/dashboard/account")
    assert 'href="/dashboard/settings?tab=benutzer"' in response.text


def test_account_overview_hides_admin_only_card_for_non_admin(
    mitarbeiter_client: TestClient,
) -> None:
    response = mitarbeiter_client.get("/dashboard/account")
    assert 'href="/dashboard/settings?tab=benutzer"' not in response.text


def test_account_overview_shows_monitoring_and_backup_cards_for_admin(
    admin_client: TestClient,
) -> None:
    """Systemstatus und Backup & Export sind seit der bereinigten
    Fuenf-Module-Sidebar (20.08., siehe app/web/templates/base.html) keine
    eigenen Hauptmenue-Eintraege mehr - fuer Admins bleiben sie ueber diese
    beiden Kacheln auf der Profil-/Einstellungen-Uebersicht erreichbar."""
    response = admin_client.get("/dashboard/account")
    assert 'href="/dashboard/monitoring"' in response.text
    assert 'href="/dashboard/backup"' in response.text


def test_account_overview_hides_monitoring_and_backup_cards_for_non_admin(
    mitarbeiter_client: TestClient,
) -> None:
    response = mitarbeiter_client.get("/dashboard/account")
    assert 'href="/dashboard/monitoring"' not in response.text
    assert 'href="/dashboard/backup"' not in response.text


def test_account_overview_links_to_all_four_sections(admin_client: TestClient) -> None:
    response = admin_client.get("/dashboard/account")
    for href in [
        "/dashboard/account/privacy",
        # "Kanzlei-Profil & Briefkopf" verlinkt seit der Owner-Direktive
        # "SETTINGS -> KANZLEI" (06.10.) direkt auf den eingebetteten
        # "Kanzlei"-Tab in den Einstellungen statt auf die inzwischen
        # entfernte eigenstaendige Seite /dashboard/settings/profile
        # (deren POST-Endpunkte unveraendert bestehen).
        "/dashboard/settings?tab=kanzlei",
        "/dashboard/account/license",
        "/dashboard/account/me",
    ]:
        assert f'href="{href}"' in response.text


def test_unauthenticated_cannot_view_account_overview(client: TestClient) -> None:
    response = client.get("/dashboard/account", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


# --- Mein Konto & Abmelden ---


def test_account_me_shows_email_and_role(admin_client: TestClient) -> None:
    response = admin_client.get("/dashboard/account/me")
    assert response.status_code == 200
    assert "admin@kanzlei.test" in response.text
    assert "Admin" in response.text


def test_account_me_has_working_logout_form(admin_client: TestClient) -> None:
    response = admin_client.get("/dashboard/account/me")
    assert 'action="/dashboard/logout"' in response.text
    assert 'method="post"' in response.text


def test_account_me_shows_email_fallback_when_no_display_name_set(
    admin_client: TestClient,
) -> None:
    """ECHTER FUND (18.09.): `User.display_name` wurde an mehreren Stellen
    mit `or user.email`-Fallback gelesen, hatte aber projektweit keinen
    Schreibweg - zeigte dadurch strukturell immer die rohe E-Mail-Adresse."""
    response = admin_client.get("/dashboard/account/me")
    assert "wird angezeigt" in response.text


def test_sidebar_footer_shows_display_name_once_set(admin_client: TestClient) -> None:
    """ECHTER FUND (19.09., UI/UX-Referenzabgleich): `display_name` war seit
    18.09. selbst editierbar, hatte aber ausgerechnet im auf JEDER Seite
    sichtbaren Sidebar-Fuss (base.html) keine Wirkung - Avatar-Initiale und
    Name kamen dort weiterhin ausschliesslich aus der rohen E-Mail-Adresse."""
    page = admin_client.get("/dashboard/account/me")
    csrf = extract_csrf(page.text)
    admin_client.post(
        "/dashboard/account/me/display-name",
        data={"csrf_token": csrf, "display_name": "Rechtsanwältin Anna Muster"},
    )

    response = admin_client.get("/dashboard/chat")

    assert "Rechtsanwältin Anna Muster" in response.text
    assert '<span class="sidebar__profile-avatar">R</span>' in response.text


def test_set_display_name_persists_it(admin_client: TestClient) -> None:
    page = admin_client.get("/dashboard/account/me")
    csrf = extract_csrf(page.text)

    response = admin_client.post(
        "/dashboard/account/me/display-name",
        data={"csrf_token": csrf, "display_name": "Rechtsanwältin Anna Muster"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated_page = admin_client.get("/dashboard/account/me")
    assert "Rechtsanwältin Anna Muster" in updated_page.text
    assert "wird angezeigt" not in updated_page.text


def test_set_display_name_to_blank_clears_it(admin_client: TestClient) -> None:
    page = admin_client.get("/dashboard/account/me")
    csrf = extract_csrf(page.text)
    admin_client.post(
        "/dashboard/account/me/display-name",
        data={"csrf_token": csrf, "display_name": "Rechtsanwältin Anna Muster"},
    )

    page2 = admin_client.get("/dashboard/account/me")
    csrf2 = extract_csrf(page2.text)
    response = admin_client.post(
        "/dashboard/account/me/display-name",
        data={"csrf_token": csrf2, "display_name": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 303
    final_page = admin_client.get("/dashboard/account/me")
    assert "wird angezeigt" in final_page.text


def test_set_display_name_requires_a_valid_csrf_token(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/dashboard/account/me/display-name",
        data={"csrf_token": "invalid", "display_name": "Sollte nicht gespeichert werden"},
    )
    assert response.status_code == 403


# --- Anonymisierung & Datenschutz ---


def test_account_privacy_returns_200(admin_client: TestClient) -> None:
    response = admin_client.get("/dashboard/account/privacy")
    assert response.status_code == 200


def test_account_privacy_shows_pseudonymization_as_always_active(
    admin_client: TestClient,
) -> None:
    response = admin_client.get("/dashboard/account/privacy")
    assert "aktiv" in response.text


def test_account_privacy_makes_no_legal_compliance_claim(admin_client: TestClient) -> None:
    """Kernanforderung dieses Prompts: keine Rechtsvorschrift, kein
    "konform"/"compliant"-Wort - siehe app/web/account_router.py-Docstring."""
    response = admin_client.get("/dashboard/account/privacy")
    lowered = response.text.lower()
    assert "brao" not in lowered
    assert "§" not in response.text
    assert "konform" not in lowered
    assert "compliant" not in lowered


def test_account_privacy_has_no_toggle_switch_for_pseudonymization(
    admin_client: TestClient,
) -> None:
    """Die Pseudonymisierung ist architektonisch fest verankert und darf
    hier nicht als (fälschlich abschaltbar wirkender) interaktiver Schalter
    dargestellt werden - keine Checkbox, kein <input type="checkbox">."""
    response = admin_client.get("/dashboard/account/privacy")
    assert 'type="checkbox"' not in response.text


def test_account_privacy_never_shows_actual_api_key(admin_client: TestClient) -> None:
    response = admin_client.get("/dashboard/account/privacy")
    assert "sk-" not in response.text


def test_unauthenticated_cannot_view_account_privacy(client: TestClient) -> None:
    response = client.get("/dashboard/account/privacy", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]
