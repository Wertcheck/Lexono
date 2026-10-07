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
from app.tasks.service import create_task, set_task_status
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


def test_tasks_badge_caps_display_at_99_plus(client: TestClient, db_session: Session) -> None:
    # Owner-Direktive "UI-QUALITAETSRUNDE" P1 (04.10.): eine dreistellige+
    # Zahl wuerde das kompakte Icon-Badge (insb. eingeklappte Sidebar,
    # siehe app.css-Kommentar bei .sidebar__group-badge) sprengen - ab 100
    # wird wie bei Messenger-Benachrichtigungen "99+" angezeigt.
    client_row = Client(name="Testmandant", contact_email="mandant@example.invalid")
    db_session.add(client_row)
    db_session.flush()
    matter = Matter(client_id=client_row.id, title="Testakte")
    db_session.add(matter)
    db_session.flush()
    for i in range(101):
        db_session.add(Task(matter_id=matter.id, title=f"Aufgabe {i}", status="open"))
    db_session.commit()
    _login(client, db_session)

    response = client.get("/dashboard/tasks/badge")

    assert response.status_code == 200
    assert "99+" in response.text
    assert "101" not in response.text


# --- Fristen auf der Seite "Aufgaben & Fristen" (14.09.) -------------------
# ECHTER FUND bei der Gold-Workflow-Pruefung ("Fristen/Aufgaben erkennen"):
# Diese Seite fragte AUSSCHLIESSLICH `Task` ab - und `Task` wird von KEINEM
# Code-Pfad der Anwendung je erzeugt. Gleichzeitig lagen in der realen
# Produktionsdatenbank 178 vom produktiven DeadlineAnalysisService erkannte
# `Deadline`-Datensaetze. Die Fristenuebersicht war damit strukturell IMMER
# leer, jede erkannte Frist nur ueber die einzelne Akte auffindbar. Fuer eine
# Kanzlei ist eine uebersehene Frist der folgenreichste Fehler ueberhaupt.


