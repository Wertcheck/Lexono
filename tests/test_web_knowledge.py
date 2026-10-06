"""Tests für app/web/knowledge_router.py (14.09., UI-Build).

Schliesst einen echten UI-Gap: `/dashboard/knowledge` war ein reiner
PLATZHALTER ("In Vorbereitung für das v0.2-Update"), obwohl die Inhalte und
der `KnowledgeItemService` laengst existierten - ein Hauptnavigationspunkt,
der vorhandenen Wert verbirgt und das Produkt unfertig wirken laesst.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import KnowledgeItem, Law, LawSection, Source
from app.models.base import Base
from tests.auth_test_utils import (
    create_test_user,
    extract_csrf,
    login,
    login_as_admin,
    seed_roles,
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
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def mitarbeiter_client(db_session: Session) -> Iterator[TestClient]:
    """Fuer Berechtigungs-Negativtests der neuen Erfassungs-/Freigabe-
    Routen (26.09., "AUTONOMOUS PRODUCT GAP AUDIT") - Anlegen/Freigeben
    von Rechtsquellen/Textbausteinen ist auf Admin/Anwalt beschraenkt,
    identisches Muster wie tests/test_web_clients.py."""
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        roles = seed_roles(db_session)
        create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
        login(test_client, "mitarbeiter@kanzlei.test")
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_install_progress() -> Iterator[None]:
    """Der Installations-Fortschritt lebt in einem modul-globalen Dict
    (siehe app/laws/install_service.py-Moduldocstring: bewusst keine DB-
    Tabelle) - MUSS zwischen Tests zurückgesetzt werden, sonst könnte ein
    "Fehler"-Zustand aus einem Test einen späteren, unabhängigen Test
    beeinflussen (z. B. den Toggle-Endpunkt in den falschen Zweig
    schicken)."""
    import app.laws.install_service as install_service

    install_service._progress.clear()
    yield
    install_service._progress.clear()


@pytest.fixture(autouse=True)
def _use_test_session_local_for_background_install(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ECHTER FUND (03.10., beim Hinzufügen der Aktualisierungs-Tests
    entdeckt): der Toggle-Endpunkt löst über `start_install` einen echten
    Hintergrund-Thread aus (`app/laws/install_service.py::_run_install`),
    der bewusst eine EIGENE DB-Session über das modulglobale `SessionLocal`
    öffnet (die Request-Session ist zu diesem Zeitpunkt oft schon
    geschlossen) - dieses Testmodul leitete `SessionLocal` bisher NICHT
    auf die isolierte In-Memory-Test-DB um (anders als
    tests/test_laws_install_service.py, das genau dafür bereits eine
    eigene `_use_test_session_local`-Fixture hat). Solange `_run_install`
    im Fehlerfall nur den In-Memory-`_progress`-Status setzte, blieb das
    unbemerkt; seit der neuen Aktualisierungs-Nachvollziehbarkeit
    (`_record_failed_check` schreibt jetzt auch bei einem Fehlschlag auf
    die `Law`-Zeile) griff der Hintergrund-Thread dadurch auf die ECHTE,
    nicht auf die Test-Datenbank zu (real reproduziert:
    `sqlite3.OperationalError: no such column: laws.is_active` in einem
    Hintergrund-Thread, weil dort eine andere/ältere Schema-Version
    liegt). Behoben nach demselben, bereits etablierten Muster."""
    import app.laws.install_service as install_service

    monkeypatch.setattr(install_service, "SessionLocal", lambda: db_session)


def test_page_requires_login(client: TestClient) -> None:
    response = client.get("/dashboard/knowledge", follow_redirects=False)
    assert response.status_code in (302, 303, 307)


