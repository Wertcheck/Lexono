"""Tests für app/web/knowledge_router.py (14.09., UI-Build).

Schliesst einen echten UI-Gap: `/dashboard/knowledge` war ein reiner
PLATZHALTER ("In Vorbereitung für das v0.2-Update"), obwohl die Inhalte und
der `KnowledgeItemService` laengst existierten - ein Hauptnavigationspunkt,
der vorhandenen Wert verbirgt und das Produkt unfertig wirken laesst.
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
from app.models import KnowledgeItem, Source
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
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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

    response = client.get("/dashboard/knowledge")

    assert "Standard-Textbaustein: Einspruchseinlegung" in response.text
    assert "Einkommensteuer" in response.text


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

    response = client.get("/dashboard/knowledge")

    assert "Ungeprüfter Entwurf" in response.text
    assert "freigegeben" not in response.text.split("Ungeprüfter Entwurf")[1][:400]


def test_page_lists_existing_sources(client: TestClient, db_session: Session) -> None:
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

    response = client.get("/dashboard/knowledge")

    assert "§ 355 AO" in response.text


def test_page_links_to_existing_law_library_instead_of_rebuilding_it(
    client: TestClient, db_session: Session
) -> None:
    """Reuse vor Rewrite: die Gesetzesbibliothek hat mit /dashboard/laws
    bereits eine eigene, funktionierende Oberflaeche - sie wird verlinkt,
    nicht nachgebaut."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")

    assert "/dashboard/laws" in response.text


def test_page_works_with_empty_knowledge_base(
    client: TestClient, db_session: Session
) -> None:
    """Leerer Zustand muss sinnvoll aussehen, nicht kaputt."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")

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

    response = client.get("/dashboard/knowledge?search=Einspruch")

    assert "Einspruch Textbaustein" in response.text
    assert "Fristverlängerung" not in response.text


def test_page_does_not_fake_unavailable_features(
    client: TestClient, db_session: Session
) -> None:
    """§6/§23 der UI-Direktive: die Referenz zeigt Dokument-Upload,
    Favoriten und Dateitypen (DOCX/PDF/XLSX). Dafuer existiert im
    Datenmodell NICHTS (`KnowledgeItem` ist ein Textbaustein ohne Datei,
    ohne Typ, ohne Favoritenkennzeichen). Solche Bedienelemente duerfen
    deshalb NICHT als funktionslose Attrappe erscheinen."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/knowledge")

    assert "Neues Dokument" not in response.text
    assert "Favoriten" not in response.text
