"""Firmennamen nach Vertraulichkeit (24.10.2026). Im allgemeinen Wissenschat (kein Akten-/Mandanten-/Dokumentkontext)
bleiben oeffentliche Firmierungen lesbar - offenbart der Beitrag aber eine Mandatsbeziehung ("Unsere Mandantin, die ...
GmbH"), wird die Firma samt Kurzform und spaeteren Erwaehnungen geschuetzt. Im Aktenkontext bleibt alles wie bisher
(volle Pseudonymisierung), Personen/Anschriften/Kennnummern sind unberuehrt.

Ausschliesslich synthetische Daten. Cloud-Aufrufe nur gegen den lokalen Loopback-Recorder bzw. Test-Doubles."""

from __future__ import annotations

import json

import pytest

import app.privacy.detectors as detectors_module
from app.privacy.detectors import DetectedSpan, is_confidential_organisation_mention
from app.privacy.gateway import (
    PRIVACY_CHECK_FAILED_REASON,
    SHORT_FORM_STATUTE_COLLISION_REASON,
    ClaudePrivacyGateway,
)
from tests.test_upload_without_ai import _ask, _pdf_bytes, _upload, _wire, client, db_session, recorder, writer  # noqa: F401


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _general(gateway, question: str, **fields):
    """Chat ohne Akte - exakt die Bedingung aus DraftingService._prepare_and_gate."""
    return gateway.prepare_request(purpose="chat_response", sachverhalt="Allgemeine Frage ohne Aktenbezug.",
                                   anwaltliche_anmerkungen=question, skip_general_knowledge_pseudonymization=True,
                                   **fields)


def _outgoing(result) -> str:
    return json.dumps(result.payload.model_dump(), ensure_ascii=False)


# --- allgemeiner Wissenschat: oeffentliche Firmierungen bleiben lesbar --------------------------------------------


@pytest.mark.parametrize(
    "question, firm",
    [
        ("Wie hoch war der Umsatz der Deutsche Bahn AG im Jahr 2024?", "Deutsche Bahn AG"),
        ("Welche Pflichten hat die Vonovia SE als Vermieterin nach § 535 BGB?", "Vonovia SE"),
        ("Im veröffentlichten BGH-Urteil war die Beklagte die Deutsche Bank AG.", "Deutsche Bank AG"),
        ("Was regelt der Gesellschaftsvertrag einer typischen Muster Holding GmbH?", "Muster Holding GmbH"),
    ],
)
def test_public_firm_in_a_general_question_stays_readable(gateway, question: str, firm: str) -> None:
    result = _general(gateway, question)
    assert result.allowed, result.reasons
    assert firm in _outgoing(result)


# --- allgemeiner Wissenschat: Mandatsbezug im Beitrag -> geschuetzt ----------------------------------------------


@pytest.mark.parametrize(
    "question, firm",
    [
        ("Unsere Mandantin, die Bremer Grundbesitz GmbH, will den Mietvertrag kündigen.", "Bremer Grundbesitz"),
        ("Wir vertreten die Nordbauholding GmbH gegen einen Lieferanten.", "Nordbauholding"),
        ("Die Gegenseite, die Kran-Logistik AG, verweigert die Zahlung.", "Kran-Logistik"),
        ("Mein Mandant hat ein Konto bei der Deutsche Bank AG - ist die Kündigung wirksam?", "Deutsche Bank"),
        ("Unsere Mandantin hat ein Problem. Die Bremer Grundbesitz GmbH zahlt nicht.", "Bremer Grundbesitz"),
        ("Unser Auftraggeber, die Muster Logistik KG, fragt nach § 280 BGB.", "Muster Logistik"),
    ],
)
def test_firm_in_a_mandate_message_is_protected(gateway, question: str, firm: str) -> None:
    result = _general(gateway, question)
    assert result.allowed, result.reasons
    assert firm not in _outgoing(result)


F = "Bremer Grundbesitz GmbH"

