"""Tests für app/web/tasks_router.py (01.09.) - schließt einen echten,
vorher unbekannten Gap: `app/models/task.py::Task` existierte bereits als
Datenmodell (Akten-Aufgaben), hatte aber keine eigene Dashboard-Seite.
Bewusst nur eine lesende Übersicht + der echte Badge-Zähler (kein
erfundener Platzhalterwert), siehe Modul-Docstring in tasks_router.py."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import Client, Matter, Task
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
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _login(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)


def _make_task(db_session: Session, *, title: str, status: str = "open") -> Task:
    client_row = Client(name="Testmandant", contact_email="mandant@example.invalid")
    db_session.add(client_row)
    db_session.flush()
    matter = Matter(client_id=client_row.id, title="Testakte")
    db_session.add(matter)
    db_session.flush()
    task = Task(matter_id=matter.id, title=title, status=status)
    db_session.add(task)
    db_session.commit()
    return task


def test_tasks_page_requires_login(client: TestClient) -> None:
    response = client.get("/dashboard/tasks", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_tasks_page_lists_open_tasks(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    _make_task(db_session, title="Frist prüfen")
    _make_task(db_session, title="Bereits erledigt", status="done")

    response = client.get("/dashboard/tasks")

    assert response.status_code == 200
    assert "Frist prüfen" in response.text
    assert "Bereits erledigt" not in response.text


def test_tasks_page_shows_empty_state_without_open_tasks(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)

    response = client.get("/dashboard/tasks")

    assert response.status_code == 200
    assert "Keine offenen Aufgaben" in response.text


def test_tasks_badge_shows_real_open_count(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    _make_task(db_session, title="Aufgabe 1")
    _make_task(db_session, title="Aufgabe 2")
    _make_task(db_session, title="Erledigt", status="done")

    response = client.get("/dashboard/tasks/badge")

    assert response.status_code == 200
    assert "2" in response.text
    assert "sidebar__group-badge" in response.text


def test_tasks_badge_empty_when_no_open_tasks(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)

    response = client.get("/dashboard/tasks/badge")

    assert response.status_code == 200
    assert "sidebar__group-badge" not in response.text
