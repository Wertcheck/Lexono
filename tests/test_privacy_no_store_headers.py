"""Kein Browser-/WebView2-Cache fuer Akten-, Dokument- und Chatinhalte (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §28).
Nur synthetische Daten.

Reproduziert: Antworten trugen kein Cache-Control; der Download sendete ETag/Last-Modified. Im installierten Build lieferte
die WebView2 eine zuvor geladene Datei nach dem Loeschen der Akte weiter aus dem Cache (§27.6). Statische Dateien bleiben
cachebar."""

from __future__ import annotations

from collections.abc import Iterator

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import Client, Document, Draft, Matter
from app.models.base import Base
from tests.auth_test_utils import login_as_admin


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        test_client = TestClient(app)
        login_as_admin(db, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def ids(db: Session, tmp_path) -> dict[str, str]:
    pdf = tmp_path / "synthetisch.pdf"
    document_pdf = fitz.open()
    document_pdf.new_page().insert_text((72, 72), "Synthetisch Zebra")
    document_pdf.save(str(pdf))
    mandant = Client(name="Synthetika Zebrowski")
    matter = Matter(client=mandant, title="Zebrafall", reference_number="ZB-1")
    db.add_all([mandant, matter])
    db.flush()
    document = Document(matter_id=matter.id, file_path=str(pdf), original_filename="synthetisch.pdf",
                        extracted_text="Synthetisch Zebra", mime_type="application/pdf")
    draft = Draft(matter_id=matter.id, content="Entwurf synthetisch", version=1, status="draft")
    db.add_all([document, draft])
    db.commit()
    return {"m": matter.id, "d": document.id, "dr": draft.id}


@pytest.mark.parametrize(
    "url",
    [
        "/dashboard/matters/{m}",
        "/dashboard/matters/{m}/document/{d}",
        "/dashboard/matters/{m}/document/{d}/page/1.png",
        "/dashboard/matters/{m}/document/{d}/download",
        "/dashboard/chat",
        "/dashboard/drafts",
        "/dashboard/drafts/{dr}",
        "/dashboard/drafts/{dr}/export.pdf",
        "/api/matters/{m}",
    ],
)
def test_sensitive_responses_are_not_cacheable(client: TestClient, ids: dict[str, str], url: str) -> None:
    response = client.get(url.format(**ids), follow_redirects=False)
    assert response.status_code == 200, response.status_code
    assert response.headers.get("cache-control") == "no-store"
    assert "etag" not in response.headers and "last-modified" not in response.headers


def test_login_page_and_redirects_are_not_cacheable_either() -> None:
    response = TestClient(app).get("/dashboard/chat", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert response.headers.get("cache-control") == "no-store"


def test_static_assets_stay_cacheable() -> None:
    response = TestClient(app).get("/dashboard/static/css/app.css")
    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-store"
    assert "etag" in response.headers  # unveraendert von StaticFiles