def _matter_with_deadline(db: Session, *, due_date, review_status="unreviewed", source_text=None):
    from datetime import date as _date

    client = Client(name="Testmandant Frist")
    matter = Matter(client=client, title="Einspruch Steuerbescheid 2025")
    db.add_all([client, matter])
    db.flush()
    deadline = Deadline(
        matter_id=matter.id,
        due_date=due_date,
        review_status=review_status,
        source_text=source_text,
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
    "Erkannte Fristen"-Anzeige) direkt bekannt ist.

    UPDATE (03.10., Owner-Direktive "AUFGABEN & FRISTEN"): dieser Link lebt
    nach dem vollstaendigen Seiten-Neuaufbau jetzt im rechten Detailpanel
    (Tab "Verknüpfte Dokumente"), nicht mehr direkt in der Listenzeile -
    siehe tasks.html. Die Funktion selbst ist unveraendert erhalten, nur
    ueber `?selected=...&selected_type=deadline` erreichbar (exakt der vom
    neuen Referenzbild `18_akte_dokumente_detail.png` verlangten
    Zeile-anklicken-oeffnet-Detailpanel-Interaktion)."""
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

    response = client.get(
        "/dashboard/tasks", params={"selected": deadline.id, "selected_type": "deadline"}
    )

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


# --- ECHTER FUND (07.10., Owner-Direktive "AUFGABEN & FRISTEN -
# REFERENZABGLEICH"): die Referenzgrafik zeigt je verknuepftem Dokument
# Icon + Dateiname + Datum + Dateigroesse + Kontextmenue (Oeffnen/
# Herunterladen) - bisher fehlten Dateigroesse und Kontextmenue komplett. ---
def test_linked_document_card_shows_real_file_size_and_context_menu(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    """Dateigroesse wird ECHT vom Dateisystem gelesen (app/documents/
    rendering.py::document_file_size_label, bereits anderswo etabliert -
    siehe dortiger Kommentar in app/web/clients_router.py), kein erfundener
    Wert. Das Kontextmenue verlinkt NUR auf bereits bestehende, echte
    Routen (Ansicht + Download aus app/web/matters_router.py)."""
    from datetime import date, timedelta

    from app.models import Document

    real_file = tmp_path / "steuerbescheid.pdf"
    real_file.write_bytes(b"x" * 2048)  # exakt 2 KB, deterministisch pruefbar

    matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    document = Document(
        matter_id=matter.id,
        file_path=str(real_file),
        original_filename="steuerbescheid.pdf",
    )
    db_session.add(document)
    db_session.flush()
    deadline.document_id = document.id
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get(
        "/dashboard/tasks", params={"selected": deadline.id, "selected_type": "deadline"}
    )

    assert response.status_code == 200
    assert "2 KB" in response.text
    assert f'/dashboard/matters/{matter.id}/document/{document.id}/download' in response.text
    assert "file-format-icon--pdf" in response.text


def test_linked_document_card_degrades_gracefully_when_file_is_missing(
    client: TestClient, db_session: Session
) -> None:
    """Fehlt die echte Datei (verschoben/geloescht), wird ehrlich '–'
    gezeigt statt eines erfundenen/geschaetzten Werts - Seite darf dabei
    nicht fehlschlagen."""
    from datetime import date, timedelta

    from app.models import Document

    matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=14)
    )
    document = Document(
        matter_id=matter.id,
        file_path="/nicht/vorhanden/steuerbescheid.pdf",
        original_filename="steuerbescheid.pdf",
    )
    db_session.add(document)
    db_session.flush()
    deadline.document_id = document.id
    db_session.commit()
    login_as_admin(db_session, client)

    response = client.get(
        "/dashboard/tasks", params={"selected": deadline.id, "selected_type": "deadline"}
    )

    assert response.status_code == 200


def test_page_heading_uses_the_scoped_brand_navy_modifier_class(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (07.10.): die Referenzgrafik zeigt die Seitenueberschrift
    in einem kraeftigen Markennavy statt im App-weiten Standard-Dunkelton.
    Nur ueber eine eigene, auf dieser Seite einzigartige Modifier-Klasse
    umgesetzt (`.topbar__title--brand-navy`) - `.topbar__title` selbst
    (von Mandanten/Akten/Posteingang/Schriftsatz-Editor geteilt) bleibt
    textuell unveraendert, siehe test_other_pages_keep_the_default_dark_
    heading_color unten fuer die Regressions-Gegenprobe."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert response.status_code == 200
    assert 'class="topbar__title topbar__title--brand-navy"' in response.text


def test_tasks_table_header_css_is_scoped_and_not_shouting_uppercase(
    client: TestClient, db_session: Session
) -> None:
    """Referenzgrafik zeigt die Tabellenkopfzeile in normaler Gross-/
    Kleinschreibung - nur fuer `.tasks-table th` ueberschrieben (nicht das
    app-weit geteilte `.draft-table th`, siehe dortiger Kommentar in
    app.css)."""
    login_as_admin(db_session, client)

    response = client.get("/dashboard/static/css/app.css")

    assert response.status_code == 200
    css = response.text
    start = css.index(".tasks-table th {")
    end = css.index("}", start)
    block = css[start:end]
    assert "text-transform: none;" in block
    # Gegenprobe: das app-weit geteilte Original bleibt unveraendert uppercase.
    shared_start = css.index(".draft-table th {")
    shared_end = css.index("}", shared_start)
    assert "text-transform: uppercase;" in css[shared_start:shared_end]


def test_other_pages_keep_the_default_dark_heading_color(
    client: TestClient, db_session: Session
) -> None:
    """Regressions-Gegenprobe: Mandanten/Akten/Posteingang duerfen durch die
    Aufgaben-&-Fristen-spezifische Markenfarbe NICHT beeinflusst werden -
    ihr `.topbar__title` traegt weiterhin KEINE `--brand-navy`-Modifier-
    Klasse."""
    login_as_admin(db_session, client)

    for path in ("/dashboard/matters", "/dashboard/clients"):
        response = client.get(path)
        assert response.status_code == 200
        assert "topbar__title--brand-navy" not in response.text


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
    offener Punkt erscheinen.

    UPDATE (03.10., Owner-Direktive "AUFGABEN & FRISTEN"): die Frist
    bekommt hier einen eigenen, vom Aktentitel ABWEICHENDEN `source_text` -
    die neue Seite zeigt den Aktentitel jetzt zusaetzlich legitim im
    "Alle Akten"-Filter-Dropdown (fuer JEDE Akte, unabhaengig vom
    Pruefstatus ihrer Fristen) - eine Pruefung auf den reinen Aktentitel
    wuerde dort faelschlich anschlagen, obwohl die Frist selbst korrekt
    ausgeblendet ist."""
    from datetime import date, timedelta

    _matter_with_deadline(
        db_session,
        due_date=date.today() + timedelta(days=5),
        review_status="rejected",
        source_text="Verworfene Einspruchsfrist",
    )
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert "Verworfene Einspruchsfrist" not in response.text


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


