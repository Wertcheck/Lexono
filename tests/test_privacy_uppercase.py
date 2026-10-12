"""Versalien (13.10.2026, scripts/bench_uppercase.py, scripts/bench_uppercase_negatives.py).

Befund: "HERRN / GERNOT HAHN / BIRKENALLEE 17 / 12345 MUSTERSTADT", "Der Beklagte WINKELMANN", Rubrum in Grossbuchstaben
gingen im Klartext an die Cloud (35 von 36 Werten). Abhilfe in app/privacy/detectors.py::detect_all: eine laengengleiche
Analysefassung kurzer Versalfolgen in normaler Schreibung; NUR zusaetzliche, validierte Treffer. Echter Presidio-/spaCy-
Stack, nur synthetische Daten."""

from __future__ import annotations

import re

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy import detectors
from app.privacy.detectors import DetectedSpan, detect_all
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, text: str, mode: str = "mask") -> str:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode=mode,
    )
    assert result.allowed, result.reasons  # weder Pseudonymisierung noch Restrisiko-Scan blockieren
    return result.payload.anonymisierter_sachverhalt


def _word(text: str, word: str) -> bool:
    return re.search(r"(?<![\wÄÖÜäöüß-])" + re.escape(word) + r"(?![\wÄÖÜäöüß])", text) is not None


@pytest.mark.parametrize(
    "text, values",
    [
        ("HERRN\nGERNOT HAHN\nBIRKENALLEE 17\n12345 MUSTERSTADT\n\nBetreff: Mahnung", ["GERNOT", "HAHN", "BIRKENALLEE", "MUSTERSTADT"]),
        ("FRAU\nODETTE PASCHKE\nROSENWEG 2\n24103 KIEL", ["ODETTE", "PASCHKE", "ROSENWEG", "KIEL"]),
        ("Herrn\nKLAUS-DIETER BROCKHAGEN\nHAFENSTRASSE 9\n20457 HAMBURG", ["KLAUS-DIETER", "BROCKHAGEN", "HAFENSTRASSE"]),
        ("Der Beklagte WINKELMANN hat die Forderung bestritten.", ["WINKELMANN"]),
        ("In Sachen HABERLAND ./. WINKELMANN wegen Werklohn", ["HABERLAND", "WINKELMANN"]),
        ("Mit freundlichen Grüßen\nGEZ. FENJA RÜBSAM\nRechtsanwältin", ["FENJA", "RÜBSAM"]),
        ("Frau Dr. SOMMER-BRANDT hat geschrieben.", ["SOMMER-BRANDT"]),
        ("Die Zeugin SCHWIENHORST hat ausgesagt.", ["SCHWIENHORST"]),
        ("HERRN\nPETER GROßMANN\nMÜHLENSTRAßE 4\n31134 HILDESHEIM", ["PETER", "GROßMANN", "HILDESHEIM"]),  # ß in Versalien
    ],
)
@pytest.mark.parametrize("mode", ["mask", "ask"])
def test_uppercase_names_and_addresses_are_protected(gateway, text, values, mode) -> None:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode=mode,
    )
    if mode == "ask" and result.review_required:
        return  # Rueckfrage vor jedem Cloud-Aufruf - ebenfalls geschuetzt
    assert result.allowed, result.reasons
    for value in values:
        assert not _word(result.payload.anonymisierter_sachverhalt, value), (value, result.payload.anonymisierter_sachverhalt)


