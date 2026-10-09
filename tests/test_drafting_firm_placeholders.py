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
