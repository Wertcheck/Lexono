"""Gesetzesabkuerzungen in Normzitaten bleiben lesbar (Folgezyklus 13.10.2026, scripts/bench_statute_citations.py).

Befund (vorbestehend): "Der Halter haftet nach § 7 StVG." -> "nach § 7 [PERSON_01]"; 37 von 180 Zitaten mit StVO, StPO,
BetrVG, ArbZG, ... verloren. Echter Presidio-/spaCy-Stack, nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy import presidio_ner as pn
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, text: str) -> str:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode="mask",
    )
    assert result.allowed, result.reasons
    return result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize("code", ["StVG", "StVO", "StPO", "BetrVG", "ArbZG", "BauGB", "BUrlG", "MuSchG", "FeV"])
@pytest.mark.parametrize(
    "frame",
    ["Der Halter haftet nach § 7 {C}.", "Der Anspruch folgt aus § 280 Abs. 1 {C}.", "Gemäß Art. 3 {C} ist das zu prüfen.",
     "Er beruft sich auf §§ 1, 2 {C}."],
)
def test_statute_abbreviation_after_citation_stays_readable(gateway, code, frame) -> None:
    assert code in _payload(gateway, frame.format(C=code))


@pytest.mark.parametrize(
    "text, name",
    [
        ("Herr Wessolowski beruft sich auf § 7 StVG.", "Wessolowski"),
        ("Nach § 7 StVG haftet Herr Sommer.", "Sommer"),
        ("Zeuge McDonald hat ausgesagt.", "McDonald"),  # Abkuerzungsform, aber kein Normzitat davor
        ("Herr DeLuca hat nach § 7 StVG Ansprueche angemeldet.", "DeLuca"),
    ],
)
def test_names_next_to_citations_stay_protected(gateway, text, name) -> None:
    assert name not in _payload(gateway, text)


def test_citation_rule_only_applies_directly_after_the_citation() -> None:
    assert pn._NORM_CITATION_BEFORE.search("nach § 7 ")
    assert pn._NORM_CITATION_BEFORE.search("aus § 280 Abs. 1 ")
    assert not pn._NORM_CITATION_BEFORE.search("nach § 7 StVG hat Herr ")
    assert not pn._STATUTE_ABBREVIATION.match("Sommer")
