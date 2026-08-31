"""Tests für app/web/schriftsatz_router.py (20.08.) - der echte
Schriftsatz-Generator (löst den bisherigen Platzhalter unter
`/dashboard/tools/schriftsatz` ab).

Gleiches Testmuster wie tests/test_web_drafts.py: In-Memory-SQLite über
app.dependency_overrides, `get_drafting_service` wird in
app.web.schriftsatz_router direkt gemonkeypatcht (bewusst kein
Depends()-Aufruf im Router, siehe dessen Moduldocstring - ermöglicht,
WritingProviderNotConfiguredError im Routenkörper selbst abzufangen)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.schriftsatz_router as schriftsatz_router_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.db.session import get_db
from app.drafting.service import DraftingService
from app.main import app
from app.models import Client, Document, Draft, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.auth_test_utils import extract_csrf, login_as_admin
from tests.fake_embedding_provider import FakeEmbeddingProvider


class FakeClaudeWritingProvider:
    def __init__(self, response_text: str = "Formulierter Schriftsatz.") -> None:
        self.response_text = response_text

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        return ClaudeWritingResult(text=self.response_text, token_count=10)


def _working_drafting_service() -> DraftingService:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        FakeClaudeWritingProvider(),
        model_name="claude-sonnet-5",
    )


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
def client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        schriftsatz_router_module, "get_drafting_service", _working_drafting_service
    )
    # Uploads landen isoliert unter tmp_path statt im echten
    # Repository-Arbeitsverzeichnis (Default "data/schriftsatz_uploads"
    # relativ zum CWD) - dasselbe Muster wie tests/test_web_backup.py.
    from app.config.settings import Settings

    fake_settings = Settings(schriftsatz_upload_storage_dir=str(tmp_path / "uploads"))
    monkeypatch.setattr(schriftsatz_router_module, "get_settings", lambda: fake_settings)
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _csrf(client: TestClient) -> str:
    page = client.get("/dashboard/tools/schriftsatz")
    return extract_csrf(page.text)


# --- GET Seite ---


def test_generator_page_is_no_longer_a_placeholder(client: TestClient) -> None:
    response = client.get("/dashboard/tools/schriftsatz")
    assert response.status_code == 200
    assert "in Vorbereitung" not in response.text
    assert "Schriftsatz-Generator" in response.text


def test_generator_page_lists_open_matters(client: TestClient, db_session: Session) -> None:
    client_row = Client(name="Testmandant GmbH")
    matter = Matter(client=client_row, title="Bestehende Testakte", status="open")
    db_session.add_all([client_row, matter])
    db_session.commit()

    response = client.get("/dashboard/tools/schriftsatz")

    assert "Bestehende Testakte" in response.text


# --- POST Generierung ---


def test_generate_without_matter_creates_matter_and_redirects_to_draft(
    client: TestClient, db_session: Session
) -> None:
    csrf_token = _csrf(client)

    response = client.post(
        "/dashboard/tools/schriftsatz/generate",
        data={
            "csrf_token": csrf_token,
            "matter_id": "",
            # Bewusst EIN Wort - zwei aufeinanderfolgende grossgeschriebene
            # Woerter wuerden vom SecurityCheckService als moegliche
            # unerkannte Namen/Entitaeten blockiert (siehe
            # app/privacy/security_check.py: _find_possible_unrecognized_names).
            "new_matter_title": "Generatortestakte",
            "new_client_name": "",
            "stil": "",
            "vorlage": "",
            "attorney_anmerkungen": "",
        },
        # Erzwingt multipart/form-data (wie ein echtes Browser-Formular mit
        # <input type="file"> es senden würde) - ohne mindestens einen
        # echten files-Eintrag würde httpx auf urlencoded zurückfallen, das
        # `documents: list[UploadFile] = File(...)` nicht parsen kann.
        files={"documents": ("leer.pdf", b"", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard/drafts/")
    matter = db_session.query(Matter).filter_by(title="Generatortestakte").first()
    assert matter is not None
    draft = db_session.query(Draft).filter_by(matter_id=matter.id).first()
    assert draft is not None
    assert draft.content == "Formulierter Schriftsatz."


def test_generate_with_existing_matter_reuses_it(
    client: TestClient, db_session: Session
) -> None:
    client_row = Client(name="Testmandant")
    matter = Matter(client=client_row, title="Testakte", status="open")
    db_session.add_all([client_row, matter])
    db_session.commit()
    csrf_token = _csrf(client)

    response = client.post(
        "/dashboard/tools/schriftsatz/generate",
        data={
            "csrf_token": csrf_token,
            "matter_id": matter.id,
            "new_matter_title": "",
            "new_client_name": "",
            "stil": "",
            "vorlage": "",
            "attorney_anmerkungen": "",
        },
        files={"documents": ("leer.pdf", b"", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(Matter).count() == 1
    draft = db_session.query(Draft).filter_by(matter_id=matter.id).first()
    assert draft is not None


def test_upload_with_disallowed_extension_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    csrf_token = _csrf(client)

    response = client.post(
        "/dashboard/tools/schriftsatz/generate",
        data={
            "csrf_token": csrf_token,
            "matter_id": "",
            "new_matter_title": "Sollte nicht entstehen",
            "new_client_name": "",
            "stil": "",
            "vorlage": "",
            "attorney_anmerkungen": "",
        },
        files={"documents": ("schadsoftware.exe", b"binary-inhalt", "application/octet-stream")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(Matter).filter_by(title="Sollte nicht entstehen").first() is None


def test_uploaded_pdf_becomes_document_linked_to_matter(
    client: TestClient, db_session: Session
) -> None:
    csrf_token = _csrf(client)

    response = client.post(
        "/dashboard/tools/schriftsatz/generate",
        data={
            "csrf_token": csrf_token,
            "matter_id": "",
            "new_matter_title": "Akte mit Upload",
            "new_client_name": "",
            "stil": "",
            "vorlage": "",
            "attorney_anmerkungen": "",
        },
        files={"documents": ("beleg.pdf", b"%PDF-1.4 kein echtes PDF, nur Testinhalt", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    matter = db_session.query(Matter).filter_by(title="Akte mit Upload").first()
    assert matter is not None
    document = db_session.query(Document).filter_by(matter_id=matter.id).first()
    assert document is not None
    assert document.original_filename == "beleg.pdf"


def test_upload_filename_with_path_traversal_stays_inside_storage_dir(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    """Sicherheitsregression (real verifizierte Luecke, siehe
    _store_uploaded_document): ein Dateiname mit eingebetteten "../"-
    Segmenten (vom Client im Content-Disposition-Header frei waehlbar,
    kein Browser-Zwang) darf NICHT dazu fuehren, dass die Datei ausserhalb
    des konfigurierten Upload-Speicherordners landet."""
    csrf_token = _csrf(client)
    marker = b"PATH-TRAVERSAL-CANARY-SHOULD-STAY-CONTAINED"

    response = client.post(
        "/dashboard/tools/schriftsatz/generate",
        data={
            "csrf_token": csrf_token,
            "matter_id": "",
            "new_matter_title": "Path Traversal Testakte",
            "new_client_name": "",
            "stil": "",
            "vorlage": "",
            "attorney_anmerkungen": "",
        },
        files={"documents": ("../../../evil_marker.pdf", marker, "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    matter = db_session.query(Matter).filter_by(title="Path Traversal Testakte").first()
    assert matter is not None
    document = db_session.query(Document).filter_by(matter_id=matter.id).first()
    assert document is not None

    from pathlib import Path

    stored_path = Path(document.file_path).resolve()
    upload_dir = (tmp_path / "uploads").resolve()
    assert upload_dir in stored_path.parents, (
        f"Datei liegt ausserhalb des Upload-Ordners: {stored_path} (erwartet unter {upload_dir})"
    )
    assert stored_path.read_bytes() == marker

    # Beweis, dass der Canary-Marker NICHT ausserhalb (z. B. direkt unter
    # tmp_path oder im Repository-Arbeitsverzeichnis) gelandet ist.
    escaped_candidates = list(tmp_path.glob("evil_marker.pdf")) + list(
        tmp_path.glob("*evil_marker.pdf")
    )
    escaped_candidates = [p for p in escaped_candidates if upload_dir not in p.resolve().parents]
    assert not escaped_candidates, f"Datei ausserhalb des Upload-Ordners gefunden: {escaped_candidates}"