MANDATE_PHRASINGS = [
    # anwaltliche Pruefung, Beratung, Vertretung - beide Wortstellungen
    f"Für die {F} prüfen wir gerade die Kündigung.",
    f"Wir prüfen gerade für die {F}, ob die Kündigung wirksam ist.",
    f"Wir beraten die {F} zu einer Kündigung.",
    f"Die {F} beraten wir seit 2024.",
    f"Wir betreuen die {F} arbeitsrechtlich.",
    f"Wir sind für die {F} tätig.",
    f"Ich vertrete die {F} vor dem Landgericht.",
    # ausdrueckliche Beauftragung, bestehendes Mandat
    f"Die {F} hat uns beauftragt.",
    f"Die {F} hat uns mit der Prüfung der Kündigung beauftragt.",
    f"Wir wurden von der {F} beauftragt, den Vertrag zu prüfen.",
    f"Die {F} beauftragte mich mit der Durchsetzung der Forderung.",
    f"Wir haben ein Mandat der {F}.",
    f"Das Mandat für die {F} betrifft eine Mietsache.",
    f"Die {F} hat uns mandatiert.",
    # Mandantin oder Gegnerin
    f"Die {F} ist unsere Mandantin.",
    f"Die {F} ist Gegnerin in unserem Verfahren.",
    f"Gegen die {F} haben wir Klage erhoben.",
    f"Wir wollen gegen die {F} vorgehen.",
    # ueber mehrere Saetze desselben Beitrags
    f"Unsere Mandantin hat ein Problem. Die {F} zahlt nicht.",
    f"Die {F} zahlt nicht. Sie hat uns gestern beauftragt.",
]


@pytest.mark.parametrize("question", MANDATE_PHRASINGS)
def test_mandate_phrasings_protect_the_firm(gateway, question: str) -> None:
    result = _general(gateway, question)
    assert result.allowed, result.reasons
    assert "Bremer Grundbesitz" not in _outgoing(result)


@pytest.mark.parametrize(
    "question, firm",
    [
        ("Gilt § 1 AEG auch für die Deutsche Bahn AG?", "Deutsche Bahn AG"),  # "für" ohne erste Person
        ("Welche Klagen gegen die Volkswagen AG wurden im Dieselskandal erhoben?", "Volkswagen AG"),
        ("Der Bundesgerichtshof entschied gegen die Deutsche Bank AG.", "Deutsche Bank AG"),
        ("Wer vertritt die Deutsche Bahn AG gesetzlich?", "Deutsche Bahn AG"),  # Vertretung ohne erste Person
        ("Wie beraten Banken wie die Deutsche Bank AG ihre Kunden?", "Deutsche Bank AG"),
        ("Die Mandanten Service GmbH bietet Software an - welche Lizenz gilt?", "Mandanten Service GmbH"),
    ],
)
def test_public_discussion_without_a_mandate_stays_readable(gateway, question: str, firm: str) -> None:
    result = _general(gateway, question)
    assert result.allowed, result.reasons
    assert firm in _outgoing(result)


def test_the_firm_name_alone_never_signals_a_mandate(gateway) -> None:
    """Signalwoerter IM Firmennamen zaehlen nicht ("Gegner Consulting AG"). Die fruehere Personenmaskierung von
    "Consulting" ist seit 24.10.2026 behoben, siehe tests/test_privacy_firm_generic_words.py."""
    for question in ("Wie ist die Gegner Consulting AG im Handelsregister eingetragen?",
                     "Die Mandanten Service GmbH bietet Software an."):
        result = _general(gateway, question)
        assert result.allowed and not [m for m in result.mappings if m.category == "organisation"], question


def test_only_the_firm_after_for_or_against_is_protected_in_a_sentence_without_mandate_words(gateway) -> None:
    result = _general(gateway, "Gegen die Bremer Grundbesitz GmbH haben wir Klage erhoben; die Deutsche Bahn AG ist "
                               "nur Zeugin.")
    payload = _outgoing(result)
    assert "Bremer Grundbesitz" not in payload and "Deutsche Bahn AG" in payload


def test_a_mandate_message_protects_every_firm_in_it(gateway) -> None:
    """Geltungsbereich Absatz: in einem Beitrag mit ausdruecklichem Mandatssignal wird auch eine zweite Firma geschuetzt
    (vertrauliche Geschaeftsbeziehung moeglich; Preis: oeffentliche Firmen dort ebenfalls maskiert)."""
    result = _general(gateway, f"Unsere Mandantin, die {F}, streitet mit der Kran-Logistik AG.")
    payload = _outgoing(result)
    assert "Bremer Grundbesitz" not in payload and "Kran-Logistik" not in payload


