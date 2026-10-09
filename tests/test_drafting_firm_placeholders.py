"""Kanzleidaten (Briefkopf/Unterzeichner) werden lokal aus dem Kanzlei-Profil eingesetzt."""

from app.drafting.firm_placeholders import fill_firm_placeholders, strip_firm_placeholders
from app.models import FirmProfile

_LETTER = "[Kanzlei einsetzen]\n\nSehr geehrte Damen und Herren,\n\nMit freundlichen Grüßen\n\n[Unterzeichner einsetzen]"


def _profile(**kwargs) -> FirmProfile:
    return FirmProfile(firm_name=kwargs.pop("firm_name", ""), **kwargs)


def test_fills_firm_name_address_and_signatory_from_profile() -> None:
    profile = _profile(
        firm_name="Kanzlei Beispiel", street="Beispielweg 1", postal_code="00000",
        city="Musterstadt", signatory_name="RA Test Beispiel",
    )
    result = fill_firm_placeholders(_LETTER, profile)
    assert result.startswith("Kanzlei Beispiel\nBeispielweg 1, 00000 Musterstadt\n")
    assert result.endswith("RA Test Beispiel")
    assert "einsetzen]" not in result


def test_keeps_placeholders_when_profile_is_empty() -> None:
    assert fill_firm_placeholders(_LETTER, _profile()) == _LETTER
    assert fill_firm_placeholders(_LETTER, None) == _LETTER


def test_does_not_touch_other_placeholders() -> None:
    text = "[Datum einsetzen] [Anschrift einsetzen]"
    assert fill_firm_placeholders(text, _profile(firm_name="X")) == text


def test_strip_removes_placeholder_lines_only_when_profile_supplies_them() -> None:
    both = _profile(firm_name="Kanzlei Beispiel", signatory_name="RA Test")
    stripped = strip_firm_placeholders(_LETTER, both)
    assert stripped == "Sehr geehrte Damen und Herren,\n\nMit freundlichen Grüßen"
    only_firm = strip_firm_placeholders(_LETTER, _profile(firm_name="Kanzlei Beispiel"))
    assert "[Kanzlei einsetzen]" not in only_firm and "[Unterzeichner einsetzen]" in only_firm
    assert strip_firm_placeholders(_LETTER, _profile()) == _LETTER
    assert strip_firm_placeholders(_LETTER, None) == _LETTER


from app.drafting.firm_placeholders import apply_firm_data  # noqa: E402
from app.drafting.review_notes import REVIEW_NOTES_HEADING, split_review_notes  # noqa: E402

_LETTER_WITH_NOTES = (
    _LETTER
    + f"\n\n{REVIEW_NOTES_HEADING}\n"
    + "- Unterzeichner und Kanzleiname wurden als Einsetz-Hinweise belassen.\n"
    + "- Das Datum fehlt und ist zu ergänzen."
)


def test_notes_do_not_report_firm_data_as_missing_when_the_profile_supplies_it() -> None:
    profile = _profile(firm_name="Kanzlei Beispiel", signatory_name="RA Test")
    letter, notes = split_review_notes(apply_firm_data(_LETTER_WITH_NOTES, profile))
    assert "Kanzlei Beispiel" in letter and "RA Test" in letter
    assert "Kanzleiname" not in notes and "Unterzeichner" not in notes
    assert "Datum fehlt" in notes, "unrelated notes stay"


def test_notes_name_what_is_really_missing_when_the_profile_is_empty() -> None:
    letter, notes = split_review_notes(apply_firm_data(_LETTER_WITH_NOTES, _profile()))
    assert "[Kanzlei einsetzen]" in letter
    assert "Briefkopf und Unterzeichner sind noch einzusetzen" in notes
    assert "[Kanzlei" not in notes


def test_notes_block_is_omitted_when_nothing_is_left_to_say() -> None:
    text = _LETTER + f"\n\n{REVIEW_NOTES_HEADING}\n- Der Unterzeichner wurde als Einsetz-Hinweis belassen."
    profile = _profile(firm_name="Kanzlei Beispiel", signatory_name="RA Test")
    result = apply_firm_data(text, profile)
    assert REVIEW_NOTES_HEADING not in result
