"""Absender nach "<Kommunikationsnomen> von X" (26.10.2026) - reproduzierte Erkennungsluecke und Fehlmaskierung.

Befund 1 (Leck): "Wir bestaetigen den Eingang des Schreibens von König vom 16.03.2024." ging ohne Maskierung und ohne
Rueckfrage hinaus - die Namensrolle nach "von" wurde erkannt, aber das Modell-Probing ergab 0 von 5 (König, Kaiser, Graf,
Bauer, Sommer, Notar ... kennt das Modell als Gattungs-/Titelwoerter). Jetzt: Rueckfrage (Modus "ask") bzw.
vorsichtiges Maskieren (Modus "mask").

Befund 2 (Fehlmaskierung): spaCy las "Schreibens von Fuchs" als EINEN Ort - "Eingang des [ORT_01]" (Nomen und
Praeposition verschluckt), bei "Stein" im Gateway sogar das Datum. Jetzt wird nur der Absender maskiert.

Negativfaelle: Redewendungen, Abstrakta, Zeitangaben, ein Titelwort vor dem eigentlichen Namen, Artikel ("vom König")
und andere Praepositionen loesen keine neue Rueckfrage aus. Ausschliesslich synthetische Saetze; echter
Presidio-/spaCy-Stack."""

from __future__ import annotations

import pytest

from app.privacy.gateway import ClaudePrivacyGateway

SACH = "Akte: Beispielakte\n[Vertrag] "
GAP_WORDS = ["König", "Kaiser", "Graf", "Bauer", "Sommer", "Notar", "Kommissar", "Pfarrer", "Direktor"]


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _ask(gateway, text):
    return gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + text,
                                   anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode="ask")


def _mask(gateway, text):
    return gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + text,
                                   anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode="mask")


def _terms(result) -> list[str]:
    return [c.term for c in result.review_candidates]


@pytest.mark.parametrize("word", GAP_WORDS)
def test_sender_known_as_common_noun_is_asked_about(gateway, word) -> None:
    result = _ask(gateway, f"Wir bestaetigen den Eingang des Schreibens von {word} vom 16.03.2024.")
    assert result.review_required and word in _terms(result)
    candidate = next(c for c in result.review_candidates if c.term == word)
    assert "absender_nach_kommunikationsnomen" in candidate.roles


@pytest.mark.parametrize("word", ["König", "Bauer"])
def test_sender_is_masked_in_fail_closed_mode_and_the_date_stays(gateway, word) -> None:
    result = _mask(gateway, f"Wir bestaetigen den Eingang des Schreibens von {word} vom 16.03.2024.")
    text = result.payload.anonymisierter_sachverhalt
    assert result.allowed and word not in text
    assert "Schreibens von [" in text and "vom 16.03.2024" in text


@pytest.mark.parametrize("phrase", ["Brief von Kaiser", "E-Mail von Graf", "Anruf von Sommer", "Stellungnahme von König"])
def test_other_communication_nouns(gateway, phrase) -> None:
    word = phrase.split()[-1]
    result = _ask(gateway, f"Wir haben den {phrase} am 12.03.2026 erhalten.")
    assert word in _terms(result)


@pytest.mark.parametrize("word", ["Fuchs", "Stein", "Berg"])
def test_location_span_no_longer_swallows_noun_preposition_and_date(gateway, word) -> None:
    result = _mask(gateway, f"Wir bestaetigen den Eingang des Schreibens von {word} vom 16.03.2024.")
    text = result.payload.anonymisierter_sachverhalt
    assert word not in text  # weiterhin geschuetzt
    assert "Eingang des Schreibens von [PERSON_" in text and "vom 16.03.2024." in text
    assert not any("Schreibens" in m.original_value for m in result.mappings)


@pytest.mark.parametrize("sentence", [
    "Das Schreiben von Amts wegen ging am 12.03.2026 ein.",
    "Das Schreiben von Bedeutung liegt der Akte bei.",
    "Der Brief von Montag liegt der Akte bei.",
    "Die Mitteilung von Interesse wurde weitergeleitet.",
    "Wir bestaetigen den Eingang des Schreibens vom König vom 16.03.2024.",
    "Das Gutachten von Seiten der Gegenseite liegt vor.",
])
def test_idioms_time_words_and_articles_raise_no_new_question(gateway, sentence) -> None:
    result = _ask(gateway, sentence)
    assert not ({"Amts", "Bedeutung", "Montag", "Interesse", "König", "Seiten"} & set(_terms(result))), _terms(result)


def test_title_word_before_the_actual_name_is_not_asked_about(gateway) -> None:
    result = _mask(gateway, "Wir bestaetigen den Eingang des Schreibens von Notar Weber vom 16.03.2024.")
    text = result.payload.anonymisierter_sachverhalt
    assert "Weber" not in text  # der eigentliche Name ist geschuetzt
    ask = _ask(gateway, "Wir bestaetigen den Eingang des Schreibens von Notar Weber vom 16.03.2024.")
    assert "Notar" not in _terms(ask)


def test_other_prepositions_are_unchanged(gateway) -> None:
    """Die neue Regel gilt nur nach einem Kommunikationsnomen: "König von Bayern" bleibt wie bisher (keine Rueckfrage)."""
    result = _ask(gateway, "Ludwig II. war König von Bayern; das Thema spielt fuer den Vertrag keine Rolle.")
    assert "Bayern" not in _terms(result)
