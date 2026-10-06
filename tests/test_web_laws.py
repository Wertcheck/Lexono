"""Tests für app/web/laws_router.py (digitale Gesetzesbibliothek, 20.08.),
unter /dashboard/laws."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.laws.service import import_law_fixture_data
from app.main import app
from app.models import Law, LawSection
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
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def anonymous_client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_test_law(db: Session) -> tuple[Law, LawSection]:
    import_law_fixture_data(
        db,
        {
            "code": "TESTG",
            "title": "Testgesetz",
            "sections": [
                {
                    "section_number": "§ 1",
                    "title": "Testparagraph",
                    "text_content": "Dies ist ein Testinhalt für die Leseansicht.",
                    "last_updated": "2026-08-20",
                }
            ],
        },
    )
    law = db.query(Law).filter_by(code="TESTG").one()
    section = db.query(LawSection).filter_by(law_code="TESTG").one()
    return law, section


# --- Zugriff ---


def test_laws_requires_login(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/dashboard/laws", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


# --- Lazy Bootstrap ("Bibliothek startet nicht leer") ---


def test_library_auto_seeds_shipped_fixtures_on_first_visit(
    client: TestClient, db_session: Session
) -> None:
    """Kernanforderung: "damit die Bibliothek nicht leer startet" - ein
    Aufruf auf einer komplett leeren DB muss automatisch BGB/StGB
    importieren, ohne dass vorher das Setup-Skript gelaufen sein muss."""
    assert db_session.query(Law).count() == 0

    response = client.get("/dashboard/laws")

    assert response.status_code == 200
    assert db_session.query(Law).filter_by(code="BGB").count() == 1
    assert db_session.query(Law).filter_by(code="StGB").count() == 1
    assert "BGB" in response.text
    assert "StGB" in response.text


def test_library_bootstrap_does_not_duplicate_on_repeated_visits(
    client: TestClient, db_session: Session
) -> None:
    client.get("/dashboard/laws")
    client.get("/dashboard/laws")
    assert db_session.query(Law).count() == 2


# --- Rendering: Übersicht/Gesetz/Paragraph ---


def test_overview_page_is_not_a_placeholder(client: TestClient) -> None:
    response = client.get("/dashboard/laws")
    assert response.status_code == 200
    assert "in Vorbereitung" not in response.text
    assert "Gesetzesbibliothek" in response.text


def test_overview_page_shows_currency_disclaimer(client: TestClient) -> None:
    """Ehrlichkeitsgebot (siehe app/models/law.py): die Seite darf NICHT den
    Eindruck einer automatisch aktuellen Gesetzessammlung erwecken.

    AKTUALISIERT 14.09.: der frühere Hinweistext sprach von "kuratierter
    Auswahl als lokale Fixture-Daten" - das war inzwischen schlicht FALSCH
    (real geprüft: alle 11.137 Normen tragen `source_name = 'Gesetze im
    Internet'`, also amtliche Texte mit Deep-Link). Die eigentliche
    Garantie - Aktualitätsvorbehalt + Verweis auf die maßgebliche amtliche
    Fassung - wird hier unverändert geprüft, nur nicht mehr über einen
    inhaltlich unrichtigen Satz."""
    response = client.get("/dashboard/laws")
    assert "§ 5 UrhG" in response.text
    # Aktualitätsvorbehalt muss weiterhin vorhanden sein.
    assert "nicht automatisch" in response.text
    assert "Maßgeblich" in response.text


def test_law_selected_page_lists_its_sections(client: TestClient, db_session: Session) -> None:
    law, section = _seed_test_law(db_session)
    response = client.get(f"/dashboard/laws/{law.code}")
    assert response.status_code == 200
    assert "Testparagraph" in response.text
    assert f'href="/dashboard/laws/{law.code}/{section.id}"' in response.text


def test_unknown_law_code_returns_404(client: TestClient) -> None:
    response = client.get("/dashboard/laws/UNBEKANNT")
    assert response.status_code == 404


def test_section_selected_page_shows_full_reading_pane(
    client: TestClient, db_session: Session
) -> None:
    law, section = _seed_test_law(db_session)
    response = client.get(f"/dashboard/laws/{law.code}/{section.id}")
    assert response.status_code == 200
    assert "Dies ist ein Testinhalt für die Leseansicht." in response.text
    assert "Testparagraph" in response.text
    assert "20.08.2026" in response.text  # last_updated, formatiert


def test_unknown_section_id_returns_404(client: TestClient, db_session: Session) -> None:
    law, _section = _seed_test_law(db_session)
    response = client.get(f"/dashboard/laws/{law.code}/does-not-exist")
    assert response.status_code == 404


# --- Schnellsuche (HTMX-Partial) ---


def test_sections_partial_filters_by_search_term(client: TestClient, db_session: Session) -> None:
    law, section = _seed_test_law(db_session)
    response = client.get(f"/dashboard/laws/{law.code}/sections-partial", params={"q": "Test"})
    assert response.status_code == 200
    assert "Testparagraph" in response.text

    response_no_match = client.get(
        f"/dashboard/laws/{law.code}/sections-partial", params={"q": "nichtvorhanden"}
    )
    assert "Keine Paragraphen" in response_no_match.text


# --- Sidebar-Integration ---


def test_sidebar_links_to_knowledge_overview(client: TestClient) -> None:
    """26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION"
    (Referenzabgleich `43_Kanzleiwissen_Gesetze.png`): der Hauptmenuepunkt
    "Kanzleiwissen" verlinkt jetzt auf die Kategorie-Uebersicht
    (/dashboard/knowledge, siehe app/web/knowledge_router.py) statt direkt
    auf die reine Gesetzes-Leseansicht (/dashboard/laws) - Letztere bleibt
    ueber die dortige "Gesetze & Normen"-Kategorie bzw. per Klick auf ein
    installiertes Gesetz weiterhin voll erreichbar (siehe test_web_laws.py
    fuer diese Route selbst, unveraendert)."""
    response = client.get("/dashboard/inbox")
    assert 'href="/dashboard/knowledge"' in response.text
    assert "Kanzleiwissen" in response.text


# --- Echte Kennzahlen je Gesetz (14.09., Abgleich mit der Referenzansicht
# "Gesetze & Normen": dort stehen Version/Stand und Umfang je Gesetzbuch) ---


def test_overview_shows_real_norm_count_and_stand_per_law(
    client: TestClient, db_session: Session
) -> None:
    """Umfang und Stand werden aus dem tatsaechlichen Bestand abgeleitet
    (`count(LawSection)` bzw. `max(last_updated)`) - keine erfundenen
    Angaben. Eine Dateigroesse in MB, wie die Referenz sie zeigt, existiert
    im Datenmodell NICHT und wird deshalb bewusst nicht dargestellt."""
    _seed_test_law(db_session)

    response = client.get("/dashboard/laws")

    assert "1 Normen" in response.text
    assert "20.08.2026" in response.text


def test_reading_pane_links_to_official_source(
    client: TestClient, db_session: Session
) -> None:
    """Jede Norm traegt eine amtliche Fundstelle - der Link dorthin ist in
    einem Rechtsprodukt die wichtigste Vertrauensinformation ueberhaupt."""
    from app.models import LawSection

    law, section = _seed_test_law(db_session)
    section.source_url = "https://www.gesetze-im-internet.de/testg/__1.html"
    db_session.commit()

    response = client.get(f"/dashboard/laws/{law.code}/{section.id}")

    assert "amtliche Quelle" in response.text
    assert "gesetze-im-internet.de/testg/__1.html" in response.text
