"""Tests fuer app/drafting/review_notes.py.

Ausschliesslich synthetische Beispieltexte (CLAUDE.md-Pflicht)."""

from app.drafting.review_notes import REVIEW_NOTES_HEADING, split_review_notes

_LETTER = "Sehr geehrte Damen und Herren,\n\nich bitte um Rueckmeldung.\n\nMit freundlichen Gruessen"


def test_splits_letter_and_notes_at_the_fixed_heading() -> None:
    text = f"{_LETTER}\n\n## {REVIEW_NOTES_HEADING}\n\n- Datum pruefen\n- Aktenzeichen fehlt"

    letter, notes = split_review_notes(text)

    assert letter == _LETTER
    assert notes == "- Datum pruefen\n- Aktenzeichen fehlt"
    assert "Prüfpunkt" not in letter and "PRÜFPUNKT" not in letter.upper()


def test_tolerates_markdown_decoration_and_separator_before_heading() -> None:
    text = f"{_LETTER}\n\n---\n\n**{REVIEW_NOTES_HEADING}**\n- Beleg fehlt"

    letter, notes = split_review_notes(text)

    assert letter == _LETTER
    assert notes == "- Beleg fehlt"


def test_text_without_heading_is_returned_unchanged() -> None:
    letter, notes = split_review_notes(_LETTER)

    assert (letter, notes) == (_LETTER, "")


def test_plain_analysis_section_named_offene_pruefpunkte_is_not_split() -> None:
    """Eine Analyse darf einen Abschnitt "Offene Prüfpunkte" im Fliesstext haben
    (dort ist es Inhalt der Antwort, kein Beiwerk zu einem Schreiben)."""
    text = "Analyse des Dokuments\n\nOffene Prüfpunkte\n- Datum unklar"

    assert split_review_notes(text) == (text, "")


def test_heading_without_any_preceding_text_keeps_everything_as_letter() -> None:
    """Nie eine leere Nachricht erzeugen."""
    text = f"## {REVIEW_NOTES_HEADING}\n- nur Hinweise"

    assert split_review_notes(text) == (text, "")


def test_empty_input() -> None:
    assert split_review_notes("") == ("", "")
    assert split_review_notes(None) == ("", "")