@pytest.mark.parametrize("earlier", [
    "Anwalt: Wir vertreten die Nordbauholding GmbH.",
    "Anwalt: Die Nordbauholding GmbH hat uns beauftragt.",
])
def test_a_mandate_in_an_earlier_message_is_not_carried_over(gateway, earlier: str) -> None:
    result = _general(gateway, "Wie hoch war der Umsatz der Deutsche Bahn AG im Jahr 2024?", gespraechsverlauf=[earlier])
    payload = _outgoing(result)
    assert "Deutsche Bahn AG" in payload  # neue, allgemeine Frage
    assert "Nordbauholding" not in payload  # die fruehere Mandatsnachricht selbst bleibt geschuetzt


def test_later_mentions_and_short_forms_follow_the_protected_firm(gateway) -> None:
    result = _general(gateway, "Unsere Mandantin, die Nordbauholding GmbH (NBH), wurde abgemahnt. Die NBH bestreitet "
                               "den Anspruch aus § 433 BGB; die Nordbauholding GmbH will Klage erheben.")
    assert result.allowed
    payload = _outgoing(result)
    assert "Nordbauholding" not in payload and "NBH" not in payload
    assert "§ 433 BGB" in payload


def test_mandate_signal_in_another_message_does_not_reach_a_general_question(gateway) -> None:
    """Geltungsbereich Absatz/Beitrag: ein frueherer Mandatsbeitrag macht eine spaetere allgemeine Frage nicht vertraulich."""
    result = _general(gateway, "Wie hoch war der Umsatz der Deutsche Bahn AG im Jahr 2024?",
                      gespraechsverlauf=["Anwalt: Unsere Mandantin hat eine Frage zur Kündigung."])
    assert result.allowed and "Deutsche Bahn AG" in _outgoing(result)


def test_persons_addresses_and_tax_numbers_stay_protected_in_both_cases(gateway) -> None:
    for prefix in ("Unsere Mandantin, die Bremer Grundbesitz GmbH,", "Die Deutsche Bahn AG"):
        result = _general(gateway, f"{prefix} schreibt an Frau Lena Vogt, Ringstraße 21, 10115 Berlin, "
                                   "Steuernummer 12/345/67890.")
        assert result.allowed
        payload = _outgoing(result)
        for value in ("Lena Vogt", "Vogt", "Ringstraße 21", "12/345/67890"):
            assert value not in payload, (prefix, value)


def test_collision_with_a_statute_in_a_mandate_message_is_blocked(gateway) -> None:
    result = _general(gateway, "Unsere Mandantin, die Bremer Grundbesitz GmbH (BGB), zahlt nicht. Die BGB verweigert "
                               "die Zahlung nach § 823 BGB.")
    assert not result.allowed and result.payload is None
    assert result.reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]


def test_statute_citations_and_terms_stay_readable_in_a_mandate_message(gateway) -> None:
    result = _general(gateway, "Unsere Mandantin fragt: Gilt § 823 Abs. 1 BGB auch für die Rohergebnis-Berechnung "
                               "nach § 275 HGB?")
    assert result.allowed
    payload = _outgoing(result)
    assert "§ 823 Abs. 1 BGB" in payload and "§ 275 HGB" in payload


def test_signal_words_inside_other_words_are_no_signal() -> None:
    span = DetectedSpan(category="organisation", start=0, end=16, value="Deutsche Bahn AG")
    assert not is_confidential_organisation_mention("Deutsche Bahn AG - Mandantenzuordnung offen", span)
    assert not is_confidential_organisation_mention("Deutsche Bahn AG - Gegnerschaftsthese", span)
    assert is_confidential_organisation_mention("Deutsche Bahn AG ist unsere Mandantin", span)
    person = DetectedSpan(category="person", start=0, end=4, value="Vogt")
    assert not is_confidential_organisation_mention("Vogt ist Mandantin", person)  # nur Firmen


