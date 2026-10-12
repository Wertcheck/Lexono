"""Nummern-Strategie (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §22): personenbeziehbare Kennnummern schuetzen, juristisch
und sachlich gebrauchte Zahlen lesbar lassen.

Stufe 1 (deterministisch, ohne Rueckfrage): bezeichnete Bezugsnummern (detectors.detect_referenznummer).
Stufe 2 (unsicher): kennnummernfoermige Folgen OHNE Bezeichnung - Rueckfrage im Modus "ask", vorsichtshalber konsistente
Maskierung als [KENNUNG_nn] im Modus "mask" (review._unlabelled_identifiers).
Gemessen vorher: 26 von 32 schutzbeduerftigen Werten im Klartext. Nur synthetische Daten."""

from __future__ import annotations

import pytest

from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.review import PrivacyDecisions, is_identifier_shaped_term


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _sv(text: str) -> str:
    return "Akte: Beispielakte\n[Schreiben] " + text


def _prepare(gateway, text: str, mode: str, decisions: PrivacyDecisions | None = None):
    return gateway.prepare_request(
        purpose="chat_response", sachverhalt=_sv(text), anwaltliche_anmerkungen="Fasse zusammen.",
        review_mode=mode, privacy_decisions=decisions,
    )


LABELLED = [
    ("Die Versicherungsscheinnummer lautet 4711-882-19.", "4711-882-19"),
    ("Policennummer KV-20391847 der Hausratversicherung.", "KV-20391847"),
    ("Schadennummer 2024-118-77341 wurde angelegt.", "2024-118-77341"),
    ("Patientennummer 100200300 laut Praxis.", "100200300"),
    ("Personalnummer 004417 des Arbeitnehmers.", "004417"),
    ("Mandatsreferenz MR-0099123 für die Lastschrift.", "MR-0099123"),
    ("Ihr Zeichen: SB-4471/23, unser Zeichen: 88/26-KB.", "88/26-KB"),
    ("Vorgangsnummer 260312045 der Krankenkasse.", "260312045"),
    ("Auftragsnummer A-2026-04471 der Werkstatt.", "A-2026-04471"),
    ("Kd.-Nr. 5512398 bei den Stadtwerken.", "5512398"),
    ("Versichertennummer A123456780 der Krankenkasse.", "A123456780"),
    ("Fallnummer 77812 beim Jugendamt.", "77812"),
    ("Laut Schreiben (Ref. KZ-778812) wurde gekündigt.", "KZ-778812"),
    ("Herr Brenner (Kunde 4471123) beschwert sich.", "4471123"),
    ("Der Vertrag 88231457 wurde gekündigt.", "88231457"),
    ("Die Police 7781234-55 deckt Wasserschäden ab.", "7781234-55"),
]


@pytest.mark.parametrize("text, value", LABELLED)
def test_labelled_reference_numbers_are_masked_without_a_question(gateway, text, value) -> None:
    asked = _prepare(gateway, text, "ask")
    assert value not in [c.term for c in asked.review_candidates]
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed, masked.reasons
    assert value not in masked.payload.anonymisierter_sachverhalt


UNLABELLED = [
    ("Bitte überweisen Sie den Betrag auf 1234567890 bei der Sparkasse.", "1234567890"),
    ("Die Kundin ist unter 30018847 registriert.", "30018847"),
    ("Die Versicherung führt den Schaden unter 2024-1187733.", "2024-1187733"),
    ("Unter der Nummer 4471-0815-22 ist der Mandant geführt.", "4471-0815-22"),
    # Zahlungskarte und Rest der Rentenversicherungsnummer: deterministisch, siehe
    # test_card_and_social_insurance_rest_are_never_releasable
]


@pytest.mark.parametrize("text, value", UNLABELLED)
def test_unlabelled_identifiers_ask_first_and_are_masked_in_mask_mode(gateway, text, value) -> None:
    asked = _prepare(gateway, text, "ask")
    assert asked.review_required and asked.payload is None
    assert any(value in c.term for c in asked.review_candidates)
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed, masked.reasons
    payload = masked.payload.anonymisierter_sachverhalt
    assert value not in payload and "[KENNUNG_" in payload
    assert gateway.reconstruct_response(payload, masked.mappings) == _sv(text)