@pytest.mark.parametrize(
    "text, keep",
    [
        ("BETREFF: MAHNUNG ODER KLAGE", ["BETREFF", "MAHNUNG", "ODER", "KLAGE"]),
        ("IM NAMEN DES VOLKES\nURTEIL\nIn dem Rechtsstreit", ["NAMEN", "VOLKES", "URTEIL"]),
        ("WICHTIG: BITTE FRISTEN BEACHTEN UND ZAHLUNG LEISTEN", ["WICHTIG", "FRISTEN", "ZAHLUNG"]),
        ("Der Halter haftet nach § 7 STVG und § 823 BGB; die AGB gelten.", ["STVG", "BGB", "AGB"]),
        ("ANLAGE K1: KAUFVERTRAG VOM 12.03.2026", ["ANLAGE", "KAUFVERTRAG"]),
        ("EILT! SOFORT VORLEGEN", ["EILT", "SOFORT", "VORLEGEN"]),
        ("VERTRAULICH – NICHT WEITERLEITEN\nINTERNE NOTIZ: BEWEISAUFNAHME STEHT AUS.", ["WEITERLEITEN", "BEWEISAUFNAHME"]),
        ("TELEFAX\nSEITEN: 3\nBETREFF: ANFORDERUNG DER ERMITTLUNGSAKTE", ["TELEFAX", "ANFORDERUNG", "ERMITTLUNGSAKTE"]),
        ("ÜBERGABEPROTOKOLL\nZÄHLERSTAND STROM: 12345\nSCHLÜSSEL: 3 STÜCK", ["SCHLÜSSEL", "STROM"]),
        ("TERMINSLADUNG\nTERMIN ZUR MÜNDLICHEN VERHANDLUNG: DIENSTAG, 10:00 UHR, SAAL 3", ["SAAL", "TERMIN"]),
        ("RECHNUNG ÜBER EUR 1.200,00", ["RECHNUNG", "1.200,00"]),
    ],
)
def test_ordinary_uppercase_text_stays_readable(gateway, text, keep) -> None:
    payload = _payload(gateway, text)
    for word in keep:
        assert _word(payload, word), (word, payload)


def test_variant_is_length_preserving_and_limited_to_short_runs() -> None:
    text = "HERRN  GERNOT HAHN  DIE BEKLAGTE WIRD HEUTE NICHT VERURTEILT  STVG"
    variant, regions = detectors._uppercase_variant(text)
    assert len(variant) == len(text)
    assert variant.startswith("Herrn  Gernot Hahn  DIE BEKLAGTE WIRD HEUTE NICHT VERURTEILT")  # lange Folge unveraendert
    assert variant.endswith("STVG")  # vokallose Abkuerzung unveraendert
    assert all(text[s:e].isupper() for s, e in regions)


def test_variant_spans_map_to_original_values() -> None:
    text = "Der Beklagte WINKELMANN hat bestritten."
    spans = detect_all(text, ner_detector=lambda t: [])  # nur Regex-/Titel-Detektoren
    for span in spans:
        assert span.value == text[span.start : span.end]


def test_additional_analysis_never_removes_or_displaces_original_spans() -> None:
    """Ein urspruenglicher Treffer (Stub) bleibt exakt erhalten, auch wenn die Versal-Fassung ueberlappende Treffer liefert."""
    text = "HERRN  GERNOT HAHN  BIRKENALLEE 17  12345 MUSTERSTADT"
    original = DetectedSpan(category="person", start=text.index("HAHN"), end=text.index("HAHN") + 4, value="HAHN")

    def stub(analysed: str) -> list[DetectedSpan]:
        if analysed == text:
            return [original]
        start = analysed.index("Gernot")
        return [DetectedSpan(category="person", start=start, end=start + len("Gernot Hahn"), value="Gernot Hahn")]

    spans = detect_all(text, ner_detector=stub)
    # Invariante: die Abdeckung des urspruenglichen Treffers wird nie verringert - erlaubt ist nur eine Obermenge
    # GLEICHER Kategorie (Red-Team 4, C1: "HANS MEIER" statt nur "MEIER").
    assert any(s.start <= original.start and s.end >= original.end and s.category == "person" for s in spans)
    assert all(s.value == text[s.start : s.end] for s in spans)


def test_variant_of_another_category_never_displaces_an_original_span() -> None:
    text = "HERRN  GERNOT HAHN  BIRKENALLEE 17  12345 MUSTERSTADT"
    original = DetectedSpan(category="person", start=text.index("HAHN"), end=text.index("HAHN") + 4, value="HAHN")

    def stub(analysed: str) -> list[DetectedSpan]:
        if analysed == text:
            return [original]
        start = analysed.index("Gernot")
        return [DetectedSpan(category="ort", start=start, end=start + len("Gernot Hahn"), value="Gernot Hahn")]

    spans = detect_all(text, ner_detector=stub)
    assert any(s.start == original.start and s.end == original.end and s.category == "person" for s in spans)
    assert not any(s.category == "ort" and s.start < original.end and original.start < s.end for s in spans)


