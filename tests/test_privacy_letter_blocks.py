"""Briefe mit Anschriftenblock und unsichtbare Formatzeichen (Folgezyklus 13.10.2026, drittes unabhaengiges Red-Team).

1. Regression aus 38361af (behoben): "Herrn<Umbruch>Name<Umbruch>Anschrift ... Betreff: ..." - im Restrisiko-Scan stand der
   Name bereits als Platzhalter (Analysefassung: Leerzeichen); die Anrede-Regel nahm "Betreff"/"Unser"/"Az" als Namen -
   Fail-Closed-Blockade bei 29 von 42 Briefvarianten (vorher 2 von 42).
2. Vorbestehendes Leck (behoben): weiches Trennzeichen U+00AD im Namen ("Hab\\u00adermehl") verhinderte die Erkennung.
Echter Presidio-/spaCy-Stack, nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy.gateway import ClaudePrivacyGateway


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _result(gateway, text: str, mode: str = "mask"):
    return gateway.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.",
        review_mode=mode,
    )


HEADS = {
    "Hahn": "Herrn\nGernot Hahn\nBirkenallee 17\n12345 Musterstadt\n\n{X}\n\nwir mahnen nach § 286 BGB.",
    "Paschke": "Frau\nOdette Paschke\nRosenweg 2\n12345 Musterstadt\n\n{X}\n\nwir mahnen nach § 286 BGB.",
}
FOLLOW = [
    "Unser Zeichen: 204/26", "Betreff: Mahnung", "Datum: heute", "Mahnung", "Rechnung Nr. 17",
    "Sehr geehrter Herr Hahn,", "Sehr geehrte Damen und Herren,", "Die Vollmacht liegt vor.", "Anlage: Rechnung",
    "Forderung aus Werkvertrag", "Kopie an Mandantschaft", "Wir zeigen an, dass wir vertreten.",
]


@pytest.mark.parametrize("name", list(HEADS))
@pytest.mark.parametrize("follow", FOLLOW)
def test_letter_with_address_block_is_not_blocked(gateway, name, follow) -> None:
    result = _result(gateway, HEADS[name].replace("{X}", follow))
    assert result.allowed, result.reasons
    assert name not in result.payload.anonymisierter_sachverhalt
    assert "§ 286 BGB" in result.payload.anonymisierter_sachverhalt


def test_signature_block_followed_by_text_is_not_blocked(gateway) -> None:
    result = _result(gateway, "Mit freundlichen Grüßen\ni.V. Knut Hartlieb\ngez. Mareike Thormählen\nDie Vollmacht liegt bei.")
    assert result.allowed, result.reasons
    payload = result.payload.anonymisierter_sachverhalt
    assert "Hartlieb" not in payload and "Thormählen" not in payload and "Vollmacht" in payload


@pytest.mark.parametrize("text, name", [
    ("Herr Hab­ermehl hat die Unterlagen übergeben.", "Habermehl"),
    ("Die Zeugin Grot­elueschen hat ausgesagt.", "Grotelueschen"),
    ("Herr Wesso​lowski hat angerufen.", "Wessolowski"),
])
def test_invisible_format_characters_do_not_hide_names(gateway, text, name) -> None:
    result = _result(gateway, text)
    payload = result.payload.anonymisierter_sachverhalt
    assert result.allowed and name not in payload and "­" not in payload and "​" not in payload


def test_invisible_format_characters_are_removed_without_changing_content(gateway) -> None:
    payload = _result(gateway, "Die Nach­besserungsfrist endet; der Betrag von 1.200,00 EUR ist offen.").payload
    assert "Nachbesserungsfrist" in payload.anonymisierter_sachverhalt and "1.200,00" in payload.anonymisierter_sachverhalt
