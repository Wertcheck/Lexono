"""Tests für /dashboard/feedback (Schritt 3)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import PilotFeedback
from app.models.base import Base
from tests.auth_test_utils import create_test_user, login, seed_roles


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


@pytest.fixture()
def roles(db_session: Session):
    return seed_roles(db_session)


def _get_csrf(client: TestClient) -> str:
    response = client.get("/dashboard/feedback")
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def test_unauthenticated_access_is_denied(client: TestClient) -> None:
    response = client.get("/dashboard/feedback", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_any_role_can_view_and_submit_feedback(
    client: TestClient, db_session: Session, roles
) -> None:
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")

    csrf = _get_csrf(client)
    response = client.post(
        "/dashboard/feedback",
        data={
            "csrf_token": csrf,
            "category": "fehler",
            "message": "Die Fristenerkennung zeigt ein falsches Datum an.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    entry = db_session.query(PilotFeedback).first()
    assert entry is not None
    assert entry.submitted_by_actor == "mitarbeiter@kanzlei.test"


def test_blank_message_is_rejected(client: TestClient, db_session: Session, roles) -> None:
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")

    csrf = _get_csrf(client)
    response = client.post(
        "/dashboard/feedback",
        data={"csrf_token": csrf, "category": "fehler", "message": "   "},
    )

    assert response.status_code == 422
    assert db_session.query(PilotFeedback).count() == 0


def test_non_admin_cannot_review(client: TestClient, db_session: Session, roles) -> None:
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")
    csrf = _get_csrf(client)
    client.post(
        "/dashboard/feedback",
        data={
            "csrf_token": csrf,
            "category": "verbesserungsvorschlag",
            "message": "Bitte das Prompt anpassen, die KI soll kürzer antworten.",
        },
    )
    entry = db_session.query(PilotFeedback).first()
    assert entry.review_status == "zur_pruefung"

    response = client.post(
        f"/dashboard/feedback/{entry.id}/review",
        data={"csrf_token": csrf, "action": "freigegeben"},
    )
    assert response.status_code == 403


def test_admin_can_approve_flagged_entry(client: TestClient, db_session: Session, roles) -> None:
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")
    csrf = _get_csrf(client)
    client.post(
        "/dashboard/feedback",
        data={
            "csrf_token": csrf,
            "category": "verbesserungsvorschlag",
            "message": "Systemregel anpassen: die KI soll förmlicher antworten.",
        },
    )
    entry = db_session.query(PilotFeedback).first()
    client.post("/dashboard/logout", data={"csrf_token": csrf})

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    admin_csrf = _get_csrf(client)

    response = client.post(
        f"/dashboard/feedback/{entry.id}/review",
        data={"csrf_token": admin_csrf, "action": "freigegeben", "comment": "Wird umgesetzt."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(entry)
    assert entry.review_status == "freigegeben"
    assert entry.reviewed_by_actor == "admin@kanzlei.test"


def test_feedback_page_lists_nav_entry(client: TestClient, db_session: Session, roles) -> None:
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    login(client, "anwalt@kanzlei.test")
    response = client.get("/dashboard/feedback")
    assert response.status_code == 200
    assert "Pilot-Feedback" in response.text


def test_feedback_list_shows_readable_status_not_the_internal_value(
    client: TestClient, db_session: Session, roles
) -> None:
    """ECHTER FUND (14.09.): die Statusspalte rief das FRISTEN-Makro
    `deadline_status_tag` auf. Dessen Wertebereich (unreviewed/confirmed/
    rejected) passt nicht zu `PilotFeedback.review_status`
    (neu/zur_pruefung/freigegeben/abgelehnt) - die Zuordnung fiel durch und
    zeigte dem Piloten den ROHEN Wert "zur_pruefung". Zusaetzlich steckte
    die fertige Pille in einer zweiten Pille, deren Farblogik nur zwei der
    vier Zustaende kannte."""
    import re

    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")
    db_session.add(
        PilotFeedback(
            submitted_by_actor="mitarbeiter@kanzlei.test",
            category="fehler",
            message="Testmeldung aus dem Pilotbetrieb.",
            review_status="zur_pruefung",
            requires_admin_review=True,
        )
    )
    db_session.commit()

    response = client.get("/dashboard/feedback")
    page = " ".join(response.text.split())

    assert response.status_code == 200
    assert "zur Prüfung" in page
    # Der rohe interne Wert darf nicht als Beschriftung erscheinen.
    assert ">zur_pruefung<" not in page
    # Keine Pille in einer Pille.
    assert re.search(r'<span class="tag[^"]*">\s*<span class="tag(?!__dot)', page) is None
