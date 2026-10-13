"""Grundbuchangaben, kurze Nummern mit allgemeiner Bezeichnung und Kennnummern nur in frueheren KI-Antworten
(13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §23). Nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.review import PrivacyDecisions, _unlabelled_identifiers, is_identifier_shaped_term


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway, text: str, mode: str = "mask", **kwargs):
    sv = "Akte: Beispielakte\n[Schreiben] " + text
    return sv, gateway.prepare_request(
        purpose="chat_response", sachverhalt=sv, anwaltliche_anmerkungen="Fasse zusammen.", review_mode=mode, **kwargs
    )


@pytest.mark.parametrize(
    "text, masked, readable",
    [
        # vorher: Fail-Closed-Blockade ("Flur 3"/"Blatt 12345" las die NER als Ort)
        ("Eingetragen im Grundbuch von Musterstadt Blatt 12345, Flur 3, Flurstück 1234/567.", ["12345", "1234/567"],
         ["Flur 3", "Grundbuch von Musterstadt Blatt"]),
        ("Grundbuchblatt 4711 des Amtsgerichts Musterstadt.", ["4711"], ["Amtsgerichts"]),
        ("Die Flurstücke 12/3 und 12/4 der Gemarkung wurden geteilt.", ["12/3", "12/4"], ["Flurstücke"]),
        ("Flst. 88 ist mit einer Grunddienstbarkeit belastet; Blatt 12 der Akte.", ["Flst. 88"], ["Blatt 12 der Akte"]),
        # Review 13.10.2026 (vorher im Klartext bzw. Blockade)
        ("Grundbuch Blatt-Nr. 4711 ist betroffen.", ["4711"], []),
        ("Grundbuchblatt-Nr.: 4711 laut Auszug.", ["4711"], []),
        ("Flst.-Nr. 77/1 wurde vermessen.", ["77/1"], []),
        ("Die Flurstücksnummern 120/4, 121 wurden vereinigt.", ["120/4", "121"], []),
        ("Grundbuch des Amtsgerichts Musterstadt von Musterdorf Blatt 998.", ["998"], []),
        ("Flurstücke 1, 2 und 3 der Gemarkung; Anlage 1 und 2.", ["Flurstücke 1, 2 und 3"], ["Anlage 1 und 2"]),
        ("Gemarkung | Flur | Flurstück\nMusterdorf | 3 | 45/1\nMusterdorf | 3 | 46", ["45/1", "| 46"], ["Flur"]),
    ],
)
def test_land_register_numbers_are_masked_without_question_and_context_stays(gateway, text, masked, readable) -> None:
    sv, asked = _prepare(gateway, text, "ask")
    assert asked.allowed and not asked.review_required, [c.term for c in asked.review_candidates]
    payload = asked.payload.anonymisierter_sachverhalt
    for value in masked:
        assert value not in payload
    for value in readable:
        assert value in payload
    assert "[GRUNDBUCH_" in payload
    assert gateway.reconstruct_response(payload, asked.mappings) == sv


def test_same_parcel_gets_the_same_placeholder(gateway) -> None:
    _, result = _prepare(gateway, "Flurstück 45/1 wurde geteilt; aus Flurstück 45/1 entstand Flurstück 45/7.")
    payload = result.payload.anonymisierter_sachverhalt
    first, _, third = (part.split()[0] for part in payload.split("Flurstück ")[1:])
    assert first.startswith("[GRUNDBUCH_") and payload.count(first) == 2 and third != first


@pytest.mark.parametrize(
    "text, found",
    [
        ("unter der Nummer 300188 geführt", ["300188"]),
        ("ID 48213 im System", ["48213"]),
        ("Nr. 77812 der Akte", ["77812"]),
        ("PLZ 80331 München", []),
        ("Nr. 3100 VV RVG", []),
        ("§ 2 Nr. 3 BGB", []),
        ("Seite 12345", []),
        ("Streitwert 50000 EUR", []),
        ("Im Jahr 2026 wurden 12345 Stück geliefert.", []),
    ],
)
def test_short_numbers_only_behind_a_generic_number_label(text, found) -> None:
    assert [value for value, _ in _unlabelled_identifiers(text)] == found
    assert all(is_identifier_shaped_term(value) for value in found)


def test_short_labelled_number_is_asked_and_the_decision_is_stored(gateway) -> None:
    _, asked = _prepare(gateway, "Die Mandantin ist unter der Nummer 300188 geführt.", "ask")
    assert "300188" in [c.term for c in asked.review_candidates]
    _, decided = _prepare(
        gateway, "Die Mandantin ist unter der Nummer 300188 geführt.", "ask",
        privacy_decisions=PrivacyDecisions(personal=frozenset({"300188"})),
    )
    assert decided.allowed and "300188" not in decided.payload.anonymisierter_sachverhalt


HISTORY = [
    "Nutzer: Was steht im Schreiben?",
    "Assistent: Die Kundin ist unter 30018847 registriert, Kennzeichen D-XY 123; Frist 14.11.2026.",
]


@pytest.mark.parametrize("mode", ["ask", "mask"])
def test_identifiers_only_in_earlier_ai_answers_are_masked_without_question(gateway, mode) -> None:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte", anwaltliche_anmerkungen="Und was folgt daraus?",
        gespraechsverlauf=HISTORY, review_mode=mode,
    )
    assert result.allowed and not result.review_required
    history = " ".join(result.payload.anonymisierter_gespraechsverlauf)
    assert "30018847" not in history and "D-XY 123" not in history
    assert "14.11.2026" in history  # Fristdatum bleibt lesbar (Datumspolicy)


def test_history_respects_the_lawyers_not_personal_decision(gateway) -> None:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte", anwaltliche_anmerkungen="Und?",
        gespraechsverlauf=HISTORY, review_mode="ask",
        privacy_decisions=PrivacyDecisions(not_personal=frozenset({"30018847"})),
    )
    assert "30018847" in " ".join(result.payload.anonymisierter_gespraechsverlauf)