def test_page_is_no_longer_a_placeholder(
    client: TestClient, db_session: Session
) -> None:
    """Kernaussage dieses Blocks: der Navigationspunkt fuehrt nicht mehr auf
    eine "In Vorbereitung"-Seite."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")

    assert response.status_code == 200
    assert "In Vorbereitung" not in response.text
    assert "v0.2-Update" not in response.text


def test_page_lists_existing_knowledge_items(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(
        KnowledgeItem(
            title="Standard-Textbaustein: Einspruchseinlegung",
            content="Namens und im Auftrag unseres Mandanten legen wir Einspruch ein.",
            category="Textbaustein",
            practice_area="Einkommensteuer",
            approval_status="approved",
        )
    )
    db_session.commit()
    login_as_admin(db_session, client)

    # "laws" ist seit der "APP-SHELL KORREKTUR"-Runde (26.09.) die
    # Standardkategorie (vorher "Alle Inhalte", jetzt entfernt) -
    # Textbausteine leben unter "Fachwissen" (category=expertise).
    response = client.get("/dashboard/knowledge", params={"category": "expertise"})

    assert "Standard-Textbaustein: Einspruchseinlegung" in response.text
    assert "Einkommensteuer" in response.text


# --- Kanzleifachprofil-Relevanz fuer Fachwissen (03.10., Owner-Direktive
# "KANZLEIFACHPROFIL UND JURISTISCHE WISSENSSTEUERUNG" §4.3) ---


def test_knowledge_items_matching_firm_profile_are_sorted_first(
    client: TestClient, db_session: Session
) -> None:
    """Direktive §4.3: "Das Profil darf passende Gesetze und Normen
    priorisieren, aber nicht automatisch alle anderen Inhalte
    ausschließen." - hier fuer Fachwissen (`KnowledgeItem`), dem einzigen
    Modell mit einem echten `practice_area`-Feld."""
    from datetime import datetime, timedelta, timezone

    from app.firm_profile import set_practice_areas

    now = datetime.now(timezone.utc)
    older_match = KnowledgeItem(
        title="Älterer Baustein Erbrecht",
        content="Inhalt",
        practice_area="Erbrecht",
        approval_status="approved",
    )
    newer_non_match = KnowledgeItem(
        title="Neuerer Baustein Mietrecht",
        content="Inhalt",
        practice_area="Mietrecht",
        approval_status="approved",
    )
    db_session.add_all([older_match, newer_non_match])
    db_session.commit()
    # Reihenfolge nach Aktualitaet umkehren, damit der Test echt etwas
    # beweist (ohne Profil waere "Neuerer..." zuerst gelistet).
    older_match.updated_at = now - timedelta(days=5)
    newer_non_match.updated_at = now
    db_session.commit()

    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "expertise"})

    assert response.status_code == 200
    pos_match = response.text.index("Älterer Baustein Erbrecht")
    pos_non_match = response.text.index("Neuerer Baustein Mietrecht")
    assert pos_match < pos_non_match, "Passender Eintrag muss trotz geringerer Aktualitaet zuerst stehen"
    assert "Kanzleischwerpunkt" in response.text


def test_knowledge_items_are_not_excluded_when_they_do_not_match_firm_profile(
    client: TestClient, db_session: Session
) -> None:
    from app.firm_profile import set_practice_areas

    db_session.add(
        KnowledgeItem(
            title="Baustein Mietrecht",
            content="Inhalt",
            practice_area="Mietrecht",
            approval_status="approved",
        )
    )
    db_session.commit()
    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "expertise"})

    assert "Baustein Mietrecht" in response.text


def test_knowledge_items_sort_order_unchanged_without_a_firm_profile(
    client: TestClient, db_session: Session
) -> None:
    """Ohne gespeichertes Fachprofil bleibt die bisherige, rein
    aktualitaetsbasierte Sortierung unveraendert (Direktive §4.3/Phase
    E §12: "Suche und Chat verhalten sich ohne gespeichertes Fachprofil
    weiterhin korrekt")."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    older = KnowledgeItem(title="Älterer Eintrag", content="x", approval_status="approved")
    newer = KnowledgeItem(title="Neuerer Eintrag", content="x", approval_status="approved")
    db_session.add_all([older, newer])
    db_session.commit()
    older.updated_at = now - timedelta(days=5)
    newer.updated_at = now
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "expertise"})

    assert response.text.index("Neuerer Eintrag") < response.text.index("Älterer Eintrag")
    assert "Kanzleischwerpunkt" not in response.text


def test_page_shows_approval_status_of_knowledge_items(
    client: TestClient, db_session: Session
) -> None:
    """Ein noch nicht freigegebener Baustein darf nicht wie ein gepruefter
    wirken - der Freigabestatus ist fachlich entscheidend."""
    db_session.add(
        KnowledgeItem(
            title="Ungeprüfter Entwurf",
            content="Entwurfstext",
            category="Textbaustein",
            approval_status="draft",
        )
    )
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "expertise"})

    assert "Ungeprüfter Entwurf" in response.text
    assert "freigegeben" not in response.text.split("Ungeprüfter Entwurf")[1][:400]