# --- Neuer Seiten-Neuaufbau (03.10., Owner-Direktive "AUFGABEN & FRISTEN",
#     Referenzabgleich `18_akte_dokumente_detail.png`): echte CRUD-Routen,
#     Filter/Sortierung/Pagination der vereinheitlichten Liste, Detailpanel.
#     Siehe tests/test_tasks_service.py fuer die darunterliegende
#     Service-Ebene (hier nur die HTTP-/Template-Ebene). ---------------------


def _matter_and_client(db_session: Session, *, matter_title: str = "Testakte") -> Matter:
    client_row = Client(name="Testmandant", contact_email="mandant@example.invalid")
    db_session.add(client_row)
    db_session.flush()
    matter = Matter(client_id=client_row.id, title=matter_title)
    db_session.add(matter)
    db_session.commit()
    return matter


def test_create_task_action_creates_real_task_and_opens_its_detail_panel(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter_and_client(db_session)
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/tasks/create",
        data={
            "csrf_token": csrf,
            "matter_id": matter.id,
            "title": "Schriftsatz finalisieren",
            "due_date": "",
            "priority": "Hoch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    task = db_session.query(Task).filter_by(title="Schriftsatz finalisieren").one()
    assert response.headers["location"] == f"/dashboard/tasks?selected={task.id}&selected_type=task"
    assert task.priority == "Hoch"

    detail = client.get(response.headers["location"])
    assert "Schriftsatz finalisieren" in detail.text


def test_create_task_action_rejects_blank_title(client: TestClient, db_session: Session) -> None:
    matter = _matter_and_client(db_session)
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/tasks/create",
        data={"csrf_token": csrf, "matter_id": matter.id, "title": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(Task).count() == 0


def test_create_task_action_404s_for_unknown_matter(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/tasks/create",
        data={"csrf_token": csrf, "matter_id": "does-not-exist", "title": "Aufgabe"},
    )

    assert response.status_code == 404


def test_update_task_action_updates_fields(client: TestClient, db_session: Session) -> None:
    task = _make_task(db_session, title="Alt")
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/task/{task.id}/update",
        data={"csrf_token": csrf, "title": "Neu", "description": "", "due_date": "", "priority": "Mittel"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(task)
    assert task.title == "Neu"
    assert task.priority == "Mittel"


def test_set_task_status_action_marks_done_and_updates_badge(
    client: TestClient, db_session: Session
) -> None:
    task = _make_task(db_session, title="Zu erledigen")
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    assert "1" in client.get("/dashboard/tasks/badge").text

    response = client.post(
        f"/dashboard/tasks/task/{task.id}/status",
        data={"csrf_token": csrf, "status": "done"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(task)
    assert task.status == "done"
    assert "sidebar__group-badge" not in client.get("/dashboard/tasks/badge").text


def test_duplicate_task_action_creates_independent_copy(
    client: TestClient, db_session: Session
) -> None:
    task = _make_task(db_session, title="Original")
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/task/{task.id}/duplicate",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(Task).count() == 2
    assert db_session.query(Task).filter_by(title="Original (Kopie)").count() == 1


def test_delete_task_action_removes_it(client: TestClient, db_session: Session) -> None:
    task = _make_task(db_session, title="Weg damit")
    task_id = task.id
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/task/{task_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(Task).filter_by(id=task_id).first() is None


def test_update_deadline_action_updates_fields(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    _matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=5), source_text="Alt"
    )
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/deadline/{deadline.id}/update",
        data={
            "csrf_token": csrf,
            "source_text": "Neu",
            "due_date": date.today().isoformat(),
            "priority": "Hoch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(deadline)
    assert deadline.source_text == "Neu"
    assert deadline.priority == "Hoch"


def test_set_deadline_status_action_marks_done(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    _matter, deadline = _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=5))
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/deadline/{deadline.id}/status",
        data={"csrf_token": csrf, "status": "done"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(deadline)
    assert deadline.status == "done"


def test_duplicate_deadline_action_creates_copy(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    _matter, deadline = _matter_with_deadline(
        db_session, due_date=date.today() + timedelta(days=5), source_text="Original-Frist"
    )
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/deadline/{deadline.id}/duplicate",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(Deadline).count() == 2


def test_delete_deadline_action_removes_it(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    _matter, deadline = _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=5))
    deadline_id = deadline.id
    login_as_admin(db_session, client)
    page = client.get("/dashboard/tasks")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/tasks/deadline/{deadline_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(Deadline).filter_by(id=deadline_id).first() is None


# --- Detailpanel: korrekter Datensatz wird geladen -----------------------


def test_selecting_a_task_shows_its_own_detail_panel(client: TestClient, db_session: Session) -> None:
    task_a = _make_task(db_session, title="Aufgabe A")
    task_b = _make_task(db_session, title="Aufgabe B")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks", params={"selected": task_a.id, "selected_type": "task"})

    assert response.status_code == 200
    assert "task-detail-panel__title\">Aufgabe A<" in response.text
    assert "task-detail-panel__title\">Aufgabe B<" not in response.text


def test_selecting_unknown_id_renders_page_without_detail_panel(
    client: TestClient, db_session: Session
) -> None:
    login_as_admin(db_session, client)

    response = client.get(
        "/dashboard/tasks", params={"selected": "does-not-exist", "selected_type": "task"}
    )

    assert response.status_code == 200
    assert "task-detail-panel" not in response.text


# --- Suche/Filter/Sortierung/Pagination der vereinheitlichten Liste ------


def test_search_filters_combined_list(client: TestClient, db_session: Session) -> None:
    _make_task(db_session, title="Findbare Aufgabe")
    _make_task(db_session, title="Andere Aufgabe")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks", params={"q": "Findbare"})

    assert "Findbare Aufgabe" in response.text
    assert "Andere Aufgabe" not in response.text


def test_empty_search_shows_empty_state(client: TestClient, db_session: Session) -> None:
    _make_task(db_session, title="Etwas")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks", params={"q": "nichts-passt-hier"})

    assert response.status_code == 200
    assert "Keine Einträge gefunden, die zu den aktuellen Filtern passen." in response.text


def test_combined_type_and_priority_filter(client: TestClient, db_session: Session) -> None:
    matter = _matter_and_client(db_session)
    create_task(db_session, matter_id=matter.id, title="Aufgabe Hoch", priority="Hoch", actor="a@kanzlei.test")
    create_task(db_session, matter_id=matter.id, title="Aufgabe Niedrig", priority="Niedrig", actor="a@kanzlei.test")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks", params={"item_type": "task", "priority": "Hoch"})

    assert "Aufgabe Hoch" in response.text
    assert "Aufgabe Niedrig" not in response.text


def test_sort_by_due_date_ascending_and_descending(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    matter = _matter_and_client(db_session)
    create_task(
        db_session, matter_id=matter.id, title="Spät fällig", due_date=date.today() + timedelta(days=60), actor="a@kanzlei.test"
    )
    create_task(
        db_session, matter_id=matter.id, title="Bald fällig", due_date=date.today() + timedelta(days=1), actor="a@kanzlei.test"
    )
    login_as_admin(db_session, client)

    asc = client.get("/dashboard/tasks", params={"sort": "due_asc"})
    assert asc.text.index("Bald fällig") < asc.text.index("Spät fällig")

    desc = client.get("/dashboard/tasks", params={"sort": "due_desc"})
    assert desc.text.index("Spät fällig") < desc.text.index("Bald fällig")


def test_pagination_after_filtering(client: TestClient, db_session: Session) -> None:
    matter = _matter_and_client(db_session)
    for i in range(15):
        create_task(
            db_session, matter_id=matter.id, title=f"Hoch {i:02d}", priority="Hoch", actor="a@kanzlei.test"
        )
    create_task(
        db_session, matter_id=matter.id, title="Ausgeblendete Aufgabe", priority="Niedrig", actor="a@kanzlei.test"
    )
    login_as_admin(db_session, client)

    first_page = client.get("/dashboard/tasks", params={"priority": "Hoch", "page": 1, "page_size": 10})
    assert "10 von 15" in first_page.text
    assert "Ausgeblendete Aufgabe" not in first_page.text

    second_page = client.get("/dashboard/tasks", params={"priority": "Hoch", "page": 2, "page_size": 10})
    assert "5 von 15" in second_page.text


def test_erledigt_tab_shows_only_done_items(client: TestClient, db_session: Session) -> None:
    matter = _matter_and_client(db_session)
    open_task = create_task(db_session, matter_id=matter.id, title="Offene Aufgabe", actor="a@kanzlei.test")
    done_task = create_task(db_session, matter_id=matter.id, title="Erledigte Aufgabe", actor="a@kanzlei.test")
    set_task_status(db_session, done_task, status="done", actor="a@kanzlei.test")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks", params={"status": "done"})

    assert "Erledigte Aufgabe" in response.text
    assert "Offene Aufgabe" not in response.text


def test_fristen_tab_shows_only_deadlines(client: TestClient, db_session: Session) -> None:
    from datetime import date, timedelta

    _make_task(db_session, title="Eine Aufgabe")
    _matter_with_deadline(db_session, due_date=date.today() + timedelta(days=5), source_text="Eine Frist")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks", params={"item_type": "deadline"})

    assert "Eine Frist" in response.text
    assert "Eine Aufgabe" not in response.text


# --- Lange Titel / fehlende optionale Angaben ----------------------------


def test_long_title_and_missing_optional_fields_render_without_error(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter_and_client(db_session)
    long_title = "Sehr " * 30 + "lange Aufgabenbezeichnung"
    create_task(db_session, matter_id=matter.id, title=long_title, actor="a@kanzlei.test")
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks")

    assert response.status_code == 200
    assert long_title in response.text


def test_task_without_due_date_or_priority_shows_placeholder(
    client: TestClient, db_session: Session
) -> None:
    task = _make_task(db_session, title="Minimal-Aufgabe")
    assert task.due_date is None
    assert task.priority is None
    login_as_admin(db_session, client)

    response = client.get("/dashboard/tasks", params={"selected": task.id, "selected_type": "task"})

    assert response.status_code == 200
    assert "Minimal-Aufgabe" in response.text
