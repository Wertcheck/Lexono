"""§72 P2: Verwaltungsgegenstand + Verfahrensnomen ("Visa-Anträge", "Steuer-Bescheide") ist kein Name.

Befund legal_e2e_v1 LE-T004 (F8 "Visa-Anträge erfasst" im Payload unverstaendlich), entwickelt am Dev-Fall LE-D007
("Steuer-Bescheide versandt"). Echter Presidio-/spaCy-Stack und echtes Gateway, nur synthetische Namen. Geprueft wird
beides: der Sachverhaltsfakt bleibt lesbar UND echte Organisations-/Personennamen bleiben geschuetzt - auch dann, wenn
sie mit einem Verfahrensnomen zusammengesetzt sind."""

from __future__ import annotations

import pytest

from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.presidio_ner import detect_presidio_entities, is_admin_procedure_compound


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _outgoing(gateway: ClaudePrivacyGateway, sachverhalt: str, known_entities: dict | None = None) -> str:
    result = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt=sachverhalt, argumentationspunkte=[], quellenverweise=[], stil=None,
        vorlage=None, anwaltliche_anmerkungen="Erstelle ein Schreiben.", known_entities=known_entities,
        gespraechsverlauf=[], skip_general_knowledge_pseudonymization=False,
    )
    assert result.allowed, result.reasons
    return result.payload.anonymisierter_sachverhalt + "\n" + (result.payload.anonymisierte_anwaltliche_anmerkungen or "")


@pytest.mark.parametrize(
    "word",
    ["Visa-Anträge", "Steuer-Bescheide", "Renten-Bescheide", "Pass-Antrag", "Visaanträge", "Steuerbescheide",
     "Bauanträge", "Einbürgerungsanträge", "Asyl-Verfahren"],
)
def test_admin_procedure_compounds_are_recognised(word: str) -> None:
    assert is_admin_procedure_compound(word)


@pytest.mark.parametrize(
    "word",
    ["Siemens-Anträge", "Müller-Anträge", "Ostermann-Holtkamp", "Visa-Anträge GmbH", "Visa Europe", "Visa",
     "Steuer-Beratung-Anträge", "Kostadinova-Bescheide", "Steuerberater", "Lindqvist", "Passau"],
)
def test_names_and_other_words_are_not_admin_procedure_compounds(word: str) -> None:
    assert not is_admin_procedure_compound(word)


@pytest.mark.parametrize(
    "sentence, fact",
    [
        ("Meine Kollegin Mirela Kostadinova kann bestätigen, dass ich nur Visa-Anträge erfasst habe.", "Visa-Anträge erfasst"),
        ("Ich habe nur Steuer-Bescheide versandt, Personalverantwortung hatte ich nie.", "Steuer-Bescheide versandt"),
        ("Sie hat Renten-Bescheide versandt.", "Renten-Bescheide versandt"),
        ("Er bearbeitete Visaanträge.", "Visaanträge"),
        ("Ich habe Visa-Anträge bearbeitet.", "Visa-Anträge bearbeitet"),
    ],
)
def test_decisive_fact_stays_readable_in_the_payload(gateway, sentence: str, fact: str) -> None:
    assert all(fact.split()[0] not in s.value for s in detect_presidio_entities(sentence))
    assert fact in _outgoing(gateway, "Akte: Beispielakte\n[Mail] " + sentence)


@pytest.mark.parametrize(
    "sentence, protected",
    [
        ("Die Ostermann-Holtkamp Personalservice GmbH hat eingeworfen.", "Ostermann-Holtkamp"),
        ("Ich habe für Ostermann-Holtkamp gearbeitet.", "Ostermann-Holtkamp"),
        ("Ich habe Siemens-Anträge bearbeitet.", "Siemens"),
        ("Ich habe Müller-Anträge erfasst.", "Müller"),
        ("Die Visa-Anträge GmbH hat gekündigt.", "Visa-Anträge GmbH"),
        ("Herr Visa hat angerufen.", "Herr Visa"),
        ("Meine Kollegin Mirela Kostadinova kann bestätigen, dass ich nur Visa-Anträge erfasst habe.", "Kostadinova"),
    ],
)
def test_real_names_stay_protected(gateway, sentence: str, protected: str) -> None:
    assert protected not in _outgoing(gateway, "Akte: Beispielakte\n[Mail] " + sentence)


def test_a_known_party_with_a_procedure_word_stays_protected(gateway) -> None:
    """Abgleichsliste wirkt unabhaengig von der NER: eine Beteiligte "Visa-Anträge Service GmbH" bleibt maskiert."""
    payload = _outgoing(gateway, "Akte: Beispielakte\n[Mail] Die Visa-Anträge Service GmbH schreibt uns.",
                        known_entities={"gegner": ["Visa-Anträge Service GmbH"]})
    assert "Visa-Anträge Service" not in payload
