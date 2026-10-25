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


# --- Wertbezogen: Abkuerzung mit Normbezug an JEDER Stelle des Textes (24.10.2026) -----------------------------------
# Befund: "Straßenverkehrs-Ordnung (StVO); nach § 1 StVO" - der Wortart-Tagger markierte beide Vorkommen als Person, die
# positionsbezogene Regel entfernte nur das Vorkommen hinter "§ 1", das Weitertragen der Namensbestandteile uebertrug den
# Klammertreffer zurueck auf das Normzitat.

DEFINED_AND_CITED = [
    ("Es gilt die Straßenverkehrs-Ordnung (StVO); nach § 1 StVO ist Rücksicht geboten.", ["(StVO)", "§ 1 StVO"]),
    ("Nach der Strafprozessordnung (StPO) gilt § 136 StPO; die StPO schützt den Beschuldigten.",
     ["(StPO)", "§ 136 StPO", "die StPO"]),
    ("Das Betriebsverfassungsgesetz (BetrVG) regelt in § 87 BetrVG die Mitbestimmung.", ["(BetrVG)", "§ 87 BetrVG"]),
    ("Das Arbeitszeitgesetz (ArbZG) begrenzt in §§ 3, 4 ArbZG die Arbeitszeit.", ["(ArbZG)", "§§ 3, 4 ArbZG"]),
    ("Nach Art. 6 DSGVO ist die Verarbeitung erlaubt; die DSGVO gilt unmittelbar.", ["Art. 6 DSGVO", "die DSGVO"]),
]


@pytest.mark.parametrize("text, readable", DEFINED_AND_CITED)
def test_cited_statute_abbreviation_stays_readable_everywhere_in_the_text(gateway, text: str, readable: list[str]) -> None:
    payload = _payload(gateway, text)
    for value in readable:
        assert value in payload, f"{value!r} wurde maskiert"


def test_without_a_norm_citation_nothing_changes() -> None:
    """Kein Normzitat im Text -> keine wertbezogene Freigabe (keine pauschale Freigabe von Abkuerzungen)."""
    assert pn.cited_statute_abbreviations("Die Straßenverkehrs-Ordnung (StVO) ist bekannt.") == frozenset()
    assert pn.cited_statute_abbreviations("Nach § 1 StVO und §§ 433, 434 BGB sowie Art. 6 DSGVO.") == frozenset(
        {"StVO", "BGB", "DSGVO"}
    )


@pytest.mark.parametrize("value", ["Thomas Berger", "Beispielstraße 17", "12/345/67890", "NBH", "McAllister"])
def test_real_values_next_to_a_cited_statute_stay_protected(gateway, value: str) -> None:
    payload = _payload(
        gateway,
        "Die Nordbauholding GmbH (NBH) beruft sich auf § 1 StVO. Laut Rechtsanwalt McAllister muss Thomas Berger, "
        "Beispielstraße 17, 10115 Berlin, Rücksicht nehmen; Steuernummer 12/345/67890. Die NBH zahlt nicht.",
    )
    assert value not in payload, f"{value!r} ging im Klartext hinaus"
