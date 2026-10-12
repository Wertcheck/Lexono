"""Platzhalter-Integritaet (Folgezyklus 13.10.2026).

Gemessen vorher (`7cd8145`): ein Dokument mit woertlichem "[PERSON_01]" und dem Namen "Karl Brenner" bekam fuer beide
denselben Platzhalter; die Rueckfuehrung setzte "Karl Brenner" auch an die Stelle des woertlichen Texts (ebenso
[ADRESSE_01], [KENNUNG_01], zwei Dokumente, Lexono-Export). "[PERSON_07]"/"[PERSON_1]" ohne Gegenstueck blockierten.
Fehlerhafte Rueckfuehrungsdaten (Duplikat mit anderem Wert, Verkettung) setzten stillschweigend fremde Werte ein.
Nur synthetische Daten."""

from __future__ import annotations

import re

import pytest

from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.pseudonymizer import PseudonymMapping, Pseudonymizer


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway, text: str):
    return gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + text,
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Herr [PERSON_01] und Herr Karl Brenner haben unterschrieben.",
        "Im Muster steht [PERSON_07] als Lücke für den Namen.",
        "[ADRESSE_01] und Lindenallee 3, 30000 Beispielstadt",
        "[KENNUNG_01] und Steuernummer 143/815/08154",
        "Vorlage: [Person_01] schuldet Herrn Karl Brenner 500 EUR.",
        "Vorlage: [PERSON_1] schuldet Herrn Karl Brenner 500 EUR.",
        "Feld PERSON_01 im Formular; Mandant Karl Brenner.",
        "[Dokument A] Herr [PERSON_01] ist Zeuge.\n[Dokument B] Zeuge ist Herr Tjark Westphal, nicht [PERSON_01].",
        "Auszug aus Lexono-Export: [PERSON_01] wohnt in [ORT_01]; echter Mandant Karl Brenner aus Kiel.",
        "Platzhalter [TEXTMARKE_01] und [TEXTMARKE_02] stehen schon im Text, dazu Herr Karl Brenner.",
    ],
)
def test_literal_placeholders_never_collide_and_round_trip_exactly(gateway, text) -> None:
    result = _prepare(gateway, text)
    assert result.allowed, result.reasons
    payload = result.payload.anonymisierter_sachverhalt
    # jeder Platzhalter in der Payload ist erzeugt und eindeutig einem Wert zugeordnet
    placeholders = {m.placeholder for m in result.mappings}
    assert set(re.findall(r"\[[A-Za-zÄÖÜäöüß_]+_\d+\]", payload)) <= placeholders
    assert len(placeholders) == len(result.mappings)
    assert gateway.reconstruct_response(payload, result.mappings) == "Akte: Beispielakte\n[Schreiben] " + text


@pytest.mark.parametrize(
    "text, leaked",
    [
        # Review: Platzhalter mitten in einem personenbezogenen Wert - der Rest des Werts darf nicht frei werden.
        # (Offen dokumentiert: "Tel. 030 [TELEFON_01] 1234567" erkennt kein Detektor als Rufnummer - auch ohne den
        # woertlichen Platzhalter nicht; auf 7cd8145 blockierte nur das unbekannte Token.)
        ("IBAN DE89 3704 [IBAN_01] 0044 0532 0130 00", ["0532"]),
    ],
)
def test_placeholder_inside_a_value_masks_the_whole_value(gateway, text, leaked) -> None:
    result = _prepare(gateway, text)
    assert result.allowed, result.reasons
    payload = result.payload.anonymisierter_sachverhalt
    for value in leaked:
        assert value not in payload, payload
    assert gateway.reconstruct_response(payload, result.mappings) == "Akte: Beispielakte\n[Schreiben] " + text


def test_literal_case_variants_stay_distinct(gateway) -> None:
    text = "Felder [Person_01] und [PERSON_01] und [person_01] in der Vorlage."
    result = _prepare(gateway, text)
    assert result.allowed, result.reasons
    assert gateway.reconstruct_response(result.payload.anonymisierter_sachverhalt, result.mappings).endswith(text)


def test_response_text_placeholders_are_restored_and_literals_stay_literal(gateway) -> None:
    result = _prepare(gateway, "Herr [PERSON_01] und Herr Karl Brenner haben unterschrieben.")
    by_value = {m.original_value: m.placeholder for m in result.mappings}
    response = f"{by_value['Karl Brenner']} hat unterschrieben; im Text steht außerdem {by_value['[PERSON_01]']}."
    restored = gateway.reconstruct_response(response, result.mappings)
    assert restored == "Karl Brenner hat unterschrieben; im Text steht außerdem [PERSON_01]."


@pytest.mark.parametrize(
    "mappings",
    [
        # dieselbe Kennung mit zwei Werten: nicht raten
        [PseudonymMapping("[PERSON_01]", "person", "Karl Brenner"), PseudonymMapping("[PERSON_01]", "person", "Tjark Westphal")],
        [PseudonymMapping("[PERSON_01]", "person", "")],  # leerer Wert
        [PseudonymMapping("PERSON_01", "person", "Karl Brenner")],  # ungueltige Form
    ],
)
def test_faulty_mappings_are_not_guessed(gateway, mappings) -> None:
    restored = gateway.reconstruct_response("Antwort an [PERSON_01].", mappings)
    assert restored == "Antwort an [Angabe einsetzen]."
    assert "Karl Brenner" not in restored and "Tjark Westphal" not in restored


def test_reconstruction_is_single_pass() -> None:
    mappings = [PseudonymMapping("[PERSON_01]", "person", "[ORT_01]"), PseudonymMapping("[ORT_01]", "ort", "Kiel")]
    assert Pseudonymizer().reconstruct("[PERSON_01] / [ORT_01]", mappings) == "[ORT_01] / Kiel"


def test_restored_originals_are_never_neutralized(gateway) -> None:
    mappings = [PseudonymMapping("[TEXTMARKE_01]", "platzhaltertext", "PERSON_07")]
    assert gateway.reconstruct_response("Feld [TEXTMARKE_01], Rest PERSON_09.", mappings) == (
        "Feld PERSON_07, Rest [Angabe einsetzen]."
    )
