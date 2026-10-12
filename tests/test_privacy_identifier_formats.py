"""Weitere Kennnummer-Formate und Bezeichnungen (Folgezyklus 13.10.2026, erweiterte Leckfaelle 10/18 -> 1/18).

Unveraendertes Prinzip: bezeichnete Kennnummern nur mit UNMITTELBAR vorangestellter Bezeichnung (erlaubt: "ist"/
"lautet" dazwischen) - die zusaetzlichen Formate loesen allein nie etwas aus. Nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
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
        ("unser kennzeichen ist hh-ab 1234, bitte notieren.", ["hh-ab", "1234"]),
        ("Halter laut Kfz-Kz. HH-AB 1234 ermittelt.", ["HH-AB 1234"]),
        ("Kennz. B-XY 815 wurde geblitzt.", ["B-XY", "815"]),
        ("Kennzeichen HH AB1234 wurde notiert.", ["HH AB1234"]),
        ("Steuernummer 2181508150 laut Bescheid.", ["2181508150"]),
        ("Steuernummer: 2181508150123", ["2181508150123"]),
        ("Steuer-Nr. 143 815 08154 beim Finanzamt.", ["143 815 08154"]),
        ("Die Steuernummer lautet 2181508150.", ["2181508150"]),
        ("FS-Nr. AB12345678X, Klasse B.", ["AB12345678X"]),
        ("Postf. 12 34 56, 20095 Hamburg", ["12 34 56"]),
        ("Eingetragen unter HRB 123 456 beim Amtsgericht.", ["HRB 123", "456"]),
        ("Profil: https://www.threads.net/@karl.brenner", ["karl.brenner"]),
        ("Profil: https://bsky.app/profile/karl-brenner.bsky.social", ["karl-brenner"]),
    ],
)
def test_identifier_formats_and_labels_are_protected(gateway, text, values) -> None:
    payload = _payload(gateway, text)
    for value in values:
        assert value not in payload, (value, payload)


@pytest.mark.parametrize(
    "text, keep",
    [
        # (vorbestehend und hier nicht Gegenstand: "Rechnungsnummer ..." maskiert der Rechnungsnummer-Detektor, die NER
        # haelt "a 7" fuer einen Ort, "HRB-Verfahren und Az. ..." blockierte bereits auf 7cd8145)
        ("Vorgang 2181508150 vom Lieferanten.", "2181508150"),  # 10 Ziffern ohne Steuer-Bezeichnung
        ("Lieferschein 1203456789012 ist eingetroffen.", "1203456789012"),
        ("Kennzeichen der A 7 ist die Ausfahrt 23.", "der A 7"),  # Artikel: kein Kennzeichen
        # Review: gewoehnliche Woerter nach "Kennzeichen" (Muster ohne Gross-/Kleinschreibung)
        ("Das Kennzeichen ist um 18 Uhr gestohlen worden.", "ist um 18 Uhr"),
        ("Ein neues Kennzeichen ist ab 2026 erforderlich.", "ist ab 2026"),
        ("Das Kennzeichen ist zu 80 Prozent lesbar.", "zu 80 Prozent"),
        ("Fahrzeugkennzeichen und Km 45 wurden notiert.", "und Km 45"),
        ("das kennzeichen ist um 15 uhr abmontiert worden.", "um 15 uhr"),
        ("Tel. 040 1234 56789 erreichbar.", "Tel."),
        ("Teilenummer HH AB1234 wurde bestellt.", "HH AB1234"),  # Zusatzformat ohne Bezeichnung
    ],
)
def test_unlabelled_formats_stay_readable(gateway, text, keep) -> None:
    assert keep in _payload(gateway, text)


def test_spaced_tax_number_shape_without_label_is_no_identifier(gateway) -> None:
    # ("08154 Belege" haelt der vorbestehende PLZ+Ort-Detektor fuer eine Anschrift - nicht Gegenstand)
    assert "[KENNUNG" not in _payload(gateway, "Der Steuerberater prüft 143 815 08154 Belege.")