def test_page_lists_existing_sources(client: TestClient, db_session: Session) -> None:
    """26.09., "APP-SHELL KORREKTUR": ein "Gesetz"-Quellentyp (manuell
    erfasste Zitierstelle, NICHT die automatisierte Gesetze-im-Internet-
    Bibliothek) hat keine eigene Kachel - "Interne Dokumente" zeigt
    bewusst ALLE Nicht-Rechtsprechung-Quellentypen, siehe
    knowledge_router.py fuer die volle Begruendung (sonst waere diese
    Quelle seit dem Entfernen von "Alle Inhalte" unerreichbar)."""
    db_session.add(
        Source(
            title="Einspruch gegen Steuerbescheide – Frist",
            source_type="Gesetz",
            reference="§ 355 AO",
            approval_level="freigegeben",
        )
    )
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "internal"})

    assert "§ 355 AO" in response.text
    assert "Gesetz" in response.text


def test_page_links_to_existing_law_library_instead_of_rebuilding_it(
    client: TestClient, db_session: Session
) -> None:
    """26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION":
    die Kategorie-Kachel fuehrt zur echten "Gesetze & Normen"-Kategorie
    (seit der "APP-SHELL KORREKTUR"-Folgerunde die Standardkategorie
    selbst) - dort verlinkt ein bereits installiertes Gesetz weiterhin auf
    die bestehende, unveraendert
    funktionierende Leseansicht /dashboard/laws/{code} (Reuse vor Rewrite,
    Direktive §7/§32: keine zweite Gesetzes-Leseoberflaeche)."""
    db_session.add(Law(code="BGB", title="Bürgerliches Gesetzbuch"))
    db_session.add(
        LawSection(
            law_code="BGB", section_number="§ 1", title="Beginn der Rechtsfähigkeit",
            text_content="Testinhalt.", last_updated=date(2024, 1, 1),
        )
    )
    db_session.commit()
    login_as_admin(db_session, client)

    overview = client.get("/dashboard/knowledge")
    assert "/dashboard/knowledge?category=laws" in overview.text

    laws_category = client.get("/dashboard/knowledge", params={"category": "laws"})
    assert "/dashboard/laws/BGB" in laws_category.text


def test_page_works_with_empty_knowledge_base(
    client: TestClient, db_session: Session
) -> None:
    """Leerer Zustand muss sinnvoll aussehen, nicht kaputt."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "expertise"})

    assert response.status_code == 200
    assert "Noch keine Textbausteine hinterlegt" in response.text


def test_search_filters_knowledge_items(client: TestClient, db_session: Session) -> None:
    db_session.add_all([
        KnowledgeItem(title="Einspruch Textbaustein", content="Einspruch einlegen",
                      category="Textbaustein", approval_status="approved"),
        KnowledgeItem(title="Fristverlängerung", content="Wir bitten um Verlängerung",
                      category="Textbaustein", approval_status="approved"),
    ])
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge?category=expertise&search=Einspruch")

    assert "Einspruch Textbaustein" in response.text
    assert "Fristverlängerung" not in response.text


def test_page_does_not_fake_unavailable_features(
    client: TestClient, db_session: Session
) -> None:
    """§6/§23 der urspruenglichen UI-Direktive: die Referenz zeigt einen
    Dokument-Upload-Button, fuer den es im Datenmodell nichts gibt - darf
    nicht als funktionslose Attrappe erscheinen."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")
    assert "Neues Dokument" not in response.text


def test_favorites_category_no_longer_exists(client: TestClient, db_session: Session) -> None:
    """26.09., Owner-Direktive "KANZLEIWISSEN REFERENCE-MATCH / PRODUCT-
    COMPLETION PASS" §4/§20 - KURSKORREKTUR gegenueber der vorigen Runde:
    dort war "Favoriten" bewusst als siebte Kachel mit einem ehrlichen
    "noch nicht verfügbar"-Hinweis sichtbar geblieben (kein Datenmodell
    dafuer vorhanden). Die neue Referenz zeigt aber nur noch SECHS
    Kacheln OHNE Favoriten ueberhaupt - die Kachel wird jetzt komplett
    entfernt statt nur ehrlich leer dargestellt (siehe DECISIONS.md fuer
    die volle Begruendung dieser Kurskorrektur). Ein Aufruf mit
    `?category=favorites` faellt transparent auf die Standardkategorie
    zurueck ("laws"/"Gesetze & Normen" seit der "APP-SHELL KORREKTUR"-
    Folgerunde, vorher "Alle Inhalte" - siehe dortiger Eintrag in
    DECISIONS.md), kein 404/Fehler (unbekannte/veraltete Kategorie-Links
    sollen nicht kaputt gehen)."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")
    assert "Favoriten" not in response.text

    fallback = client.get("/dashboard/knowledge", params={"category": "favorites"})
    assert fallback.status_code == 200
    assert "Favoriten" not in fallback.text
    assert "knowledge-category-card--active" in fallback.text
    assert "Kürzel" in fallback.text  # laws-Tabelle ist die Fallback-Ansicht


# --- Kategorie-Navigation (26.09., Owner-Direktive "KANZLEIWISSEN FINAL
# PRODUCT IMPLEMENTATION", Referenzabgleich 43_Kanzleiwissen_Gesetze.png) ---


def test_category_cards_show_real_counts_not_reference_numbers(
    client: TestClient, db_session: Session
) -> None:
    """Direktive §12: die Referenz zeigt z. B. "Gesetze & Normen 18" - das
    ist eine Demo-Zahl aus dem Screenshot, keine echte Produktdaten. Die
    Kachel muss stattdessen die tatsächliche Anzahl zeigen (hier: 0, da
    kein einziges Gesetz importiert ist)."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")

    assert 'class="knowledge-category-card' in response.text
    assert "Gesetze &amp; Normen" in response.text or "Gesetze & Normen" in response.text


