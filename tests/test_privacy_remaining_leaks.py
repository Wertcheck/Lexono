"""Verbliebene Lecks der eingefrorenen Testbasis (Folgezyklus 13.10.2026, scripts/bench_presidio_audit.py: 8/80 -> 1/80).

Ursachen (je Fall Stufe fuer Stufe verfolgt):
- Steuernummer, Personalausweis, Kfz-Kennzeichen, IP-Adresse: Presidio erkennt sie, die Recognizer waren aber wegen
  kontextabhaengiger Scores (neue Blockaden im Restrisiko-Scan) bzw. Fehlalarmen nicht genutzt. Jetzt: Treffer nur mit
  UNMITTELBAR vorangestellter Bezeichnung - deterministisch und gleich in Pseudonymisierung und Restrisiko-Scan.
- Postfachnummer: kein Detektor.
- Griechische Versalnamen: Vokalpruefung nur lateinisch; das deutsche Wortartmodell taggt griechische Namen als NOUN.
- Profil-Link mit Namen: nur Profil-Plattformen, Quellenangaben bleiben lesbar.
Echter Presidio-/spaCy-Stack, nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _result(gateway, text: str):
    return gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode="mask",
    )


def _payload(gateway, text: str) -> str:
    result = _result(gateway, text)
    assert result.allowed, result.reasons
    return result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text, value",
    [
        ("Die Steuer-Nr. 21/815/08150 ist dem Bescheid zu entnehmen.", "21/815/08150"),
        # Nummern mit gueltiger Pruefziffer (Presidio-Validierung), synthetisch erzeugt
        ("Bitte die Ausweisnummer GMG0WF911 notieren.", "GMG0WF911"),
        ("Reisepassnummer: MVVYGKXV8", "MVVYGKXV8"),
        ("Unfallgegner, amtliches Kennzeichen B-XY 815, flüchtete.", "B-XY 815"),
        ("Der Pkw mit Kfz-Kennzeichen M AB 1234 stand im Halteverbot.", "M AB 1234"),
        ("Die Zugriffe stammen von der IP-Adresse 10.0.12.77.", "10.0.12.77"),
        ("Postfach 12 34 56, 20095 Hamburg", "12 34 56"),
        ("Antwort bitte an Postfach 4711, 50667 Köln.", "4711"),
        ("Postfach 0221 4711, 50667 Köln", "4711"),  # Review: zweite Gruppe darf nicht frei werden
        ("AMTLICHES KENNZEICHEN HH-AB 1234", "HH-AB 1234"),
        ("Halter laut amtl. Kz. KI-XY 77", "KI-XY 77"),
        ("Ihre Steuernummer und Ihr Kennzeichen HH-AB 1234 wurden gespeichert.", "HH-AB 1234"),  # Review D39
        ("Profil: https://de.linkedin.com/in/karl-brenner", "karl-brenner"),  # Review C45 (Sprach-Subdomain)
        ("Siehe xing.com/profile/Odette_Paschke und facebook.com/odette.paschke", "Odette_Paschke"),
    ],
)
def test_labelled_identifiers_and_profiles_are_pseudonymized(gateway, text, value) -> None:
    payload = _payload(gateway, text)
    assert value not in payload, payload


@pytest.mark.parametrize(
    "text, values",
    [
        ("ΓΙΑΝΝΗΣ ΠΑΠΑΔΟΠΟΥΛΟΣ ist Geschäftsführer der Firma.", ["ΓΙΑΝΝΗΣ", "ΠΑΠΑΔΟΠΟΥΛΟΣ"]),
        # Vorname von der NER als Organisation erkannt (Review, installierter Build Fall 13)
        ("Mieter: ΓΙΑΝΝΗΣ ΠΑΠΑΔΟΠΟΥΛΟΣ", ["ΓΙΑΝΝΗΣ", "ΠΑΠΑΔΟΠΟΥΛΟΣ"]),
        ("KARL BRENNER ΓΙΑΝΝΗΣ ΠΑΠΑΔΟΠΟΥΛΟΣ", ["ΓΙΑΝΝΗΣ", "ΠΑΠΑΔΟΠΟΥΛΟΣ"]),
        ("ИВАН ПЕТРОВ ist Mieter.", ["ИВАН", "ПЕТРОВ"]),
    ],
)
def test_non_latin_uppercase_full_names_are_protected(gateway, text, values) -> None:
    payload = _payload(gateway, text)
    for value in values:
        assert value not in payload, (value, payload)


@pytest.mark.parametrize(
    "text, keep",
    [
        # ohne Bezeichnung: keine Kennnummer (Fahrzeugmodelle, Strassen, Liefer-/Vorgangsnummern, Versionen)
        # BEWUSST GEAENDERT 13.10.2026 (PRIVACY_REVIEW_ARCHITECTURE.md §21.3): "KL AB 12" sind echte Kennzeichen ohne
        # Bezeichnung - jetzt vorsichtshalber maskiert (tests/test_privacy_unlabelled_identifiers.py); der Rest bleibt lesbar.
        ("Lkw-Flotte: Fahrzeuge KL AB 12, KL CD 34 und KL EF 56 der Spedition.", "der Spedition"),
        ("Kfz: VW T 6 Transporter, Erstzulassung 2019.", "VW T 6"),
        ("DER PKW FUHR AUF DER B 51 UND DANN AUF DER A 1.", "DER B 51"),
        ("Lieferschein 1203456789012 vom Lieferanten.", "vom Lieferanten"),  # BEWUSST GEAENDERT 13.10.2026 (§22 Nummern-Strategie): Nummer jetzt maskiert, geprueft wird der lesbare Rest
        ("Version 2.4.1.0 der Software; Kapitel 10.2.3.4 des Gutachtens.", "Version 2.4.1.0"),
        ("Ersatzteil T12345678 wurde nachbestellt.", "T12345678"),
        # Bezeichnung vorhanden, aber Pruefziffer ungueltig: keine Kennnummer
        ("Bitte die Ausweisnummer L01X00T47 notieren.", "notieren"),  # BEWUSST GEAENDERT 13.10.2026 (§22 Nummern-Strategie): Nummer jetzt maskiert, geprueft wird der lesbare Rest (ungueltige Pruefziffer macht eine Ausweisnummer nicht unbedenklich)
        # Quellenangaben und gewoehnliche Links bleiben lesbar
        ("Quelle: https://www.gesetze-im-internet.de/bgb/__535.html", "gesetze-im-internet.de/bgb/__535.html"),
        ("Rechtsprechung: https://dejure.org/gesetze/BGB/280.html", "dejure.org/gesetze/BGB/280.html"),
        # Postfach ohne Postfachnummer (Review)
        ("Das Schreiben lag im Postfach der Kanzlei.", "Postfach der Kanzlei"),
        ("Im Postfach 3 neue Nachrichten.", "Postfach 3 neue"),
        # (das Satzanfangs-Wort "Postfach" selbst haelt die NER hier vorbestehend fuer eine Person - nicht Gegenstand)
        ("Postfach 2025 vom 01.02.2025 wurde archiviert.", "2025 vom"),
        # Profil-Muster nur mit Grenze vor dem Host; Repository-Links bleiben Quellenangaben (Review)
        ("Download: https://www.dropbox.com/s/abc123/vertrag.pdf", "dropbox.com/s/abc123/vertrag.pdf"),
        ("Sendungsverfolgung https://www.fedex.com/de-de/tracking.html", "fedex.com/de-de/tracking.html"),
        ("Quellcode: https://github.com/microsoft/presidio", "github.com/microsoft/presidio"),
        # "Kennzeichen" als Bezugszeichen bzw. Modell-/Gliederungsangabe (Review)
        ("Kennzeichen VW T 6 laut Fahrzeugschein.", "VW T 6"),
        ("Kennzeichen EU A 3 der Richtlinie.", "EU A 3"),
    ],
)
def test_unlabelled_numbers_and_sources_stay_readable(gateway, text, keep) -> None:
    assert keep in _payload(gateway, text)


def test_identifier_placeholders_round_trip(gateway) -> None:
    result = _result(gateway, "Steuernummer 143/815/08154, Kennzeichen HH-AB 1234, Profil linkedin.com/in/gernot-hahn-1980")
    assert result.allowed, result.reasons
    placeholders = [m.placeholder for m in result.mappings]
    restored = gateway.reconstruct_response("Antwort: " + " ".join(placeholders), result.mappings)
    for original in ("143/815/08154", "HH-AB 1234", "linkedin.com/in/gernot-hahn-1980"):
        assert original in restored, restored
