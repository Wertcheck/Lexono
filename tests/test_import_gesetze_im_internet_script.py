"""Tests für scripts/import_gesetze_im_internet.py (14.09., SGB-Import) -
prüft NUR die reinen Zuordnungstabellen (`_KNOWN_TITLES`/`_CODE_OVERRIDES`),
KEIN echter Netzwerkzugriff (siehe app/laws/gesetze_im_internet.py für die
bereits getestete Download-/Parsing-Logik selbst)."""

from __future__ import annotations

from scripts.import_gesetze_im_internet import _CODE_OVERRIDES, _KNOWN_TITLES

_SGB_SLUGS = [
    "sgb_1", "sgb_2", "sgb_3", "sgb_4", "sgb_5", "sgb_6", "sgb_7",
    "sgb_8", "sgb_9_2018", "sgb_10", "sgb_11", "sgb_12",
]

_EXPECTED_CODES = [
    "SGBI", "SGBII", "SGBIII", "SGBIV", "SGBV", "SGBVI", "SGBVII",
    "SGBVIII", "SGBIX", "SGBX", "SGBXI", "SGBXII",
]


def test_all_twelve_sgb_slugs_have_a_known_title() -> None:
    for slug in _SGB_SLUGS:
        assert slug in _KNOWN_TITLES, f"Kein Titel fuer Slug {slug!r} hinterlegt"
        assert _KNOWN_TITLES[slug].strip(), f"Leerer Titel fuer Slug {slug!r}"


def test_all_twelve_sgb_slugs_map_to_the_natural_roman_numeral_code() -> None:
    """ECHTER FUND (14.09.): der Slug enthaelt eine arabische Ziffer bzw.
    ein Jahres-Suffix ("sgb_1", "sgb_9_2018", "sgb_10") - `slug.upper()`
    alleine wuerde NIE den vom Chat-Fast-Path erwarteten Code liefern
    (siehe app/chat/service.py::_normalize_law_code, das Gegenstueck bei
    der Nutzereingabe)."""
    for slug, expected_code in zip(_SGB_SLUGS, _EXPECTED_CODES, strict=True):
        assert _CODE_OVERRIDES.get(slug) == expected_code


def test_sgb_codes_contain_no_digits_or_whitespace() -> None:
    """Der gespeicherte `law_code` muss ein reines Buchstaben-Token sein
    (konsistent mit allen anderen Gesetzeskuerzeln wie "BGB"/"StGB") -
    ein Code mit Ziffern/Leerzeichen wuerde vom Chat-Fast-Path-Regex
    (`_NORM_QUESTION_PATTERN` in app/chat/service.py) nie als
    Gesetzeskuerzel-Gruppe erkannt."""
    for code in _EXPECTED_CODES:
        assert code.isalpha()
        assert code == code.upper()
