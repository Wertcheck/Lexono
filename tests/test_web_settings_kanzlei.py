"""Tests für den in app/web/settings_router.py + settings.html eingebetteten
"Kanzlei"-Tab (06.10., Owner-Direktive "SETTINGS -> KANZLEI", erweitert um
die Owner-Direktive "SETTINGS -> KANZLEI UX REFACTOR" - kompakte
Übersichtskarten + Bearbeiten-Dialoge statt permanent sichtbarer
Großformulare).

Deckt NUR ab, was dieser Tab NEU hinzufügt: legal_form/address_addition,
"Standorte & Adressen" als ehrliche Einzelstandort-Darstellung, read-only
Markenfarben, ehrliche "Nicht verbunden"-Integrationen, eingebettete
Fachliche-Schwerpunkte-Karte, sowie (UX-Refactor) die kompakten
Zusammenfassungskarten + der Dialog-Auto-Reopen-Mechanismus bei Fehlern.
Die bestehenden Stammdaten-/Logo-/Signatur-/Fachbereich-POST-Endpunkte
selbst sind bereits ausführlich in tests/test_web_settings.py getestet
(Zugriffsschutz, Validierung, Upload-Sicherheit, Singleton-Verhalten) -
hier bewusst KEINE Duplikation. Echtes Oeffnen/Schliessen eines <dialog>
per showModal()/close() ist clientseitiges JS und damit ausserhalb der
Reichweite von TestClient-Tests - das wird separat per echtem Browser
(CDP) visuell verifiziert (siehe Abschlussbericht); hier wird geprüft,
was serverseitig tatsächlich beeinflussbar ist: Dialog-Markup ist im
HTML vorhanden, enthält die richtigen Werte, und der Auto-Reopen-
Marker (`open_dialog`) landet nach einem Fehler korrekt im Response."""

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
from app.models import FirmProfile
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


def _login_admin(client: TestClient, db_session: Session, email: str = "admin@kanzlei.test") -> None:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["admin"], email)
    login(client, email)


def _login_non_admin(client: TestClient, db_session: Session) -> None:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")


def _kanzlei_tab(client: TestClient) -> str:
    response = client.get("/dashboard/settings?tab=kanzlei")
    assert response.status_code == 200
    return response.text


# --- Rechtsform/Adresszusatz: echte, neue Stammdatenfelder ---


