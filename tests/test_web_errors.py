"""Web-Layer-Tests für /dashboard/errors (Prompt 31).

Deckt ab: Zugriff für alle drei angemeldeten Rollen (bewusste
Design-Entscheidung, siehe app/web/errors_router.py-Docstring - die
bestehende Rechte-Matrix aus Prompt 26 sah diesen Bereich nicht vor),
CSRF-Schutz, erfolgreiche manuelle Wiederholung, nicht authentifizierter
Zugriff wird abgewiesen.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.errors import RetryService
from app.main import app
from app.models import Client, Document, Matter, ProcessingError
from app.models.base import Base
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles


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


@pytest.fixture()
def seeded_error(db_session: Session) -> dict[str, str]:
    client_ = Client(name="Testmandant")
    matter = Matter(client=client_, title="Testakte")
    document = Document(
        matter=matter, original_filename="scan.pdf", file_path="/data/scan.pdf"
    )
    db_session.add_all([client_, matter, document])
    db_session.commit()

    retry_service = RetryService()
    error = retry_service.record_failure(
        db_session,
        entity_type="Document",
        entity_id=document.id,
        operation="ocr",
        error_category="transient",
        error_message="Tesseract nicht gefunden",
    )
    return {"document_id": document.id, "error_id": error.id}


# ==========================================================================
# Zugriff: alle drei angemeldeten Rollen dürfen lesen
# ==========================================================================


@pytest.mark.parametrize("role_name", ["admin", "anwalt", "mitarbeiter"])
def test_all_roles_can_view_errors_list(
    client: TestClient, db_session: Session, roles, role_name: str
) -> None:
    create_test_user(db_session, roles[role_name], f"{role_name}@kanzlei.test")
    login(client, f"{role_name}@kanzlei.test")

    response = client.get("/dashboard/errors")
    assert response.status_code == 200


def test_errors_list_shows_seeded_error(
    client: TestClient, db_session: Session, roles, seeded_error: dict
) -> None:
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    login(client, "anwalt@kanzlei.test")

    response = client.get("/dashboard/errors")
    assert "ocr" in response.text
    assert "Tesseract nicht gefunden" in response.text


def test_intake_file_path_never_leaks_into_errors_page(
    client: TestClient, db_session: Session, roles
) -> None:
    """Sicherheitsregression (real gefundene Luecke): `entity_id`/
    `error_message` sind bei `entity_type="IntakeFile"` der volle
    Quelldateipfad im ueberwachten Scan-Ordner und koennen daher einen
    echten Mandantennamen tragen (Dateien werden in der Praxis oft nach
    dem Mandanten benannt). Diese Seite ist fuer ALLE angemeldeten Rollen
    sichtbar (siehe Moduldocstring), unabhaengig von Aktenzuordnung - ein
    Mandantenname im Dateipfad darf hier daher NIE im Klartext auftauchen,
    auch nicht in Tooltip/title-Attribut (nur CSS-Ellipsis wuerde den Text
    NICHT aus dem HTML entfernen)."""
    client_marker = "Mustermann_ABC_987654"
    retry_service = RetryService()
    retry_service.record_failure(
        db_session,
        entity_type="IntakeFile",
        entity_id=f"C:\\Kanzlei\\Scan_Eingang\\{client_marker}_Steuerbescheid.pdf",
        operation="intake",
        error_category="permanent",
        error_message=(
            "Symbolische Verknüpfungen werden aus Sicherheitsgründen nicht erfasst: "
            f"C:\\Kanzlei\\Scan_Eingang\\{client_marker}_Steuerbescheid.pdf"
        ),
    )
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")

    response = client.get("/dashboard/errors")

    assert response.status_code == 200
    assert client_marker not in response.text
    assert "Scan_Eingang" not in response.text
    assert "***" in response.text


@pytest.mark.parametrize("role_name", ["admin", "anwalt", "mitarbeiter"])
def test_all_roles_can_trigger_manual_retry(
    client: TestClient, db_session: Session, roles, seeded_error: dict, role_name: str
) -> None:
    """Bewusste Design-Entscheidung (dokumentiert in
    app/web/errors_router.py): eine fehlgeschlagene OCR/Intake-
    Verarbeitung ist eine operative Wiederherstellungsaktion ohne
    Kostenrisiko - alle drei Rollen dürfen sie auslösen, nicht nur
    Admin/Anwalt wie bei den Claude-kostenpflichtigen Aktionen."""
    create_test_user(db_session, roles[role_name], f"{role_name}@kanzlei.test")
    login(client, f"{role_name}@kanzlei.test")

    page = client.get("/dashboard/errors")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/errors/{seeded_error['error_id']}/retry",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


# ==========================================================================
# CSRF-Schutz
# ==========================================================================


def test_retry_without_csrf_token_is_rejected(
    client: TestClient, db_session: Session, roles, seeded_error: dict
) -> None:
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    login(client, "anwalt@kanzlei.test")

    response = client.post(f"/dashboard/errors/{seeded_error['error_id']}/retry", data={})
    assert response.status_code == 422  # csrf_token-Formularfeld fehlt komplett


def test_retry_with_wrong_csrf_token_is_rejected(
    client: TestClient, db_session: Session, roles, seeded_error: dict
) -> None:
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    login(client, "anwalt@kanzlei.test")

    response = client.post(
        f"/dashboard/errors/{seeded_error['error_id']}/retry",
        data={"csrf_token": "falsches-token"},
    )
    assert response.status_code == 403


# ==========================================================================
# Erfolgreiche manuelle Wiederholung (End-to-End über die HTTP-Schicht)
# ==========================================================================


def test_manual_retry_resolves_error_when_underlying_problem_is_fixed(
    client: TestClient, db_session: Session, roles, seeded_error: dict
) -> None:
    """Simuliert: der Anwalt hat das zugrunde liegende Problem behoben
    (hier: Dokument bekommt gültigen, direkt extrahierbaren Text-Pfad),
    danach löst 'Jetzt erneut versuchen' tatsächlich eine Auflösung aus."""
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    login(client, "anwalt@kanzlei.test")

    document = db_session.get(Document, seeded_error["document_id"])
    import tempfile
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp())
    real_file = tmp_dir / "reparierter_scan.txt"
    real_file.write_text("Testinhalt nach Reparatur.")
    document.file_path = str(real_file)
    document.original_filename = "reparierter_scan.txt"
    db_session.commit()

    page = client.get("/dashboard/errors")
    csrf = extract_csrf(page.text)
    client.post(
        f"/dashboard/errors/{seeded_error['error_id']}/retry",
        data={"csrf_token": csrf},
    )

    db_session.expire_all()
    error = db_session.get(ProcessingError, seeded_error["error_id"])
    assert error.status == "resolved"


def test_retry_not_found_returns_404(
    client: TestClient, db_session: Session, roles, seeded_error: dict
) -> None:
    """seeded_error sorgt dafür, dass die Seite ein gültiges CSRF-Token
    rendert (ohne Eintrag gäbe es kein Formular und damit keinen echten
    Token zu extrahieren) - der eigentliche Test betrifft eine ANDERE,
    nicht existierende ID."""
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    login(client, "anwalt@kanzlei.test")
    page = client.get("/dashboard/errors")
    csrf = extract_csrf(page.text)
    response = client.post(
        "/dashboard/errors/does-not-exist/retry", data={"csrf_token": csrf}
    )
    assert response.status_code == 404


# ==========================================================================
# Nicht authentifizierter Zugriff wird abgewiesen
# ==========================================================================


def test_unauthenticated_cannot_view_errors_list(client: TestClient) -> None:
    response = client.get("/dashboard/errors", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_unauthenticated_cannot_trigger_retry(
    client: TestClient, seeded_error: dict
) -> None:
    response = client.post(
        f"/dashboard/errors/{seeded_error['error_id']}/retry",
        data={"csrf_token": "irrelevant"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]
