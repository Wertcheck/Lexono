"""Tests fuer das serverseitig gerenderte Dashboard (Prompt 22 - Inbox).

Gleiches Testmuster wie tests/test_api.py: geteilte In-Memory-SQLite-DB
ueber `app.dependency_overrides`, StaticPool wegen FastAPIs Thread-Pool
(siehe ausfuehrliche Begruendung dort).
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
from app.models import Client, Document, Matter, Message
from app.models.base import Base
from tests.auth_test_utils import create_test_user, login, login_as_admin, seed_roles


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
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def mitarbeiter_client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        test_client = TestClient(app)
        roles = seed_roles(db_session)
        create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
        login(test_client, "mitarbeiter@kanzlei.test")
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def seeded(db_session: Session) -> dict[str, str]:
    """Ausschliesslich synthetische Testdaten (Grundregel) - ein Mandant,
    eine Akte, eine zugeordnete und eine nicht zugeordnete Nachricht, ein
    Dokument als Anhang der zugeordneten Nachricht."""
    mandant = Client(name="Synthetischer Testmandant GmbH")
    db_session.add(mandant)
    db_session.flush()

    matter = Matter(
        client_id=mandant.id,
        title="Einspruch Steuerbescheid 2025",
        reference_number="2025/0142-ESt",
    )
    db_session.add(matter)
    db_session.flush()

    matched = Message(
        matter_id=matter.id,
        direction="inbound",
        sender="j.mueller@steuerkanzlei-test.invalid",
        subject="Steuerbescheid 2025 - Einspruchsfrist beachten",
        body_text="Testinhalt, keine echten Mandantendaten.",
    )
    unmatched = Message(
        matter_id=None,
        direction="inbound",
        sender="neuermandant@example-testdomain.invalid",
        subject="Anfrage: Betriebspruefung angekuendigt",
        body_text="Testinhalt.",
    )
    outbound = Message(
        matter_id=matter.id,
        direction="outbound",
        sender="kanzlei@steuerkanzlei-test.invalid",
        subject="RE: Steuerbescheid 2025",
        body_text="Testinhalt.",
    )
    db_session.add_all([matched, unmatched, outbound])
    db_session.flush()

    document = Document(
        matter_id=matter.id,
        message_id=matched.id,
        original_filename="steuerbescheid_2025_test.pdf",
        file_path="/data/intake/test/steuerbescheid_2025_test.pdf",
        classified_type="steuerbescheid",
    )
    db_session.add(document)
    db_session.commit()

    return {
        "matter_id": matter.id,
        "matched_message_id": matched.id,
        "unmatched_message_id": unmatched.id,
        "outbound_message_id": outbound.id,
        "document_id": document.id,
    }


# --- Grundfunktionen ---


def test_dashboard_root_redirects_to_chat(client: TestClient) -> None:
    """UI-Überarbeitung: Chat ist jetzt die zentrale Startseite nach dem
    Login (vormals Posteingang) - siehe app/web/chat_router.py. Der
    Posteingang selbst bleibt unverändert unter /dashboard/inbox erreichbar
    (siehe test_inbox_page_returns_200 unten)."""
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard/chat"


def test_inbox_page_returns_200(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_static_css_is_served(client: TestClient) -> None:
    response = client.get("/dashboard/static/css/app.css")
    assert response.status_code == 200


def test_static_htmx_is_served_locally(client: TestClient) -> None:
    """Grundregel Prompt 22: HTMX wird lokal ausgeliefert, nicht per CDN
    (Offline-first-Prinzip, siehe app/web/static/js/VENDORED.md)."""
    response = client.get("/dashboard/static/js/htmx.min.js")
    assert response.status_code == 200
    assert len(response.content) > 1000


# --- Inhalt / Aktenisolation-Badges ---


def test_inbox_shows_matched_message_with_reference_number(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox")
    assert "2025/0142-ESt" in response.text
    assert "Steuerbescheid 2025" in response.text


def test_inbox_shows_unmatched_badge_for_message_without_matter(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox")
    assert "nicht zugeordnet" in response.text
    assert "Betriebspruefung angekuendigt" in response.text


def test_inbox_marks_outbound_message(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/inbox")
    assert "ausgehend" in response.text


# --- Filter ---


def test_unmatched_filter_excludes_matched_message(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox", params={"filter": "unmatched"})
    assert response.status_code == 200
    assert "Betriebspruefung angekuendigt" in response.text
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" not in response.text


def test_outbound_filter_shows_only_outbound(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/inbox", params={"filter": "outbound"})
    assert response.status_code == 200
    assert "RE: Steuerbescheid 2025" in response.text
    assert "Betriebspruefung angekuendigt" not in response.text


def test_unknown_filter_value_falls_back_to_all(
    client: TestClient, seeded: dict
) -> None:
    """Ein manipulierter/unbekannter Filter-Query-Parameter darf hoechstens
    auf 'alle Nachrichten' zurueckfallen, nie zu einem Serverfehler
    fuehren."""
    response = client.get("/dashboard/inbox", params={"filter": "__hack__"})
    assert response.status_code == 200
    assert "Betriebspruefung angekuendigt" in response.text
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" in response.text


def test_inbox_list_partial_returns_only_fragment(
    client: TestClient, seeded: dict
) -> None:
    """HTMX-Partial-Endpunkt liefert nur die Liste, keine volle Seite (kein
    <html>/<nav>-Grundgeruest)."""
    response = client.get("/dashboard/inbox/list", params={"filter": "all"})
    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="message-list"' in response.text


# --- Detailansicht ---


def test_inbox_message_page_shows_detail_and_document(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert response.status_code == 200
    assert "steuerbescheid_2025_test.pdf" in response.text
    assert "steuerbescheid" in response.text


def test_inbox_message_detail_partial_returns_only_fragment(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(
        f"/dashboard/inbox/{seeded['matched_message_id']}/detail"
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="detail-pane"' in response.text


def test_detail_response_excludes_internal_file_path(
    client: TestClient, seeded: dict
) -> None:
    """Dieselbe Allowlist-Grundregel wie bei der JSON-API (Prompt 21): der
    interne Ablagepfad darf nicht im HTML landen."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert "/data/intake/test/" not in response.text


