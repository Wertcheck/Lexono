"""Kurzform einer Firma in Klammern (24.10.2026): "Nordbauholding GmbH (NBH)" - die Firma wurde erkannt, die Kurzform
ging an keiner Stelle maskiert hinaus (vorbestehende Luecke). Die Kurzform ist geschuetzt, wenn sie eindeutig einer
direkt davor genannten Firma zugeordnet ist; Gesetzesabkuerzungen, Rechtsformen und unbekannte Klammerinhalte bleiben
unveraendert. Ausschliesslich synthetische Daten, lokal, kein Cloud-Aufruf."""

from __future__ import annotations

import json

import pytest

from app.privacy.detectors import _abbreviation_fits, _organisation_short_forms, detect_all
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, text: str) -> tuple[str, list[str]]:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + text,
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
    )
    assert result.allowed, result.reasons
    return json.dumps(result.payload.model_dump(), ensure_ascii=False), [m.original_value for m in result.mappings]


NORDBAU = "Die Nordbauholding GmbH (NBH) hat Einspruch eingelegt. Die NBH ist Mandantin."
TWO_FIRMS = "Die Muster & Beispiel Handelsservice GmbH (MBH) und die Kran-Logistik AG (KLA) streiten. MBH zahlt, KLA nicht."
DEFINED = "Vertragspartner ist die Alpha Bau GmbH (nachfolgend „AB“). AB schuldet die Zahlung ab 01.01.2027."


@pytest.mark.parametrize(
    "text, value",
    [
        (NORDBAU, "NBH"), (NORDBAU, "Nordbauholding"),
        (TWO_FIRMS, "MBH"), (TWO_FIRMS, "KLA"), (TWO_FIRMS, "Muster & Beispiel Handelsservice GmbH"),
        (DEFINED, "AB schuldet"),
    ],
)
def test_short_form_and_its_later_mentions_are_protected(gateway, text: str, value: str) -> None:
    payload, _ = _payload(gateway, text)
    assert value not in payload, f"{value!r} ging im Klartext hinaus"


def test_every_mention_gets_the_same_placeholder(gateway) -> None:
    payload, originals = _payload(gateway, NORDBAU)
    assert originals.count("NBH") == 1  # ein Wert -> ein Platzhalter fuer Definition und Folgeerwaehnung
    assert "NBH" not in payload


def test_two_firms_keep_separate_short_forms() -> None:
    spans = detect_all(TWO_FIRMS, None)
    short = {(s.value, s.category) for s in spans if s.value in ("MBH", "KLA")}
    assert short == {("MBH", "organisation"), ("KLA", "organisation")}
    assert sum(s.value == "MBH" for s in spans) == 2 and sum(s.value == "KLA" for s in spans) == 2


def test_matching_is_case_sensitive_and_whole_word(gateway) -> None:
    payload, _ = _payload(gateway, DEFINED)
    assert "ab 01.01.2027" in payload  # "ab" ist nicht die Kurzform "AB"
    _, originals = _payload(gateway, TWO_FIRMS)
    assert "GmbH" not in originals  # "MBH" zerlegt die Rechtsform "GmbH" nicht


# --- Gesetze, Rechtsformen und unbekannte Klammerinhalte bleiben unveraendert -------------------------------------------


@pytest.mark.parametrize(
    "text, value",
    [
        ("Nach dem Bürgerlichen Gesetzbuch (BGB) gilt § 433 BGB. Das BGB regelt den Kauf.", "§ 433 BGB"),
        ("Nach dem Bürgerlichen Gesetzbuch (BGB) gilt § 433 BGB. Das BGB regelt den Kauf.", "Das BGB"),
        # Kollision mit einem Gesetz ("... GmbH (BGB) ... § 433 BGB"): seit 24.10.2026 blockiert der Gateway (Option C),
        # siehe tests/test_privacy_short_form_collision.py
        ("Die Anlage (ABC) liegt bei. Bitte ABC beachten.", "ABC"),
        ("Die Alpha Bau GmbH (AG) hat geliefert.", "(AG)"),
        ("Die Alpha Bau GmbH (XYZ) hat geliefert. XYZ ist bekannt.", "XYZ"),
    ],
)
def test_statutes_legal_forms_and_unrelated_abbreviations_stay_readable(gateway, text: str, value: str) -> None:
    payload, _ = _payload(gateway, text)
    assert value in payload, f"{value!r} wurde maskiert"


def test_statute_long_form_never_defines_a_company_short_form() -> None:
    text = "Die Gesetzbuch Verlag GmbH (GVG) beruft sich auf das Gerichtsverfassungsgesetz."
    assert not [s for s in _organisation_short_forms(text, detect_all(text, None)) if s.value == "GVG"]


def test_abbreviation_must_fit_the_company_name() -> None:
    assert _abbreviation_fits("NBH", "Nordbauholding GmbH")
    assert _abbreviation_fits("MBH", "Muster & Beispiel Handelsservice GmbH")
    assert _abbreviation_fits("KLA", "die Kran-Logistik AG")
    assert not _abbreviation_fits("XYZ", "Alpha Bau GmbH")
    assert not _abbreviation_fits("BNH", "Nordbauholding GmbH")  # Anfangsbuchstabe passt nicht


# --- echte personenbezogene Daten bleiben geschuetzt ----------------------------------------------------------------


@pytest.mark.parametrize("value", ["Thomas Berger", "Beispielstraße 17", "12/345/67890", "NBH"])
def test_real_personal_data_next_to_a_short_form_stays_protected(gateway, value: str) -> None:
    payload, _ = _payload(
        gateway,
        "Geschäftsführer der Nordbauholding GmbH (NBH) ist Thomas Berger, Beispielstraße 17, 10115 Berlin, "
        "Steuernummer 12/345/67890. Die NBH hat Einspruch eingelegt.",
    )
    assert value not in payload


def test_statute_abbreviation_defined_in_parentheses_stays_readable_in_citations(gateway) -> None:
    """Frueher Fehlalarm (behoben 24.10.2026, siehe presidio_ner.cited_statute_abbreviations)."""
    payload, _ = _payload(gateway, "Es gilt die Straßenverkehrs-Ordnung (StVO); nach § 1 StVO ist Rücksicht geboten.")
    assert "§ 1 StVO" in payload