def test_laws_category_is_marked_active_when_selected(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "laws"})

    assert "knowledge-category-card--active" in response.text
    # "Kürzel" ist eindeutig die Tabellen-Spaltenueberschrift (nicht die
    # Kategorie-Kachel) - unterscheidet "Panel zeigt die Tabelle" von
    # "Kachel-Label kommt zufällig im Response vor".
    assert "Kürzel" in response.text


def test_laws_category_panel_endpoint_returns_partial_only(
    client: TestClient, db_session: Session
) -> None:
    """HTMX-Kategoriewechsel darf keine volle Seite (Sidebar/Suchfeld)
    zurückliefern, nur den austauschbaren Panel-Inhalt."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge/panel", params={"category": "laws"})

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Kürzel" in response.text


def test_laws_category_shows_not_installed_catalog_entries(
    client: TestClient, db_session: Session
) -> None:
    """26.09., Direktive §12/§19, Sprache seit der zweiten Referenzrunde
    ("REFERENCE-MATCH / PRODUCT-COMPLETION PASS" §12) auf "Nicht
    verfügbar" umgestellt (der Toggle repraesentiert lokale
    Verfuegbarkeit, nicht nur einen Download): ein Katalogeintrag ohne
    lokale `Law`-Zeile muss ehrlich als "Nicht verfügbar" erscheinen -
    kein erfundener "Lokal verfügbar"-Status."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "laws"})

    assert "Bürgerliches Gesetzbuch" in response.text
    assert "Nicht verfügbar" in response.text
    assert "law-toggle" in response.text


def test_laws_category_shows_installed_law_as_installed_with_real_stats(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(Law(code="BGB", title="Bürgerliches Gesetzbuch", source_size_bytes=12_000))
    db_session.add(
        LawSection(
            law_code="BGB", section_number="§ 1", title="Beginn der Rechtsfähigkeit",
            text_content="Testinhalt.", last_updated=date(2024, 8, 1),
        )
    )
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "laws"})

    assert "Lokal verfügbar" in response.text
    assert "08/2024" in response.text
    assert "11.7 kB" in response.text or "12 kB" in response.text or "kB" in response.text


def test_laws_category_shows_deactivated_law_distinctly(client: TestClient, db_session: Session) -> None:
    db_session.add(Law(code="BGB", title="Bürgerliches Gesetzbuch", is_active=False))
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "laws"})

    assert "Deaktiviert" in response.text


def test_toggle_endpoint_starts_a_real_install_for_a_not_installed_law(
    client: TestClient, db_session: Session
) -> None:
    """26.09., Direktive §4/§14: Klick auf den Toggle einer nicht
    installierten Quelle muss einen ECHTEN Download starten (der Download
    selbst wird gemockt, siehe test_laws_install_service.py für die
    ungemockte Orchestrierung) und SOFORT den Zwischenzustand zeigen -
    niemals sofort "Installiert". Der Mock bleibt bewusst ueber `.start()`/
    `.stop()` (statt eines `with`-Blocks) aktiv, bis der Hintergrund-
    Thread wirklich fertig ist - sonst koennte der Thread nach Ablauf des
    `with`-Blocks die ECHTE, ungemockte Funktion aufrufen (Race
    Condition)."""
    import time

    from app.laws.install_service import is_install_running

    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "laws"})
    csrf = extract_csrf(page.text)

    def _slow_bad_fetch(slug: str, **kwargs):
        time.sleep(0.3)  # laesst dem Request genug Zeit, den "downloading"-Zwischenzustand zu rendern
        return b"\x00" * 4  # kein gueltiges ZIP - Installation endet danach bewusst in "error"

    patcher = patch("app.laws.install_service.fetch_law_xml_zip", side_effect=_slow_bad_fetch)
    patcher.start()
    try:
        response = client.post("/dashboard/knowledge/laws/BGB/toggle", data={"csrf_token": csrf})
        assert response.status_code == 200
        assert "Wird heruntergeladen" in response.text

        deadline = time.monotonic() + 5.0
        while is_install_running("BGB") and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        patcher.stop()