def test_legal_form_and_address_addition_persist_and_round_trip(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    page = client.get("/dashboard/settings?tab=kanzlei")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/settings/profile",
        data={
            "csrf_token": csrf,
            "firm_name": "Kanzlei Mustermann Rechtsanwälte",
            "legal_form": "Partnerschaft mbB",
            "street": "Musterstraße 12",
            "address_addition": "c/o Bürogemeinschaft Musterhaus",
            "postal_code": "10115",
            "city": "Berlin",
            "phone": "",
            "email": "",
            "website": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "tab=kanzlei" in response.headers["location"]

    profile = db_session.query(FirmProfile).one()
    assert profile.legal_form == "Partnerschaft mbB"
    assert profile.address_addition == "c/o Bürogemeinschaft Musterhaus"

    html = _kanzlei_tab(client)
    assert "Partnerschaft mbB" in html
    assert "Bürogemeinschaft Musterhaus" in html


# --- Standorte & Adressen: ehrliche Einzelstandort-Darstellung, keine
# erfundenen Zweigstellen ---


def test_standorte_card_shows_real_single_address_not_fake_branches(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/no-fake-functionality + /real-data: die Referenz zeigt München/
    Hamburg als Zweigstellen-BEISPIELE - Lexono hat kein Mehrstandort-
    Modell, diese Namen dürfen nirgends auftauchen. Die eine echte
    Anschrift wird stattdessen ehrlich als "Hauptsitz" dargestellt."""
    _login_admin(client, db_session)
    profile = db_session.query(FirmProfile).first()
    if profile is None:
        profile = FirmProfile(firm_name="")
        db_session.add(profile)
    profile.street = "Kurfürstendamm 1"
    profile.postal_code = "10707"
    profile.city = "Berlin"
    db_session.commit()

    html = _kanzlei_tab(client)
    assert "Standorte &amp; Adressen" in html
    assert "Hauptsitz" in html
    assert "Kurfürstendamm 1" in html
    for fake_branch in ("Zweigstelle München", "Zweigstelle Hamburg", "Maximilianstraße", "Jungfernstieg"):
        assert fake_branch not in html


def test_standorte_card_shows_honest_empty_state_without_address(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    html = _kanzlei_tab(client)
    assert "Noch keine Anschrift hinterlegt" in html


# --- Branding: echte, feste CI-Farben, NICHT editierbar/fake ---


def test_branding_shows_real_fixed_brand_colors_read_only(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/no-fake-functionality: es gibt keinen Mechanismus, die Lexono-
    Markenfarben pro Installation zu aendern (bestaetigt per Grep im
    /inspect) - die Karte muss die ECHTEN, aktuellen CI-Werte (siehe
    app/web/static/css/app.css :root) zeigen, NICHT editierbare
    Texteingaben und NICHT die veraltete Referenzbild-Farbe #16A34A."""
    _login_admin(client, db_session)
    html = _kanzlei_tab(client)
    assert "#249D74" in html
    assert "#101828" in html
    assert "#16A34A" not in html
    assert 'name="primary_color"' not in html
    assert 'name="secondary_color"' not in html


# --- Integrationen: ehrlich "Nicht verbunden", kein Fake-Connect ---


def test_integrations_card_shows_honest_not_connected_status(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Weder Kalender- noch DMS-Integration existiert in dieser Codebasis
    (bestaetigt per Grep im /inspect) - beide Karten muessen "Nicht
    verbunden" zeigen, kein funktionsloser "Verbinden"-Button."""
    _login_admin(client, db_session)
    html = _kanzlei_tab(client)
    assert "Kalender" in html
    assert "Dokumentenmanagement" in html
    assert html.count("Nicht verbunden") >= 2

    # Beschraenkt auf die Kanzlei-Integrationen-Karte selbst - andere Tabs
    # (z. B. "KI & Datenschutz") haben legitime, echte "Verbinden"-Buttons
    # fuer eine andere, tatsaechlich funktionierende Funktion; die
    # Behauptung betrifft nur DIESE Karte.
    integrations_start = html.index("Kanzlei-Integrationen")
    next_tabpanel_start = html.index('data-tabpanel="lizenz"', integrations_start)
    integrations_block = html[integrations_start:next_tabpanel_start]
    assert ">Verbinden<" not in integrations_block


# --- Fachliche Schwerpunkte bleiben nach der Einbettung erreichbar ---


def test_practice_areas_still_reachable_from_embedded_kanzlei_tab(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    html = _kanzlei_tab(client)
    assert "Fachliche Schwerpunkte" in html
    assert "Erbrecht" in html


# --- Alte Standalone-Seite leitet jetzt hierher um (keine zweite UI) ---


def test_old_standalone_profile_page_redirects_to_settings_tab(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings/profile", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/settings?tab=kanzlei"


def test_old_standalone_profile_page_still_requires_admin(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/negative-tests #4 (nicht berechtigter Benutzer): die Rollenpruefung
    greift VOR dem Redirect, nicht erst auf der Zielseite."""
    _login_non_admin(client, db_session)
    response = client.get("/dashboard/settings/profile", follow_redirects=False)
    assert response.status_code == 403


# ==========================================================================
# UX-Refactor (06.10., Owner-Direktive "SETTINGS -> KANZLEI UX REFACTOR") -
# kompakte Übersichtskarten + Bearbeiten-Dialoge statt Großformularen.
# ==========================================================================


def test_overview_cards_are_compact_not_full_forms(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/overview-first + /compactness: die Hauptseite zeigt nur kurze
    Zusammenfassungen + "Bearbeiten"/"Verwalten"-Buttons - die vollen
    Formularfelder (z. B. das Rechtsform-Eingabefeld) duerfen NICHT
    permanent sichtbar in der Uebersicht stehen, nur innerhalb der
    Dialoge weiter unten im Markup."""
    _login_admin(client, db_session)
    profile = db_session.query(FirmProfile).first() or FirmProfile(firm_name="")
    if profile.id is None:
        db_session.add(profile)
    profile.firm_name = "Kanzlei Weidmann & Partner"
    profile.legal_form = "Partnerschaft mbB"
    db_session.commit()

    html = _kanzlei_tab(client)
    overview_start = html.index('data-tabpanel="kanzlei"')
    dialogs_start = html.index("BEARBEITEN-DIALOGE") if "BEARBEITEN-DIALOGE" in html else html.index('id="kanzleiinformationen-dialog"')
    overview_html = html[overview_start:dialogs_start]

    # Kompakte Zusammenfassung sichtbar in der Uebersicht ...
    assert "Kanzlei Weidmann &amp; Partner" in overview_html
    assert "Partnerschaft mbB" in overview_html
    assert "Bearbeiten" in overview_html
    # ... aber KEIN permanent sichtbares Eingabefeld fuer Rechtsform/
    # Adresszusatz/Telefon/etc. in der Uebersicht selbst.
    assert 'name="legal_form"' not in overview_html
    assert 'name="address_addition"' not in overview_html
    assert 'name="phone"' not in overview_html


def test_edit_dialogs_contain_full_forms_with_real_values(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/functional-preservation: ALLE bisherigen Felder bleiben
    bearbeitbar - nur eben innerhalb des Dialogs statt permanent
    sichtbar."""
    _login_admin(client, db_session)
    profile = db_session.query(FirmProfile).first() or FirmProfile(firm_name="")
    if profile.id is None:
        db_session.add(profile)
    profile.firm_name = "Kanzlei Weidmann & Partner"
    profile.legal_form = "Partnerschaft mbB"
    profile.phone = "+49 30 1234567"
    db_session.commit()

    html = _kanzlei_tab(client)
    for field in (
        "firm_name", "legal_form", "address_addition", "street", "postal_code",
        "city", "phone", "email", "website", "signatory_name",
    ):
        assert f'name="{field}"' in html
    assert 'value="Partnerschaft mbB"' in html
    assert 'value="+49 30 1234567"' in html
    assert 'id="kanzleiinformationen-dialog"' in html
    assert 'id="branding-dialog"' in html
    assert 'id="standorte-dialog"' in html
    assert 'id="fachliche-schwerpunkte-dialog"' in html


def test_blank_name_error_reopens_kanzleiinformationen_dialog(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/error-handling: "Dialog bleibt geöffnet" - nach einem
    Validierungsfehler muss der Redirect den richtigen Dialog zum
    automatischen Wiederoeffnen markieren (open_dialog) UND die Seite
    muss das JS-Signal zum tatsaechlichen Wiederoeffnen enthalten."""
    _login_admin(client, db_session)
    page = client.get("/dashboard/settings?tab=kanzlei")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/settings/profile",
        data={"csrf_token": csrf, "firm_name": "   ", "legal_form": "", "street": "",
              "address_addition": "", "postal_code": "", "city": "", "phone": "",
              "email": "", "website": "", "signatory_name": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "open_dialog=kanzleiinformationen" in response.headers["location"]

    followed = client.get(response.headers["location"])
    assert 'var activeDialog = "kanzleiinformationen";' in followed.text
    # Fehlermeldung erscheint INNERHALB des betroffenen Dialogs, nicht nur
    # als allgemeiner Seiten-Banner (der hinter dem Dialog-Backdrop
    # optisch verschwinden würde).
    dialog_start = followed.text.index('id="kanzleiinformationen-dialog"')
    dialog_end = followed.text.index("</dialog>", dialog_start)
    assert "Kanzleiname darf nicht leer sein" in followed.text[dialog_start:dialog_end]


def test_invalid_logo_upload_reopens_branding_dialog(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    page = client.get("/dashboard/settings?tab=kanzlei")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/settings/profile/logo",
        data={"csrf_token": csrf},
        files={"logo": ("logo.svg", b"<svg></svg>", "image/svg+xml")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "open_dialog=branding" in response.headers["location"]

    followed = client.get(response.headers["location"])
    assert 'var activeDialog = "branding";' in followed.text


def test_invalid_practice_area_reopens_its_dialog(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    page = client.get("/dashboard/settings?tab=kanzlei")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf, "practice_areas": ["Erfundenes Rechtsgebiet"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "open_dialog=fachliche-schwerpunkte" in response.headers["location"]

    followed = client.get(response.headers["location"])
    assert 'var activeDialog = "fachliche-schwerpunkte";' in followed.text


def test_open_dialog_query_param_rejects_unknown_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Dieselbe Allowlist-Haertung wie beim bestehenden `tab`-Parameter
    (test_general_settings_tab_param_is_validated_against_allowlist in
    tests/test_web_settings.py) - ein beliebiger `open_dialog`-Wert darf
    nicht ungeprueft in den JS-Kontext gelangen."""
    _login_admin(client, db_session)
    response = client.get(
        "/dashboard/settings?tab=kanzlei&open_dialog=<script>alert(1)</script>"
    )
    assert response.status_code == 200
    assert 'var activeDialog = "";' in response.text
    assert "<script>alert(1)</script>" not in response.text


def test_successful_save_does_not_auto_reopen_any_dialog(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Nur bei einem Fehler soll sich ein Dialog automatisch wieder
    oeffnen - nach einem erfolgreichen Speichern zeigt die Uebersicht
    einfach die aktualisierten Werte (/save: "Card aktualisiert")."""
    _login_admin(client, db_session)
    page = client.get("/dashboard/settings?tab=kanzlei")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/settings/profile",
        data={"csrf_token": csrf, "firm_name": "Kanzlei Erfolgreich", "legal_form": "",
              "street": "", "address_addition": "", "postal_code": "", "city": "",
              "phone": "", "email": "", "website": "", "signatory_name": ""},
        follow_redirects=False,
    )
    assert "open_dialog" not in response.headers["location"]

    followed = client.get(response.headers["location"])
    assert 'var activeDialog = "";' in followed.text
    assert "Kanzlei Erfolgreich" in followed.text
