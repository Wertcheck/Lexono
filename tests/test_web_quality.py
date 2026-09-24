"""Tests für app/web/quality_router.py (Draft Quality Ratings, Prompt 43;
UI-Anbindung 18.09., Owner-Direktive "WEITERARBEITEN" Fortsetzung).

ECHTER FUND: der Router existierte bereits vollständig, gesichert und vom
14.09. an schon einmal gehärtet (Prompt 46) - blieb aber projektweit ohne
jede Verlinkung, siehe app/web/quality_router.py-Moduldocstring.

Gleiches Testmuster wie tests/test_web_parties.py: In-Memory-SQLite über
app.dependency_overrides."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import Client, Draft, DraftQualityRating, Matter
from app.models.base import Base
from tests.auth_test_utils import extract_csrf, login_as_admin


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


def _draft(db: Session, *, status: str = "approved") -> Draft:
    client_row = Client(name="Testmandant GmbH", client_number=f"K-{uuid4().hex[:8]}")
    matter = Matter(client=client_row, title="Einspruch Steuerbescheid 2025")
    db.add_all([client_row, matter])
    db.commit()
    draft = Draft(matter_id=matter.id, content="Entwurfsinhalt.", status=status)
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def _csrf(client: TestClient, draft_id: str) -> str:
    page = client.get(f"/dashboard/drafts/{draft_id}")
    return extract_csrf(page.text)


def test_draft_detail_shows_rating_form_only_for_approved_drafts(
    client: TestClient, db_session: Session
) -> None:
    approved = _draft(db_session, status="approved")
    not_approved = _draft(db_session, status="draft")

    approved_page = client.get(f"/dashboard/drafts/{approved.id}")
    draft_page = client.get(f"/dashboard/drafts/{not_approved.id}")

    assert "Qualitätsbewertung" in approved_page.text
    assert 'name="content_quality"' in approved_page.text
    assert "Qualitätsbewertung" not in draft_page.text


def test_submit_rating_persists_it_and_redirects(
    client: TestClient, db_session: Session
) -> None:
    draft = _draft(db_session, status="approved")
    csrf = _csrf(client, draft.id)

    response = client.post(
        f"/dashboard/drafts/{draft.id}/ratings",
        data={
            "csrf_token": csrf,
            "content_quality": "5",
            "usefulness": "4",
            "completeness": "",
            "language_quality": "",
            "comment": "Sehr guter Entwurf.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/drafts/{draft.id}"
    rating = db_session.query(DraftQualityRating).filter_by(draft_id=draft.id).first()
    assert rating is not None
    assert rating.content_quality == 5
    assert rating.usefulness == 4
    assert rating.completeness is None
    assert rating.comment == "Sehr guter Entwurf."


def test_submitted_rating_appears_on_the_page(
    client: TestClient, db_session: Session
) -> None:
    draft = _draft(db_session, status="approved")
    csrf = _csrf(client, draft.id)
    client.post(
        f"/dashboard/drafts/{draft.id}/ratings",
        data={
            "csrf_token": csrf,
            "content_quality": "5",
            "usefulness": "",
            "completeness": "",
            "language_quality": "",
            "comment": "Sehr guter Entwurf.",
        },
    )

    response = client.get(f"/dashboard/drafts/{draft.id}")

    assert "1 Bewertung" in response.text
    assert "Sehr guter Entwurf." in response.text
    assert "5.0" in response.text or "5,0" in response.text


def test_submit_rating_without_any_content_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    draft = _draft(db_session, status="approved")
    csrf = _csrf(client, draft.id)

    response = client.post(
        f"/dashboard/drafts/{draft.id}/ratings",
        data={
            "csrf_token": csrf,
            "content_quality": "",
            "usefulness": "",
            "completeness": "",
            "language_quality": "",
            "comment": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(DraftQualityRating).filter_by(draft_id=draft.id).count() == 0


def test_submit_rating_for_non_approved_draft_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    draft = _draft(db_session, status="draft")
    csrf = _csrf(client, draft.id)

    response = client.post(
        f"/dashboard/drafts/{draft.id}/ratings",
        data={"csrf_token": csrf, "content_quality": "5"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(DraftQualityRating).filter_by(draft_id=draft.id).count() == 0


def test_submit_rating_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    draft = _draft(db_session, status="approved")

    response = client.post(
        f"/dashboard/drafts/{draft.id}/ratings",
        data={"csrf_token": "invalid", "content_quality": "5"},
    )

    assert response.status_code == 403
    assert db_session.query(DraftQualityRating).filter_by(draft_id=draft.id).count() == 0


def test_get_ratings_route_still_returns_json(
    client: TestClient, db_session: Session
) -> None:
    """Die lesenden GET-Endpunkte bleiben unveraendert JSON (dienen
    kuenftigen/externen Lesezugriffen) - nur der POST-Endpunkt wurde auf
    Formular+Redirect umgestellt, siehe Moduldocstring."""
    draft = _draft(db_session, status="approved")
    csrf = _csrf(client, draft.id)
    client.post(
        f"/dashboard/drafts/{draft.id}/ratings",
        data={"csrf_token": csrf, "content_quality": "3"},
    )

    response = client.get(f"/dashboard/drafts/{draft.id}/ratings")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["content_quality"] == 3