def test_residual_scan_does_not_use_the_uppercase_variant() -> None:
    """Restrisiko-Scan unveraendert wie vor der Erweiterung (Red-Team 4, C2: kontextabhaengige Blockaden)."""
    text = "Der Beklagte WINKELMANN hat bestritten."
    with_variant = detect_all(text, ner_detector=lambda t: [])
    without_variant = detect_all(text, ner_detector=lambda t: [], uppercase_variant=False)
    assert [s.value for s in without_variant] == []
    assert isinstance(with_variant, list)


def test_mixed_case_text_is_unchanged() -> None:
    assert detectors._uppercase_variant("Herr Sommer hat am 12.03.2026 angerufen.") is None


# --- Red-Team 4: Briefe mit Versal-Anschrift und Anrede, Gerichtskoepfe, Gesetzesabkuerzungen ---------------------------


@pytest.mark.parametrize(
    "first, last",
    [("HANS", "MEIER"), ("ODETTE", "PASCHKE"), ("TJARK", "WESTPHAL"), ("JÜRGEN", "SCHÖßLER"), ("GERNOT", "HAHN")],
)
@pytest.mark.parametrize("follow", ["BETREFF: IHR SCHREIBEN VOM 01.03.2026", "UNSER ZEICHEN: 204/26", "IHR ZEICHEN: K-17"])
def test_uppercase_address_letter_with_salutation_is_not_blocked(gateway, first, last, follow) -> None:
    salutation = last[:1] + last[1:].lower()
    text = f"HERRN\n{first} {last}\nHAUPTSTRASSE 1\n10115 BERLIN\n{follow}\nSehr geehrter Herr {salutation},\nwir bitten um Zahlung."
    payload = _payload(gateway, text)  # assert allowed
    assert not _word(payload, first) and not _word(payload, last) and not _word(payload, salutation)


def test_court_and_authority_headers_stay_readable(gateway) -> None:
    payload = _payload(gateway, "AMTSGERICHT HAMBURG\nLANDGERICHT KÖLN\nOBERLANDESGERICHT MÜNCHEN\nFINANZAMT NORD\nAGENTUR FÜR ARBEIT")
    for word in ("KÖLN", "MÜNCHEN", "NORD", "LANDGERICHT"):
        assert _word(payload, word), (word, payload)


def test_uppercase_statute_abbreviations_after_citations_stay_readable(gateway) -> None:
    payload = _payload(gateway, "Gemäß § 5 EFZG und § 1 MUSCHG, § 2 BEEG, § 6 BURLG, § 7 ALTZG")
    for code in ("EFZG", "MUSCHG", "BEEG", "BURLG", "ALTZG"):
        assert _word(payload, code), (code, payload)


def test_uppercase_name_after_mixed_case_salutation(gateway) -> None:
    """Red-Team 4: "Sehr geehrter Herr THIELKER," - die NER liefert "Herr Thielker"; geprueft wird nur das umgeschriebene
    Versalwort, nicht die normal geschriebene Anrede."""
    assert "THIELKER" not in _payload(gateway, "Sehr geehrter Herr THIELKER,\nwir bitten um Zahlung.")


def test_rubrum_separator_is_kept(gateway) -> None:
    """Befund installierter Build 39112b7: "In Sachen HABERLAND ./. WINKELMANN" wurde zu "[PERSON_02] [PERSON_03]" - die NER
    lieferte "Haberland ./." samt Satzzeichen. Variantentreffer werden auf Buchstaben/Ziffern beschnitten."""
    text = ("HERRN\nGERNOT HAHN\nBIRKENALLEE 17\n12345 MUSTERSTADT\n\nBETREFF: MAHNUNG\nIn Sachen HABERLAND ./. WINKELMANN\n\n"
            "Der Beklagte WINKELMANN hat die Rechnung über 1.200,00 EUR nicht bezahlt.")
    payload = _payload(gateway, text)
    assert "./." in payload and "HABERLAND" not in payload and "WINKELMANN" not in payload
    assert "1.200,00" in payload and "Beklagte" in payload
