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
from app.models import Client, Deadline, Matter, Task
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


# --- Fristen auf der Seite "Aufgaben & Fristen" (14.09.) -------------------
# ECHTER FUND bei der Gold-Workflow-Pruefung ("Fristen/Aufgaben erkennen"):
# Diese Seite fragte AUSSCHLIESSLICH `Task` ab - und `Task` wird von KEINEM
# Code-Pfad der Anwendung je erzeugt. Gleichzeitig lagen in der realen
# Produktionsdatenbank 178 vom produktiven DeadlineAnalysisService erkannte
# `Deadline`-Datensaetze. Die Fristenuebersicht war damit strukturell IMMER
# leer, jede erkannte Frist nur ueber die einzelne Akte auffindbar. Fuer eine
# Kanzlei ist eine uebersehene Frist der folgenreichste Fehler ueberhaupt.


def _matter_with_deadline(db: Session, *, due_date, review_status="unreviewed"):
    from datetime import date as _date

    client = Client(name="Testmandant Frist")
    matter = Matter(client=client, title="Einspruch Steuerbescheid 2025")
    db.add_all([client, matter])
    db.flush()
    deadline = Deadline(
        matter_id=matter.id, due_date=due_date, review_status=review_status
    )
    db.add(deadline)
    db.commit()
    return matter, deadline


