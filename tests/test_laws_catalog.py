"""Tests für app/laws/catalog.py (26.09., Owner-Direktive "KANZLEIWISSEN
FINAL PRODUCT IMPLEMENTATION") - der geteilte "Server-Katalog", den sowohl
scripts/import_gesetze_im_internet.py als auch die neue Kanzleiwissen-
Weboberfläche (app/web/knowledge_router.py) verwenden."""

from __future__ import annotations

from app.laws.catalog import CODE_OVERRIDES, KNOWN_TITLES, get_catalog, get_catalog_entry_for_code


def test_get_catalog_returns_one_entry_per_known_title() -> None:
    catalog = get_catalog()
    assert len(catalog) == len(KNOWN_TITLES)


def test_get_catalog_entries_have_non_empty_code_slug_and_title() -> None:
    for entry in get_catalog():
        assert entry.code
        assert entry.slug
        assert entry.title.strip()


def test_get_catalog_applies_code_overrides() -> None:
    catalog = {entry.slug: entry.code for entry in get_catalog()}
    for slug, expected_code in CODE_OVERRIDES.items():
        assert catalog[slug] == expected_code


def test_get_catalog_uses_uppercased_slug_when_no_override_exists() -> None:
    catalog = {entry.slug: entry.code for entry in get_catalog()}
    assert catalog["bgb"] == "BGB"
    assert catalog["stgb"] == "STGB"


def test_get_catalog_entry_for_code_finds_a_known_code() -> None:
    entry = get_catalog_entry_for_code("BGB")
    assert entry is not None
    assert entry.slug == "bgb"
    assert entry.title == "Bürgerliches Gesetzbuch"


def test_get_catalog_entry_for_code_returns_none_for_unknown_code() -> None:
    assert get_catalog_entry_for_code("DEFINITIV_KEIN_GESETZ") is None


def test_catalog_includes_two_new_verified_entries_not_yet_locally_installed() -> None:
    """26.09.: URHG/BDSG wurden real gegen die Live-Quelle verifiziert
    (HTTP 200 auf .../xml.zip) neu ergänzt, damit der "nicht installiert ->
    Download"-Fluss an echten, tatsächlich noch nicht importierten Daten
    geprüft werden kann (siehe app/laws/catalog.py-Moduldocstring)."""
    codes = {entry.code for entry in get_catalog()}
    assert "URHG" in codes
    assert "BDSG" in codes
