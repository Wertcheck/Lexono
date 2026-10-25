"""Gattungsnomen in ASCII-Umschrift (24.10.2026): "Nach § 1 StVO gilt Ruecksicht." wurde als Person maskiert - das Modell
kennt "Ruecksicht" nicht, die gleichwertige Schreibweise "Rücksicht" schon (und liest sie nie als Person). Die Umschrift
eines Abstraktnomens wird jetzt wie die Umlautform behandelt; Namen in Umschrift bleiben geschuetzt, auch neben
Normzitaten und wenn der Name selbst ein Gattungswort ist ("Fuerst" = "Fürst").

Ausschliesslich synthetische Daten, lokal, kein Cloud-Aufruf."""

from __future__ import annotations

import json

import pytest

from app.privacy import presidio_ner as pn
from app.privacy.gateway import ClaudePrivacyGateway

CONTROL = (
    "Die Nordbauholding GmbH (NBH) legt Einspruch ein; Steuernummer 12/345/67890.\n"
    "Geschaeftsfuehrer: Thomas Berger. Nach § 1 StVO gilt Ruecksicht."
)


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway, text: str, mode: str):
    return gateway.prepare_request(purpose="formulate_draft", sachverhalt=text, known_entities={}, review_mode=mode)


def _outgoing(result) -> str:
    return json.dumps(result.payload.model_dump(), ensure_ascii=False) if result.payload is not None else ""


# --- Befund: Kontrolldokument ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["ask", "mask"])
def test_control_document_keeps_the_term_and_protects_every_identifier(gateway, mode: str) -> None:
    result = _prepare(gateway, CONTROL, mode)
    assert not any(c.term == "Ruecksicht" for c in result.review_candidates or [])
    if mode == "ask" and result.review_required:
        pytest.fail(f"unerwartete Rueckfrage: {[c.term for c in result.review_candidates]}")
    assert result.allowed, result.reasons
    payload = _outgoing(result)
    assert "gilt Ruecksicht" in payload and "§ 1 StVO" in payload
    for value in ("Thomas Berger", "Berger", "Nordbauholding", "NBH", "12/345/67890"):
        assert value not in payload, value
    assert "Ruecksicht" not in [m.original_value for m in result.mappings]


@pytest.mark.parametrize("text", ["Nach § 1 StVO gilt Ruecksicht.", "Nach § 1 StVO gilt Rücksicht."])
def test_both_spellings_are_treated_alike(text: str) -> None:
    word = text.split()[-1].rstrip(".")
    i = text.index(word)
    assert not any(s.start < i + len(word) and i < s.end for s in pn.detect_presidio_entities(text))


@pytest.mark.parametrize(
    "text, word",
    [
        ("Es gilt Ruecksicht.", "Ruecksicht"),
        ("Nach § 7 StVG gilt Gefaehrdung als Haftungsgrund.", "Gefaehrdung"),
        ("Nach § 1 StVO gilt Raeumung.", "Raeumung"),
        ("Gemäß § 823 BGB folgt Ueberweisung.", "Ueberweisung"),
    ],
)
def test_transliterated_abstract_noun_is_not_a_person(text: str, word: str) -> None:
    i = text.index(word)
    assert not any(s.start < i + len(word) and i < s.end for s in pn.detect_presidio_entities(text))


# --- Gegenproben: Namen bleiben geschuetzt -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, name",
    [
        ("Nach § 1 StVO haftet Thomas Berger.", "Berger"),
        ("Gemäß § 823 BGB haftet Herr Fuerst.", "Fuerst"),
        ("Nach § 1 StVO gilt Fuerst.", "Fuerst"),  # "Fürst" ist ein Gattungswort ohne Abstraktsuffix
        ("Anwesend war Fuerst.", "Fuerst"),
        ("Nach § 1 StVO gilt Rueckert.", "Rueckert"),
        ("Herr Poehlmann sagte aus. Nach § 1 StVO gilt Ruecksicht.", "Poehlmann"),
        ("Laut Kuester war die Ampel rot.", "Kuester"),
    ],
)
def test_transliterated_names_near_statute_citations_stay_protected(gateway, text: str, name: str) -> None:
    result = _prepare(gateway, text, "mask")
    assert result.allowed, result.reasons
    assert name not in _outgoing(result)


@pytest.mark.parametrize(
    "text, word",
    [
        ("Herr Raeumung sagte aus.", "Raeumung"),  # Anrede
        ("Zeuge: Raeumung.", "Raeumung"),  # Feldwert hinter Doppelpunkt
        ("Raeumung hat gestern angerufen.", "Raeumung"),  # Subjekt vor finitem Singular-Verb
        ("Die Zahlung erfolgte durch Ueberweisung.", "Ueberweisung"),  # Agens-Praeposition (bekannte Grenze, xfail)
        ("Laut Raeumung war die Ampel rot.", "Raeumung"),  # Quellenangabe
        ("Nach § 1 StVO gilt Fuerst.", "Fuerst"),  # kein Abstraktsuffix
        ("Nach § 1 StVO gilt Mueller.", "Mueller"),  # Umschrift dem Modell bekannt - keine Umlautfassung
    ],
)
def test_rule_needs_every_signal(text: str, word: str) -> None:
    i = text.index(word)
    assert not pn.is_transliterated_common_noun(text, i, i + len(word))


def test_rule_applies_in_the_reported_sentence() -> None:
    text = "Nach § 1 StVO gilt Ruecksicht."
    i = text.index("Ruecksicht")
    assert pn.is_transliterated_common_noun(text, i, i + len("Ruecksicht"))