def test_toggle_endpoint_deactivates_an_installed_active_law(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(Law(code="BGB", title="Bürgerliches Gesetzbuch", is_active=True))
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "laws"})
    csrf = extract_csrf(page.text)

    response = client.post("/dashboard/knowledge/laws/BGB/toggle", data={"csrf_token": csrf})

    assert response.status_code == 200
    assert "Deaktiviert" in response.text
    db_session.expire_all()
    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.is_active is False


def test_toggle_endpoint_requires_csrf_token(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    response = client.post("/dashboard/knowledge/laws/BGB/toggle", data={"csrf_token": "falsch"})
    assert response.status_code in (400, 403)


def test_toggle_endpoint_unknown_catalog_code_returns_404(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "laws"})
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/knowledge/laws/DEFINITIV_KEIN_GESETZ/toggle", data={"csrf_token": csrf}
    )

    assert response.status_code == 404


# --- Automatisierte Aktualisierung (03.10., Owner-Direktive "RELIABLE
# LEGAL KNOWLEDGE UPDATES") - manuelle Pruef-/Uebernahme-Routen.


def test_check_update_endpoint_shows_unchanged_when_etag_matches(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(
        Law(code="BGB", title="Bürgerliches Gesetzbuch", is_active=True, source_etag='"same"')
    )
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "laws"})
    csrf = extract_csrf(page.text)

    with patch("app.laws.install_service.fetch_source_etag", return_value='"same"'):
        response = client.post("/dashboard/knowledge/laws/BGB/check", data={"csrf_token": csrf})

    assert response.status_code == 200
    assert "Aktualisierung verfügbar" not in response.text
    db_session.expire_all()
    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.last_check_status == "unchanged"


def test_check_update_endpoint_shows_update_available_when_etag_differs(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(
        Law(code="BGB", title="Bürgerliches Gesetzbuch", is_active=True, source_etag='"old"')
    )
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "laws"})
    csrf = extract_csrf(page.text)

    with patch("app.laws.install_service.fetch_source_etag", return_value='"new"'):
        response = client.post("/dashboard/knowledge/laws/BGB/check", data={"csrf_token": csrf})

    assert response.status_code == 200
    assert "Aktualisierung verfügbar" in response.text
    assert "Jetzt aktualisieren" in response.text


def test_check_update_endpoint_requires_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(Law(code="BGB", title="Bürgerliches Gesetzbuch", is_active=True))
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.post("/dashboard/knowledge/laws/BGB/check", data={"csrf_token": "falsch"})

    assert response.status_code in (400, 403)


def test_check_update_endpoint_is_available_to_any_logged_in_role(
    db_session: Session, mitarbeiter_client: TestClient
) -> None:
    """Dieselbe Berechtigungsstufe wie der bereits bestehende Gesetze-
    Toggle (`toggle_or_install_law`, siehe Moduldocstring: "rein
    TECHNISCHE Verfuegbarkeits-Umschaltung... kein Freigabe-Workflow
    noetig") - KEINE Admin/Anwalt-Einschraenkung wie bei den Source-/
    KnowledgeItem-Kuratierungsrouten; nur Login+CSRF sind Pflicht."""
    db_session.add(Law(code="BGB", title="Bürgerliches Gesetzbuch", is_active=True))
    db_session.commit()
    page = mitarbeiter_client.get("/dashboard/knowledge", params={"category": "laws"})
    csrf = extract_csrf(page.text)

    with patch("app.laws.install_service.fetch_source_etag", return_value='"x"'):
        response = mitarbeiter_client.post(
            "/dashboard/knowledge/laws/BGB/check", data={"csrf_token": csrf}
        )

    assert response.status_code == 200


