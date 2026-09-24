"""Tests für app/web/global_search_router.py (Universal Command Bar,
20.08.).

Gleiches Testmuster wie tests/test_web_schriftsatz.py: In-Memory-SQLite
über app.dependency_overrides, `get_global_search_service` wird in
app.web.global_search_router direkt gemonkeypatcht (kein echtes
fastembed-Modell in Tests, siehe tests/fake_embedding_provider.py)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.global_search_router as global_search_router_module
from app.db.session import get_db
from app.laws.service import import_law_fixture_data
from app.main import app
from app.models import Client, LawSection, Matter, Source
from app.models.base import Base
from app.search.global_search_service import GlobalSearchService
from app.search.service import DocumentSearchService
from tests.auth_test_utils import login_as_admin
from tests.fake_embedding_provider import FakeEmbeddingProvider


def _fake_global_search_service() -> GlobalSearchService:
    return GlobalSearchService(DocumentSearchService(FakeEmbeddingProvider()))


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
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        global_search_router_module, "get_global_search_service", _fake_global_search_service
    )
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def anonymous_client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        global_search_router_module, "get_global_search_service", _fake_global_search_service
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --- Zugriff ---


def test_search_requires_login(anonymous_client: TestClient) -> None:
    response = anonymous_client.get(
        "/dashboard/search/results", params={"q": "test"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


# --- Ergebnisse ---


def test_search_below_min_length_shows_hint(client: TestClient) -> None:
    response = client.get("/dashboard/search/results", params={"q": "a"})
    assert response.status_code == 200
    assert "Mindestens 2 Zeichen" in response.text


def test_search_empty_query_shows_hint(client: TestClient) -> None:
    response = client.get("/dashboard/search/results")
    assert response.status_code == 200
    assert "Mindestens 2 Zeichen" in response.text


def test_search_no_matches_shows_empty_state(client: TestClient) -> None:
    response = client.get("/dashboard/search/results", params={"q": "gibtesnicht"})
    assert response.status_code == 200
    assert "Keine Treffer" in response.text


def test_search_finds_client_with_local_badge(client: TestClient, db_session: Session) -> None:
    row = Client(name="Suchbarer Mandant", client_number="S-1", status="active")
    db_session.add(row)
    db_session.commit()

    response = client.get("/dashboard/search/results", params={"q": "Suchbarer"})
    assert response.status_code == 200
    assert "Suchbarer Mandant" in response.text
    assert f'href="/dashboard/clients/{row.id}"' in response.text
    assert "tag--matched" in response.text
    assert "Lokal" in response.text


def test_search_finds_source_with_extern_badge(client: TestClient, db_session: Session) -> None:
    source = Source(
        title="Handelsgesetzbuch § 377",
        source_type="Gesetz",
        approval_level="freigegeben",
    )
    db_session.add(source)
    db_session.commit()
    service = _fake_global_search_service()
    service._document_search_service.index_source(source, db_session)

    response = client.get("/dashboard/search/results", params={"q": "Handelsgesetzbuch"})
    assert response.status_code == 200
    assert "Handelsgesetzbuch" in response.text
    assert "tag--extern" in response.text
    assert "Extern" in response.text
    # ECHTER FUND (17.09.): verlinkt seit heute auf die echte, befuellte
    # Rechtsquellen-Tabelle unter Kanzleiwissen statt des toten
    # "/dashboard/sources"-Platzhalters (siehe global_search_service.py).
    assert 'href="/dashboard/knowledge"' in response.text


def test_search_finds_law_section_with_extern_gesetz_badge(
    client: TestClient, db_session: Session
) -> None:
    import_law_fixture_data(
        db_session,
        {
            "code": "BGB",
            "title": "Bürgerliches Gesetzbuch",
            "sections": [
                {
                    "section_number": "§ 985",
                    "title": "Herausgabeanspruch",
                    "text_content": "Der Eigentümer kann von dem Besitzer die Herausgabe der Sache verlangen.",
                    "last_updated": "2026-08-20",
                }
            ],
        },
    )
    section = db_session.query(LawSection).one()

    response = client.get("/dashboard/search/results", params={"q": "Herausgabeanspruch"})
    assert response.status_code == 200
    assert "Herausgabeanspruch" in response.text
    assert "Extern/Gesetz" in response.text
    assert f'href="/dashboard/laws/BGB/{section.id}"' in response.text


def test_search_result_badges_carry_explanatory_title_attribute(
    client: TestClient, db_session: Session
) -> None:
    """Ehrlichkeitsgebot (siehe app/search/global_search_service.py): der
    "Extern"-Badge muss klarstellen, dass technisch trotzdem keine
    Cloud-Anfrage stattfindet - nicht nur ein blankes Label."""
    row = Client(name="Badge-Test GmbH", client_number="B-1", status="active")
    db_session.add(row)
    db_session.commit()

    response = client.get("/dashboard/search/results", params={"q": "Badge-Test"})
    assert "ausschließlich lokal" in response.text


# --- Sidebar-/Basis-Layout-Integration ---


def test_command_bar_modal_and_sidebar_trigger_present_on_every_page(
    client: TestClient,
) -> None:
    """Sichtbarkeit (Aufgabenstellung: "Verlinke das Suchfeld direkt in der
    Haupt-Sidebar") - Modal + Trigger muessen im Basis-Layout stecken,
    nicht nur auf einer einzelnen Seite."""
    response = client.get("/dashboard/inbox")
    assert response.status_code == 200
    assert 'id="command-bar-modal"' in response.text
    assert 'data-command-bar-open' in response.text
    assert 'hx-get="/dashboard/search/results"' in response.text
