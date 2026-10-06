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
from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME
from app.main import app
from app.models import AuditEvent, Client, Deadline, Document, Matter, Message
from app.models.base import Base
from tests.auth_test_utils import create_test_user, extract_csrf, login, login_as_admin, seed_roles


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


def test_matched_filter_excludes_unmatched_message(client: TestClient, seeded: dict) -> None:
    """ECHTER FUND (19.09., UI/UX-Referenzabgleich "04_posteingang_
    nachricht_detail.png"): die Referenz zeigt "Zugewiesen" als eigenen
    Filter-Tab neben "Nicht zugeordnet" - bisher gab es nur den
    Ausschluss-Filter, keinen Weg, GEZIELT nur zugeordnete Nachrichten
    zu sehen."""
    response = client.get("/dashboard/inbox", params={"filter": "matched"})
    assert response.status_code == 200
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" in response.text
    assert "Betriebspruefung angekuendigt" not in response.text


def test_with_attachment_filter_shows_only_messages_with_documents(
    client: TestClient, seeded: dict
) -> None:
    """ECHTER FUND (19.09., UI/UX-Referenzabgleich, dieselbe Referenz):
    "Mit Anhang" als eigener Filter-Tab - rein lesender Filter auf der
    bereits bestehenden `Message.documents`-Beziehung."""
    response = client.get("/dashboard/inbox", params={"filter": "with_attachment"})
    assert response.status_code == 200
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" in response.text
    assert "Betriebspruefung angekuendigt" not in response.text
    assert "RE: Steuerbescheid 2025" not in response.text


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


def test_inbox_page_shows_search_box(client: TestClient, seeded: dict) -> None:
    """ECHTER FUND (18.09., Owner-Direktive "WEITERARBEITEN"): der
    Posteingang hatte projektweit KEIN Suchfeld, nur vier grobe
    Filter-Tabs."""
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert 'name="q"' in response.text
    assert "durchsuchen" in response.text