def test_apply_update_endpoint_starts_a_real_background_update(
    client: TestClient, db_session: Session
) -> None:
    """Direktive Phase D: "Jetzt aktualisieren" startet denselben
    Hintergrund-Download wie eine Erstinstallation (Download selbst
    gemockt, ungemockte Orchestrierung ist bereits in
    test_laws_install_service.py abgedeckt)."""
    import time

    from app.laws.install_service import is_install_running

    db_session.add(
        Law(code="BGB", title="Bürgerliches Gesetzbuch", is_active=True, source_etag='"old"')
    )
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "laws"})
    csrf = extract_csrf(page.text)

    def _slow_bad_fetch(slug: str, **kwargs):
        time.sleep(0.3)
        return b"\x00" * 4

    patcher = patch("app.laws.install_service.fetch_law_xml_zip", side_effect=_slow_bad_fetch)
    patcher.start()
    try:
        response = client.post("/dashboard/knowledge/laws/BGB/update", data={"csrf_token": csrf})
        assert response.status_code == 200
        assert "Wird heruntergeladen" in response.text

        deadline = time.monotonic() + 5.0
        while is_install_running("BGB") and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        patcher.stop()


def test_row_endpoint_reflects_current_state_for_polling(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge/laws/BGB/row")

    assert response.status_code == 200
    assert "Nicht verfügbar" in response.text


def test_knowledge_page_has_no_back_link(client: TestClient, db_session: Session) -> None:
    """26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION",
    Referenzabgleich 43_Kanzleiwissen_Gesetze.png: Kanzleiwissen ist wie
    Chat/Posteingang eine Hauptnavigationsebene (jetzt direktes Sidebar-
    Ziel) - ein "Zurück"-Pfeil impliziert fälschlich einen sinnvollen
    Vorgänger-Kontext."""
    login_as_admin(db_session, client)
    response = client.get("/dashboard/knowledge")
    assert "header-back" not in response.text


def test_topbar_subtitle_reflects_active_category(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)

    laws_page = client.get("/dashboard/knowledge", params={"category": "laws"})
    meta_section = laws_page.text.split('class="topbar__meta"')[1][:200]
    assert "Gesetze &amp; Normen" in meta_section or "Gesetze & Normen" in meta_section

    # "laws" ist die Standardkategorie (26.09., "APP-SHELL KORREKTUR" -
    # "Alle Inhalte" wurde entfernt, siehe DECISIONS.md) - derselbe
    # Beschreibungstext wie bei explizitem `category=laws`.
    default_page = client.get("/dashboard/knowledge")
    assert "Wählen Sie aus, welche Rechtsquellen" in default_page.text


def test_category_panel_response_includes_oob_swap_for_active_card_and_subtitle(
    client: TestClient, db_session: Session
) -> None:
    """26.09., ECHTER FUND waehrend eigener Visual-QA (real im installierten
    Lexono.exe beobachtet): ein Klick auf eine Kategorie-Kachel tauschte
    per HTMX nur den Panel-Inhalt aus - die vorher aktive Kachel blieb
    optisch "aktiv", obwohl bereits eine andere Kategorie angezeigt wurde
    (Direktive §10: die aktive Kategorie "muss eindeutig aktiv sein").
    Behoben durch zwei Out-of-Band-Swaps (Kategorie-Kacheln + Untertitel),
    siehe partials/knowledge_categories.html."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge/panel", params={"category": "laws"})

    assert response.status_code == 200
    assert 'id="knowledge-categories" hx-swap-oob="true"' in response.text
    assert 'id="knowledge-subtitle" hx-swap-oob="true"' in response.text
    # Genau EINE Kachel ist aktiv - und zwar "Gesetze & Normen".
    assert response.text.count("knowledge-category-card--active") == 1
    active_pos = response.text.index("knowledge-category-card--active")
    next_title_pos = response.text.index("knowledge-category-card__title", active_pos)
    snippet = response.text[next_title_pos : next_title_pos + 120]
    assert "Gesetze" in snippet


def test_all_category_no_longer_exists(client: TestClient, db_session: Session) -> None:
    """26.09., Owner-Direktive "KANZLEIWISSEN FINAL POLISH + APP-SHELL
    KORREKTUR" §3: "Alle Inhalte" war zu einer eigenen, unnoetigen zweiten
    Dashboard-Ebene geworden (Gesetzesbibliothek-Zusammenfassung + eine
    "Textbausteine & Kanzleiwissen"-Tabelle, die 1:1 die "Fachwissen"-
    Tabelle duplizierte + eine gemischte "Rechtsquellen"-Liste) - komplett
    entfernt, genau FUENF Kacheln bleiben. `?category=all` (veralteter
    Link) faellt transparent auf die neue Standardkategorie "laws" zurueck,
    kein 404/Fehler. Die zugrunde liegenden Daten (KnowledgeItem/Source)
    bleiben unveraendert - nur diese eine Sammelansicht verschwindet, siehe
    `test_page_lists_existing_knowledge_items`/`test_page_lists_existing_
    sources` fuer den Beweis, dass die Daten unter ihrer jeweils passenden
    Kategorie weiterhin erreichbar sind."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")
    # "Alle Inhalte" als Substring kollidiert mit der rechten Info-Karte
    # ("Alle Inhalte lokal auf Ihrem System") - stattdessen gezielt die
    # Kategorie-Kachel selbst pruefen (eindeutiges Kachel-Label-Markup).
    assert 'knowledge-category-card__title">Alle Inhalte<' not in response.text
    assert "Textbausteine &amp; Kanzleiwissen" not in response.text
    assert "Textbausteine & Kanzleiwissen" not in response.text
    assert "Gesetzesbibliothek" not in response.text

    fallback = client.get("/dashboard/knowledge", params={"category": "all"})
    assert fallback.status_code == 200
    assert 'knowledge-category-card__title">Alle Inhalte<' not in fallback.text
    assert "Kürzel" in fallback.text  # laws-Tabelle ist die Fallback-Ansicht