KEEP = [
    ("Der Anspruch folgt aus § 823 Abs. 1 BGB und § 286 Abs. 2 Nr. 1 BGB.", ["§ 823 Abs. 1 BGB", "§ 286 Abs. 2 Nr. 1 BGB"]),
    ("Vgl. BGH VI ZR 123/19, NJW 2020, 2345 Rn. 12.", ["VI ZR 123/19", "NJW 2020, 2345", "Rn. 12"]),
    ("Die Forderung beträgt 1.234.567,89 EUR zzgl. 5 Prozentpunkten.", ["1.234.567,89 EUR", "5 Prozentpunkten"]),
    ("Gebühr nach Nr. 3100 VV RVG, Gegenstandswert 2500000 EUR.", ["Nr. 3100 VV RVG", "2500000 EUR"]),
    ("Siehe BT-Drs. 19/12345, S. 17, und Richtlinie (EU) 2019/1937.", ["19/12345", "2019/1937"]),
    ("Mangel gemäß DIN 18560-2, DIN EN 1504-3 und ISO 27001.", ["DIN 18560-2", "DIN EN 1504-3", "ISO 27001"]),
    ("Laufleistung 1450000 km, Baujahr 2017, Zeitraum 2019-2021.", ["1450000 km", "2017", "2019-2021"]),
    ("Ersatzteil T12345678, Artikelnummer A123456789, Seriennummer CF3297401, EAN 4006381333931.",
     ["T12345678", "A123456789", "CF3297401", "4006381333931"]),
    ("Version 2.4.1.0; Anlage K 3, Seite 12; ECLI:DE:BGH:2020:120520UVIZR123.19.0.", ["Version 2.4.1.0", "Anlage K 3"]),
    ("Kontoverbindung: BLZ 37040044 der Bank.", ["37040044"]),
]


@pytest.mark.parametrize("text, keep", KEEP)
def test_legal_and_factual_numbers_stay_readable_without_a_question(gateway, text, keep) -> None:
    asked = _prepare(gateway, text, "ask")
    assert not [c.term for c in asked.review_candidates if "kennnummernform" in c.roles]
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed, masked.reasons
    for value in keep:
        assert value in masked.payload.anonymisierter_sachverhalt


REVIEW_LEAKS = [
    # Review 13.10.2026, gemessen im Klartext bzw. als Bruchstueck neben einem Platzhalter
    ("Die Kundin ist unter 123.456.789 registriert.", ["123.456.789"]),
    ("Der Vertrag wird unter kv20391847 geführt.", ["kv20391847"]),
    ("Die Kundin ist unter 3001\n8847 registriert.", ["3001\n8847", "8847"]),
    ("Kundennummer 7781 2345 bei den Stadtwerken.", ["7781 2345", "2345"]),
    ("Überweisung auf 3704 0044 0532 0130 00 erbeten.", ["0532", "0130"]),
    ("Der Wagen mit FIN WVWZZZ1JZXW000001 wurde verkauft.", ["WVWZZZ1JZXW"]),
]


@pytest.mark.parametrize("text, leaked", REVIEW_LEAKS)
def test_review_leaks_never_leave_in_plain_text(gateway, text, leaked) -> None:
    asked = _prepare(gateway, text, "ask")
    assert asked.review_required or (asked.payload and not any(v in asked.payload.anonymisierter_sachverhalt for v in leaked))
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed, masked.reasons
    assert not any(v in masked.payload.anonymisierter_sachverhalt for v in leaked)


@pytest.mark.parametrize("text", ["Kd.Nr. 77812345 der Stadtwerke.", "BG-Nummer 12345BG0012345 beim Jobcenter."])
def test_review_blocks_are_resolved_by_masking(gateway, text) -> None:
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed, masked.reasons
    assert "77812345" not in masked.payload.anonymisierter_sachverhalt
    assert "12345BG" not in masked.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text, value",
    [("Gezahlt wurde mit Karte 4111 1111 1111 1111.", "4111 1111 1111 1111"),
     ("Frau Lindner, Nr. 66 120380 L 512, beantragt Rente.", "120380 L 512")],
)
def test_card_and_social_insurance_rest_are_never_releasable(gateway, text, value) -> None:
    """Auch ein "nicht personenbezogen" des Anwalts gibt sie nicht frei (deterministische Stufe)."""
    released = _prepare(gateway, text, "ask", PrivacyDecisions(not_personal=frozenset({value, "120380"})))
    assert released.allowed and value not in released.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text, keep",
    [
        ("Zuständigkeit nach Verordnung (EG) Nr. 1393/2007 und VO 1215/2012.", ["1393/2007", "1215/2012"]),
        ("EuGH, Urt. v. 11.6.2020, C-311/18.", ["C-311/18"]),
        ("Vgl. BeckRS 2021, 1234567 und GVBl. 2020, 1234567.", ["BeckRS 2021, 1234567"]),
        ("Der Gegenstandswert wird auf 2500000 festgesetzt.", ["2500000"]),
        ("Das Land hat 83200000 Einwohner.", ["83200000"]),
        ("Kommentar, ISBN 978-3-406-12345-6, S. 17.", ["978-3-406-12345-6"]),
    ],
)
def test_review_false_positives_stay_readable(gateway, text, keep) -> None:
    asked = _prepare(gateway, text, "ask")
    assert not [c.term for c in asked.review_candidates if "kennnummernform" in c.roles]
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed, masked.reasons
    for value in keep:
        assert value in masked.payload.anonymisierter_sachverhalt


