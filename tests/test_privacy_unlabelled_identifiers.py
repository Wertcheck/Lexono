"""Unbeschriftete, kennzeichenfoermige Kennnummern (Sicherheitspruefung 13.10.2026, app/privacy/review.py _PLATE_SHAPE).

Gemessen vorher: "Kennzeichen des Gegners: D-XY 123" ging im Klartext an die Cloud - die Bezeichnung steht nicht
unmittelbar vor dem Wert, presidio_ner maskiert nur bezeichnete Kennnummern. Die Form allein ist mehrdeutig: im Modus
"ask" (Chat) fragt Lexono vor dem Versand, im Modus "mask" wird vorsichtshalber maskiert. Nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.review import PrivacyDecisions, detect_decision_terms


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway, text: str, mode: str, decisions: PrivacyDecisions | None = None):
    return gateway.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: Beispielakte\n[Schreiben] " + text,
        anwaltliche_anmerkungen="Fasse zusammen.",
        review_mode=mode,
        privacy_decisions=decisions,
    )


PLATES = [
    ("Kennzeichen des Gegners: D-XY 123", "D-XY 123"),
    ("Das Fahrzeug des Unfallgegners, amtlich HH-AB 1234, fuhr zu schnell.", "HH-AB 1234"),
    ("Zeugin notierte noch D-XY 4711 am Unfallort.", "D-XY 4711"),
    # Review: Schreibweise des Schildes, Gedankenstrich (Autokorrektur), Zeilenumbruch (PDF), eckige Klammern
    ("Der Gegner fuhr D XY 123 zu schnell.", "D XY 123"),
    ("Der Wagen D–XY 123 stand quer.", "D–XY 123"),
    ("Beschaedigt wurde [B-AB 12] im Hof.", "B-AB 12"),
    # vorher bewusst lesbar gelassen (test_privacy_remaining_leaks), jetzt Rueckfrage bzw. Maskierung (§21.3)
    ("Lkw-Flotte: Fahrzeuge KL AB 12, KL CD 34 und KL EF 56 der Spedition.", "KL EF 56"),
]
# Diese Formen erkennt bereits das NER-Modell (als Ort) - entscheidend ist hier nur: kein Klartext-Kennzeichen.
PLATES_ALSO_SEEN_BY_NER = [
    ("Gesehen wurde M-AB 123E vor dem Haus.", "M-AB 123E"),
    ("Halter von KÖ-AB 12 ist unbekannt.", "KÖ-AB 12"),
    ("Zeuge notierte D-XY\n123 am Unfallort.", "D-XY\n123"),
]


@pytest.mark.parametrize("text, plate", PLATES)
def test_ask_mode_asks_before_sending_an_unlabelled_plate(gateway, text, plate) -> None:
    result = _prepare(gateway, text, "ask")
    assert not result.allowed and result.payload is None and result.review_required
    assert plate in [c.term for c in result.review_candidates]


@pytest.mark.parametrize("text, plate", PLATES)
def test_mask_mode_masks_an_unlabelled_plate_as_identifier(gateway, text, plate) -> None:
    result = _prepare(gateway, text, "mask")
    assert result.allowed, result.reasons
    payload = result.payload.anonymisierter_sachverhalt
    assert plate not in payload and "[KENNUNG_" in payload
    assert plate in gateway.reconstruct_response(payload, result.mappings)


@pytest.mark.parametrize(
    "plate",
    # Review: Unterscheidungszeichen, die zugleich wie Normen-/Funktionswoerter aussehen, duerfen nie ausgenommen sein
    ["EN-AB 123", "GG-AB 123", "V-AB 123", "UN-AB 12", "EU-AB 12", "DIN-AB 12", "B-AR 123", "B-RR 1234", "SB AB 123",
     "RA XY 123", "AM AB 12", "AN AB 12", "KM AB 12", "DEM AB 123"],
)
def test_real_district_codes_are_never_exempt(gateway, plate) -> None:
    result = _prepare(gateway, f"Kennzeichen des Gegners: {plate}", "mask")
    assert result.allowed, result.reasons
    assert plate not in result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize("mode", ["ask", "mask"])
@pytest.mark.parametrize("text, plate", PLATES_ALSO_SEEN_BY_NER)
def test_plate_never_leaves_in_plain_text(gateway, text, plate, mode) -> None:
    result = _prepare(gateway, text, mode)
    assert result.payload is None or plate not in result.payload.anonymisierter_sachverhalt


def test_lawyer_decision_not_personal_sends_the_plate_unchanged(gateway) -> None:
    result = _prepare(gateway, PLATES[0][0], "ask", PrivacyDecisions(not_personal=frozenset({"D-XY 123"})))
    assert result.allowed, result.reasons
    assert "D-XY 123" in result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text",
    [
        "Die Abdichtung entspricht DIN-EN 1504 nicht.",
        "Geprueft nach ISO-IEC 27001 und VDE-AR 4105.",
        "Nach § 823 Abs. 1 BGB und § 286 ZPO haftet der Schuldner.",
        "Der Betrag von 1.234,56 EUR ist faellig, Az. 12 O 345/26.",
        "Die Leistung nach VOB/B § 13 Nr. 5 ist mangelhaft.",
        "Raum B-12 im 3. OG, Termin um 10-12 Uhr.",
        # Review: Zeitschriften-Reporte, Senats-/Registerzeichen, Gliederungen
        "Vgl. NJW-RR 2020, 1234 und NZA-RR 2019, 12.",
        "BGH, Urt. v. 1.2.2020 - IX ZR 123/20; BGH VI ZR 12/19.",
        "Siehe unter II A 3 und Ziff. IV B 2 der Begruendung.",
        # Leerzeichenform ohne Kennzeichen-Bedeutung (Fehlalarm-Satz)
        "Das Fahrzeug, ein VW T 5 Transporter, und der KIA EV 6 wurden besichtigt.",
        "BETREFF: VERKEHRSUNFALL AUF DER B 51 AM 12.03.2026",
        "Regulierung Kfz-Schaden, Ihr Zeichen: SB KL 123, unser Zeichen: RA BK 26.",
    ],
)
def test_ordinary_legal_text_triggers_no_identifier_question(gateway, text) -> None:
    result = _prepare(gateway, text, "ask")
    assert result.allowed and not result.review_required, [c.term for c in result.review_candidates]


def test_decision_term_category_follows_the_term_shape() -> None:
    spans = detect_decision_terms("Herr Brenner fuhr D-XY 123.", {"Brenner", "D-XY 123"})
    assert {(s.value, s.category) for s in spans} == {("Brenner", "person"), ("D-XY 123", "kennung")}
