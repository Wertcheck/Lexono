"""F (§34.6/§34.15): Fachwort-Komposita und Possessiv-Verwandtschaftswoerter duerfen nicht als Person maskiert werden; echte
Namen - auch Komposita-Nachnamen - bleiben geschuetzt. Nur synthetische Saetze; die Beispielsaetze sind NICHT aus den
Testfaellen des Legal-E2E-Benchmarks uebernommen (dort wird die Wirkung gemessen)."""

from __future__ import annotations

import json

import pytest

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


# Fachwort-Komposita ohne Ableitungsfuge (Reparatur|dauer, Tagebuch|nummer, ...) - vorher als Person/Ort maskiert
TERMS = [
    ("Lieferdauer: 3 Wochen ab Bestellung.", "Lieferdauer"),
    ("Vorgangsnummer 2026-118 der Polizeidienststelle.", "Vorgangsnummer"),
    ("Aktennotiz vom 03.02.2026 zur Besprechung.", "Aktennotiz"),
    ("Schadensmitteilung an die Versicherung vom 05.03.2026.", "Schadensmitteilung"),
    ("Monatsvergütung: 2.900,00 EUR brutto.", "Monatsvergütung"),
    ("Im Badezimmer zeigt sich Feuchtigkeit.", "Badezimmer"),
    ("Das Wohnhaus hat ein Dachgeschoss.", "Dachgeschoss"),
    ("Sie ist Vorerbin des Nachlasses.", "Vorerbin"),
    ("Die Zustimmung des Abteilungsleiters liegt vor.", "Abteilungsleiters"),
    ("Kontrollbeamter: Name siehe Anlage.", "Kontrollbeamter"),
    ("Der Bescheid über das Ordnungsgeld wurde zugestellt; ein Strafbescheid folgte.", "Strafbescheid"),
    ("Das Abnahmeprotokoll liegt vor.", "Abnahmeprotokoll"),
]


@pytest.mark.parametrize(("text", "term"), TERMS)
def test_technical_compound_stays_readable(gateway, text: str, term: str) -> None:
    payload, originals = _payload(gateway, text)
    assert term in payload, (term, originals)


# Nachnamen, die selbst Komposita sind oder einem Kopfwort gleichen - muessen weiterhin maskiert werden
NAMES = [
    "Herr Bergmann", "Frau Zimmermann", "Zeuge Steinbrecher", "Herr Hofmeister", "Frau Albrecht", "Herr Zimmer",
    "Frau Leiter", "Herr Rosenschein", "Zeugin Hausmann", "Herr Baumgartner", "Frau Kirchhoff", "Herr Waldleiter",
]


@pytest.mark.parametrize("name", NAMES)
def test_compound_surnames_remain_protected(gateway, name: str) -> None:
    surname = name.split()[-1]
    payload, _ = _payload(gateway, f"{name} erschien am 03.02.2026 und gab eine Erklärung ab. {surname} unterschrieb.")
    assert surname not in payload


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("Mein Sohn soll das Haus erhalten.", "Mein Sohn"),
        ("Meine Tochter wohnt dort.", "Meine Tochter"),
        ("Unser Vater hat das Testament geschrieben.", "Unser Vater"),
    ],
)
def test_possessive_kinship_phrase_is_not_a_name(gateway, text: str, kept: str) -> None:
    payload, originals = _payload(gateway, text)
    assert kept in payload, originals


def test_kinship_word_with_real_name_stays_protected(gateway) -> None:
    payload, _ = _payload(gateway, "Mein Sohn Lukas Brandhuber soll das Haus erhalten.")
    assert "Brandhuber" not in payload and "Lukas" not in payload


def test_document_type_label_unknown_is_not_a_person(gateway) -> None:
    """Lexono schreibt nicht klassifizierte Dokumente als "[Unbekannt] ..." in den Sachverhalt - das ist ein Systemlabel."""
    result = ClaudePrivacyGateway().prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Unbekannt] Schreiben vom 03.02.2026 über 400,00 EUR.",
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
    )
    assert "[Unbekannt]" in result.payload.anonymisierter_sachverhalt



# Red-Team-Review §34.15: blosse, kompositaartige Nachnamen OHNE Anrede - erste Fassung liess 11 von 13 durch
BARE_NAME_SENTENCES = [
    ("Laut Hochleiter ist der Wagen nicht fahrbereit.", "Hochleiter"),
    ("Hochleiter hat die Zahlung verweigert.", "Hochleiter"),
    ("Waldleiter erschien nicht zum Termin.", "Waldleiter"),
    ("Die Zeugin sagte, Sonnleiter sei schuld.", "Sonnleiter"),
    ("Gespräch mit Rosenzimmer am 03.02.2026.", "Rosenzimmer"),
    ("Bitte Sommerdauer anrufen.", "Sommerdauer"),
    ("Kirchleiter schrieb uns am Montag.", "Kirchleiter"),
    ("Die Ware wurde bei Holzleiter bestellt.", "Holzleiter"),
    ("Nach Angaben von Breitschaden fehlt ein Beleg.", "Breitschaden"),
    ("Lindenleiter und Peters waren anwesend.", "Lindenleiter"),
    ("Laut Wiesenfrist ist alles bezahlt.", "Wiesenfrist"),
    ("Zeuge: Waldleiter. Laut Waldleiter war die Ampel rot.", "Waldleiter"),
    ("Die Firma Steinkosten lieferte zu spät.", "Steinkosten"),
    ("Sie zog nach Rosenzimmer um.", "Rosenzimmer"),
]


