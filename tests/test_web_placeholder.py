"""Web-Layer-Tests für die Platzhalterseiten (Prompt 48, erweitert Prompt 49).

Deckt ab: alle Platzhalter-Routen liefern 200 mit dem erwarteten Titel/
Beschreibungstext (nicht als 404 oder stiller Redirect), erfordern eine
Anmeldung wie jede andere Dashboard-Seite, und "/dashboard/settings" (Prompt
48: generischer Platzhalter, Prompt 49: Redirect auf den Profil-Bereich) ist
seit 20.08. eine echte Seite (app/web/settings_router.py, siehe
tests/test_web_settings.py), kein Platzhalter/Redirect mehr.
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


_PLACEHOLDER_CASES = [
    ("/dashboard/recent", "Letzte Akten"),
    # "/dashboard/matters" ("Akten") ist seit der UI/UX-Ueberarbeitung
    # (13.09.) KEIN Platzhalter mehr - siehe tests/test_web_matters.py
    # (echte, rein lesende Akten-Uebersicht + Detailansicht).
    ("/dashboard/documents", "Dokumenten-Viewer"),
    ("/dashboard/archive", "Archiv"),
    # "/dashboard/tools/schriftsatz" ("Schriftsatz-Generator") ist seit
    # 20.08. KEIN Platzhalter mehr - siehe tests/test_web_schriftsatz.py.
    ("/dashboard/tools/fristen", "Fristen-Check"),
    ("/dashboard/tools/zeitleiste", "Zeitleiste"),
    ("/dashboard/tools/beleg-extraktion", "Beleg-Extraktion"),
    ("/dashboard/sources", "Rechtsquellen"),
    # "/dashboard/library/prompts" ("Standard-Prompts") ist seit Schritt 3
    # KEIN Platzhalter mehr - siehe tests/test_web_prompt_library.py.
    # "/dashboard/knowledge" ist seit 14.09. KEIN Platzhalter mehr -
    # echte Uebersicht, siehe app/web/knowledge_router.py und
    # tests/test_web_knowledge.py.
    ("/dashboard/history/analysen", "Gespeicherte Analysen"),
    # "/dashboard/account/profile" ("Kanzlei-Profil & Briefkopf") ist seit
    # 20.08. KEIN Platzhalter mehr - siehe tests/test_web_settings.py
    # (echte Seite jetzt unter /dashboard/settings/profile).
    ("/dashboard/account/license", "System & Lizenz"),
]


@pytest.mark.parametrize(("path", "expected_title"), _PLACEHOLDER_CASES)
def test_placeholder_page_returns_200_with_title(
    client: TestClient, path: str, expected_title: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    # placeholder.html rendert den Titel ueber {{ placeholder_title }} - Jinja
    # escaped "&" dabei automatisch zu "&amp;" (anders als die Sidebar-Labels
    # in base.html, die als literaler Template-Text nicht escaped werden).
    assert expected_title.replace("&", "&amp;") in response.text
    assert "In Vorbereitung" in response.text


@pytest.mark.parametrize(("path", "_expected_title"), _PLACEHOLDER_CASES)
def test_placeholder_page_links_back_to_inbox(
    client: TestClient, path: str, _expected_title: str
) -> None:
    response = client.get(path)
    assert 'href="/dashboard/inbox"' in response.text


@pytest.mark.parametrize(("path", "_expected_title"), _PLACEHOLDER_CASES)
def test_unauthenticated_cannot_view_placeholder_page(
    anonymous_client: TestClient, path: str, _expected_title: str
) -> None:
    response = anonymous_client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_settings_route_is_a_real_page_not_a_redirect(client: TestClient) -> None:
    """"/dashboard/settings" war in Prompt 48/49 ein Redirect auf den
    Profil-/Einstellungen-Bereich - seit 20.08. eine echte, bedienbare Seite
    (app/web/settings_router.py), siehe tests/test_web_settings.py für die
    volle Abdeckung. Hier nur der Regressionsschutz, dass es kein Redirect
    mehr ist."""
    response = client.get("/dashboard/settings", follow_redirects=False)
    assert response.status_code == 200
    assert "Einstellungen" in response.text
