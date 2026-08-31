"""Web-Layer-Tests für /dashboard/backup (Prompt 35) - NUR Admin."""

from __future__ import annotations

import os
import time
import zipfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import Client, Matter
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
def matter(db_session: Session) -> Matter:
    client_ = Client(name="Testmandant")
    m = Matter(client=client_, title="Testakte", reference_number="2026/0001-ESt")
    db_session.add_all([client_, m])
    db_session.commit()
    return m


# --- Zugriff ---


def test_admin_can_view_backup_page(client: TestClient, db_session: Session, roles) -> None:
    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    response = client.get("/dashboard/backup")
    assert response.status_code == 200
    assert "Backup" in response.text


@pytest.mark.parametrize("role_name", ["anwalt", "mitarbeiter"])
def test_non_admin_roles_denied_view(
    client: TestClient, db_session: Session, roles, role_name: str
) -> None:
    create_test_user(db_session, roles[role_name], f"{role_name}@kanzlei.test")
    login(client, f"{role_name}@kanzlei.test")
    response = client.get("/dashboard/backup")
    assert response.status_code == 403


def test_unauthenticated_denied(client: TestClient) -> None:
    response = client.get("/dashboard/backup", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


# --- Vollständiges Backup ---


def _prepare_backup_environment(tmp_path, monkeypatch) -> None:
    """Der Backup-Router liest `settings.database_url` direkt (nicht die
    In-Memory-Test-DB-Session) - für einen Web-Layer-Test wird daher eine
    ECHTE, kleine SQLite-Datei am konfigurierten Pfad benötigt. Testet
    hier bewusst nur die WEB-Verdrahtung (Route -> Service -> Download) -
    die Kernlogik von BackupService ist bereits in
    tests/test_backup_and_export.py umfassend abgedeckt."""
    import sqlite3

    import app.web.backup_router as backup_module
    from app.config.settings import Settings

    db_path = tmp_path / "test.db"
    sqlite3.connect(str(db_path)).close()

    fake_settings = Settings(
        database_url=f"sqlite:///{db_path}",
        intake_storage_dir=str(tmp_path / "intake"),
        mail_attachment_storage_dir=str(tmp_path / "mail"),
    )
    monkeypatch.setattr(backup_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(backup_module, "_DOWNLOAD_STAGING_DIR", tmp_path / "staging")


def test_admin_can_trigger_full_backup_download(
    client: TestClient, db_session: Session, roles, tmp_path, monkeypatch
) -> None:
    _prepare_backup_environment(tmp_path, monkeypatch)

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    page = client.get("/dashboard/backup")
    csrf = extract_csrf(page.text)

    response = client.post("/dashboard/backup/full", data={"csrf_token": csrf})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


@pytest.mark.parametrize("role_name", ["anwalt", "mitarbeiter"])
def test_non_admin_cannot_trigger_full_backup(
    client: TestClient, db_session: Session, roles, role_name: str
) -> None:
    create_test_user(db_session, roles[role_name], f"{role_name}@kanzlei.test")
    login(client, f"{role_name}@kanzlei.test")
    response = client.post("/dashboard/backup/full", data={"csrf_token": "irrelevant"})
    assert response.status_code == 403


def test_unauthenticated_cannot_trigger_full_backup(client: TestClient) -> None:
    response = client.post(
        "/dashboard/backup/full", data={"csrf_token": "irrelevant"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_full_backup_download_is_valid_zip(
    client: TestClient, db_session: Session, roles, tmp_path, monkeypatch
) -> None:
    _prepare_backup_environment(tmp_path, monkeypatch)

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    page = client.get("/dashboard/backup")
    csrf = extract_csrf(page.text)
    response = client.post("/dashboard/backup/full", data={"csrf_token": csrf})

    downloaded = tmp_path / "downloaded.zip"
    downloaded.write_bytes(response.content)
    assert zipfile.is_zipfile(downloaded)


# --- Aktenexport ---


def test_admin_can_export_matter(
    client: TestClient, db_session: Session, roles, matter: Matter, tmp_path, monkeypatch
) -> None:
    import app.web.backup_router as backup_module

    monkeypatch.setattr(backup_module, "_DOWNLOAD_STAGING_DIR", tmp_path)

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    page = client.get("/dashboard/backup")
    assert matter.title in page.text
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/backup/matter/{matter.id}", data={"csrf_token": csrf}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


def test_export_unknown_matter_returns_404(
    client: TestClient, db_session: Session, roles, tmp_path, monkeypatch
) -> None:
    import app.web.backup_router as backup_module

    monkeypatch.setattr(backup_module, "_DOWNLOAD_STAGING_DIR", tmp_path)

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    page = client.get("/dashboard/backup")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/backup/matter/does-not-exist", data={"csrf_token": csrf}
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role_name", ["anwalt", "mitarbeiter"])
def test_non_admin_cannot_export_matter(
    client: TestClient, db_session: Session, roles, matter: Matter, role_name: str
) -> None:
    create_test_user(db_session, roles[role_name], f"{role_name}@kanzlei.test")
    login(client, f"{role_name}@kanzlei.test")
    response = client.post(
        f"/dashboard/backup/matter/{matter.id}", data={"csrf_token": "irrelevant"}
    )
    assert response.status_code == 403


def test_backup_without_csrf_token_is_rejected(
    client: TestClient, db_session: Session, roles
) -> None:
    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    response = client.post("/dashboard/backup/full", data={})
    assert response.status_code == 422


# --- Aufräumen temporärer Download-Archive (Pilot-Readiness-Härtung) ---
# Vor dem Fix blieb die ZIP-Datei nach dem Download dauerhaft im Staging-
# Verzeichnis liegen (unpseudonymisierte Mandanteninhalte). Siehe
# app/web/download_staging.py.


def test_full_backup_archive_deleted_after_download(
    client: TestClient, db_session: Session, roles, tmp_path, monkeypatch
) -> None:
    _prepare_backup_environment(tmp_path, monkeypatch)
    staging_dir = tmp_path / "staging"

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    page = client.get("/dashboard/backup")
    csrf = extract_csrf(page.text)

    response = client.post("/dashboard/backup/full", data={"csrf_token": csrf})
    assert response.status_code == 200

    remaining = list(staging_dir.glob("*")) if staging_dir.exists() else []
    assert remaining == [], f"Staging-Verzeichnis nicht aufgeräumt: {remaining}"


def test_matter_export_archive_deleted_after_download(
    client: TestClient, db_session: Session, roles, matter: Matter, tmp_path, monkeypatch
) -> None:
    import app.web.backup_router as backup_module

    monkeypatch.setattr(backup_module, "_DOWNLOAD_STAGING_DIR", tmp_path)

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    page = client.get("/dashboard/backup")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/backup/matter/{matter.id}", data={"csrf_token": csrf}
    )
    assert response.status_code == 200

    remaining = list(tmp_path.glob("*.zip"))
    assert remaining == [], f"Akten-Export-Archiv nicht aufgeräumt: {remaining}"


def test_stale_staging_file_cleaned_up_on_next_backup(
    client: TestClient, db_session: Session, roles, tmp_path, monkeypatch
) -> None:
    """Sicherheitsnetz-Pfad: eine liegen gebliebene alte Datei (z. B. nach
    einem harten Prozessabbruch) wird beim nächsten Backup-Aufruf entfernt,
    auch ohne dass sie selbst heruntergeladen wurde."""
    _prepare_backup_environment(tmp_path, monkeypatch)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    stale_file = staging_dir / "liegen_geblieben.zip"
    stale_file.write_bytes(b"alt")
    old_time = time.time() - 3600  # 1h alt, > 15 Minuten Schwelle
    os.utime(stale_file, (old_time, old_time))

    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")
    page = client.get("/dashboard/backup")
    csrf = extract_csrf(page.text)

    response = client.post("/dashboard/backup/full", data={"csrf_token": csrf})
    assert response.status_code == 200
    assert not stale_file.exists()
