"""§69: Befunde F4/F6 aus dem App-Test §68 (installierter Build e8d0a0f).

F4 - Fristauszug begann mitten in der Gerichtsbezeichnung ("Kiel, Az. ..."): "Kiel" war dort ein gewoehnlicher Ort, die
Wiederholungsregel maskierte ihn dann auch im Sachverhalt. Jetzt gehoert die Gerichtsart aus dem ORIGINALTEXT zum Auszug
(neu erzeugt) bzw. wird fuer gespeicherte Auszuege nur ergaenzt, wenn sie im Volltext nachweisbar davor steht.
F6 - die System-Typbezeichnung "[Gerichtliches Schreiben]" wurde von der NER uneinheitlich als Person gelesen; der
Restrisiko-Scan blockierte dann fail-closed. Treffer genau innerhalb dieser Bezeichnungen (geschlossenes Vokabular)
entfallen. Nur synthetische Daten, keine Cloud-Aufrufe."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.deadlines.extractor import PlaceholderDeadlineExtractor, with_institution_prefix
from app.models import Client, Deadline, Document, Matter
from app.privacy.gateway import ClaudePrivacyGateway
from tests.test_upload_without_ai import db_session  # noqa: F401  (Fixture)

COURT_TEXT = "Klage beim Amtsgericht Kiel, Az. 14 C 233/26, Termin zur mündlichen Verhandlung am 15.04.2026."


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


# --- F4: Fristauszug -----------------------------------------------------------------------------------------------


def test_a_new_excerpt_keeps_the_court_type_from_the_original_text() -> None:
    excerpts = [e.source_text for e in PlaceholderDeadlineExtractor().extract(COURT_TEXT)]
    assert excerpts and all(e.startswith("Amtsgericht Kiel") for e in excerpts), excerpts


@pytest.mark.parametrize(
    "snippet, full, expected",
    [
        ("Kiel, Az. 14 C 233/26, Termin am 15.04.2026.", "Klage beim Amtsgericht Kiel, Az. 14 C 233/26, Termin am "
         "15.04.2026.", "Amtsgericht Kiel, Az. 14 C 233/26, Termin am 15.04.2026."),
        ("Kiel, Termin am 15.04.2026.", "Herr Weber wohnt in Kiel, Termin am 15.04.2026.", "Kiel, Termin am 15.04.2026."),
        ("Kiel, Termin am 15.04.2026.", None, "Kiel, Termin am 15.04.2026."),  # kein Volltext: unveraendert
        ("Kiel, Termin am 15.04.2026.", "Ganz anderer Text.", "Kiel, Termin am 15.04.2026."),  # nicht nachweisbar
        ("Kiel, Termin am 15.04.2026.", "Amtsgericht Kiel, Termin am 15.04.2026. Wohnort Kiel, Termin am 15.04.2026.",
         "Kiel, Termin am 15.04.2026."),  # nicht eindeutig: unveraendert
    ],
)
def test_stored_excerpts_are_only_extended_when_provable(snippet, full, expected) -> None:
    assert with_institution_prefix(snippet, full) == expected


def _payload(db_session, text: str, excerpt: str) -> str:
    from tests.test_web_chat import FakeClaudeWritingProvider, _working_drafting_service

    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Fristauszug")
    db_session.add(matter)
    db_session.flush()
    document = Document(matter_id=matter.id, file_path="x", original_filename="ladung.txt", extracted_text=text,
                        ocr_status="not_needed", classified_type="Gerichtliches Schreiben")
    db_session.add(document)
    db_session.flush()
    # wie ein vor §69 gespeicherter Vorschlag: Auszug beginnt mitten in der Gerichtsbezeichnung
    db_session.add(Deadline(matter_id=matter.id, document_id=document.id, source_text=excerpt,
                            due_date=date(2026, 4, 15), reasoning="Art: Termin", review_status="unreviewed"))
    db_session.commit()
    writer = FakeClaudeWritingProvider("Entwurf.")
    _working_drafting_service(writer).create_draft(matter.id, "formulate_draft", db_session)
    assert writer.received_payloads, "keine Anfrage erzeugt"
    return json.dumps(writer.received_payloads[-1].model_dump(), ensure_ascii=False)


def test_court_stays_readable_in_text_and_excerpt_and_file_number_stays_masked(db_session) -> None:
    sent = _payload(db_session, COURT_TEXT, "Kiel, Az. 14 C 233/26, Termin zur mündlichen Verhandlung am 15.04.2026.")
    assert sent.count("Amtsgericht Kiel") >= 2  # Sachverhalt und Fristauszug
    assert "[ORT_" not in sent and "14 C 233/26" not in sent and "15.04.2026" in sent


def test_a_residence_in_the_excerpt_stays_masked(db_session) -> None:
    text = "Herr Bertram Ollendorf wohnt in Kiel, Termin zur mündlichen Verhandlung am 15.04.2026."
    sent = _payload(db_session, text, "Kiel, Termin zur mündlichen Verhandlung am 15.04.2026.")
    assert "Kiel" not in sent and "Ollendorf" not in sent


def test_a_seat_in_an_address_keeps_court_and_excerpt_masked(db_session) -> None:
    text = "Amtsgericht Kiel, Ladung an Frau Hannelore Brüggemann, Am Weidenhang 17, 24103 Kiel, Termin am 15.04.2026."
    sent = _payload(db_session, text, "Kiel, Ladung an Frau Hannelore Brüggemann, Am Weidenhang 17, 24103 Kiel, "
                                      "Termin am 15.04.2026.")
    for value in ("Kiel", "Brüggemann", "Weidenhang", "24103"):
        assert value not in sent, value


# --- F6: System-Typbezeichnung --------------------------------------------------------------------------------------


def test_the_f6_case_is_no_longer_blocked_and_still_masks_everything(gateway) -> None:
    sach = ("Akte: QA-68 Fall sechs\n[Gerichtliches Schreiben] Amtsgericht Kiel, Ladung an Frau Hannelore Brüggemann, "
            "Am Weidenhang 17, 24103 Kiel.")
    result = gateway.prepare_request(purpose="chat_response", sachverhalt=sach,
                                     anwaltliche_anmerkungen="Fasse den Sachverhalt der Akte zusammen.",
                                     known_entities={"mandant": ["Synthetische Mandantin QA GmbH"]}, review_mode="ask")
    assert result.allowed and result.payload is not None, result.reasons
    sent = json.dumps(result.payload.model_dump(), ensure_ascii=False)
    assert "[Gerichtliches Schreiben]" in sent  # Systembezeichnung lesbar
    for value in ("Kiel", "Brüggemann", "Weidenhang", "24103"):
        assert value not in sent, value


@pytest.mark.parametrize("label", ["Gerichtliches Schreiben", "Klage/Schriftsatz", "Kündigungsschreiben", "Vertrag",
                                   "Unbekannt", "unklassifiziert"])
def test_document_type_labels_are_not_masked_but_names_after_them_are(gateway, label) -> None:
    result = gateway.prepare_request(purpose="formulate_draft",
                                     sachverhalt=f"Akte\n[{label}] Herr Bertram Ollendorf schreibt am 03.03.2026.")
    assert result.payload is not None, result.reasons
    sach = result.payload.anonymisierter_sachverhalt
    assert f"[{label}]" in sach and "Ollendorf" not in sach


def test_a_label_word_outside_the_system_label_position_is_not_exempted(gateway) -> None:
    """Nur am Zeilenanfang in eckigen Klammern - im Fliesstext gilt die normale Erkennung."""
    result = gateway.prepare_request(purpose="formulate_draft",
                                     sachverhalt="Akte\nDer Brief an Frau Gerichtliches-Ollendorf wurde zugestellt.")
    sent = json.dumps(result.payload.model_dump(), ensure_ascii=False) if result.payload else ""
    assert "Ollendorf" not in sent


def test_ner_failure_still_blocks_with_labels_and_excerpts() -> None:
    from app.privacy.pseudonymizer import Pseudonymizer

    def failing(*args, **kwargs):
        raise RuntimeError("NER nicht verfuegbar (simuliert)")

    gateway = ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(ner_detector=failing))
    try:
        result = gateway.prepare_request(purpose="formulate_draft",
                                         sachverhalt="Akte\n[Gerichtliches Schreiben] Amtsgericht Kiel, Frau Hannelore "
                                                     "Brüggemann.",
                                         argumentationspunkte=["Möglicher Termin (unreviewed): 15.04.2026 :: "
                                                               "Amtsgericht Kiel, Termin am 15.04.2026."])
        created = bool(result.allowed and result.payload is not None)
    except Exception:  # noqa: BLE001
        created = False
    assert not created
