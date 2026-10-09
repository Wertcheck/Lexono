"""Briefkopf/Unterzeichner kommen aus dem Briefkopfprofil, nie vom Modell (app/drafting/firm_placeholders.py)."""

from app.drafting.firm_placeholders import compose_letter_notes, strip_firm_placeholders
from app.drafting.review_notes import REVIEW_NOTES_HEADING, split_review_notes
from app.models import FirmProfile

_LETTER = "[Kanzlei einsetzen]\n\nSehr geehrte Damen und Herren,\n\nMit freundlichen Grüßen\n\n[Unterzeichner einsetzen]"


def _profile(**kwargs) -> FirmProfile:
    return FirmProfile(firm_name=kwargs.pop("firm_name", ""), **kwargs)


def test_strip_removes_model_written_placeholder_lines() -> None:
    assert strip_firm_placeholders(_LETTER) == "Sehr geehrte Damen und Herren,\n\nMit freundlichen Grüßen"
    assert strip_firm_placeholders("") == ""


def test_strip_does_not_touch_other_placeholders() -> None:
    text = "[Datum einsetzen]\n\n[Anschrift einsetzen]"
    assert strip_firm_placeholders(text) == text


_WITH_NOTES = (
    _LETTER
    + f"\n\n{REVIEW_NOTES_HEADING}\n"
    + "- Unterzeichner und Kanzleiname wurden als Einsetz-Hinweise belassen.\n"
    + "- Das Datum fehlt und ist zu ergänzen."
)


def test_notes_do_not_report_letterhead_data_as_missing_when_the_profile_supplies_it() -> None:
    profile = _profile(firm_name="Kanzlei Beispiel", signatory_name="RA Test")
    letter, notes = split_review_notes(compose_letter_notes(_WITH_NOTES, profile))
    assert "einsetzen]" not in letter and "Kanzlei Beispiel" not in letter, "Briefkopf steht nicht im Schreibtext"
    assert "Kanzleiname" not in notes and "Unterzeichner" not in notes
    assert "Datum fehlt" in notes, "unrelated notes stay"


def test_notes_name_what_is_really_missing_in_the_letterhead_profile() -> None:
    letter, notes = split_review_notes(compose_letter_notes(_WITH_NOTES, _profile()))
    assert "einsetzen]" not in letter
    assert "Briefkopf und Unterzeichner sind im gewählten Briefkopfprofil nicht hinterlegt" in notes
    assert "Datum fehlt" in notes


def test_only_the_missing_part_is_reported() -> None:
    profile = _profile(firm_name="Kanzlei Beispiel")  # kein Unterzeichner
    _letter, notes = split_review_notes(compose_letter_notes(_WITH_NOTES, profile))
    assert "Unterzeichner sind im gewählten Briefkopfprofil nicht hinterlegt" in notes
    assert "Briefkopf und" not in notes


def test_notes_block_is_omitted_when_nothing_is_left_to_say() -> None:
    text = _LETTER + f"\n\n{REVIEW_NOTES_HEADING}\n- Der Unterzeichner wurde als Einsetz-Hinweis belassen."
    profile = _profile(firm_name="Kanzlei Beispiel", signatory_name="RA Test")
    assert REVIEW_NOTES_HEADING not in compose_letter_notes(text, profile)
