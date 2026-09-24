"""Tests für app/export/pdf_text.py::sanitize_for_base14_font (19.09.,
echter Fund - siehe dortiger Moduldocstring: die PDF-Standard-14-
Schriften rendern Halbgeviertstrich/typografische Anführungszeichen/
Euro-Zeichen als falschen Mittelpunkt-Platzhalter statt eines Fehlers).
"""

from __future__ import annotations

from app.export.pdf_text import sanitize_for_base14_font


def test_replaces_en_dash_with_hyphen() -> None:
    assert sanitize_for_base14_font("Betrag – strittig") == "Betrag - strittig"


def test_replaces_em_dash_with_hyphen() -> None:
    assert sanitize_for_base14_font("Betrag — strittig") == "Betrag - strittig"


def test_replaces_euro_sign_with_eur() -> None:
    assert sanitize_for_base14_font("12.350 €") == "12.350 EUR"


def test_replaces_german_low_quotes() -> None:
    assert sanitize_for_base14_font("der „Bescheid“") == 'der "Bescheid"'


def test_replaces_curly_quotes() -> None:
    assert sanitize_for_base14_font("‘Test’ “Test”") == "'Test' \"Test\""


def test_replaces_ellipsis_and_bullet() -> None:
    assert sanitize_for_base14_font("usw… • Punkt") == "usw... - Punkt"


def test_leaves_umlauts_and_supported_characters_unchanged() -> None:
    """Umlaute/ß/§/°/© rendern mit den Standard-14-Schriften nachweislich
    korrekt (real per gerendertem Pixmap bestätigt) - dürfen deshalb NICHT
    unnötig ersetzt werden."""
    text = "Äußerst wichtiger Absatz § 286 BGB, 20° Celsius, © Lexono"
    assert sanitize_for_base14_font(text) == text


def test_leaves_plain_ascii_text_unchanged() -> None:
    text = "Sehr geehrte Damen und Herren, mit freundlichen Gruessen."
    assert sanitize_for_base14_font(text) == text


def test_empty_string_stays_empty() -> None:
    assert sanitize_for_base14_font("") == ""