def test_inbox_search_by_subject_filters_messages(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox", params={"q": "Betriebspruefung"})
    assert response.status_code == 200
    assert "Betriebspruefung angekuendigt" in response.text
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" not in response.text


def test_inbox_search_by_sender_filters_messages(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox", params={"q": "j.mueller"})
    assert response.status_code == 200
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" in response.text
    assert "Betriebspruefung angekuendigt" not in response.text


def test_inbox_search_is_case_insensitive(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/inbox", params={"q": "steuerbescheid"})
    assert response.status_code == 200
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" in response.text


def test_inbox_search_combines_with_filter_tab(
    client: TestClient, seeded: dict
) -> None:
    """Suche UND Filter-Tab muessen gemeinsam wirken (UND-Verknuepfung),
    nicht eines das andere ueberschreiben."""
    response = client.get(
        "/dashboard/inbox", params={"filter": "outbound", "q": "Steuerbescheid"}
    )
    assert response.status_code == 200
    assert "RE: Steuerbescheid 2025" in response.text
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" not in response.text


def test_inbox_search_with_no_matches_shows_empty_state(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox", params={"q": "nichtvorhanden-xyz"})
    assert response.status_code == 200
    assert "Betriebspruefung angekuendigt" not in response.text
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" not in response.text


def test_inbox_list_partial_supports_search(client: TestClient, seeded: dict) -> None:
    response = client.get(
        "/dashboard/inbox/list", params={"filter": "all", "q": "Betriebspruefung"}
    )
    assert response.status_code == 200
    assert "Betriebspruefung angekuendigt" in response.text
    assert "Steuerbescheid 2025 - Einspruchsfrist beachten" not in response.text


# --- Detailansicht ---


def test_inbox_message_page_shows_detail_and_document(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert response.status_code == 200
    assert "steuerbescheid_2025_test.pdf" in response.text
    assert "steuerbescheid" in response.text


def test_matched_message_detail_links_to_the_real_matter_page(
    client: TestClient, seeded: dict
) -> None:
    """ECHTER FUND (18.09., Flow-Audit "Posteingang -> Akte"): "Akte: ..."
    zeigte bereits die echte zugeordnete Akte an, war aber kein Link
    dorthin - identisches Bug-Muster wie die bereits behobenen
    Dokument-Chips in derselben Datei."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert response.status_code == 200
    assert f'href="/dashboard/matters/{seeded["matter_id"]}"' in response.text


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


def test_matched_message_shows_summarize_and_reply_actions(
    client: TestClient, seeded: dict
) -> None:
    """Referenz `04_posteingang_nachricht_detail.png` zeigt "Zusammenfassen"/
    "Antworten" als eigene Aktionen (16.09., UI/UX-Sweep) - beide starten
    eine neue Chat-Unterhaltung, siehe tests/test_web_chat.py fuer die
    eigentliche Pipeline."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert response.status_code == 200
    assert "Zusammenfassen" in response.text
    assert "Antworten" in response.text
    assert f'/dashboard/chat/from-message/{seeded["matched_message_id"]}' in response.text


def test_matched_message_summarize_and_reply_actions_have_ai_loading_wiring(
    client: TestClient, seeded: dict
) -> None:
    """KI-Waiting-/Buffering-UX (20.09., Owner-Direktive "KI-WAITING-/
    BUFFERING-UX PROJEKTWEIT PRÜFEN UND VERBESSERN"): beide Aktionen lösen
    `start_conversation_from_message` aus, das den Claude-Aufruf SYNCHRON
    vor dem Redirect ausführt (app/web/chat_router.py) - ohne Feedback sah
    ein Klick hier wie ein eingefrorenes System aus."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert response.status_code == 200
    assert response.text.count('class="js-ai-form"') >= 2
    assert response.text.count('data-ai-loading-label="Wird gestartet') >= 2


def test_unmatched_message_never_shows_summarize_and_reply_actions(
    client: TestClient, seeded: dict
) -> None:
    """Aktenisolation: ohne zugeordnete Akte gibt es fuer den Chat-Pfad
    keinen Kontext, in dem eine Unterhaltung entstehen duerfte - erst
    zuordnen (siehe die Zuordnungs-Karten), dann Zusammenfassen/Antworten."""
    response = client.get(f"/dashboard/inbox/{seeded['unmatched_message_id']}")
    assert response.status_code == 200
    assert "Zusammenfassen" not in response.text
    assert "Antworten" not in response.text


def test_message_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/dashboard/inbox/does-not-exist")
    assert response.status_code == 404


def test_message_detail_partial_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/dashboard/inbox/does-not-exist/detail")
    assert response.status_code == 404


# --- Sidebar / ehrliche Darstellung des Entwicklungsstands ---
#
# UI/UX-Ueberarbeitung (13.09.): die Sidebar ist jetzt auf EXAKT sechs
# Hauptbereiche reduziert (verbindliche Vorgabe) - Chat/Mandanten/Akten/
# Posteingang/Aufgaben & Fristen/Kanzleiwissen, alle als flache Links OHNE
# Aufklapp-Gruppen. "Einstellungen" ist KEIN Hauptmenuepunkt mehr, sondern
# nur noch Teil eines Profilmenues (Klick auf Name/Avatar unten links).
# Bereiche, die vorher als eigene Sidebar-Gruppen/-Zeilen existierten
# (Schriftsatz-Generator, Standard-Prompts, Monitoring, Backup, Fehler &
# Logs, Postausgang, Entwuerfe zur Pruefung, Rechtsquellen, ...) bleiben
# als echte Router erreichbar, aber NICHT mehr im Hauptmenue - siehe
# app/web/templates/account_overview.html ("Einstellungen"-Hub) bzw.
# law_library.html/inbox.html fuer die jeweiligen Unterbereiche.


def test_sidebar_shows_exactly_the_six_mandated_main_nav_items(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox")
    for label in [
        "Chat",
        "Mandanten",
        "Akten",
        "Posteingang",
        "Aufgaben &amp; Fristen",
        "Kanzleiwissen",
    ]:
        assert label in response.text


def test_sidebar_no_longer_shows_removed_main_nav_items(
    client: TestClient, seeded: dict
) -> None:
    """Diese Bereiche existieren weiterhin als echte Router (siehe
    test_sidebar_removed_items_still_reachable_via_settings_hub unten),
    duerfen aber NICHT mehr als eigene Hauptmenue-Zeile in der Sidebar
    auftauchen - insbesondere "Schriftsatz-Generator" (verbindlich
    verboten als Hauptmenuepunkt) und "Einstellungen" selbst."""
    response = client.get("/dashboard/inbox")
    for removed_label in [
        "Akten &amp; Ordner",
        "E-Mail &amp; Posteingang",
        "Schriftsatz-Generator",
        "Kanzleiwissen &amp; KI",
        "Mandantendatenbank",
        "Fehler &amp; Logs",
    ]:
        assert removed_label not in response.text
    # "Einstellungen" darf als MAIN-NAV-Zeile (sidebar__group-label) nicht
    # vorkommen - im Profilmenue selbst ist der Text legitim (siehe
    # test_sidebar_profile_menu_has_all_four_mandated_items).
    assert 'sidebar__group-label">Einstellungen<' not in response.text


def test_global_header_has_logo_and_global_search_before_sidebar(
    client: TestClient, seeded: dict
) -> None:
    """25.09., Owner-Direktive "POSTEINGANG / STRICT REFERENCE
    IMPLEMENTATION" §5/§6, Referenzabgleich
    `04_posteingang_nachricht_detail.png`: Logo + globale Suche leben jetzt
    in einer eigenen, seitenuebergreifenden Kopfzeile OBERHALB von
    Sidebar+Hauptbereich, nicht mehr verstreut in der Sidebar selbst."""
    response = client.get("/dashboard/inbox")
    header_start = response.text.index('<header class="global-header">')
    sidebar_start = response.text.index('<nav class="sidebar">')
    assert header_start < sidebar_start
    header_html = response.text[header_start:sidebar_start]
    assert "sidebar__brand-logo" in header_html
    assert "In E-Mails, Mandanten, Akten oder Inhalten suchen" in header_html
    assert 'data-command-bar-open' in header_html


def test_sidebar_no_longer_has_its_own_search_but_has_new_chat_button(
    client: TestClient, seeded: dict
) -> None:
    """25.09., Owner-Direktive "POSTEINGANG / STRICT REFERENCE
    IMPLEMENTATION" §5/§7: die Sidebar-eigene Suche ("Suchen… Strg K") ist
    durch die globale Kopfzeilen-Suche ersetzt (nicht dupliziert) - dieser
    Teil der damaligen Entscheidung bleibt unveraendert bestehen.

    KURSKORREKTUR (26.09., Owner-Direktive "KANZLEIWISSEN FINAL POLISH +
    APP-SHELL KORREKTUR" §P0, ausdruecklich als Fehler benannt): "Neuen
    Chat starten" wurde in der 25.09.-Runde faelschlich ebenfalls entfernt
    (Begruendung damals: "vollstaendig redundant zum '+'-Button auf der
    Chat-Seite selbst") - der Owner hat klargestellt, dass die globale
    Suche (Mandanten/Akten/Dokumente FINDEN) und "Neuen Chat starten" (die
    primaere KI-Arbeitsflaeche OEFFNEN) verschiedene Zwecke haben, keine
    Redundanz. Der Button ist jetzt wieder ein permanenter, von jeder
    Seite aus sichtbarer Sidebar-Eintrag (siehe base.html/app.css
    `.sidebar__new-chat-btn`), wiederverwendet dieselbe bestehende Route
    wie der "+"-Button in chat.html (`/dashboard/chat?new=1`)."""
    response = client.get("/dashboard/inbox")
    sidebar_start = response.text.index('<nav class="sidebar">')
    sidebar_end = response.text.index("</nav>", sidebar_start)
    sidebar_html = response.text[sidebar_start:sidebar_end]
    assert "sidebar__search-trigger" not in sidebar_html
    assert "sidebar__new-chat-btn" in sidebar_html
    assert "Neuen Chat starten" in sidebar_html
    assert 'href="/dashboard/chat?new=1"' in sidebar_html


def test_sidebar_has_profile_menu_trigger_at_bottom(client: TestClient, seeded: dict) -> None:
    """Der Sidebar-Footer zeigt jetzt einen Profilmenue-Ausloeser
    (Button, kein <a href="/dashboard/account"> mehr direkt) - das
    eigentliche Ziel "Mein Profil" liegt jetzt EINE Ebene tiefer, im
    Dropdown selbst (siehe test_sidebar_profile_menu_has_all_four_items)."""
    response = client.get("/dashboard/inbox")
    assert 'id="sidebar-profile-trigger"' in response.text
    assert "sidebar__profile" in response.text


def test_sidebar_profile_menu_has_all_four_mandated_items(
    client: TestClient, seeded: dict
) -> None:
    """Verbindliche Struktur: Mein Profil / Einstellungen / Hilfe &
    Support / Abmelden - in dieser Reihenfolge, alle vier real verlinkt.

    Sucht bewusst NUR innerhalb von `#sidebar-profile-menu` (25.09., ECHTER
    FUND: seit dem Umzug der Kopfzeilen-Icons in die neue globale Kopfzeile,
    siehe base.html/`.global-header`, enthaelt die Seite VOR dem
    Profilmenue bereits einen eigenen Logout-Button mit `title="Abmelden"`
    - eine ungescopte Suche nach "Abmelden" traf danach zuerst DIESEN statt
    des Profilmenue-Eintrags)."""
    response = client.get("/dashboard/inbox")
    assert 'id="sidebar-profile-menu"' in response.text
    menu_start = response.text.index('id="sidebar-profile-menu"')
    menu_html = response.text[menu_start:]
    mein_profil_pos = menu_html.index("Mein Profil")
    einstellungen_pos = menu_html.index(">Einstellungen<")
    hilfe_pos = menu_html.index("Hilfe &amp; Support")
    abmelden_pos = menu_html.index("Abmelden")
    assert mein_profil_pos < einstellungen_pos < hilfe_pos < abmelden_pos
    assert 'href="/dashboard/account/me"' in response.text
    assert 'action="/dashboard/logout"' in response.text


def test_sidebar_links_main_nav_items_to_real_pages(client: TestClient, seeded: dict) -> None:
    """Jeder der sechs Hauptmenuepunkte ist ein echter, klickbarer Link -
    kein `sidebar__link--disabled` mehr (unveraendert seit Prompt 48).

    "Kanzleiwissen" verlinkt seit 26.09. (Owner-Direktive "KANZLEIWISSEN
    FINAL PRODUCT IMPLEMENTATION") auf die neue Kategorie-Uebersicht
    (/dashboard/knowledge) statt direkt auf die reine Gesetzes-Leseansicht
    (/dashboard/laws, siehe test_web_laws.py fuer diese Route selbst)."""
    response = client.get("/dashboard/inbox")
    for href in [
        "/dashboard/chat",
        "/dashboard/clients",
        "/dashboard/matters",
        "/dashboard/inbox",
        "/dashboard/tasks",
        "/dashboard/knowledge",
    ]:
        assert f'href="{href}"' in response.text
    assert "sidebar__link--disabled" not in response.text


def test_sidebar_removed_items_still_reachable_via_settings_hub(
    client: TestClient, seeded: dict
) -> None:
    """Kein Backend-Router wurde entfernt - alle vorher in der Sidebar
    sichtbaren Zusatzbereiche bleiben real erreichbar, jetzt ueber die
    Einstellungen-Uebersicht (account_overview.html) statt ueber die
    Sidebar selbst."""
    response = client.get("/dashboard/account")
    for href in [
        "/dashboard/library/prompts",
        "/dashboard/library/mustertexte",
        "/dashboard/tools/schriftsatz",
        "/dashboard/errors",
        "/dashboard/feedback",
    ]:
        assert f'href="{href}"' in response.text


def test_sidebar_shows_admin_only_items_for_admin(client: TestClient, seeded: dict) -> None:
    """`client` meldet sich als Admin an (login_as_admin) - Systemstatus/
    Backup sind admin-only und muessen daher auf der Einstellungen-
    Uebersicht sichtbar sein (nicht mehr in der Sidebar selbst)."""
    response = client.get("/dashboard/account")
    assert 'href="/dashboard/monitoring"' in response.text
    assert 'href="/dashboard/backup"' in response.text


def test_sidebar_active_item_gets_active_class_and_stays_in_place(
    client: TestClient, seeded: dict
) -> None:
    """Kein Aufklapp-Mechanismus mehr (keine <details>-Gruppen) - der
    aktive Hauptmenuepunkt bekommt stattdessen direkt die aktive Klasse,
    an seiner FESTEN Position (siehe verbindliche Reihenfolge oben).

    Sucht bewusst erst AB `<nav class="sidebar">` (25.09., ECHTER FUND:
    die neue globale Kopfzeile, siehe base.html/`.global-header`, enthaelt
    VOR der Sidebar bereits einen eigenen Posteingang-Link
    (`header-icons__btn`, Umschlag-Icon) - eine ungescopte Suche nach
    `href="/dashboard/inbox"` traf danach zuerst DIESEN statt des
    eigentlichen Sidebar-Navigationseintrags)."""
    response = client.get("/dashboard/inbox")
    assert "<details" not in response.text
    sidebar_start = response.text.index('<nav class="sidebar">')
    sidebar_html = response.text[sidebar_start:]
    posteingang_start = sidebar_html.index('href="/dashboard/inbox"')
    row_start = sidebar_html.rindex("<a", 0, posteingang_start)
    row_end = sidebar_html.index(">", posteingang_start)
    assert "sidebar__group-summary--active" in sidebar_html[row_start:row_end]


def test_sidebar_profile_menu_settings_item_points_to_settings_page_for_admin(
    client: TestClient, seeded: dict
) -> None:
    """UI/UX-Ueberarbeitung (13.09.): der vorherige eigene Zahnrad-Button ist
    jetzt der "Einstellungen"-Eintrag im Profilmenue - fuehrt fuer Admins
    weiterhin direkt auf die echte Einstellungsseite."""
    response = client.get("/dashboard/inbox")
    einstellungen_link_start = response.text.rindex(
        "<a", 0, response.text.index(">Einstellungen<")
    )
    einstellungen_link_end = response.text.index(">", einstellungen_link_start)
    assert 'href="/dashboard/settings"' in response.text[einstellungen_link_start:einstellungen_link_end]


def test_sidebar_profile_menu_settings_item_points_to_account_overview_for_non_admin(
    mitarbeiter_client: TestClient, seeded: dict
) -> None:
    """Nicht-Admins haben keine eigene Einstellungsseite (/dashboard/settings
    ist admin_only, siehe app/web/settings_router.py) - der "Einstellungen"-
    Eintrag im Profilmenue fuehrt fuer sie stattdessen auf die Einstellungen-
    Uebersicht (account_overview.html)."""
    response = mitarbeiter_client.get("/dashboard/inbox")
    einstellungen_link_start = response.text.rindex(
        "<a", 0, response.text.index(">Einstellungen<")
    )
    einstellungen_link_end = response.text.index(">", einstellungen_link_start)
    assert 'href="/dashboard/account"' in response.text[einstellungen_link_start:einstellungen_link_end]


# --- Leerer Posteingang bleibt ein echter Posteingang (06.10., Owner-
# Direktive "POSTEINGANG AUF DEN BESTEHENDEN REFERENZSTAND ZURUECKFUEHREN"
# §5) - ersetzt die frueheren Onboarding-Banner-Tests (Prompt 48), die noch
# das jetzt bewusst geaenderte Verhalten voraussetzten ("total_count == 0
# zeigt das Onboarding-Banner STATT der Split-Pane-Ansicht"). Root Cause
# des urspruenglichen Befunds: genau diese Kopplung liess eine frische/
# leere Installation wie einen alten Setup-Screen wirken, obwohl Header/
# Tabs/Filter/Nachrichtenbereich bereits vollstaendig implementiert waren -
# siehe DECISIONS.md fuer die volle Herleitung.


def test_inbox_shows_real_shell_not_onboarding_when_empty(client: TestClient) -> None:
    """Ohne jede Nachricht (total_count == 0) bleibt der Posteingang die
    echte Arbeitsoberflaeche (Split-Pane mit Liste+Detail) - die grosse
    "Willkommen bei Lexono - Erste Schritte"-Karte ersetzt sie NICHT mehr."""
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert "Erste Schritte" not in response.text
    assert 'class="split"' in response.text
    # Sinnvoller Empty State INNERHALB der Nachrichtenliste statt dessen.
    assert "Noch keine Nachrichten." in response.text


def test_empty_inbox_still_shows_filter_tabs_and_search(client: TestClient) -> None:
    """Tabs/Filterleiste/Suche bleiben auch bei leerem Posteingang sichtbar
    und bedienbar (Direktive §5 "Tabs bleiben erhalten"/"Filter bleiben
    erhalten") - der fruehere Grund, sie bei total_count == 0 zu
    verbergen (ihr `hx-target="#message-list"` existierte dann nicht,
    siehe Git-Historie), entfaellt: `#message-list` existiert jetzt immer."""
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert 'hx-target="#message-list"' in response.text
    assert "Absender oder Betreff durchsuchen" in response.text


def test_nonempty_inbox_still_shows_filter_tabs_and_search(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert 'hx-target="#message-list"' in response.text
    assert "Absender oder Betreff durchsuchen" in response.text


def test_onboarding_banner_hidden_when_messages_exist(
    client: TestClient, seeded: dict
) -> None:
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert "Erste Schritte" not in response.text
    assert 'class="split"' in response.text


def test_email_accounts_link_always_available_even_when_inbox_empty(
    client: TestClient,
) -> None:
    """Direktive §5 "E-Mail-Konten verwalten bleibt verfügbar" - dieser
    Link stand schon vorher ausserhalb der total_count-Verzweigung im
    Seitenkopf, hier als expliziter Regressionstest festgehalten."""
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert "E-Mail-Konten verwalten" in response.text
    assert 'href="/dashboard/settings#email-postfach"' in response.text


# --- "Automatische Zuordnung (Vorschlag)" (14.09.) -------------------------
#
# ECHTER FUND: `MatterAssignmentService`/`MatterMatchingService` (Prompt 09)
# existierten bereits vollstaendig implementiert und isoliert getestet,
# wurden aber nie mit dem Posteingang verbunden - siehe app/web/router.py::
# _build_match_suggestion fuer den vollen Befund. Diese Tests decken die
# NEUE Verbindung ab, nicht die bereits an anderer Stelle getestete
# Matching-Logik selbst (siehe tests/test_matching_*.py).


def test_unmatched_message_without_candidate_shows_no_suggestion_card(
    client: TestClient, seeded: dict
) -> None:
    """Die bestehende `unmatched`-Nachricht aus `seeded` hat KEIN
    Aktenzeichen/keine bekannte E-Mail im Text - kein Kandidat, keine
    Karte (keine nutzlose leere Karte statt dessen)."""
    response = client.get(f"/dashboard/inbox/{seeded['unmatched_message_id']}")
    assert response.status_code == 200
    assert "Automatische Zuordnung (Vorschlag)" not in response.text


def test_matched_message_never_shows_suggestion_card(
    client: TestClient, seeded: dict
) -> None:
    """Eine bereits zugeordnete Nachricht zeigt NIE eine Vorschlagskarte -
    unabhängig davon, was der Matcher fände (message.matter_id is not None
    ist der alleinige Ausschlussgrund, siehe _load_detail_context)."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert response.status_code == 200
    assert "Automatische Zuordnung (Vorschlag)" not in response.text


def test_unmatched_message_with_reference_number_match_shows_suggestion_card(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Ein im Nachrichtentext gefundenes Aktenzeichen, das exakt zu einer
    bestehenden Akte passt, muss real als Vorschlag angezeigt werden -
    echter `MatterMatchingService`-Aufruf, kein Mock."""
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt. Bitte um Rückmeldung.",
    )
    db_session.add(candidate_message)
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{candidate_message.id}")

    assert response.status_code == 200
    assert "Automatische Zuordnung (Vorschlag)" in response.text
    assert "Einspruch Steuerbescheid 2025" in response.text
    assert "Synthetischer Testmandant GmbH" in response.text
    assert f'value="{seeded["matter_id"]}"' in response.text


def test_unmatched_message_with_match_shows_suggestion_card_via_detail_partial(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """ECHTER FUND (16.09., UI/UX-Sweep, real in der laufenden installierten
    Instanz reproduziert): `partials/message_detail.html` nutzt `icons.
    folder(...)` fuer die Vorschlagskarte, importierte `_icons.html` aber
    NIRGENDS selbst - beim vollen Seitenaufruf (`/dashboard/inbox/{id}`,
    siehe Test oben) unsichtbar, weil `inbox.html` das Makro bereits
    importiert und `{% include %}` den Kontext vererbt. Die tatsaechlich
    von der UI beim Klick auf eine Nachrichtenzeile verwendete Route ist
    aber die HTMX-Partial-Route `/dashboard/inbox/{id}/detail`
    (`inbox_message_detail_partial`), die `message_detail.html` OHNE
    umgebendes `inbox.html` direkt rendert - dort fehlte der Import
    tatsaechlich und jede nicht zugeordnete Nachricht MIT gefundenem
    Zuordnungsvorschlag brach mit `jinja2.exceptions.UndefinedError: 'icons'
    is undefined` (HTTP 500) ab. In der echten UI aeusserte sich das so,
    dass ein Klick auf eine solche Nachricht die Zeile als ausgewaehlt
    markierte, das Detail-Panel aber leer/auf dem Platzhaltertext blieb -
    ausgerechnet fuer den fuer den Gold-Workflow zentralen Fall (nicht
    zugeordnete Nachricht MIT Zuordnungsvorschlag). Behoben durch Ergaenzen
    des fehlenden `{% import "_icons.html" as icons %}` DIREKT in
    `message_detail.html` (nicht nur in `inbox.html`), damit beide
    Renderpfade (voller Seitenaufruf UND HTMX-Partial) funktionieren."""
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt. Bitte um Rückmeldung.",
    )
    db_session.add(candidate_message)
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{candidate_message.id}/detail")

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Automatische Zuordnung (Vorschlag)" in response.text
    assert f'value="{seeded["matter_id"]}"' in response.text


# --- "Erkannte Frist"-Vorschau in der Zuordnungs-Karte (24.09., Owner-
# Direktive "PRODUCT COMPLETION MODE" §2/§9 - Referenzbild
# `04_posteingang_nachricht_detail.png` zeigt ein drittes Feld "Frist"
# neben Mandant/Akte; bereits am 14.09. als bewusst zurueckgestellte
# Luecke dokumentiert, siehe PROJECT_STATE.md/DECISIONS.md) -----------------


def test_unmatched_message_with_deadline_in_text_shows_preview_card(
    client: TestClient, db_session: Session
) -> None:
    """Reine Vorschau (kein neuer Schreibpfad) - dieselbe Erkennung, die
    `DeadlineAnalysisService.analyze_message` beim tatsaechlichen Zuordnen
    ohnehin ausfuehrt (app/web/router.py::_preview_deadline)."""
    message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Fristsetzung",
        body_text="Bitte antworten Sie bis spätestens zum 15.03.2027.",
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{message.id}")

    assert response.status_code == 200
    assert "Erkannte Frist im Nachrichtentext" in response.text
    assert "15.03.2027" in response.text


def test_unmatched_message_with_deadline_shows_preview_via_detail_partial(
    client: TestClient, db_session: Session
) -> None:
    """Derselbe historische Fund wie bei der Zuordnungs-Vorschlagskarte
    oben (fehlender `icons`-Import brach nur die HTMX-Partial-Route) -
    beide Renderpfade muessen fuer die neue Karte funktionieren."""
    message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Fristsetzung",
        body_text="Bitte antworten Sie bis spätestens zum 15.03.2027.",
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{message.id}/detail")

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Erkannte Frist im Nachrichtentext" in response.text


def test_unmatched_message_without_deadline_shows_no_preview_card(
    client: TestClient, db_session: Session
) -> None:
    message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Allgemeine Frage",
        body_text="Guten Tag, ich habe eine allgemeine Frage zu meinem Fall.",
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{message.id}")

    assert response.status_code == 200
    assert "Erkannte Frist im Nachrichtentext" not in response.text


def test_already_matched_message_shows_no_deadline_preview(
    client: TestClient, seeded: dict
) -> None:
    """Die Vorschau ist nur fuer NICHT zugeordnete Nachrichten sinnvoll -
    eine bereits zugeordnete Nachricht hat laengst eine echte
    Fristenanalyse durchlaufen (siehe accept_matter_suggestion)."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")

    assert response.status_code == 200
    assert "Erkannte Frist im Nachrichtentext" not in response.text


# --- Manuelle Aktenzuordnung (16.09., "EXECUTION ORDER CORRECTION" -
# UI erst vervollstaendigen, bevor der Gold-Workflow ueber sie E2E getestet
# wird). Referenz `04_posteingang_nachricht_detail.png` zeigt eine
# editierbare Zuordnung; der dafuer noetige Endpunkt (`accept_matter_
# suggestion`) existierte bereits und akzeptiert jede existierende
# `matter_id`, wurde aber nur vom "Übernehmen"-Button der automatischen
# Vorschlagskarte genutzt - eine Nachricht OHNE gefundenen Vorschlag liess
# sich dadurch bisher UEBERHAUPT NICHT manuell zuordnen. ---


def test_unmatched_message_without_candidate_shows_manual_assignment_picker(
    client: TestClient, seeded: dict
) -> None:
    """Die bestehende `unmatched`-Nachricht hat KEINEN Zuordnungsvorschlag
    (siehe test_unmatched_message_without_candidate_shows_no_suggestion_card),
    muss aber trotzdem manuell zuordenbar sein - vorher war das fuer genau
    diesen Fall gar nicht moeglich."""
    response = client.get(f"/dashboard/inbox/{seeded['unmatched_message_id']}")
    assert response.status_code == 200
    assert "Manuell einer Akte zuordnen" in response.text
    assert "Einspruch Steuerbescheid 2025" in response.text
    assert f'value="{seeded["matter_id"]}"' in response.text


def test_matched_message_never_shows_manual_assignment_picker(
    client: TestClient, seeded: dict
) -> None:
    """Eine bereits zugeordnete Nachricht braucht keine (erneute)
    Zuordnungsmoeglichkeit."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")
    assert response.status_code == 200
    assert "Manuell einer Akte zuordnen" not in response.text


def test_manual_assignment_picker_also_shown_alongside_a_found_suggestion(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Der manuelle Picker erscheint bewusst AUCH neben einer vorhandenen
    Vorschlagskarte - als Korrekturmoeglichkeit, falls der automatische
    Vorschlag falsch liegt (kein Entweder-Oder)."""
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt. Bitte um Rückmeldung.",
    )
    db_session.add(candidate_message)
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{candidate_message.id}")

    assert response.status_code == 200
    assert "Automatische Zuordnung (Vorschlag)" in response.text
    assert "Manuell einer Akte zuordnen" in response.text


def test_manual_assignment_picker_excludes_schnellentwurf_placeholder_matters(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """ECHTER FUND (siehe OPEN_ISSUES.md "225 Junk-Fristen"): Schnellentwurf-
    Akten des Sammel-Platzhalter-Mandanten sind keine echten Akten - eine
    E-Mail dorthin zuzuordnen waere sinnlos. Dieselbe Ausschlussregel wie
    beim Aktenbestand-Fastpath (app/chat/service.py)."""
    placeholder_client = Client(name=PLACEHOLDER_CLIENT_NAME)
    db_session.add(placeholder_client)
    db_session.flush()
    db_session.add(
        Matter(client_id=placeholder_client.id, title="Schnellentwurf 2026-09-16")
    )
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{seeded['unmatched_message_id']}")

    assert response.status_code == 200
    assert "Schnellentwurf 2026-09-16" not in response.text
    # Die echte Akte bleibt weiterhin auswaehlbar.
    assert "Einspruch Steuerbescheid 2025" in response.text


def test_manual_assignment_form_assigns_previously_unmatchable_message(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Ende-zu-Ende: eine Nachricht OHNE jeden automatischen Vorschlag
    liess sich vor diesem Fix ueberhaupt keiner Akte zuordnen - jetzt
    per manuellem Picker moeglich, ueber denselben, bereits bestehenden
    und getesteten Endpunkt wie die automatische Vorschlagskarte."""
    response = client.get(f"/dashboard/inbox/{seeded['unmatched_message_id']}")
    csrf_token = extract_csrf(response.text)

    response = client.post(
        f"/dashboard/inbox/{seeded['unmatched_message_id']}/assign-matter",
        data={"csrf_token": csrf_token, "matter_id": seeded["matter_id"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    message = db_session.get(Message, seeded["unmatched_message_id"])
    db_session.refresh(message)
    assert message.matter_id == seeded["matter_id"]


def test_accept_suggestion_assigns_matter_and_cascades_to_documents(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt.",
    )
    db_session.add(candidate_message)
    db_session.flush()
    attached_document = Document(
        matter_id=None,
        message_id=candidate_message.id,
        original_filename="anhang_test.pdf",
        file_path="/data/intake/test/anhang_test.pdf",
    )
    db_session.add(attached_document)
    db_session.commit()

    page = client.get(f"/dashboard/inbox/{candidate_message.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/inbox/{candidate_message.id}/assign-matter",
        data={"csrf_token": csrf, "matter_id": seeded["matter_id"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(candidate_message)
    db_session.refresh(attached_document)
    assert candidate_message.matter_id == seeded["matter_id"]
    assert attached_document.matter_id == seeded["matter_id"]

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Message", entity_id=candidate_message.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "matter_match_accepted_by_user"


def test_accept_suggestion_triggers_deadline_analysis_on_message_text(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """ECHTER FUND (20.09., siehe app/deadlines/service.py-Moduldocstring):
    eine Frist im blossen Nachrichtentext (kein Dokumentanhang) wurde vor
    diesem Fix NIE erkannt - `DeadlineAnalysisService` lief ausschliesslich
    fuer Dokumente. Jetzt wird sie bei der Aktenzuordnung (derselbe Endpunkt
    wie oben) nachgeholt, GENAU wie fuer Dokumente bereits etabliert -
    review_status bleibt unreviewed (nie automatisch verbindlich)."""
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt. Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(candidate_message)
    db_session.commit()

    page = client.get(f"/dashboard/inbox/{candidate_message.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/inbox/{candidate_message.id}/assign-matter",
        data={"csrf_token": csrf, "matter_id": seeded["matter_id"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    created = db_session.query(Deadline).filter_by(message_id=candidate_message.id).all()
    assert len(created) == 1
    assert created[0].matter_id == seeded["matter_id"]
    assert created[0].due_date is not None
    assert created[0].review_status == "unreviewed"


def test_accept_suggestion_retroactively_analyzes_already_processed_attachment(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """ECHTER FUND: ein Anhang, der VOR der Aktenzuordnung bereits
    Volltext extrahiert bekam (z. B. durch OCR bei der Mail-Ingestion),
    wurde bei diesem ersten Versuch mangels Aktenzuordnung nur
    uebersprungen (siehe DeadlineAnalysisService.analyze_document) - ohne
    diesen Fix blieb er fuer immer unanalysiert, weil die Textextraktion
    kein zweites Mal laeuft. Jetzt wird die Fristenanalyse fuer bereits
    verarbeitete Anhaenge bei der Zuordnung nachgeholt."""
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt.",
    )
    db_session.add(candidate_message)
    db_session.flush()
    attached_document = Document(
        matter_id=None,
        message_id=candidate_message.id,
        original_filename="anhang_test.pdf",
        file_path="/data/intake/test/anhang_test.pdf",
        extracted_text="Bitte antworten Sie bis zum 15.03.2027.",
    )
    db_session.add(attached_document)
    db_session.commit()

    page = client.get(f"/dashboard/inbox/{candidate_message.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/inbox/{candidate_message.id}/assign-matter",
        data={"csrf_token": csrf, "matter_id": seeded["matter_id"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    created = db_session.query(Deadline).filter_by(document_id=attached_document.id).all()
    assert len(created) == 1
    assert created[0].matter_id == seeded["matter_id"]


def test_attachment_chip_links_to_the_real_document_page_when_matter_known(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """ECHTER FUND (17.09., Overnight-Direktive §6 "Dokumente sind ein
    Hauptluecken-Bereich"): der Anhang-Chip zeigte bereits ein echtes,
    persistiertes `Document` an, war aber ein reines <span> ohne jeden Weg,
    das Dokument tatsaechlich zu oeffnen - identisches Muster wie das
    bereits geloeste `chat.html`-Dokumentkontext-Chip. Die zugeordnete
    Nachricht in `seeded` hat bereits ein angehaengtes Dokument mit
    gesetzter `matter_id` - der Chip muss jetzt ein echter Link auf die
    (bereits vollstaendige) Aktendokument-Seite sein."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")

    assert response.status_code == 200
    assert (
        f'/dashboard/matters/{seeded["matter_id"]}/document/' in response.text
    )
    assert '<a href="/dashboard/matters/' in response.text
    assert 'class="attachment-card__name"' in response.text


def test_attachment_chip_stays_plain_span_when_not_yet_assigned_to_a_matter(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Gegenprobe: ein Dokument OHNE `matter_id` (Nachricht noch nicht
    zugeordnet) darf KEINEN Link vortaeuschen, den es noch nicht geben
    kann (die Aktendokument-Route braucht zwingend eine matter_id in der
    URL) - bleibt bewusst ein reines <span>, ehrlich statt ein toter
    Link."""
    unassigned_message = Message(
        matter_id=None,
        direction="inbound",
        sender="neuer.kontakt@example-testdomain.invalid",
        subject="Neue Anfrage mit Anhang",
        body_text="Testinhalt.",
    )
    db_session.add(unassigned_message)
    db_session.flush()
    db_session.add(
        Document(
            matter_id=None,
            message_id=unassigned_message.id,
            original_filename="noch_nicht_zugeordnet.pdf",
            file_path="/data/intake/test/noch_nicht_zugeordnet.pdf",
        )
    )
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{unassigned_message.id}")

    assert response.status_code == 200
    assert "noch_nicht_zugeordnet.pdf" in response.text
    assert '<a href="/dashboard/matters/' not in response.text
    assert "/document/" not in response.text


def test_accept_suggestion_rejects_wrong_csrf_token(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt.",
    )
    db_session.add(candidate_message)
    db_session.commit()

    response = client.post(
        f"/dashboard/inbox/{candidate_message.id}/assign-matter",
        data={"csrf_token": "falscher-token", "matter_id": seeded["matter_id"]},
        follow_redirects=False,
    )

    assert response.status_code == 403
    db_session.refresh(candidate_message)
    assert candidate_message.matter_id is None


def test_accept_suggestion_rejects_unknown_matter_id(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Ein manipulierter `matter_id`-Wert darf hoechstens einen 404
    ausloesen, nie eine Zuordnung zu einer nicht existierenden Akte."""
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage",
        body_text="Kein Aktenzeichen enthalten.",
    )
    db_session.add(candidate_message)
    db_session.commit()

    page = client.get(f"/dashboard/inbox/{candidate_message.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/inbox/{candidate_message.id}/assign-matter",
        data={"csrf_token": csrf, "matter_id": "does-not-exist"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    db_session.refresh(candidate_message)
    assert candidate_message.matter_id is None


def test_mitarbeiter_can_accept_suggestion(
    mitarbeiter_client: TestClient, db_session: Session, seeded: dict
) -> None:
    """`accept_matter_suggestion` nutzt `require_role()` OHNE explizite
    Rollen-/Berechtigungs-Einschraenkung (identisches Muster wie
    app/web/lock_router.py::lock_now) - jede angemeldete Rolle darf einen
    Vorschlag uebernehmen, nicht nur Admin."""
    candidate_message = Message(
        matter_id=None,
        direction="inbound",
        sender="unbekannt@example-testdomain.invalid",
        subject="Rückfrage zur Akte",
        body_text="Aktenzeichen: 2025/0142-ESt.",
    )
    db_session.add(candidate_message)
    db_session.commit()

    page = mitarbeiter_client.get(f"/dashboard/inbox/{candidate_message.id}")
    csrf = extract_csrf(page.text)

    response = mitarbeiter_client.post(
        f"/dashboard/inbox/{candidate_message.id}/assign-matter",
        data={"csrf_token": csrf, "matter_id": seeded["matter_id"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(candidate_message)
    assert candidate_message.matter_id == seeded["matter_id"]


# --- Posteingang Produkt-Completion (25.09., Owner-Direktive "POSTEINGANG
# PRODUCT COMPLETION" - Referenzabgleich `04_posteingang_nachricht_
# detail.png") ---------------------------------------------------------


def test_message_row_shows_real_avatar_initials_from_sender_name(
    client: TestClient, seeded: dict
) -> None:
    """Absender-Avatar (app/web/router.py::_avatar_initials) - echte
    Initialen aus dem echten Absendernamen, kein Fake-Icon. "j.mueller@..."
    hat keine Leerzeichen/Buchstaben-"Woerter" im ueblichen Sinn aus der
    E-Mail-Adresse selbst - die Funktion faellt dann auf den ersten
    Buchstaben zurueck."""
    response = client.get("/dashboard/inbox")

    assert response.status_code == 200
    assert 'class="message-row__avatar message-row__avatar--' in response.text


def test_message_row_shows_attachment_icon_only_when_documents_exist(
    client: TestClient, seeded: dict
) -> None:
    """Das Anhang-Icon in der Liste (25.09.) haengt an ECHTEN `Document`-
    Zeilen (`message.documents`) - die zugeordnete Nachricht hat eines,
    die unzugeordnete keins."""
    response = client.get("/dashboard/inbox")

    assert response.status_code == 200
    # Von den drei Nachrichten in `seeded` hat GENAU die zugeordnete
    # ("matched") ein Dokument - das Icon darf daher genau einmal
    # auftauchen (nicht bei "unmatched"/"outbound").
    assert response.text.count("message-row__attachment-icon") == 1


def test_attachment_card_shows_real_file_size_when_file_exists_on_disk(
    client: TestClient, db_session: Session, tmp_path, seeded: dict
) -> None:
    """Dateigroesse (app/web/router.py::_document_file_size) kommt live
    von der echten Datei auf der Platte - kein DB-Feld, kein Fake-Wert.
    Existierende Tests nutzen bewusst nicht existierende Pfade (siehe
    `seeded`-Fixture) - dieser Test schreibt eine ECHTE Testdatei, um die
    tatsaechliche Groessen-Anzeige zu verifizieren."""
    real_file = tmp_path / "echte_testdatei.pdf"
    real_file.write_bytes(b"%PDF-1.4 Testinhalt" * 50)

    message = Message(
        matter_id=seeded["matter_id"],
        direction="inbound",
        sender="mandant@example-testdomain.invalid",
        subject="Nachricht mit echter Testdatei",
        body_text="Testinhalt.",
    )
    db_session.add(message)
    db_session.flush()
    document = Document(
        matter_id=seeded["matter_id"],
        message_id=message.id,
        original_filename="echte_testdatei.pdf",
        file_path=str(real_file),
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/inbox/{message.id}")

    assert response.status_code == 200
    assert "attachment-card" in response.text
    assert "KB" in response.text or "Bytes" in response.text


def test_message_detail_body_renders_before_attachments_and_actions(
    client: TestClient, seeded: dict
) -> None:
    """Verbindliche Reihenfolge (Owner-Direktive §12): Nachrichtentext vor
    Anhaengen vor Aktionen - vorher standen Aktionen/Zuordnungs-Karten VOR
    dem Nachrichtentext."""
    response = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}")

    assert response.status_code == 200
    body_pos = response.text.index('class="detail-body"')
    attachments_pos = response.text.index("attachment-card")
    actions_pos = response.text.index('class="detail-actions"')
    assert body_pos < attachments_pos < actions_pos


def test_inbox_filter_by_matter_shows_only_that_matters_messages(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Echte Akten-Filterleiste (25.09., Owner-Direktive §9) - beeinflusst
    reale Daten, kein Kosmetik-Dropdown."""
    other_client = Client(name="Anderer Testmandant GmbH")
    db_session.add(other_client)
    db_session.flush()
    other_matter = Matter(client_id=other_client.id, title="Andere Akte")
    db_session.add(other_matter)
    db_session.flush()
    other_message = Message(
        matter_id=other_matter.id,
        direction="inbound",
        sender="andere@example-testdomain.invalid",
        subject="Nachricht der anderen Akte",
        body_text="Testinhalt.",
    )
    db_session.add(other_message)
    db_session.commit()

    response = client.get(f"/dashboard/inbox?matter={seeded['matter_id']}")

    assert response.status_code == 200
    assert "Steuerbescheid 2025 - Einspruchsfrist" in response.text
    assert "Nachricht der anderen Akte" not in response.text


def test_inbox_sort_oldest_first_reverses_default_order(
    client: TestClient, seeded: dict
) -> None:
    """Echte Sortierung (25.09.) - kein Fake-Toggle, tatsaechliche
    Umkehrung der Datenbank-Reihenfolge."""
    newest_first = client.get("/dashboard/inbox?sort=newest")
    oldest_first = client.get("/dashboard/inbox?sort=oldest")

    assert newest_first.status_code == 200
    assert oldest_first.status_code == 200
    newest_positions = newest_first.text.index("Steuerbescheid 2025 - Einspruchsfrist")
    newest_outbound_pos = newest_first.text.index("RE: Steuerbescheid 2025")
    oldest_positions = oldest_first.text.index("Steuerbescheid 2025 - Einspruchsfrist")
    oldest_outbound_pos = oldest_first.text.index("RE: Steuerbescheid 2025")
    # Outbound wurde nach "matched" angelegt, ist also juenger - bei
    # "newest" zuerst, bei "oldest" zuletzt.
    assert newest_outbound_pos < newest_positions
    assert oldest_positions < oldest_outbound_pos


def test_inbox_sort_toggle_button_targets_the_opposite_sort_order(
    client: TestClient, seeded: dict
) -> None:
    """26.09., Owner-Direktive "POSTEINGANG FINAL POLISH" §10: das volle
    "Neueste zuerst"-Textdropdown wurde durch einen kompakten Icon-Button
    ersetzt (Referenzbild zeigt ein kleines quadratisches Sortier-Icon
    statt eines Text-Dropdowns) - dieselbe, bereits bestehende
    `sort`-Backend-Logik (siehe test_inbox_sort_oldest_first_reverses_
    default_order), nur eine andere Bedienoberflaeche."""
    default_response = client.get("/dashboard/inbox")
    assert 'class="inbox-sort-toggle"' in default_response.text
    assert '<select name="sort"' not in default_response.text
    assert 'input type="hidden" name="sort" value="newest"' in default_response.text
    # Standard ist "newest" - der Button muss auf "oldest" umschalten.
    assert 'hx-vals=\'{"sort": "oldest"}\'' in default_response.text

    oldest_response = client.get("/dashboard/inbox?sort=oldest")
    assert 'hx-vals=\'{"sort": "newest"}\'' in oldest_response.text


def test_inbox_filter_row_and_search_share_one_row(client: TestClient, seeded: dict) -> None:
    """26.09., Owner-Direktive "POSTEINGANG FINAL POLISH" §4/§11: Filter-
    Dropdowns, Sortier-Button und Suche sind jetzt EINE gemeinsame Zeile
    (vorher zwei) - spart eine ganze Zeile vertikale Hoehe. Reine
    Struktur-/CSS-Aenderung, dieselbe bestehende Filter-Logik."""
    response = client.get("/dashboard/inbox")
    filter_bar_start = response.text.index('class="inbox-filter-bar"')
    filter_bar_html = response.text[filter_bar_start : filter_bar_start + 3000]
    assert 'name="client"' in filter_bar_html
    assert 'name="matter"' in filter_bar_html
    assert 'name="period"' in filter_bar_html
    assert 'class="inbox-sort-toggle"' in filter_bar_html
    assert 'name="q"' in filter_bar_html


def test_email_accounts_link_visible_for_admin_only(
    client: TestClient, mitarbeiter_client: TestClient, seeded: dict
) -> None:
    """"E-Mail-Konten verwalten" verlinkt echt auf den bestehenden
    IMAP-Bereich der (admin-only) Einstellungsseite - fuer Nicht-Admins
    bewusst gar nicht sichtbar statt eines Links, der ohnehin 403 werfen
    wuerde."""
    admin_response = client.get("/dashboard/inbox")
    mitarbeiter_response = mitarbeiter_client.get("/dashboard/inbox")

    assert "E-Mail-Konten verwalten" in admin_response.text
    assert "E-Mail-Konten verwalten" not in mitarbeiter_response.text


# --- Posteingang Final UI/UX Product-Completion (25.09., Owner-Direktive
# "POSTEINGANG FINAL UI/UX PRODUCT-COMPLETION" - Referenzabgleich
# `04_posteingang_nachricht_detail.png`) ---------------------------------


def test_inbox_page_auto_selects_a_message_by_default(
    client: TestClient, seeded: dict
) -> None:
    """Kein leerer Standardzustand mehr (Direktive §17) - beim Oeffnen von
    `/dashboard/inbox` OHNE explizite Nachrichten-ID zeigt das rechte
    Panel direkt eine echte Nachricht (die erste der aktuellen Sortierung,
    hier "newest" -> das zuletzt angelegte "outbound", siehe
    `test_inbox_sort_oldest_first_reverses_default_order` fuer die
    Zeitstempel-Reihenfolge in `seeded`), kein "Wähle links..."-Hinweis."""
    response = client.get("/dashboard/inbox")

    assert response.status_code == 200
    assert "Wähle links eine Nachricht aus" not in response.text
    assert "RE: Steuerbescheid 2025" in response.text
    assert 'class="detail-body"' in response.text


def test_inbox_page_shows_empty_state_when_filter_matches_nothing(
    client: TestClient, seeded: dict
) -> None:
    """Gegenprobe: liefert der aktuelle Filter/die aktuelle Suche keine
    Treffer, bleibt der ehrliche Empty State bestehen - keine erzwungene
    Auswahl aus einer leeren Liste."""
    response = client.get("/dashboard/inbox?q=definitiv-kein-treffer-xyz")

    assert response.status_code == 200
    assert "Wähle links eine Nachricht aus" in response.text


def test_inbox_page_has_no_back_link(client: TestClient, seeded: dict) -> None:
    """Posteingang ist eine Hauptnavigationsebene wie Chat, kein
    Unterpunkt - der "Zurueck"-Pfeil (base.html) impliziert faelschlich
    einen sinnvollen Vorgaenger-Kontext (Direktive §9)."""
    response = client.get("/dashboard/inbox")

    assert response.status_code == 200
    assert "header-back" not in response.text


def test_inbox_client_filter_shows_only_that_clients_messages(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Echter, eigenstaendiger Mandanten-Filter (25.09., Korrektur einer
    frueheren zu engen Annahme - ein Mandant kann mehrere Akten haben)."""
    other_client = Client(name="Anderer Mandant fuer Client-Filter GmbH")
    db_session.add(other_client)
    db_session.flush()
    other_matter = Matter(client_id=other_client.id, title="Andere Akte (Client-Filter)")
    db_session.add(other_matter)
    db_session.flush()
    other_message = Message(
        matter_id=other_matter.id,
        direction="inbound",
        sender="andere@example-testdomain.invalid",
        subject="Nachricht des anderen Mandanten",
        body_text="Testinhalt.",
    )
    db_session.add(other_message)
    db_session.commit()

    seeded_client_id = (
        db_session.query(Matter).filter_by(id=seeded["matter_id"]).first().client_id
    )
    response = client.get(f"/dashboard/inbox?client={seeded_client_id}")

    assert response.status_code == 200
    assert "Steuerbescheid 2025 - Einspruchsfrist" in response.text
    assert "Nachricht des anderen Mandanten" not in response.text


def test_inbox_period_filter_excludes_older_messages(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Echter Zeitraum-Filter (25.09.) - feste Presets statt eines freien
    Datumsbereichs (Direktive §13: "kein neues Backend-System pro
    Filter"), aber real auf `Message.created_at` filternd."""
    from datetime import datetime, timedelta, timezone

    old_message = Message(
        matter_id=None,
        direction="inbound",
        sender="alt@example-testdomain.invalid",
        subject="Sehr alte Nachricht",
        body_text="Testinhalt.",
        created_at=datetime.now(timezone.utc) - timedelta(days=200),
    )
    db_session.add(old_message)
    db_session.commit()

    all_time = client.get("/dashboard/inbox?period=all")
    last_30_days = client.get("/dashboard/inbox?period=30d")

    assert all_time.status_code == 200
    assert last_30_days.status_code == 200
    assert "Sehr alte Nachricht" in all_time.text
    assert "Sehr alte Nachricht" not in last_30_days.text
    # Die aktuellen (gerade erst angelegten) seeded-Nachrichten bleiben in
    # beiden Zeitraeumen sichtbar.
    assert "Steuerbescheid 2025 - Einspruchsfrist" in last_30_days.text


def test_inbox_filter_tab_click_overrides_filter_param_via_htmx(
    client: TestClient, seeded: dict
) -> None:
    """Das gemeinsame `#inbox-filter-form` (25.09.-Refactoring) darf den
    Filter-Tab-Wert nicht verlieren - `hx-vals` muss den versteckten
    "filter"-Wert des Formulars fuer den jeweiligen Tab ueberschreiben,
    nicht nur zusaetzlich senden. Direkter Server-seitiger Nachweis (kein
    echter Browser/htmx in diesem Test): derselbe Query-Parameter, den
    `hx-vals` erzeugen wuerde, liefert tatsaechlich nur unzugeordnete
    Nachrichten."""
    response = client.get("/dashboard/inbox/list?filter=unmatched")

    assert response.status_code == 200
    assert "Betriebspruefung angekuendigt" in response.text
    assert "Steuerbescheid 2025 - Einspruchsfrist" not in response.text