@pytest.mark.parametrize(("text", "name"), BARE_NAME_SENTENCES)
def test_bare_compound_shaped_surnames_are_not_released(text: str, name: str) -> None:
    """Geschuetzt heisst: nicht im Klartext im Payload - maskiert ODER fail-closed blockiert."""
    result = ClaudePrivacyGateway().prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + text,
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
    )
    if result.allowed:
        assert name not in json.dumps(result.payload.model_dump(), ensure_ascii=False)


def test_unknown_compound_with_name_prone_head_stays_masked_conservatively() -> None:
    """Bewusste Grenze (§34.15): namensnahe Kopfnomen (-dauer, -leiter, -zimmer, ...) werden nur freigegeben, wenn das
    ganze Wort dem Modell bekannt ist und die Namensprobe 0 ergibt - ein seltenes Kompositum wie "Wiederherstellungsdauer"
    bleibt deshalb maskiert (sichere Richtung, Inhaltsverlust in Kauf genommen)."""
    from app.privacy.presidio_ner import is_common_compound

    assert not is_common_compound("Wiederherstellungsdauer")
    assert is_common_compound("Reparaturdauer") and is_common_compound("Zahlungsfrist")
    assert not is_common_compound("Holzleiter") and not is_common_compound("Schlafzimmer")
    # Red-Team Runde 2: auch "sichere" Kopfnomen nur mit Namensprobe 0 - "Gartengrundstücks" (Probe 5) bleibt maskiert
    assert not is_common_compound("Gartengrundstücks") and is_common_compound("Mietvertrag")


# Red-Team Runde 2: erfundene/seltene Namen mit "sicherem" Kopfnomen, ohne Anrede
BARE_SAFE_HEAD_NAMES = [
    ("Laut Waldnotiz ist alles erledigt.", "Waldnotiz"),
    ("Kirchbericht hat die Zahlung verweigert.", "Kirchbericht"),
    ("Gespräch mit Lindenvertrag am 03.02.2026.", "Lindenvertrag"),
    ("Die Firma Steinerbin lieferte zu spät.", "Steinerbin"),
    ("Nach Angaben von Hochbeamter fehlt ein Beleg.", "Hochbeamter"),
    ("Bitte Sonnbericht anrufen.", "Sonnbericht"),
    ("Sie zog nach Rosengrundstück um.", "Rosengrundstück"),
    ("Rotherbin, Klaus, geb. 1970, ist Zeuge.", "Rotherbin"),
]


@pytest.mark.parametrize(("text", "name"), BARE_SAFE_HEAD_NAMES)
def test_bare_names_with_safe_heads_are_not_released(text: str, name: str) -> None:
    result = ClaudePrivacyGateway().prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + text,
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
    )
    if result.allowed:
        assert name not in json.dumps(result.payload.model_dump(), ensure_ascii=False)


def test_role_label_before_a_replaced_person_does_not_block() -> None:
    """LE-T003-Muster mit neuen Namen: "Kontrollbeamter: Polizeihauptmeister Ole Sandbrinker" - die Bezeichnung ist eine
    Rolle; vorher hielt der Restrisiko-Scan sie fuer einen weiteren Namen und blockierte die Anfrage (fail-closed)."""
    result = ClaudePrivacyGateway().prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: Beispielakte\n[Protokoll] Messung um 17:24 Uhr.  Messbeamter: Polizeihauptmeister Ole Sandbrinker  "
                    "Eichschein gültig bis 31.12.2025.",
        anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask",
    )
    assert result.allowed, result.reasons
    payload = json.dumps(result.payload.model_dump(), ensure_ascii=False)
    assert "Sandbrinker" not in payload and "Ole" not in payload and "17:24 Uhr" in payload


def test_speaker_label_with_free_text_is_still_checked() -> None:
    from app.privacy.presidio_ner import is_role_label_before_person

    assert not is_role_label_before_person("Hochleiter: Ich war dort.", len("Hochleiter"))
    assert not is_role_label_before_person("Hochleiter: [PERSON_01] war dort.", len("Hochleiter"))
    assert is_role_label_before_person("Messbeamter: [PERSON_04] [BETEILIGTER_02]  Eichschein", len("Messbeamter"))