def test_juris_document_id_triggers_no_identifier_question(gateway) -> None:
    """Review: oeffentliche juris-Dokumentnummer - keine Rueckfrage (die NER maskiert die Wendung vorbestehend teils als
    Person; das ist nicht Gegenstand der Nummern-Strategie)."""
    asked = _prepare(gateway, "Juris-Dokument KORE312342019 zitiert.", "ask")
    assert not [c.term for c in asked.review_candidates if "kennnummernform" in c.roles]


@pytest.mark.parametrize("separator", ["–", "‑", "\n"])
def test_other_separators_inside_a_number_are_asked_and_the_decision_matches(gateway, separator) -> None:
    """Re-Review: Gedankenstrich, geschuetzter Bindestrich und Zeilenumbruch. Tabulator/Doppelleerzeichen bewusst nicht
    (3. Runde: verbanden Tabellenspalten und Aktenzeichen-Nachbarn) - dokumentierte Restluecke, siehe §22.
    Der gespeicherte Begriff muss die Originalschreibweise treffen, sonst fragt Lexono endlos."""
    text = f"Die Kundin ist unter 3001{separator}8847 registriert."
    asked = _prepare(gateway, text, "ask")
    terms = [c.term for c in asked.review_candidates if "kennnummernform" in c.roles]
    assert asked.review_required and len(terms) == 1 and is_identifier_shaped_term(terms[0])
    decided = _prepare(gateway, text, "ask", PrivacyDecisions(personal=frozenset(terms)))
    assert decided.allowed and "8847" not in decided.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "text", ["Kundennummer 4471-0815-22 gemäß § 22 BGB.", "Kundennummer 30018847, siehe Rn. 1884.",
             "IBAN DE89 3704 0044 0532 0130 00, Termin um 10:00 Uhr."],
)
def test_fully_masked_number_is_not_asked_because_a_part_appears_elsewhere(gateway, text) -> None:
    asked = _prepare(gateway, text, "ask")
    assert not [c.term for c in asked.review_candidates if "kennnummernform" in c.roles]


@pytest.mark.parametrize(
    "text, value",
    [("Der Betrag betrug 1234567890128 EUR.", "1234567890128"), ("Siehe Seite 1234567890128.", "1234567890128"),
     ("ISBN 9783406123457 im Verzeichnis.", "9783406123457")],
)
def test_luhn_valid_numbers_in_amount_or_source_context_are_no_card(gateway, text, value) -> None:
    from app.privacy.detectors import detect_zahlungskarte_und_sv_rest

    assert value not in [s.value for s in detect_zahlungskarte_und_sv_rest(text)]


def test_identifier_label_next_to_masked_number_is_not_asked_as_a_name(gateway) -> None:
    asked = _prepare(gateway, "Kfz-Haftpflicht: Schadennummer 12345678X001 der Versicherung.", "ask")
    assert "Schadennummer" not in [c.term for c in asked.review_candidates]


@pytest.mark.parametrize("separator", [" ", "\t"])
def test_number_after_a_file_reference_is_not_lost_in_the_overlap(gateway, separator) -> None:
    """3. Review-Runde: der Kandidat "345/26 1234567" ragte ins Aktenzeichen, das die Ueberlappung gewann - "1234567" ging
    im Klartext hinaus. Jetzt wird an der Detektorgrenze gekuerzt."""
    text = f"Az. 12 O 345/26{separator}1234567 Kläger"
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed and "1234567" not in masked.payload.anonymisierter_sachverhalt
    asked = _prepare(gateway, text, "ask")
    assert "1234567" in [c.term for c in asked.review_candidates]


@pytest.mark.parametrize(
    "text", ["Pos\tMenge\tPreis\n1\t1200\t3400\n2\t1500\t2600", "Zeitraum 2019 2020 2021", "Umsatz  1234  5678  9012"]
)
def test_tables_and_year_lists_are_not_merged_into_an_identifier(gateway, text) -> None:
    asked = _prepare(gateway, text, "ask")
    assert not [c.term for c in asked.review_candidates if "kennnummernform" in c.roles]


