"""Tests für app/web/deadline_actions_router.py (18.09., Owner-Direktive
"WEITERARBEITEN" Fortsetzung).

Gleiches Testmuster wie tests/test_web_parties.py: In-Memory-SQLite über
app.dependency_overrides."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import AuditEvent, Client, Deadline, Matter, User
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


def _matter(db: Session, *, title: str = "Testakte") -> Matter:
    client_row = Client(name="Max Mustermann", client_number=f"K-{uuid4().hex[:8]}")
    matter = Matter(client=client_row, title=title)
    db.add_all([client_row, matter])
    db.commit()
    db.refresh(matter)
    return matter


def _csrf(client: TestClient, matter_id: str) -> str:
    page = client.get(f"/dashboard/matters/{matter_id}")
    return extract_csrf(page.text)


def test_matter_detail_page_shows_add_deadline_form(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert response.status_code == 200
    assert "Frist hinzufügen" in response.text
    assert 'name="source_text"' in response.text
    assert 'name="due_date"' in response.text


def test_create_deadline_persists_it_as_confirmed(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/deadlines",
        data={
            "csrf_token": csrf,
            "source_text": "Gerichtstermin laut Anruf",
            "due_date": "2026-12-01",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/matters/{matter.id}"
    deadline = db_session.query(Deadline).filter_by(matter_id=matter.id).first()
    assert deadline is not None
    assert deadline.source_text == "Gerichtstermin laut Anruf"
    assert deadline.due_date == date(2026, 12, 1)
    assert deadline.review_status == "confirmed"
    assert deadline.document_id is None

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Deadline", entity_id=deadline.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "deadline_added_manually"


def test_create_deadline_with_blank_label_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/deadlines",
        data={"csrf_token": csrf, "source_text": "   ", "due_date": "2026-12-01"},
    )

    assert response.status_code == 400
    assert db_session.query(Deadline).filter_by(matter_id=matter.id).count() == 0


def test_create_deadline_with_invalid_date_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/deadlines",
        data={"csrf_token": csrf, "source_text": "Frist", "due_date": "nicht-ein-datum"},
    )

    assert response.status_code == 400
    assert db_session.query(Deadline).filter_by(matter_id=matter.id).count() == 0


def test_create_deadline_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)

    response = client.post(
        f"/dashboard/matters/{matter.id}/deadlines",
        data={"csrf_token": "invalid", "source_text": "Frist", "due_date": "2026-12-01"},
    )

    assert response.status_code == 403
    assert db_session.query(Deadline).filter_by(matter_id=matter.id).count() == 0


def test_create_deadline_404s_for_unknown_matter(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)

    response = client.post(
        "/dashboard/matters/does-not-exist/deadlines",
        data={"csrf_token": csrf, "source_text": "Frist", "due_date": "2026-12-01"},
    )

    assert response.status_code == 404


def test_manually_created_deadline_appears_on_matter_page(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)
    client.post(
        f"/dashboard/matters/{matter.id}/deadlines",
        data={
            "csrf_token": csrf,
            "source_text": "Gerichtstermin laut Anruf",
            "due_date": "2026-12-01",
        },
    )

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "Gerichtstermin laut Anruf" in response.text
    assert "01.12.2026" in response.text
    assert "bestätigt" in response.text