def test_failure_of_the_confidentiality_check_blocks(gateway, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("Pruefung nicht moeglich")

    monkeypatch.setattr(detectors_module, "is_confidential_organisation_mention", broken)
    result = _general(gateway, "Unsere Mandantin, die Bremer Grundbesitz GmbH, will kündigen.")
    assert not result.allowed and result.payload is None and result.reasons == [PRIVACY_CHECK_FAILED_REASON]


# --- Aktenkontext: unveraendert volle Pseudonymisierung -------------------------------------------------------------


@pytest.mark.parametrize("role", ["mandant", "gegner"])
def test_firm_as_matter_party_is_protected(gateway, role: str) -> None:
    result = gateway.prepare_request(purpose="chat_response", sachverhalt="Akte: Mietsache",
                                     anwaltliche_anmerkungen="Was schreibt die Deutsche Bahn AG zur Kündigung?",
                                     known_entities={role: ["Deutsche Bahn AG"]})
    assert result.allowed and "Deutsche Bahn" not in _outgoing(result)


def test_public_firm_in_a_confidential_contract_document_is_protected(gateway) -> None:
    """Dokumentkontext: der Gateway-Aufruf erfolgt ohne Allgemeinwissen-Modus - auch oeffentliche Firmen werden maskiert."""
    result = gateway.prepare_request(
        purpose="chat_response",
        sachverhalt="[Rahmenvertrag] Zwischen der Deutsche Bahn AG und der Nordbauholding GmbH wird ein Rabatt von 18 % "
                    "auf alle Lieferungen vereinbart. Vertraulich.",
        anwaltliche_anmerkungen="Fasse den Vertrag zusammen.",
    )
    assert result.allowed
    payload = _outgoing(result)
    assert "Deutsche Bahn" not in payload and "Nordbauholding" not in payload and "18 %" in payload


# --- Chat auf Leitungsebene --------------------------------------------------------------------------------------------


def test_chat_without_matter_sends_public_firm_but_masks_a_mandate_firm(client, recorder) -> None:
    _ask(client, "", "Wie hoch war der Umsatz der Deutsche Bahn AG im Jahr 2024?")
    assert len(recorder.requests) == 1 and "Deutsche Bahn AG" in _wire(recorder.requests[0])
    _ask(client, "", "Unsere Mandantin, die Bremer Grundbesitz GmbH, will den Mietvertrag kündigen.")
    assert len(recorder.requests) == 2 and "Bremer Grundbesitz" not in _wire(recorder.requests[1])


def test_chat_without_matter_collision_sends_nothing(client, recorder) -> None:
    events = _ask(client, "", "Unsere Mandantin, die Bremer Grundbesitz GmbH (BGB), zahlt nicht. Die BGB verweigert "
                              "die Zahlung nach § 823 BGB.")
    done = [e for e in events if e["kind"] == "done"][0]
    assert done["blocked"] is True and "die Gesellschaft" in done["content"]
    assert recorder.requests == []


def test_chat_with_a_document_masks_a_public_firm(client, recorder) -> None:
    conversation_id = _upload(client, data=_pdf_bytes(["Rahmenvertrag: Die Deutsche Bahn AG erhält 18 % Rabatt."]))[0][
        "conversation_id"]
    _ask(client, conversation_id, "Fasse das Dokument zusammen.")
    assert len(recorder.requests) == 1
    wire = _wire(recorder.requests[0])
    assert "Deutsche Bahn" not in wire and "18 %" in wire


def test_engagement_phrasing_with_a_short_form_and_statute_collision_is_blocked(gateway) -> None:
    result = _general(gateway, "Die Bremer Grundbesitz GmbH (BGB) hat uns beauftragt. Die BGB verweigert die Zahlung "
                               "nach § 823 BGB.")
    assert not result.allowed and result.payload is None and result.reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]


def test_engagement_phrasing_protects_short_form_and_later_mentions(gateway) -> None:
    result = _general(gateway, "Für die Nordbauholding GmbH (NBH) prüfen wir die Abmahnung. Die NBH bestreitet den "
                               "Anspruch aus § 433 BGB.")
    payload = _outgoing(result)
    assert "Nordbauholding" not in payload and "NBH" not in payload and "§ 433 BGB" in payload


def test_chat_engagement_phrasings_are_not_sent_in_clear_text(client, recorder) -> None:
    for question in ("Für die Bremer Grundbesitz GmbH prüfen wir gerade die Kündigung.",
                     "Die Bremer Grundbesitz GmbH hat uns beauftragt."):
        _ask(client, "", question)
    assert len(recorder.requests) == 2
    for request in recorder.requests:
        assert "Bremer Grundbesitz" not in _wire(request)
    _ask(client, "", "Gilt § 1 AEG auch für die Deutsche Bahn AG?")
    assert "Deutsche Bahn AG" in _wire(recorder.requests[2])
