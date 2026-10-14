"""OCR: Paragraphenzeichen, das Tesseract bei schlechten Scans als "$" liest (Legal-E2E-Benchmark §34.12).

Entwickelt an allgemeinen Zitierformen (nicht an den Testfaellen des Benchmarks); der Benchmark misst die Wirkung auf
den schlechten Scans. Nur synthetische Texte."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.documents.ocr import normalize_paragraph_signs


@pytest.mark.parametrize(
    ("ocr", "expected"),
    [
        ("gem. $ 314 Abs. 2 BGB", "gem. § 314 Abs. 2 BGB"),
        ("($ 1 Abs. 2 KSchG)", "(§ 1 Abs. 2 KSchG)"),
        ("$ 634a Abs. 1 Nr. 2 BGB", "§ 634a Abs. 1 Nr. 2 BGB"),
        ("$$ 535, 536 BGB", "§§ 535, 536 BGB"),
        ("nach $ 13 GmbHG", "nach § 13 GmbHG"),
        ("$ 67 Abs. 1 OWiG", "§ 67 Abs. 1 OWiG"),
        ("$5 ZPO", "§5 ZPO"),
    ],
)
def test_dollar_before_a_statute_citation_becomes_paragraph_sign(ocr: str, expected: str) -> None:
    assert normalize_paragraph_signs(ocr) == expected


@pytest.mark.parametrize(
    "text",
    ["Preis $ 500", "USD $ 20 Gebühr", "Kosten $ 20 pro Monat", "$ 20 EUR Aufschlag", "US$ 30 BGB-Kommentar",
     "Variable $HOME", "§ 24 StVG",
     # Review §34.12: kein "§" vor Namen/Firmen/Waehrungen (ein falsches "§" liesse einen Namen als Gesetz durchgehen)
     "$ 500 DeLuca", "$ 10 IBM-Aktien", "$ 50 PayPal", "$ 2 AG", "$ 5 GmbH", "$ 10 CAD", "$ 10 EURO", "$ 3 TV",
     "$ 500 US-Dollar"],
)
def test_dollar_amounts_and_existing_signs_stay_unchanged(text: str) -> None:
    assert normalize_paragraph_signs(text) == text


def test_degraded_scan_keeps_paragraph_citations(tmp_path: Path) -> None:
    """Schlechter Scan (geringe Aufloesung, Schraeglage, Rauschen, JPEG) mit Zitaten - echte OCR."""
    from app.documents.ocr import configure_tesseract, run_ocr
    from scripts.legal_bench import e2e
    from scripts.legal_bench.render import render_degraded_scan

    tesseract = e2e._tesseract()
    if tesseract is None:
        pytest.skip("Tesseract nicht verfuegbar")
    configure_tesseract(tesseract)
    document = {"paragraphs": ["Kündigung gemäß § 626 Abs. 1 BGB, hilfsweise ordentlich nach § 622 BGB.",
                               "Die Kosten trägt die Beklagte (§ 91 ZPO)."]}
    text = run_ocr(render_degraded_scan(document, tmp_path / "scan.pdf"), languages="deu+eng")
    assert "$" not in text
    assert "§ 91 ZPO" in text and "BGB" in text
