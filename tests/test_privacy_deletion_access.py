"""Datenschutz-Pruefung §27: eine soft-geloeschte Akte (`Matter.deleted_at`)
verhaelt sich wie ein geloeschtes Dokument (404) - auch fuer ihre
Dokumente (Viewer/Seitenbild/Download), die globale Suche und die REST-API.

Ausschliesslich synthetische Testdaten, In-Memory-SQLite, tmp-Dateien.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import Client, Document, Draft, Matter, Message
from app.models.base import Base
from app.search.global_search_service import GlobalSearchService
from tests.auth_test_utils import login_as_admin


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def _seed(db: Session, tmp_path, *, deleted: bool) -> dict[str, str]:
    mandant = Client(name="Synthetika Zebrowski")
    matter = Matter(client=mandant, title="Zebrafall Synthetisch", reference_number="ZB-77")
    db.add_all([mandant, matter])
    db.flush()
    datei = tmp_path / "zebra_vertrag.txt"
    datei.write_text("Zebra Inhalt synthetisch", encoding="utf-8")
    document = Document(
        matter_id=matter.id,
        file_path=str(datei),
        original_filename="zebra_vertrag.txt",
        extracted_text="Zebra Inhalt synthetisch",
        mime_type="text/plain",
    )
    message = Message(
        matter_id=matter.id, direction="inbound",
        sender="zebra@example.test", subject="Zebra Betreff",
    )
    draft = Draft(matter_id=matter.id, content="Zebra Entwurf geheim", version=1, status="draft")
    db.add_all([document, message, draft])
    if deleted:
        matter.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"matter": matter.id, "document": document.id, "draft": draft.id}


# --- Dokumentrouten der Akte -------------------------------------------


@pytest.mark.parametrize(
    "suffix", ["", "/download", "/page/1.png"],
)
def test_document_routes_404_for_deleted_matter(client, db, tmp_path, suffix):
    ids = _seed(db, tmp_path, deleted=True)
    response = client.get(f"/dashboard/matters/{ids['matter']}/document/{ids['document']}{suffix}")
    assert response.status_code == 404
    assert b"Zebra Inhalt" not in response.content


def test_document_routes_still_work_for_active_matter(client, db, tmp_path):
    ids = _seed(db, tmp_path, deleted=False)
    base = f"/dashboard/matters/{ids['matter']}/document/{ids['document']}"
    assert client.get(base).status_code == 200
    download = client.get(f"{base}/download")
    assert download.status_code == 200
    assert b"Zebra Inhalt" in download.content


# --- Globale Suche -----------------------------------------------------


def test_global_search_excludes_children_of_deleted_matter(db, tmp_path):
    _seed(db, tmp_path, deleted=True)
    results = GlobalSearchService(document_search_service=None)._search_documents("zebra", db, 5)
    results += GlobalSearchService(document_search_service=None)._search_messages("zebra", db, 5)
    texts = " ".join(f"{r.title} {r.subtitle}" for r in results)
    assert "zebra_vertrag.txt" not in texts
    assert "Zebra Betreff" not in texts
    assert "Zebrafall" not in texts


def test_global_search_keeps_active_and_unassigned_messages(db, tmp_path):
    _seed(db, tmp_path, deleted=False)
    db.add(Message(direction="inbound", sender="zebra-neu@example.test", subject="Zebra offen"))
    db.commit()
    service = GlobalSearchService(document_search_service=None)
    doc_titles = [r.title for r in service._search_documents("zebra", db, 5)]
    msg_titles = [r.title for r in service._search_messages("zebra", db, 5)]
    assert "zebra_vertrag.txt" in doc_titles
    assert "Zebra Betreff" in msg_titles
    assert "Zebra offen" in msg_titles


def test_global_search_source_failure_logs_no_search_term(db, caplog):
    class _Failing:
        def search_sources(self, query, db, limit):
            raise RuntimeError(f"Fehler bei {query}")

    # test_migrations.py (Alembic fileConfig) deaktiviert bestehende Logger - hier ausdruecklich wieder einschalten
    search_logger = logging.getLogger("app.search.global_search_service")
    previously_disabled, search_logger.disabled = search_logger.disabled, False
    try:
        with caplog.at_level(logging.WARNING, logger="app.search.global_search_service"):
            GlobalSearchService(_Failing()).search("Zebrowski", db)
    finally:
        search_logger.disabled = previously_disabled
    assert caplog.records
    for record in caplog.records:
        assert "Zebrowski" not in record.getMessage()
        assert record.exc_info is None


# --- REST-API ----------------------------------------------------------


def test_api_hides_deleted_matter_and_its_children(client, db, tmp_path):
    ids = _seed(db, tmp_path, deleted=True)
    assert ids["matter"] not in client.get("/api/matters").text
    assert client.get(f"/api/matters/{ids['matter']}").status_code == 404
    assert ids["document"] not in client.get("/api/documents").text
    assert client.get(f"/api/documents/{ids['document']}").status_code == 404
    drafts_list = client.get("/api/drafts").text
    assert ids["draft"] not in drafts_list
    assert "Zebra Entwurf geheim" not in drafts_list
    draft = client.get(f"/api/drafts/{ids['draft']}")
    assert draft.status_code == 404
    assert "Zebra Entwurf geheim" not in draft.text


def test_api_still_lists_active_matter_and_children(client, db, tmp_path):
    ids = _seed(db, tmp_path, deleted=False)
    assert ids["matter"] in client.get("/api/matters").text
    assert client.get(f"/api/matters/{ids['matter']}").status_code == 200
    assert ids["document"] in client.get("/api/documents").text
    assert client.get(f"/api/documents/{ids['document']}").status_code == 200
    assert ids["draft"] in client.get("/api/drafts").text
    assert client.get(f"/api/drafts/{ids['draft']}").status_code == 200
