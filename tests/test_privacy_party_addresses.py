"""Zuordnung Beteiligte -> Anschrift aus der Dokumentstruktur (nur Platzhalter, nur Benachbartes)."""

from app.privacy.party_addresses import PARTY_ADDRESS_MARKER, build_party_address_note


def test_adjacent_person_and_address_are_paired() -> None:
    text = "Verkaeufer: [PERSON_01], [ADRESSE_01]  Kaeuferin: [PERSON_02], [ADRESSE_02]  Fahrzeug: Kombi"
    note = build_party_address_note(text)
    assert note is not None and PARTY_ADDRESS_MARKER in note
    assert "[PERSON_01] wohnt/sitzt unter [ADRESSE_01]" in note
    assert "[PERSON_02] wohnt/sitzt unter [ADRESSE_02]" in note


def test_organisation_with_line_break_between_name_and_address() -> None:
    assert "[ORGANISATION_03] wohnt/sitzt unter [ADRESSE_04]" in build_party_address_note(
        "[ORGANISATION_03]  [ADRESSE_04]"
    )


def test_nothing_is_paired_when_the_address_is_not_directly_next_to_the_party() -> None:
    """Keine Anschrift wird einer Person zugeordnet, bei der sie nicht unmittelbar steht."""
    text = "[PERSON_01] erschien am [DATUM_01] und nannte eine Adresse. Spaeter: [ADRESSE_01]"
    assert build_party_address_note(text) is None


def test_note_contains_only_placeholders() -> None:
    note = build_party_address_note("[PERSON_01], [ADRESSE_01]")
    assert "Neumann" not in note and "Lindenallee" not in note


def test_duplicate_pairs_are_listed_once() -> None:
    note = build_party_address_note("[PERSON_01], [ADRESSE_01]  und noch einmal [PERSON_01], [ADRESSE_01]")
    assert note.count("[PERSON_01] wohnt/sitzt unter [ADRESSE_01]") == 1