# --- Manuelle Erfassung: Rechtsprechung/Interne Dokumente (Source) und
# Fachwissen (KnowledgeItem) - 26.09., Owner-Direktive "AUTONOMOUS PRODUCT
# GAP AUDIT". Vorher gab es PROJEKTWEIT keinen Web-Aufrufer fuer
# `SourceService.import_source`/`KnowledgeItemService.import_item` -
# beide bereits vollstaendig gebaute Services, siehe knowledge_router.py-
# Moduldocstring fuer die volle Begruendung dieser Kurskorrektur.


def test_case_law_category_shows_create_form_for_admin(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge", params={"category": "case_law"})

    assert "Neue Gerichtsentscheidung erfassen" in response.text


def test_case_law_category_hides_create_form_for_mitarbeiter(
    mitarbeiter_client: TestClient,
) -> None:
    response = mitarbeiter_client.get("/dashboard/knowledge", params={"category": "case_law"})

    assert "Neue Gerichtsentscheidung erfassen" not in response.text


def test_create_source_route_creates_a_real_case_law_entry(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "case_law"})
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/knowledge/sources",
        data={
            "csrf_token": csrf,
            "title": "BFH, Urt. v. 12.03.2026 - VI R 5/25",
            "source_type": "Rechtsprechung",
            "reference": "VI R 5/25",
            "document_date": "2026-03-12",
        },
    )

    assert response.status_code == 200
    assert "category=case_law" in str(response.url)
    source = db_session.query(Source).filter_by(title="BFH, Urt. v. 12.03.2026 - VI R 5/25").first()
    assert source is not None
    assert source.source_type == "Rechtsprechung"
    assert source.approval_level == "entwurf"
    assert "entwurf" in response.text


def test_create_source_route_requires_admin_or_anwalt(mitarbeiter_client: TestClient) -> None:
    # "case_law" zeigt fuer Mitarbeiter (bewusst) kein Formular und damit
    # kein echtes CSRF-Feld - Token stattdessen von der Standardansicht
    # ("laws", fuer alle drei Rollen les-/nutzbar) holen.
    page = mitarbeiter_client.get("/dashboard/knowledge")
    csrf = extract_csrf(page.text)

    response = mitarbeiter_client.post(
        "/dashboard/knowledge/sources",
        data={
            "csrf_token": csrf,
            "title": "Sollte nicht angelegt werden",
            "source_type": "Rechtsprechung",
        },
    )

    assert response.status_code == 403


def test_approve_source_route_changes_status_to_freigegeben(
    client: TestClient, db_session: Session
) -> None:
    source = Source(title="Test-Urteil", source_type="Rechtsprechung")
    db_session.add(source)
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "case_law"})
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/knowledge/sources/{source.id}/approve", data={"csrf_token": csrf}
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.query(Source).filter_by(id=source.id).first().approval_level == "freigegeben"
    assert "freigegeben" in response.text


def test_mark_source_outdated_requires_a_real_reason(
    client: TestClient, db_session: Session
) -> None:
    source = Source(title="Ueberholtes Urteil", source_type="Rechtsprechung", approval_level="freigegeben")
    db_session.add(source)
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "case_law"})
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/knowledge/sources/{source.id}/mark-outdated",
        data={"csrf_token": csrf, "reason": "   "},
    )

    assert response.status_code == 200
    db_session.expire_all()
    # Leere Begruendung wird von SourceService.mark_outdated abgelehnt -
    # der Status bleibt unveraendert "freigegeben", kein stiller Erfolg.
    assert db_session.query(Source).filter_by(id=source.id).first().approval_level == "freigegeben"