def test_fourth_review_round_regressions(gateway) -> None:
    from app.privacy.review import detect_decision_terms

    # Rest nach Aktenzeichen mit Bindestrich: "-123456" ging im Klartext hinaus
    masked = _prepare(gateway, "Az. 12 O 345/26-123456 Kläger", "mask")
    assert masked.allowed and "123456" not in masked.payload.anonymisierter_sachverhalt
    # Langer Kandidat muss als Entscheidung speicherbar sein (sonst endlose Rueckfrage) und als Kennung maskiert werden
    text = "unter 123456789 123456789 123456789 123456789 123456789"
    terms = [c.term for c in _prepare(gateway, text, "ask").review_candidates if "kennnummernform" in c.roles]
    assert terms and all(is_identifier_shaped_term(t) for t in terms)
    assert "[PERSON_" not in _prepare(gateway, text, "mask").payload.anonymisierter_sachverhalt
    # Kurze Entscheidung ("12345") trifft keine Fundstelle, keinen Betrag, keinen Paragraphen
    keep = "BT-Drs. 19/12345; BeckRS 2020, 12345; Streitwert 12345 EUR; § 12345; Konto 12345 bei der Bank."
    hits = [s.start for s in detect_decision_terms(keep, {"12345"})]
    assert hits == [keep.index("Konto 12345") + len("Konto ")]


def test_measurement_chain_h9_regressions(gateway) -> None:
    # ISO-Datum mit Uhrzeit ist keine Kennnummer (vorher Kandidat und danach Blockade des Logauszugs)
    log = "Logauszug:  2025-01-01 10:00 IP 203.0.113.5 Login karl.brenner@example.org"
    assert _prepare(gateway, log, "mask").allowed
    assert not [c.term for c in _prepare(gateway, log, "ask").review_candidates if "kennnummernform" in c.roles]
    # Steuernummer am Zeilenende: kein Schnitt an einer nur im lokalen Text vorhandenen "Anschrift" ueber Zeilen hinweg
    text = "Kennzeichen HH-AB 1234Steuernummer 143/815/08154"
    masked = _prepare(gateway, text, "mask")
    assert masked.allowed and "08154" not in masked.payload.anonymisierter_sachverhalt
    assert "143/815/08154" in [c.term for c in _prepare(gateway, text, "ask").review_candidates]


def test_land_register_sheet_is_not_treated_as_a_page_number() -> None:
    from app.privacy.review import _unlabelled_identifiers

    assert _unlabelled_identifiers("Grundbuch von Musterstadt Blatt 1234567")
    assert not _unlabelled_identifiers("Blatt 1234567 der Gerichtsakte")


def test_repeated_identifier_gets_one_consistent_placeholder(gateway) -> None:
    text = "4471123 zahlte am Montag; 4471123 hatte schon im Vormonat gezahlt, anders als 9988776."
    payload = _prepare(gateway, text, "mask").payload.anonymisierter_sachverhalt
    first, second = payload.split("zahlte")[0].split()[-1], payload.split(";")[1].split()[0]
    assert first == second and first.startswith("[KENNUNG_")
    assert payload.count(first) == 2 and "9988776" not in payload


def test_mixed_case_keeps_chronology_amounts_and_sources(gateway) -> None:
    text = "Kunde 4471123 zahlte 1.200,00 EUR auf 1234567890; Anspruch aus § 433 Abs. 2 BGB, vgl. NJW 2019, 1234."
    masked = _prepare(gateway, text, "mask")
    payload = masked.payload.anonymisierter_sachverhalt
    assert "4471123" not in payload and "1234567890" not in payload
    for value in ("1.200,00 EUR", "§ 433 Abs. 2 BGB", "NJW 2019, 1234"):
        assert value in payload
    assert gateway.reconstruct_response(payload, masked.mappings) == _sv(text)


def test_lawyer_decision_on_an_identifier_is_respected(gateway) -> None:
    text = "Bitte überweisen Sie auf 1234567890 bei der Sparkasse."
    sent = _prepare(gateway, text, "ask", PrivacyDecisions(not_personal=frozenset({"1234567890"})))
    assert sent.allowed and "1234567890" in sent.payload.anonymisierter_sachverhalt
    masked = _prepare(gateway, text, "ask", PrivacyDecisions(personal=frozenset({"1234567890"})))
    assert masked.allowed and "1234567890" not in masked.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize(
    "term, shaped",
    [("1234567890", True), ("4471-0815-22", True), ("KZ-778812", True), ("Richter", False), ("1234", False),
     ("12.345,67", False), ("a@b 1234567", False), ("1234567\n890", False), ("[KENNUNG_01]", False)],
)
def test_identifier_shaped_terms_for_stored_decisions(term, shaped) -> None:
    assert is_identifier_shaped_term(term) is shaped
