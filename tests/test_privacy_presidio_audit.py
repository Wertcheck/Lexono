"""Presidio-Audit (13.10.2026, scripts/bench_presidio_audit.py, eingefrorene Testbasis scripts/presidio_audit_corpus.json).

1. Integrationsfehler behoben: normalize_ner_span_boundaries verwarf jeden Presidio-Treffer ohne LATEINISCHEN Buchstaben -
   griechische/kyrillische Namen, die Presidio korrekt als PERSON erkannt hatte, gingen im Klartext an die Cloud.
2. Ungenutzte Presidio-Funktion: die offiziellen deutschen Recognizer (Steuernummer, USt-IdNr., Sozialversicherungs-,
   Ausweis-, Handelsregisternummer, Kfz-Kennzeichen ...) sind in Presidio enthalten, aber standardmaessig deaktiviert.
Echter Presidio-/spaCy-Stack, nur synthetische Daten (Nummern mit gueltiger Pruefziffer, keine realen Personen)."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy import presidio_ner as pn
from app.privacy.detectors import DetectedSpan
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _payload(gateway, text: str) -> str:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode="mask",
    )
    assert result.allowed, result.reasons
    return result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text, values",
    [
        ("Der Mandant Γιάννης Παπαδόπουλος hat die Vollmacht unterschrieben.", ["Γιάννης", "Παπαδόπουλος"]),
        ("Die Zeugin Анна Смирнова hat ausgesagt.", ["Анна", "Смирнова"]),
    ],
)
def test_greek_and_cyrillic_names_found_by_presidio_are_kept(gateway, text, values) -> None:
    payload = _payload(gateway, text)
    for value in values:
        assert value not in payload, (value, payload)


def test_spans_without_any_letter_are_still_dropped() -> None:
    text = "Frist bis 30.11.2026.  Danach Klage."
    span = DetectedSpan(category="ort", start=10, end=21, value=text[10:21])
    assert pn.normalize_ner_span_boundaries(text, [span]) == []
    greek = DetectedSpan(category="person", start=0, end=7, value="Γιάννης")
    assert pn.normalize_ner_span_boundaries("Γιάννης kam.", [greek]) == [greek]


def test_official_german_recognizers_are_registered() -> None:
    names = {type(r).__name__ for r in pn._get_analyzer_engine().registry.get_recognizers(language="de", all_fields=True)}
    assert set(pn._GERMAN_ID_RECOGNIZERS) <= names


def test_only_context_independent_patterns_are_registered() -> None:
    """Kontextabhaengige Muster (Grundscore < Schwelle) erreichten die Schwelle im Restrisiko-Scan, aber nicht in der
    Pseudonymisierung (Platzhalter verkuerzen den Abstand zum Kontextwort) - neue Fail-Closed-Blockaden."""
    recognizers = [
        r for r in pn._get_analyzer_engine().registry.get_recognizers(language="de", all_fields=True)
        if type(r).__name__ in pn._GERMAN_ID_RECOGNIZERS
    ]
    assert recognizers and all(pattern.score >= pn._MIN_SCORE for r in recognizers for pattern in r.patterns)


@pytest.mark.parametrize(
    "text, value",
    [
        ("Die Gegenseite (USt-IdNr. DE489681509) hat die Rechnung gestellt.", "DE489681509"),
        ("ust-idnr de489681509", "de489681509"),
        ("Die Beklagte ist eingetragen unter HRB 98765 beim Registergericht.", "HRB 98765"),
        ("Bitte die Versicherungsnummer 70180849W036 auf dem Antrag eintragen.", "70180849W036"),
    ],
)
def test_german_identifiers_are_pseudonymized(gateway, text, value) -> None:
    payload = _payload(gateway, text)
    assert value not in payload and "[KENNUNG_01]" in payload, payload


@pytest.mark.parametrize(
    "text, keep",
    [
        # Unabhaengiges Review: mit allen deutschen Recognizern neue Blockaden bzw. Fehlalarme
        # BEWUSST GEAENDERT 13.10.2026 (PRIVACY_REVIEW_ARCHITECTURE.md §21.3): echte Kennzeichen ohne Bezeichnung werden im
        # Modus "mask" vorsichtshalber maskiert (vorher Klartext) - geprueft bleibt: nicht blockiert, Text lesbar.
        ("Lkw-Flotte: Fahrzeuge KL AB 12, KL CD 34 und KL EF 56 der Spedition.", "der Spedition"),
        ("Regulierung Kfz-Schaden, Ihr Zeichen: SB KL 123, unser Zeichen: RA BK 26.", "RA BK 26"),
        ("Die Praxis teilte mit, dass Vorgang 260312045 erledigt ist.", "erledigt ist"),  # BEWUSST GEAENDERT 13.10.2026 (§22 Nummern-Strategie): Nummer jetzt maskiert, geprueft wird der lesbare Rest
        ("Praxis für Physiotherapie, Patientennummer 100200300.", "Physiotherapie"),  # BEWUSST GEAENDERT 13.10.2026 (§22 Nummern-Strategie): Nummer jetzt maskiert, geprueft wird der lesbare Rest
        ("Kfz: VW T 6 Transporter, Erstzulassung 2019.", "VW T 6"),
        ("DER PKW FUHR AUF DER B 51 UND DANN AUF DER A 1.", "DER B 51"),
        ("Lieferschein 1203456789012 vom Finanzamt-Lieferanten.", "Lieferschein"),  # BEWUSST GEAENDERT 13.10.2026 (§22 Nummern-Strategie): Nummer jetzt maskiert, geprueft wird der lesbare Rest
        ("Ersatzteil T12345678 wurde nachbestellt.", "T12345678"),
        # bewusst NICHT uebernommen: IP-/URL-Recognizer (Versions-/Kapitelnummern, Quellenangaben)
        ("Version 2.4.1.0 der Software; Kapitel 10.2.3.4 des Gutachtens.", "Kapitel 10.2.3.4"),
        ("Quelle: https://www.gesetze-im-internet.de/bgb/__535.html", "gesetze-im-internet.de/bgb/__535.html"),
        ("Gebühr Nr. 3100 VV RVG; Az. 12 O 345/26; BGBl. I S. 1234; DIN 18560-2", "Nr. 3100 VV RVG"),
    ],
)
def test_number_heavy_text_stays_readable_and_is_not_blocked(gateway, text, keep) -> None:
    assert keep in _payload(gateway, text)  # _payload prueft auch: nicht blockiert
