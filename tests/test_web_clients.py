"""Tests für app/web/clients_router.py (Mandantendatenbank, 20.08.) - löst
den bisherigen Platzhalter unter `/dashboard/clients` ab.

Gleiches Testmuster wie tests/test_web_schriftsatz.py: In-Memory-SQLite
über app.dependency_overrides."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import Client, Matter, Message
from app.models.base import Base
from tests.auth_test_utils import create_test_user, extract_csrf, login, login_as_admin, seed_roles


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
    """Admin-Client - gleicher Fixture-Name wie in den übrigen Testdateien."""

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
def anwalt_client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        test_client = TestClient(app)
        roles = seed_roles(db_session)
        create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
        login(test_client, "anwalt@kanzlei.test")
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def mitarbeiter_client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        test_client = TestClient(app)
        roles = seed_roles(db_session)
        create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
        login(test_client, "mitarbeiter@kanzlei.test")
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _csrf(test_client: TestClient, path: str = "/dashboard/clients") -> str:
    page = test_client.get(path)
    return extract_csrf(page.text)


def _create_client_row(db_session: Session, *, name: str = "Muster GmbH", number: str = "M-1") -> Client:
    row = Client(name=name, client_number=number, status="active")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


# --- Liste ---


def test_clients_page_is_no_longer_a_placeholder(client: TestClient) -> None:
    response = client.get("/dashboard/clients")
    assert response.status_code == 200
    assert "in Vorbereitung" not in response.text
    assert "Mandantendatenbank" in response.text


def test_clients_page_lists_created_client(client: TestClient, db_session: Session) -> None:
    _create_client_row(db_session, name="Sichtbarer Mandant", number="V-1")
    response = client.get("/dashboard/clients")
    assert "Sichtbarer Mandant" in response.text


def test_clients_page_search_filters_by_name(client: TestClient, db_session: Session) -> None:
    _create_client_row(db_session, name="Findbar GmbH", number="F-1")
    _create_client_row(db_session, name="Anderer Mandant", number="F-2")
    response = client.get("/dashboard/clients", params={"q": "Findbar"})
    assert "Findbar GmbH" in response.text
    assert "Anderer Mandant" not in response.text


def test_clients_page_hides_archived_by_default(client: TestClient, db_session: Session) -> None:
    row = _create_client_row(db_session, name="Archiviert GmbH", number="AR-1")
    row.status = "archived"
    db_session.commit()
    response = client.get("/dashboard/clients")
    assert "Archiviert GmbH" not in response.text

    response_all = client.get("/dashboard/clients", params={"status": "all"})
    assert "Archiviert GmbH" in response_all.text


# --- Anlegen ---


def test_create_client_as_admin_redirects_to_detail_page(client: TestClient) -> None:
    csrf_token = _csrf(client)
    response = client.post(
        "/dashboard/clients/create",
        data={
            "csrf_token": csrf_token,
            "name": "Neuer Mandant",
            "client_number": "N-1",
            "contact_email": "neu@muster.test",
            "contact_phone": "030 111",
            "practice_area": "Mietrecht",
            "responsible_user_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard/clients/")

    detail = client.get(response.headers["location"])
    assert "Neuer Mandant" in detail.text


def test_create_client_missing_name_redirects_with_error(client: TestClient) -> None:
    csrf_token = _csrf(client)
    # Bewusst Whitespace statt eines echten Leerstrings - ein leerer
    # `str = Form(...)`-Wert wird von FastAPI/Pydantic in dieser
    # Projektumgebung bereits VOR dem Routenkörper als "Feld fehlt" (422)
    # abgelehnt, siehe dasselbe Muster in tests/test_web_settings.py
    # (firm_name="   "), statt bis zur eigentlichen ClientValidationError-
    # Pruefung im Routenkoerper vorzudringen.
    response = client.post(
        "/dashboard/clients/create",
        data={"csrf_token": csrf_token, "name": "   ", "client_number": "N-2"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/dashboard/clients?error=" in response.headers["location"]


def test_create_client_as_anwalt_is_allowed(anwalt_client: TestClient) -> None:
    csrf_token = _csrf(anwalt_client)
    response = anwalt_client.post(
        "/dashboard/clients/create",
        data={"csrf_token": csrf_token, "name": "Anwalt-Mandant", "client_number": "AW-1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_create_client_as_mitarbeiter_is_forbidden(mitarbeiter_client: TestClient) -> None:
    csrf_token = _csrf(mitarbeiter_client)
    response = mitarbeiter_client.post(
        "/dashboard/clients/create",
        data={"csrf_token": csrf_token, "name": "Verboten", "client_number": "V-1"},
    )
    assert response.status_code == 403


# --- Detail ---


def test_client_detail_page_shows_matters_messages_documents(
    client: TestClient, db_session: Session
) -> None:
    row = _create_client_row(db_session, name="Detail-Mandant", number="D-1")
    matter = Matter(client_id=row.id, title="Verknüpfte Akte", status="open")
    db_session.add(matter)
    db_session.flush()
    db_session.add(
        Message(matter_id=matter.id, direction="inbound", sender="a@b.test", subject="Betreff X")
    )
    db_session.commit()

    response = client.get(f"/dashboard/clients/{row.id}")
    assert response.status_code == 200
    assert "Detail-Mandant" in response.text
    assert "Verknüpfte Akte" in response.text
    assert "Betreff X" in response.text


def test_client_detail_page_404_for_unknown_id(client: TestClient) -> None:
    response = client.get("/dashboard/clients/does-not-exist")
    assert response.status_code == 404


def test_client_detail_shows_ai_cta_link_for_single_open_matter(
    client: TestClient, db_session: Session
) -> None:
    row = _create_client_row(db_session, name="Ein-Akte-Mandant", number="AI-1")
    matter = Matter(client_id=row.id, title="Einzige Akte", status="open")
    db_session.add(matter)
    db_session.commit()

    response = client.get(f"/dashboard/clients/{row.id}")
    assert f"/dashboard/tools/schriftsatz?matter_id={matter.id}" in response.text


# --- Bearbeiten ---


def test_update_client_changes_name(client: TestClient, db_session: Session) -> None:
    row = _create_client_row(db_session, name="Alter Name", number="UP-1")
    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    response = client.post(
        f"/dashboard/clients/{row.id}/update",
        data={
            "csrf_token": csrf_token,
            "name": "Neuer Name",
            "client_number": "UP-1",
            "contact_email": "",
            "contact_phone": "",
            "practice_area": "",
            "responsible_user_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(row)
    assert row.name == "Neuer Name"


# --- Archivieren/Reaktivieren ---


def test_archive_then_reactivate_client(client: TestClient, db_session: Session) -> None:
    row = _create_client_row(db_session, name="Archiv-Test", number="AR-2")
    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    client.post(
        f"/dashboard/clients/{row.id}/archive",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    db_session.refresh(row)
    assert row.status == "archived"

    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    client.post(
        f"/dashboard/clients/{row.id}/reactivate",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    db_session.refresh(row)
    assert row.status == "active"


# --- Löschen (Kernanforderung: kein Loeschen mit Akten) ---


def test_delete_client_without_matters_succeeds(client: TestClient, db_session: Session) -> None:
    row = _create_client_row(db_session, name="Loeschbar", number="DEL-1")
    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    response = client.post(
        f"/dashboard/clients/{row.id}/delete",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/clients"
    assert db_session.get(Client, row.id) is None


def test_delete_client_with_matters_is_blocked_and_client_survives(
    client: TestClient, db_session: Session
) -> None:
    row = _create_client_row(db_session, name="Mit Akte", number="DEL-2")
    matter = Matter(client_id=row.id, title="Akte bleibt", status="open")
    db_session.add(matter)
    db_session.commit()

    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    response = client.post(
        f"/dashboard/clients/{row.id}/delete",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert f"/dashboard/clients/{row.id}?error=" in response.headers["location"]
    assert db_session.get(Client, row.id) is not None
    assert db_session.query(Matter).filter_by(client_id=row.id).count() == 1


def test_delete_client_with_matters_shows_error_banner_on_detail_page(
    client: TestClient, db_session: Session
) -> None:
    """Regressionsschutz: der Redirect nach einem blockierten Loeschversuch
    haengt `?error=...` an die Detail-URL an - die GET-Route muss diesen
    Query-Parameter tatsaechlich lesen UND an das Template durchreichen,
    sonst verschwindet die Fehlermeldung stillschweigend (gefunden bei der
    manuellen Verifikation im Browser, 20.08.)."""
    row = _create_client_row(db_session, name="Mit Akte Banner", number="DEL-4")
    matter = Matter(client_id=row.id, title="Akte", status="open")
    db_session.add(matter)
    db_session.commit()

    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    response = client.post(
        f"/dashboard/clients/{row.id}/delete",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "banner--error" in response.text
    assert "Aufbewahrungsgruenden gesperrt" in response.text


def test_delete_client_as_anwalt_is_forbidden(
    anwalt_client: TestClient, db_session: Session
) -> None:
    """PERM_CLIENT_DELETE ist admin-exklusiv - anders als
    PERM_CLIENT_MANAGE (Anlegen/Archivieren), siehe app/auth/permissions.py."""
    row = _create_client_row(db_session, name="Nur Admin loescht", number="DEL-3")
    csrf_token = _csrf(anwalt_client, f"/dashboard/clients/{row.id}")
    response = anwalt_client.post(
        f"/dashboard/clients/{row.id}/delete",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 403
    assert db_session.get(Client, row.id) is not None


# --- DSGVO-Datenauszug ---


def test_export_client_returns_zip_file(client: TestClient, db_session: Session) -> None:
    row = _create_client_row(db_session, name="Export-Mandant", number="EX-1")
    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    response = client.post(
        f"/dashboard/clients/{row.id}/export",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.content[:2] == b"PK"  # ZIP-Magic-Bytes


def test_export_client_archive_deleted_after_download(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
) -> None:
    """Pilot-Readiness-Härtung: das DSGVO-Datenauszug-Archiv (enthält
    unpseudonymisierte Mandanteninhalte) darf nach dem Download nicht im
    Staging-Verzeichnis liegen bleiben. Siehe app/web/download_staging.py."""
    import app.web.clients_router as clients_module

    monkeypatch.setattr(clients_module, "_DOWNLOAD_STAGING_DIR", tmp_path)

    row = _create_client_row(db_session, name="Export-Mandant-Cleanup", number="EX-2")
    csrf_token = _csrf(client, f"/dashboard/clients/{row.id}")
    response = client.post(
        f"/dashboard/clients/{row.id}/export",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 200

    remaining = list(tmp_path.glob("*.zip"))
    assert remaining == [], f"DSGVO-Export-Archiv nicht aufgeräumt: {remaining}"


# --- CSV-/Excel-Import ---


def test_import_csv_creates_clients_and_shows_result(client: TestClient, db_session: Session) -> None:
    csrf_token = _csrf(client)
    csv_content = "Name,Mandantennummer\r\nImport Eins,IMP-1\r\nImport Zwei,IMP-2\r\n"
    response = client.post(
        "/dashboard/clients/import",
        data={"csrf_token": csrf_token},
        files={"file": ("mandanten.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200
    assert "2</strong> Mandant" in response.text
    assert db_session.query(Client).filter(Client.client_number.in_(["IMP-1", "IMP-2"])).count() == 2


def test_import_xlsx_creates_clients(client: TestClient, db_session: Session) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Mandantennummer"])
    sheet.append(["Excel Mandant", "XL-1"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    csrf_token = _csrf(client)
    response = client.post(
        "/dashboard/clients/import",
        data={"csrf_token": csrf_token},
        files={
            "file": (
                "mandanten.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200
    assert db_session.query(Client).filter_by(client_number="XL-1").count() == 1


def test_import_unsupported_file_type_shows_error(client: TestClient) -> None:
    csrf_token = _csrf(client)
    response = client.post(
        "/dashboard/clients/import",
        data={"csrf_token": csrf_token},
        files={"file": ("mandanten.txt", b"irrelevant", "text/plain")},
    )
    assert response.status_code == 200
    assert "Import fehlgeschlagen" in response.text


def test_import_as_mitarbeiter_is_forbidden(mitarbeiter_client: TestClient) -> None:
    csrf_token = _csrf(mitarbeiter_client)
    response = mitarbeiter_client.post(
        "/dashboard/clients/import",
        data={"csrf_token": csrf_token},
        files={"file": ("mandanten.csv", b"Name,Mandantennummer\r\nX,Y\r\n", "text/csv")},
    )
    assert response.status_code == 403


# --- Unauthentifiziert ---


def test_unauthenticated_cannot_view_clients_list(db_session: Session) -> None:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        anon_client = TestClient(app)
        response = anon_client.get("/dashboard/clients", follow_redirects=False)
        assert response.status_code == 303
        assert "/dashboard/login" in response.headers["location"]
    finally:
        app.dependency_overrides.clear()
