"""Tests für app/web/document_actions_router.py (18.09., Owner-Direktive
"WEITERARBEITEN" §5/§6 "fehlende Bearbeiten-Aktionen implementieren").

Gleiches Testmuster wie tests/test_web_parties.py: In-Memory-SQLite über
app.dependency_overrides."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import AuditEvent, Client, Document, Matter, User
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
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _matter(db: Session, *, title: str = "Testakte") -> Matter:
    client_row = Client(name="Max Mustermann", client_number=f"K-{uuid4().hex[:8]}")
    matter = Matter(client=client_row, title=title)
    db.add_all([client_row, matter])
    db.commit()
    db.refresh(matter)
    return matter


def _document(db: Session, matter: Matter, *, original_filename: str = "Alt.pdf") -> Document:
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/doc.pdf",
        original_filename=original_filename,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _csrf(client: TestClient, matter_id: str, document_id: str) -> str:
    page = client.get(f"/dashboard/matters/{matter_id}/document/{document_id}")
    return extract_csrf(page.text)


def test_document_view_shows_rename_form(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter, original_filename="Steuerbescheid.pdf")

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "Umbenennen" in response.text
    assert 'name="original_filename"' in response.text


def test_rename_document_persists_new_name(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter, original_filename="Alt.pdf")
    csrf = _csrf(client, matter.id, document.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/rename",
        data={"csrf_token": csrf, "original_filename": "Steuerbescheid_2025_final.pdf"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/dashboard/matters/{matter.id}/document/{document.id}"
    )
    db_session.refresh(document)
    assert document.original_filename == "Steuerbescheid_2025_final.pdf"

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Document", entity_id=document.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "document_renamed"
    assert "Alt.pdf" in event.details
    assert "Steuerbescheid_2025_final.pdf" in event.details


def test_rename_document_with_blank_name_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter, original_filename="Alt.pdf")
    csrf = _csrf(client, matter.id, document.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/rename",
        data={"csrf_token": csrf, "original_filename": "   "},
    )

    assert response.status_code == 400
    db_session.refresh(document)
    assert document.original_filename == "Alt.pdf"


def test_rename_document_respects_matter_isolation(
    client: TestClient, db_session: Session
) -> None:
    """Ein Dokument einer FREMDEN Akte darf ueber die URL einer anderen
    Akte nicht umbenennbar sein - Aktenisolation (CLAUDE.md)."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B")
    document_of_b = _document(db_session, matter_b, original_filename="GehoertZuB.pdf")
    csrf = _csrf(client, matter_b.id, document_of_b.id)

    response = client.post(
        f"/dashboard/matters/{matter_a.id}/document/{document_of_b.id}/rename",
        data={"csrf_token": csrf, "original_filename": "Umbenannt.pdf"},
    )

    assert response.status_code == 404
    db_session.refresh(document_of_b)
    assert document_of_b.original_filename == "GehoertZuB.pdf"


def test_rename_document_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter, original_filename="Alt.pdf")

    response = client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/rename",
        data={"csrf_token": "invalid", "original_filename": "Neu.pdf"},
    )

    assert response.status_code == 403
    db_session.refresh(document)
    assert document.original_filename == "Alt.pdf"


def test_rename_document_404s_for_unknown_matter(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter, original_filename="Alt.pdf")
    csrf = _csrf(client, matter.id, document.id)

    response = client.post(
        f"/dashboard/matters/does-not-exist/document/{document.id}/rename",
        data={"csrf_token": csrf, "original_filename": "Neu.pdf"},
    )

    assert response.status_code == 404


def test_matter_detail_page_shows_upload_button(client: TestClient, db_session: Session) -> None:
    """ECHTER FUND (18.09., Referenzabgleich Akte-Dokumente-Ansichten): es
    gab projektweit keinen direkten Upload-Weg zu einer bereits
    bestehenden Akte."""
    matter = _matter(db_session)

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert response.status_code == 200
    assert "Dokument hochladen" in response.text
    assert f'action="/dashboard/matters/{matter.id}/documents/upload"' in response.text


def test_upload_document_persists_it_with_matter_isolation(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    page = client.get(f"/dashboard/matters/{matter.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/matters/{matter.id}/documents/upload",
        data={"csrf_token": csrf},
        files={"documents": ("beleg.pdf", b"%PDF-1.4 kein echtes PDF, nur Testinhalt", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/matters/{matter.id}"
    document = db_session.query(Document).filter_by(matter_id=matter.id).first()
    assert document is not None
    assert document.original_filename == "beleg.pdf"

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Document", entity_id=document.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "document_uploaded_to_matter"


def test_upload_multiple_documents_creates_multiple_rows(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    page = client.get(f"/dashboard/matters/{matter.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/matters/{matter.id}/documents/upload",
        data={"csrf_token": csrf},
        files=[
            ("documents", ("erste.pdf", b"%PDF-1.4 eins", "application/pdf")),
            ("documents", ("zweite.pdf", b"%PDF-1.4 zwei", "application/pdf")),
        ],
        follow_redirects=False,
    )

    assert response.status_code == 303
    names = {
        d.original_filename
        for d in db_session.query(Document).filter_by(matter_id=matter.id).all()
    }
    assert names == {"erste.pdf", "zweite.pdf"}


def test_upload_document_rejects_oversized_file(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    page = client.get(f"/dashboard/matters/{matter.id}")
    csrf = extract_csrf(page.text)
    too_big = b"x" * (25 * 1024 * 1024 + 1)

    response = client.post(
        f"/dashboard/matters/{matter.id}/documents/upload",
        data={"csrf_token": csrf},
        files={"documents": ("riesig.pdf", too_big, "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(Document).filter_by(matter_id=matter.id).count() == 0


def test_upload_document_sanitizes_path_traversal_filename(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    page = client.get(f"/dashboard/matters/{matter.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        f"/dashboard/matters/{matter.id}/documents/upload",
        data={"csrf_token": csrf},
        files={"documents": ("../../../evil_marker.pdf", b"marker", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    document = db_session.query(Document).filter_by(matter_id=matter.id).first()
    assert document is not None
    assert ".." not in Path(document.file_path).parts


def test_upload_document_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)

    response = client.post(
        f"/dashboard/matters/{matter.id}/documents/upload",
        data={"csrf_token": "invalid"},
        files={"documents": ("beleg.pdf", b"%PDF-1.4 x", "application/pdf")},
    )

    assert response.status_code == 403
    assert db_session.query(Document).filter_by(matter_id=matter.id).count() == 0


def test_upload_document_404s_for_unknown_matter(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    page = client.get(f"/dashboard/matters/{matter.id}")
    csrf = extract_csrf(page.text)

    response = client.post(
        "/dashboard/matters/does-not-exist/documents/upload",
        data={"csrf_token": csrf},
        files={"documents": ("beleg.pdf", b"%PDF-1.4 x", "application/pdf")},
    )

    assert response.status_code == 404
