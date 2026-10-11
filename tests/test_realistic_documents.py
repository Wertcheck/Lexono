"""Realistische synthetische Kanzleidokumente durch den echten Pfad bis zum versiegelten Payload (12.10.2026).

Generator: scripts/make_realistic_privacy_docs.py (Goldstandard), Harness: scripts/bench_realistic_documents.py. Derselbe
Inhalt als DOCX (Kopf-/Fusszeile, Liste, zwei Tabellen), PDF (Umbrueche, Tabellen als positionierter Text) und Scan (OCR).
Ausschliesslich synthetische Daten."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bench_realistic_documents as harness  # noqa: E402
import make_realistic_privacy_docs as generator  # noqa: E402

from app.documents.extraction import extract_text  # noqa: E402


@pytest.fixture(scope="module")
def docs(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("realistic_docs")
    generator.make_docx(folder / "protokoll.docx")
    generator.make_pdf(folder / "protokoll.pdf")
    generator.make_scan(folder / "protokoll.pdf", folder / "protokoll_scan.pdf")
    return folder


def test_docx_extraction_keeps_tables_header_and_footer(docs) -> None:
    """Frueher nur `paragraphs`: Beteiligten-/Rechnungstabelle, Kopf- und Fusszeile fehlten (Inhaltsverlust)."""
    text = extract_text(docs / "protokoll.docx").text
    assert "Zeuge | Lange | Hafenstraße 9, 24105 Kiel" in text
    assert "Estrich | 3 | 1.200,00 EUR" in text
    assert text.splitlines()[0] == generator.HEADER
    assert text.splitlines()[-1].startswith("Bankverbindung: IBAN")


@pytest.mark.parametrize("name", ["protokoll.docx", "protokoll.pdf"])
def test_realistic_document_payload_matches_the_gold_standard(docs, name) -> None:
    result = harness.run(docs / name, generator.GOLD)
    assert result["payload_sent"]
    assert result["extracted_missing"] == []
    assert result["leaks"] == []  # kein personenbezogener Goldwert im Klartext im Payload
    assert result["keep_missing"] == []  # Fachbegriffe und Betraege bleiben lesbar
    assert set(result["questions"]) <= set(generator.GOLD["personal"])  # keine Rueckfrage zu Fachwoertern


@pytest.mark.skipif(not (harness.TESSERACT_DIR / "bin" / "tesseract.exe").is_file(), reason="Tesseract nicht vorhanden")
def test_realistic_scan_payload_matches_the_gold_standard(docs) -> None:
    result = harness.run(docs / "protokoll_scan.pdf", generator.GOLD)
    assert result["leaks"] == []
    assert result["keep_missing"] == []
    assert set(result["questions"]) <= set(generator.GOLD["personal"])