def test_internal_category_shows_source_type_dropdown_and_creates_entry(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "internal"})
    assert "Neue Quelle erfassen" in page.text
    assert "Gesetz" in page.text  # source_types-Dropdown enthaelt die anderen Typen
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/knowledge/sources",
        data={
            "csrf_token": csrf,
            "title": "BMF-Schreiben zur Umsatzsteuer",
            "source_type": "Verwaltungsanweisung",
        },
    )

    assert response.status_code == 200
    source = db_session.query(Source).filter_by(title="BMF-Schreiben zur Umsatzsteuer").first()
    assert source is not None
    assert source.source_type == "Verwaltungsanweisung"
    assert "Verwaltungsanweisung" in response.text


def test_expertise_category_shows_create_form_and_creates_entry(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "expertise"})
    assert "Neuen Textbaustein erfassen" in page.text
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/knowledge/items",
        data={
            "csrf_token": csrf,
            "title": "Standardformulierung Fristverlängerung",
            "content": "Wir bitten um Verlängerung der gesetzten Frist um zwei Wochen.",
            "category": "Textbaustein",
            "practice_area": "Steuerrecht",
        },
    )

    assert response.status_code == 200
    item = (
        db_session.query(KnowledgeItem)
        .filter_by(title="Standardformulierung Fristverlängerung")
        .first()
    )
    assert item is not None
    assert item.approval_status == "pending"
    assert item.version == 1
    assert "pending" in response.text


def test_create_knowledge_item_route_requires_admin_or_anwalt(
    mitarbeiter_client: TestClient,
) -> None:
    page = mitarbeiter_client.get("/dashboard/knowledge")
    csrf = extract_csrf(page.text)

    response = mitarbeiter_client.post(
        "/dashboard/knowledge/items",
        data={
            "csrf_token": csrf,
            "title": "Sollte nicht angelegt werden",
            "content": "Inhalt",
        },
    )

    assert response.status_code == 403


def test_approve_knowledge_item_route_changes_status(
    client: TestClient, db_session: Session
) -> None:
    item = KnowledgeItem(title="Test-Baustein", content="Inhalt", approval_status="pending")
    db_session.add(item)
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "expertise"})
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/knowledge/items/{item.id}/approve", data={"csrf_token": csrf}
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.query(KnowledgeItem).filter_by(id=item.id).first().approval_status == "approved"


def test_deactivate_knowledge_item_requires_a_real_reason(
    client: TestClient, db_session: Session
) -> None:
    item = KnowledgeItem(title="Veralteter Baustein", content="Inhalt", approval_status="approved")
    db_session.add(item)
    db_session.commit()
    login_as_admin(db_session, client)
    page = client.get("/dashboard/knowledge", params={"category": "expertise"})
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/knowledge/items/{item.id}/deactivate",
        # Eine wortwoertlich leere Form-Feld-Zeichenkette wird von
        # httpx/TestClient beim Multipart-Encoding als FEHLENDES Feld
        # behandelt (eigener Fund, siehe DECISIONS.md) - eine Zeichenkette
        # aus nur Leerzeichen kommt dagegen echt an und prueft den
        # eigentlich beabsichtigten Fall (KnowledgeItemService.deactivate
        # lehnt eine Begruendung ab, die nach `.strip()` leer ist).
        data={"csrf_token": csrf, "reason": "   "},
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.query(KnowledgeItem).filter_by(id=item.id).first().approval_status == "approved"


def test_new_source_and_item_appear_in_real_category_counts(
    client: TestClient, db_session: Session
) -> None:
    """Ende-zu-Ende-Beweis auf Router-Ebene: eine neu erfasste Quelle/ein
    neuer Textbaustein aktualisiert die echten Kachel-Zaehler sofort -
    kein Cache/keine manuelle Neuberechnung noetig."""
    login_as_admin(db_session, client)
    before = client.get("/dashboard/knowledge")
    csrf = extract_csrf(before.text)

    client.post(
        "/dashboard/knowledge/sources",
        data={"csrf_token": csrf, "title": "Neues Urteil", "source_type": "Rechtsprechung"},
    )
    client.post(
        "/dashboard/knowledge/items",
        data={"csrf_token": csrf, "title": "Neuer Baustein", "content": "Inhalt"},
    )

    assert db_session.query(Source).filter_by(title="Neues Urteil").count() == 1
    assert db_session.query(KnowledgeItem).filter_by(title="Neuer Baustein").count() == 1

    after = client.get("/dashboard/knowledge", params={"category": "case_law"})
    case_law_card = after.text.split('class="knowledge-category-card__title">Rechtsprechung<')[1]
    count_span = case_law_card.split('knowledge-category-card__count">')[1]
    assert count_span.split("<")[0].strip() == "1"