def test_deadline_with_document_links_directly_to_that_document(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (17.09., Owner-Direktive §6/§7 "Workflows verbinden"):
    `Deadline.document_id` existiert bereits (gesetzt vom produktiven
    `DeadlineAnalysisService`), wurde auf dieser Seite aber nie genutzt -
    jede Frist verlinkte immer nur auf die Akte-Uebersicht, obwohl das
    tatsaechlich erkennende Dokument (inkl. der dort bereits gebauten
    "Erkannte Fristen"-Anzeige) direkt bekannt ist."""
    from datetime import date, timedelta

    from app.models import Document

    matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/steuerbescheid.pdf",
        original_filename="steuerbescheid.pdf",
    )
    db_session.add(document)
    db_session.flush()
    deadline.document_id = document.id
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert response.status_code == 200
    assert f'/dashboard/matters/{matter.id}/document/{document.id}' in response.text


def test_deadline_without_document_links_to_the_matter_overview(
    client: TestClient, db_session: Session
) -> None:
    """Gegenprobe: ohne bekanntes Dokument bleibt der bisherige, weiterhin
    funktionierende Akte-Link bestehen - kein toter Link vorgetaeuscht."""
    from datetime import date, timedelta

    matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    assert deadline.document_id is None
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert response.status_code == 200
    assert f'href="/dashboard/matters/{matter.id}"' in response.text


def test_page_lists_detected_deadlines(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    matter, _ = _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=14))
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert response.status_code == 200
    assert "Einspruch Steuerbescheid 2025" in response.text
    assert "Testmandant Frist" in response.text


def test_page_marks_unreviewed_deadlines_as_such(
    client: TestClient, db_session: Session
) -> None:
    """Eine nur VERMUTETE Frist darf optisch nicht wie eine bestaetigte
    wirken - der Anwalt muss den Pruefstatus sehen."""
    from datetime import date, timedelta

    _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=7))
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert "ungeprüft" in response.text


def test_rejected_deadlines_are_not_listed(
    client: TestClient, db_session: Session
) -> None:
    """Eine vom Anwalt ausdruecklich verworfene Frist soll nicht weiter als
    offener Punkt erscheinen."""
    from datetime import date, timedelta

    _matter_with_deadline(
        db_session,
        due_date=date.today() + timedelta(days=5),
        review_status="rejected",
    )
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert "Einspruch Steuerbescheid 2025" not in response.text


def test_badge_counts_deadlines_not_only_tasks(
    client: TestClient, db_session: Session
) -> None:
    """Der Navigationspunkt heisst "Aufgaben & Fristen" - die Zahl daneben
    stand vorher dauerhaft auf 0, obwohl real erkannte Fristen offen waren."""
    from datetime import date, timedelta

    _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=3))
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks/badge")

    assert response.status_code == 200
    assert "1" in response.text


def test_overdue_deadline_is_marked_as_overdue(
    client: TestClient, db_session: Session
) -> None:
    """Eine versaeumte Frist ist in einer Kanzlei der folgenreichste Fehler
    ueberhaupt - "ueberfaellig" muss als Text erscheinen, nicht nur als
    Farbe (Farbe allein waere fuer farbfehlsichtige Nutzer kein Signal)."""
    from datetime import date, timedelta

    _matter_with_deadline(db_session, due_date=date.today() - timedelta(days=3))
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert "überfällig" in response.text


def test_future_deadline_is_not_marked_overdue(
    client: TestClient, db_session: Session
) -> None:
    from datetime import date, timedelta

    _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=30))
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert "überfällig" not in response.text


# --- Frist bestaetigen/verwerfen (17.09., Owner-Direktive §5/§6/§10) ------
# ECHTER FUND: `Deadline.review_status` (unreviewed/confirmed/rejected)
# existierte im Datenmodell und in der Anzeige bereits vollstaendig, aber es
# gab PROJEKTWEIT keinen einzigen Schreibpfad, der ihn tatsaechlich aendert -
# weder im Dashboard noch in der read-only REST-API (app/api/routers/
# tasks.py). Der "Anwalt prueft"-Schritt des Gold-Workflows fuer Fristen war
# damit reine Anzeige ohne Aktion.


def test_unreviewed_deadline_shows_confirm_and_reject_buttons(
    client: TestClient, db_session: Session
) -> None:
    from datetime import date, timedelta

    _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=14))
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert "Bestätigen" in response.text
    assert "Verwerfen" in response.text


def test_already_confirmed_deadline_hides_the_action_buttons(
    client: TestClient, db_session: Session
) -> None:
    from datetime import date, timedelta

    _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14), review_status="confirmed"
    )
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert "Bestätigen" not in response.text
    assert "Verwerfen" not in response.text


def test_confirm_action_updates_review_status_and_logs_audit_event(
    client: TestClient, db_session: Session
) -> None:
    from datetime import date, timedelta

    from app.models import AuditEvent

    _matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/{deadline.id}/review",
        data={"csrf_token": csrf, "status": "confirmed"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/tasks"
    db_session.refresh(deadline)
    assert deadline.review_status == "confirmed"
    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Deadline", entity_id=deadline.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "deadline_review_status_changed"


def test_reject_action_updates_review_status(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    _matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    client.post(
        f"/dashboard/tasks/{deadline.id}/review",
        data={"csrf_token": csrf, "status": "rejected"},
        follow_redirects=False,
    )

    db_session.refresh(deadline)
    assert deadline.review_status == "rejected"


def test_review_action_rejects_an_invalid_status_value(
    client: TestClient, db_session: Session
) -> None:
    """Fail-Closed: nur "confirmed"/"rejected" sind gueltige Ziele - "unre-
    viewed" (kein sinnvoller manueller Fall) und beliebige andere Werte
    werden abgelehnt statt stillschweigend uebernommen."""
    from datetime import date, timedelta

    _matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/{deadline.id}/review",
        data={"csrf_token": csrf, "status": "unreviewed"},
    )

    assert response.status_code == 400
    db_session.refresh(deadline)
    assert deadline.review_status == "unreviewed"


def test_review_action_404s_for_unknown_deadline(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    # Eine (andere) echte Frist nur, damit die Seite ueberhaupt ein
    # csrf_token-Feld rendert (siehe tasks.html: das Feld steht innerhalb
    # der Fristen-Schleife) - die eigentliche Anfrage unten zielt bewusst
    # auf eine ANDERE, nicht existierende ID.
    _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=14))
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/tasks/does-not-exist/review",
        data={"csrf_token": csrf, "status": "confirmed"},
    )

    assert response.status_code == 404


def test_review_action_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    from datetime import date, timedelta

    _matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    login_as_admin(db_session, client)

    response = client.post(
        f"/dashboard/tasks/{deadline.id}/review",
        data={"csrf_token": "invalid-token", "status": "confirmed"},
    )

    assert response.status_code == 403
    db_session.refresh(deadline)
    assert deadline.review_status == "unreviewed"
