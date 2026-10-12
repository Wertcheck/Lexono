"""Grenzfaelle der Pre-Cloud-Grenze (Sicherheitspruefung 13.10.2026; gemessen vorher: 8 Lecks, 2 Lookalike-Kollisionen).

- Verwechselbare Platzhalter ("[PЕRSON_01]" mit kyrillischem Е, griechischem Ο, Vollbreite): galten nicht als Platzhalter;
  "normalisierte" das Modell sie, setzte die Rueckfuehrung einen fremden Wert ein.
- Woertlicher Platzhalter mitten in einer Rufnummer: die restlichen Ziffern gingen im Klartext hinaus.
- Bezeichnete Kennnummern: Fahrzeugwort vor dem Kennzeichen, SV-/RV-Nummer mit Leerzeichen, Konto-/Mitgliedsnummer.
Nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway, text: str):
    sachverhalt = "Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text)
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt=sachverhalt, anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask"
    )
    assert result.allowed, result.reasons
    return sachverhalt, result


@pytest.mark.parametrize(
    "text, normalize",
    [
        ("Muster: [PЕRSON_01] und Herr Karl Brenner.", lambda s: s.replace("Е", "E")),
        ("Muster: [PERSΟN_01] und Herr Karl Brenner.", lambda s: s.replace("Ο", "O")),
        ("Muster: ［PERSON_01］ und Herr Karl Brenner.", lambda s: s.replace("［", "[").replace("］", "]")),
    ],
)
def test_lookalike_placeholders_never_restore_a_foreign_value(gateway, text, normalize) -> None:
    sachverhalt, result = _prepare(gateway, text)
    payload = result.payload.anonymisierter_sachverhalt
    assert gateway.reconstruct_response(payload, result.mappings) == sachverhalt
    # Selbst wenn das Modell die Zeichen in seiner Antwort "normalisiert": kein fremder Wert an der Stelle des Musters
    assert "Muster: Karl Brenner" not in gateway.reconstruct_response(normalize(payload), result.mappings)


def test_digits_around_a_literal_placeholder_are_masked_and_restored(gateway) -> None:
    sachverhalt, result = _prepare(gateway, "Tel. 030 [TELEFON_01] 1234567 ist die Nummer.")
    payload = result.payload.anonymisierter_sachverhalt
    assert "1234567" not in payload and "030" not in payload.split("[Schreiben]", 1)[1]
    assert gateway.reconstruct_response(payload, result.mappings) == sachverhalt


@pytest.mark.parametrize(
    "text, value",
    [
        ("Der PKW HH-AB 1234 stand im Halteverbot.", "HH-AB 1234"),
        ("SV-Nr. 65 170839 J 003 liegt vor.", "170839"),
        ("Rentenversicherungsnummer 12 150380 M 012", "150380"),
        # Messkette: hier hielt der Restrisiko-Scan die Bezeichnung selbst fuer eine Person und blockierte
        ("Rentenversicherungsnummer 70 180849 W 036", "180849"),
        ("Kontonummer 1234567890 bei der Sparkasse.", "1234567890"),
        ("Kto.-Nr. 98 765 432, BLZ 370 400 44", "98 765 432"),
        ("Mitgliedsnummer 4711-0815 der Krankenkasse.", "4711-0815"),
    ],
)
def test_labelled_identifiers_are_masked_without_blocking(gateway, text, value) -> None:
    _, result = _prepare(gateway, text)  # prueft zugleich: keine Blockade
    assert value not in result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text",
    [
        # Review: Zeichen ohne ASCII-Uebersetzung - Kombinationszeichen, mathematisch fett, "O" statt Null,
        # kyrillische Kleinbuchstaben, Kapitaelchen
        "Feld: [PÉRSON_01] und Herr Karl Brenner.",
        "Feld: [𝐏𝐄𝐑𝐒𝐎𝐍_01] und Herr Karl Brenner.",
        "Feld: [PERSON_O1] und Herr Karl Brenner.",
        "Feld: [pеrson_01] und Herr Karl Brenner.",
        "Feld: [ᴘᴇʀꜱᴏɴ_01] und Herr Karl Brenner.",
    ],
)
def test_loose_lookalikes_are_text_marks_and_never_take_a_foreign_value(gateway, text) -> None:
    sachverhalt, result = _prepare(gateway, text)
    payload = result.payload.anonymisierter_sachverhalt
    assert "Feld: [TEXTMARKE_" in payload
    assert gateway.reconstruct_response(payload, result.mappings) == sachverhalt


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("Tel. 0221 [TELEFON_01] 12345678901234567890123456789012345678901234 ist die Durchwahl.", "901234"),
        ("Steuernummer 143/815/[DATUM_01]/08154 laut Bescheid.", "08154"),
    ],
)
def test_long_or_slash_joined_digits_around_a_literal_are_masked(gateway, text, leaked) -> None:
    sachverhalt, result = _prepare(gateway, text)
    payload = result.payload.anonymisierter_sachverhalt
    assert leaked not in payload
    assert gateway.reconstruct_response(payload, result.mappings) == sachverhalt


def test_bank_name_next_to_masked_account_number_is_not_blocked(gateway) -> None:
    """Review M1: nach "[KENNUNG_01]" hielt der Restrisiko-Scan "Sparkasse" fuer eine Person und blockierte."""
    _, result = _prepare(gateway, "Kontonummer 98 765 432 bei der Sparkasse, Inhaber Karl Brenner.")
    payload = result.payload.anonymisierter_sachverhalt
    assert "98 765 432" not in payload and "Karl Brenner" not in payload and "Sparkasse" in payload


@pytest.mark.parametrize(
    "text, keep",
    [
        ("Das Kennzeichen ist um 18 Uhr gestohlen worden; zu 80 Prozent lesbar.", "um 18 Uhr"),
        ("Nach § 7 Abs. 1 StVG und § 286 Abs. 1 BGB, Art. 6 Abs. 1 lit. b DSGVO.", "§ 286 Abs. 1 BGB"),
        ("Forderung 1.200,00 EUR zzgl. 5 Prozentpunkte über dem Basiszinssatz.", "1.200,00 EUR"),
        ("Der Pkw BMW X5 und der Lkw VW T 6 wurden beschädigt.", "BMW X5"),
        ("Der Pkw BMW X5 und der Lkw VW T 6 wurden beschädigt.", "VW T 6"),
        ("Das Konto wurde 2024 eröffnet, die Kontonummer ist unbekannt.", "Kontonummer ist unbekannt"),
        ("Die Mitgliedsnummer folgt per Post.", "Mitgliedsnummer folgt"),
        ("Die Nachbesserungsfrist von 14 Tagen ist abgelaufen.", "14 Tagen"),
        ("Versicherungsnummer folgt, Fahrzeug zu 30 Prozent beschädigt.", "30 Prozent"),
    ],
)
def test_ordinary_text_stays_readable(gateway, text, keep) -> None:
    _, result = _prepare(gateway, text)
    assert keep in result.payload.anonymisierter_sachverhalt
