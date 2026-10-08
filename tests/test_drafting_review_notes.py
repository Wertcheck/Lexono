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


def test_markers_cut_a_preamble_and_trailing_remarks_out_of_the_letter() -> None:
    """ECHTER FUND (Real-E2E 08.10.): bei einer Ueberarbeitung stand ein Einleitungsabsatz
    im kopierbaren Text."""
    from app.drafting.review_notes import LETTER_END_MARKER, LETTER_START_MARKER

    text = (
        "Da die Frist nur relativ angegeben wird, formuliere ich sie ohne Datum.\n\n"
        f"{LETTER_START_MARKER}\n{_LETTER}\n{LETTER_END_MARKER}\n\n"
        f"Ich habe die Klageandrohung entfernt.\n\n## {REVIEW_NOTES_HEADING}\n\n- Datum pruefen"
    )

    letter, notes = split_review_notes(text)

    assert letter == _LETTER
    assert "Da die Frist nur relativ" in notes
    assert "Ich habe die Klageandrohung entfernt." in notes
    assert "- Datum pruefen" in notes
    assert "===" not in letter and "Datum pruefen" not in letter


def test_markers_without_end_marker_take_everything_after_the_start_marker() -> None:
    from app.drafting.review_notes import LETTER_START_MARKER

    text = f"Vorbemerkung.\n{LETTER_START_MARKER}\n{_LETTER}\n\n## {REVIEW_NOTES_HEADING}\n- Beleg fehlt"

    letter, notes = split_review_notes(text)

    assert letter == _LETTER
    assert notes.startswith("Vorbemerkung.") and "- Beleg fehlt" in notes


def test_text_without_markers_behaves_exactly_as_before_and_strays_are_removed() -> None:
    from app.drafting.review_notes import LETTER_END_MARKER

    assert split_review_notes(_LETTER) == (_LETTER, "")
    letter, _ = split_review_notes(f"{_LETTER}\n{LETTER_END_MARKER}")
    assert LETTER_END_MARKER not in letter


def test_empty_content_between_markers_never_produces_an_empty_letter() -> None:
    from app.drafting.review_notes import LETTER_END_MARKER, LETTER_START_MARKER

    text = f"Nur eine Erlaeuterung.\n{LETTER_START_MARKER}\n{LETTER_END_MARKER}"

    assert split_review_notes(text)[0] == text


def test_heading_directly_after_the_end_marker_is_not_repeated_inside_the_notes() -> None:
    """Befund (Real-E2E 08.10.): die Hinweis-Ueberschrift erschien im Kasten doppelt."""
    from app.drafting.review_notes import LETTER_END_MARKER, LETTER_START_MARKER

    text = (
        f"Vorbemerkung.\n{LETTER_START_MARKER}\n{_LETTER}\n{LETTER_END_MARKER}\n\n"
        f"## {REVIEW_NOTES_HEADING}\n- Datum pruefen"
    )

    letter, notes = split_review_notes(text)

    assert letter == _LETTER
    assert notes == "Vorbemerkung.\n\n- Datum pruefen"
    assert "OFFENE PR" not in notes.upper()