def test_unmatched_message_detail_shows_unmatched_badge_not_matter(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(
        f"/dashboard/inbox/{seeded['unmatched_message_id']}/detail"
    )
    assert response.status_code == 200
    assert "nicht zugeordnet" in response.text


def test_message_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/dashboard/inbox/does-not-exist")
    assert response.status_code == 404


def test_message_detail_partial_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/dashboard/inbox/does-not-exist/detail")
    assert response.status_code == 404


# --- Sidebar / ehrliche Darstellung des Entwicklungsstands ---


def test_sidebar_shows_all_group_and_item_labels(client: TestClient, seeded: dict) -> None:
    """Seit der bereinigten Navigation (20.08.) ist die Sidebar auf EXAKT
    fuenf logische Kernmodule fokussiert - "Uebersicht" und
    "Mandantendatenbank" sind bewusst flache Einzel-Links (keine
    "Dashboard"-Unterpunkt-unter-"Dashboard"-Gruppe mehr), die restlichen
    drei bleiben aufklappbare Gruppen. "Einstellungen" ist weiterhin kein
    sichtbares Hauptmenue-Label - der Profil-/Einstellungen-Bereich wird
    ueber die Account-Zeile im Sidebar-Footer erreicht (siehe
    test_sidebar_has_profile_link_at_bottom).

    UI-Ueberarbeitung: der vormals flache "Uebersicht"-Link wurde durch
    "Chat" ersetzt (neue zentrale Startseite, siehe app/web/chat_router.py) -
    "Uebersicht" existiert als Nav-Label nicht mehr."""
    response = client.get("/dashboard/inbox")
    for label in [
        "Chat",
        "Akten &amp; Ordner",
        "Posteingang",
        "Aktive Akten",
        "E-Mail &amp; Posteingang",
        "Entwürfe zur Prüfung",
        "Rechtsquellen",
        "Kanzleiwissen &amp; KI",
        "Kanzlei-Wissen",
        "Postausgang",
        "Mandantendatenbank",
    ]:
        assert label in response.text


def test_sidebar_has_profile_link_at_bottom(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/inbox")
    assert 'href="/dashboard/account"' in response.text
    assert "sidebar__profile" in response.text


def test_sidebar_links_all_areas_to_real_pages(client: TestClient, seeded: dict) -> None:
    """Seit Prompt 48 ist JEDER Sidebar-Bereich ein echter, klickbarer Link
    (<a href>) - Bereiche ohne eigene Fachlogik fuehren auf eine ehrliche
    Platzhalterseite (app/web/placeholder_router.py) statt entweder auf
    einen toten Link oder ein nicht anklickbares "bald"-Badge (fruehere
    Loesung, siehe Git-Historie). Seit der bereinigten Fuenf-Module-
    Navigation (20.08.) sind drei der fuenf Module weiterhin aufklappbare
    Gruppen (<details>/<summary>), "Uebersicht" und "Mandantendatenbank"
    sind flache Einzel-Links - es gibt weiterhin keine
    `sidebar__link--disabled`-Klasse."""
    response = client.get("/dashboard/inbox")
    for href in [
        "/dashboard",
        "/dashboard/inbox",
        "/dashboard/outbox",
        "/dashboard/drafts",
        "/dashboard/errors",
        "/dashboard/matters",
        "/dashboard/recent",
        "/dashboard/documents",
        "/dashboard/archive",
        "/dashboard/tools/schriftsatz",
        "/dashboard/tools/fristen",
        "/dashboard/tools/zeitleiste",
        "/dashboard/tools/beleg-extraktion",
        "/dashboard/sources",
        "/dashboard/library/mustertexte",
        "/dashboard/library/prompts",
        "/dashboard/knowledge",
        "/dashboard/history/analysen",
        "/dashboard/clients",
        "/dashboard/account",
    ]:
        assert f'href="{href}"' in response.text
    assert "sidebar__link--disabled" not in response.text


def test_sidebar_shows_admin_only_items_for_admin(client: TestClient, seeded: dict) -> None:
    """`client` meldet sich als Admin an (login_as_admin) - Systemstatus ist
    admin_only=True in base.html (Gruppe "Kanzleiwissen & KI") und muss daher
    sichtbar sein. Backup & Export ist seit der bereinigten Navigation kein
    Sidebar-Eintrag mehr, sondern nur noch eine Admin-Kachel auf der
    Profil-/Einstellungen-Uebersicht (siehe tests/test_web_account.py)."""
    response = client.get("/dashboard/inbox")
    assert 'href="/dashboard/monitoring"' in response.text


def test_sidebar_group_containing_active_item_is_open(
    client: TestClient, seeded: dict
) -> None:
    """Die Gruppe, die den gerade aktiven Nav-Punkt enthaelt, ist automatisch
    aufgeklappt (<details open>), damit der aktuelle Ort im Menü sichtbar
    bleibt, statt hinter einer eingeklappten Gruppe versteckt zu sein."""
    response = client.get("/dashboard/inbox")
    # "Posteingang" gehoert seit der bereinigten Navigation zur Gruppe
    # "E-Mail & Posteingang" - deren <details> muss "open" tragen, waehrend
    # z. B. "Kanzleiwissen & KI" geschlossen bleibt.
    mail_group_start = response.text.index("E-Mail &amp; Posteingang")
    details_tag_start = response.text.rindex("<details", 0, mail_group_start)
    details_tag_end = response.text.index(">", details_tag_start)
    assert "open" in response.text[details_tag_start:details_tag_end]


def test_sidebar_footer_has_help_status(client: TestClient, seeded: dict) -> None:
    """Der Sidebar-Footer zeigt einen dezenten Hilfe-/Systemstatus-Streifen
    mit Link auf Pilot-Feedback & Support (§63: kein Ollama-Erreichbarkeits-
    Badge mehr, da kein lokales LLM mehr existiert)."""
    response = client.get("/dashboard/inbox")
    assert "sidebar__footer-status" in response.text
    assert 'href="/dashboard/feedback"' in response.text
    assert "sidebar__footer-help" in response.text


def test_sidebar_settings_gear_points_to_settings_page_for_admin(
    client: TestClient, seeded: dict
) -> None:
    """Der Zahnrad-Button im Footer ist ein eigener Link (getrennt von der
    Avatar-Zeile) und fuehrt fuer Admins direkt auf die echte
    Einstellungsseite."""
    response = client.get("/dashboard/inbox")
    assert 'class="sidebar__settings-btn"' in response.text
    assert 'href="/dashboard/settings" class="sidebar__settings-btn"' in response.text


def test_sidebar_settings_gear_points_to_account_overview_for_non_admin(
    mitarbeiter_client: TestClient, seeded: dict
) -> None:
    """Nicht-Admins haben keine eigene Einstellungsseite (/dashboard/settings
    ist admin_only, siehe app/web/settings_router.py) - der Zahnrad-Button
    fuehrt fuer sie stattdessen auf die Profil-/Einstellungen-Uebersicht."""
    response = mitarbeiter_client.get("/dashboard/inbox")
    assert 'href="/dashboard/account" class="sidebar__settings-btn"' in response.text


# --- Onboarding-Banner (Prompt 48) ---


def test_onboarding_banner_shown_when_inbox_empty(client: TestClient) -> None:
    """Ohne jede Nachricht (total_count == 0) zeigt der Posteingang das
    Onboarding-Banner statt der leeren Split-Pane-Ansicht."""
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert "Erste Schritte" in response.text
    assert 'class="split"' not in response.text


def test_onboarding_banner_includes_claude_api_check(client: TestClient) -> None:
    """§63: Schritt 3 des Onboarding-Banners prüft die Claude-API-
    Erreichbarkeit (kein lokaler Installations-/Update-Assistent mehr, da
    kein lokales LLM mehr existiert)."""
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert "Claude-API prüfen" in response.text
    assert 'id="onboarding-api-result"' in response.text


def test_onboarding_banner_hidden_when_messages_exist(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert "Erste Schritte" not in response.text
    assert 'class="split"' in response.text
