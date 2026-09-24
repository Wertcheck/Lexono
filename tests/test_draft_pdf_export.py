"""Tests für app/export/pdf_export_service.py und die zugehörige Route
GET /dashboard/drafts/{draft_id}/export.pdf (18.09., Owner-Direktive
"WEITERARBEITEN" - siehe app/export/pdf_export_service.py-Moduldocstring).

Gleiches Testmuster wie tests/test_draft_docx_export.py (DOCX-Pendant) und
tests/test_document_generator_exports.py (PyMuPDF-Lesegegenprobe)."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.export.pdf_export_service import PDF_MEDIA_TYPE, DraftPdfExportService
from app.main import app
from app.models import AuditEvent, Client, Draft, FirmProfile, Matter
from app.models.base import Base
from tests.auth_test_utils import login_as_admin

# Kleinstmögliches valides PNG (1x1, transparent) - identisch zum DOCX-Test.
_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _write_tiny_png(path: Path) -> str:
    path.write_bytes(base64.b64decode(_TINY_PNG_BASE64))
    return str(path)


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


def _seed_draft(db: Session) -> Draft:
    client = Client(name="Testmandant GmbH")
    matter = Matter(client=client, title="Einspruch Steuerbescheid 2025")
    db.add_all([client, matter])
    db.commit()
    draft = Draft(matter_id=matter.id, content="Erster Absatz.\n\nZweiter Absatz.")
    db.add(draft)
    db.commit()
    return draft


def test_export_service_produces_valid_pdf_with_readable_content(db_session: Session) -> None:
    draft = _seed_draft(db_session)

    buffer = DraftPdfExportService().export_draft(draft, draft.matter)
    content = buffer.read()

    assert content[:5] == b"%PDF-"
    pdf = pymupdf.open(stream=content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert "Einspruch Steuerbescheid 2025" in full_text
    assert "Erster Absatz." in full_text
    assert "Zweiter Absatz." in full_text


def test_export_service_does_not_corrupt_typographic_characters(db_session: Session) -> None:
    """ECHTER FUND (19.09., Owner-Direktive "REAL DOCUMENT SYSTEM"): die
    PDF-Standard-14-Schrift ("helv") ersetzt Halbgeviertstrich/
    Anführungszeichen/Euro-Zeichen kommentarlos durch einen falschen
    Mittelpunkt-Platzhalter (·) statt eines Fehlers - real per gerendertem
    Pixmap bestätigt, nicht nur vermutet. Für ein deutsches
    Kanzleischreiben mit Euro-Beträgen ein echter Korruptionsfehler.
    `sanitize_for_base14_font` (app/export/pdf_text.py) ersetzt die
    betroffenen Zeichen durch bedeutungsgleiche Alternativen, BEVOR sie
    geschrieben werden - dieser Test prüft das Endergebnis (den
    extrahierten Text), nicht nur die Sanitizer-Funktion isoliert."""
    client = Client(name="Testmandant GmbH")
    matter = Matter(client=client, title="Einspruch Steuerbescheid 2025")
    db_session.add_all([client, matter])
    db_session.commit()
    draft = Draft(
        matter_id=matter.id,
        content=(
            "Der Betrag von 12.350 € ist strittig – bitte prüfen Sie den "
            "„Bescheid“ erneut."
        ),
    )
    db_session.add(draft)
    db_session.commit()

    buffer = DraftPdfExportService().export_draft(draft, draft.matter)
    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()

    assert "EUR" in full_text
    assert "12.350" in full_text
    assert "strittig - bitte" in full_text
    assert '"Bescheid"' in full_text
    # Kein stiller Mittelpunkt-Ersatz an den betroffenen Stellen:
    assert "12.350 ·" not in full_text
    assert "strittig · bitte" not in full_text


def test_export_service_handles_a_draft_with_no_matter_gracefully(db_session: Session) -> None:
    """Identische Degradierung wie beim DOCX-Export (17.09.-Fund) - siehe
    app/export/docx_export_service.py fuer die volle Begruendung."""
    client = Client(name="Testmandant GmbH")
    matter = Matter(client=client, title="Wird gleich wieder entfernt")
    db_session.add_all([client, matter])
    db_session.commit()
    draft = Draft(matter_id=matter.id, content="Inhalt ohne Aktenbezug.")
    db_session.add(draft)
    db_session.commit()

    buffer = DraftPdfExportService().export_draft(draft, None)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert "Schriftsatz" in full_text
    assert "Inhalt ohne Aktenbezug." in full_text


def test_export_service_without_firm_profile_has_no_letterhead(db_session: Session) -> None:
    draft = _seed_draft(db_session)

    buffer = DraftPdfExportService().export_draft(draft, draft.matter, firm_profile=None)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert "Kanzlei" not in full_text


def test_export_service_with_empty_firm_profile_has_no_letterhead(db_session: Session) -> None:
    draft = _seed_draft(db_session)
    empty_profile = FirmProfile(firm_name="")

    buffer = DraftPdfExportService().export_draft(draft, draft.matter, empty_profile)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert "Kanzlei" not in full_text


def test_export_service_with_firm_profile_adds_letterhead(db_session: Session) -> None:
    draft = _seed_draft(db_session)
    profile = FirmProfile(
        firm_name="Kanzlei Mustermann Rechtsanwälte",
        street="Musterstraße 12",
        postal_code="10115",
        city="Berlin",
        phone="+49 30 1234567",
        email="kanzlei@beispiel.de",
        website=None,
    )

    buffer = DraftPdfExportService().export_draft(draft, draft.matter, profile)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert "Kanzlei Mustermann Rechtsanwälte" in full_text
    assert "Musterstraße 12, 10115 Berlin" in full_text


def test_export_service_embeds_logo_and_signature_images(
    db_session: Session, tmp_path: Path
) -> None:
    draft = _seed_draft(db_session)
    logo_path = _write_tiny_png(tmp_path / "logo.png")
    signature_path = _write_tiny_png(tmp_path / "signature.png")
    profile = FirmProfile(
        firm_name="Kanzlei Mustermann Rechtsanwälte",
        logo_path=logo_path,
        signature_path=signature_path,
        signatory_name="Rechtsanwältin Anna Muster",
    )

    buffer = DraftPdfExportService().export_draft(draft, draft.matter, profile)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    total_images = sum(len(page.get_images()) for page in pdf)
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert total_images == 2
    assert "Rechtsanwältin Anna Muster" in full_text


def test_export_service_logo_does_not_overlap_the_firm_name_line(
    db_session: Session, tmp_path: Path
) -> None:
    """ECHTER FUND (20.09., beim realen Export-Test mit einem echten
    Logo-Bild sichtbar, siehe OPEN_ISSUES.md): `state["y"]` ist die
    BASELINE der folgenden Textzeile, nicht deren obere Kante - bei zu
    knappem Abstand ragte der Aufstrich der Kanzleiname-Zeile sichtbar in
    die untere Kante der Logo-Box hinein. Prüft geometrisch (per
    PyMuPDF-Bounding-Boxen), dass die Logo-Bildfläche und die
    Kanzleiname-Textzeile sich NICHT überlappen."""
    draft = _seed_draft(db_session)
    logo_path = _write_tiny_png(tmp_path / "logo.png")
    profile = FirmProfile(
        firm_name="Kanzlei Mustermann Rechtsanwälte",
        logo_path=logo_path,
    )

    buffer = DraftPdfExportService().export_draft(draft, draft.matter, profile)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    page = pdf[0]
    image_rects = [r for img in page.get_images() for r in page.get_image_rects(img[0])]
    assert image_rects, "Logo-Bild wurde nicht in die Seite eingefügt"
    logo_bottom = max(r.y1 for r in image_rects)

    text_dict = page.get_text("dict")
    firm_name_top = None
    for block in text_dict["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if "Kanzlei Mustermann" in span["text"]:
                    firm_name_top = span["bbox"][1]
    pdf.close()

    assert firm_name_top is not None, "Kanzleiname-Zeile nicht gefunden"
    assert firm_name_top >= logo_bottom, (
        f"Kanzleiname-Zeile (top={firm_name_top}) überlappt die Logo-Box "
        f"(bottom={logo_bottom})"
    )


def test_export_service_skips_missing_image_files_gracefully(db_session: Session) -> None:
    """Ein in der DB referenzierter, aber von der Platte verschwundener
    Bildpfad darf den Export nicht zum Absturz bringen - identischer Fund
    wie beim DOCX-Export."""
    draft = _seed_draft(db_session)
    profile = FirmProfile(
        firm_name="Kanzlei Mustermann Rechtsanwälte",
        logo_path="/nicht/vorhanden/logo.png",
        signature_path="/nicht/vorhanden/signatur.png",
        signatory_name="Rechtsanwältin Anna Muster",
    )

    buffer = DraftPdfExportService().export_draft(draft, draft.matter, profile)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    total_images = sum(len(page.get_images()) for page in pdf)
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert total_images == 0
    assert "Rechtsanwältin Anna Muster" in full_text


def test_export_service_paginates_long_content(db_session: Session) -> None:
    """Deterministische Paginierung darf bei sehr langem Text nicht
    haengen/abstuerzen - identisches Muster wie
    GeneratedDocumentPdfExportService (siehe dortiger Moduldocstring)."""
    client = Client(name="Testmandant GmbH")
    matter = Matter(client=client, title="Lange Akte")
    db_session.add_all([client, matter])
    db_session.commit()
    long_content = "\n\n".join(
        f"Absatz Nummer {i} mit etwas Text zum Füllen der Seite." * 3 for i in range(60)
    )
    draft = Draft(matter_id=matter.id, content=long_content)
    db_session.add(draft)
    db_session.commit()

    buffer = DraftPdfExportService().export_draft(draft, matter)

    pdf = pymupdf.open(stream=buffer.read(), filetype="pdf")
    assert pdf.page_count > 1
    pdf.close()


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


def test_export_route_returns_pdf(client: TestClient, db_session: Session) -> None:
    draft = _seed_draft(db_session)

    response = client.get(f"/dashboard/drafts/{draft.id}/export.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == PDF_MEDIA_TYPE
    assert "attachment" in response.headers["content-disposition"]
    pdf = pymupdf.open(stream=response.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert "Erster Absatz." in full_text


def test_export_route_handles_a_draft_with_no_matter_gracefully(
    client: TestClient, db_session: Session
) -> None:
    draft = _seed_draft(db_session)
    matter_id = draft.matter_id
    db_session.query(Matter).filter_by(id=matter_id).delete()
    db_session.commit()
    db_session.refresh(draft)
    assert draft.matter is None

    response = client.get(f"/dashboard/drafts/{draft.id}/export.pdf")

    assert response.status_code == 200
    assert 'filename="Schriftsatz_v' in response.headers["content-disposition"]


def test_export_route_filename_uses_hyphen_not_dropped_en_dash(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (19.09., Live-Test gegen die echte Produktions-DB): ein
    Aktentitel mit Halbgeviertstrich (z. B. "Einspruch Steuerbescheid 2025
    – sabine", real vorhanden) fuehrte zu einem Dateinamen mit haesslichem
    doppeltem Leerzeichen ("...2025  sabine_v1.pdf"), weil das Zeichen von
    der alten Filter-Logik stillschweigend GELOESCHT statt ersetzt wurde
    (`c.isalnum() or c in " -_"` enthaelt nur den gewoehnlichen
    ASCII-Bindestrich, nicht den Halbgeviertstrich). `safe_download_filename`
    (app/export/filenames.py) ersetzt es jetzt durch einen Bindestrich."""
    client_ = Client(name="Testmandant GmbH")
    matter = Matter(client=client_, title="Einspruch Steuerbescheid 2025 – sabine")
    db_session.add_all([client_, matter])
    db_session.commit()
    draft = Draft(matter_id=matter.id, content="Inhalt.")
    db_session.add(draft)
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{draft.id}/export.pdf")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "sabine  " not in disposition and "  sabine" not in disposition
    assert "2025 - sabine" in disposition


def test_export_route_logs_audit_event(client: TestClient, db_session: Session) -> None:
    draft = _seed_draft(db_session)

    client.get(f"/dashboard/drafts/{draft.id}/export.pdf")

    events = (
        db_session.query(AuditEvent)
        .filter_by(entity_id=draft.id, event_type="draft_exported_pdf")
        .all()
    )
    assert len(events) == 1


def test_draft_detail_page_shows_pdf_export_link(client: TestClient, db_session: Session) -> None:
    draft = _seed_draft(db_session)

    response = client.get(f"/dashboard/drafts/{draft.id}")

    assert response.status_code == 200
    assert f'href="/dashboard/drafts/{draft.id}/export.pdf"' in response.text
    assert "Als PDF exportieren" in response.text
