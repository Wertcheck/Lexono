"""Allgemeinwort im Firmennamen (24.10.2026): "Consulting" in "Muster Consulting AG" wurde im Chat ohne Akte als Person
maskiert ("Muster [PERSON_01] AG") - vorbestehend seit mindestens 8a219ec. Ursache: das Zweitsignal
presidio_ner.detect_unlabelled_proper_nouns (spaCy-PROPN fuer das Lehnwort); im Aktenmodus ueberdeckt der Firmentreffer
die Stelle, im Allgemeinwissen-Modus wurde er verworfen und der Personentreffer rueckte nach. Jetzt entfaellt ein solcher
Treffer mit seiner Firma - nur fuer bekannte Woerter ohne jeden Namensrahmen-Treffer; Nachnamen bleiben unberuehrt.

Ausschliesslich synthetische Daten. Cloud-Aufrufe nur gegen den lokalen Loopback-Recorder bzw. Test-Doubles."""

from __future__ import annotations

import json

import pytest

import app.privacy.detectors as detectors_module
from app.privacy.detectors import DetectedSpan, _is_generic_word_inside_dropped_firm
from app.privacy.gateway import (
    PRIVACY_CHECK_FAILED_REASON,
    SHORT_FORM_STATUTE_COLLISION_REASON,
    ClaudePrivacyGateway,
)
from app.privacy.security_check import check_response_placeholder_integrity
from tests.test_upload_without_ai import _ask, _wire, client, db_session, recorder, writer  # noqa: F401


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _general(gateway, question: str):
    return gateway.prepare_request(purpose="chat_response", sachverhalt="Allgemeine Frage ohne Aktenbezug.",
                                   anwaltliche_anmerkungen=question, skip_general_knowledge_pseudonymization=True)


def _outgoing(result) -> str:
    return json.dumps(result.payload.model_dump(), ensure_ascii=False)


# --- Befund: Allgemeinwort im erkannten Firmennamen ------------------------------------------------------------------


@pytest.mark.parametrize(
    "firm",
    ["Gegner Consulting AG", "Muster Consulting AG", "Muster Consulting GmbH", "Muster Solutions GmbH",
     "Muster Services AG", "Muster Partners GmbH"],
)
def test_generic_word_in_a_public_firm_name_is_not_a_person(gateway, firm: str) -> None:
    result = _general(gateway, f"Wie ist die {firm} im Handelsregister eingetragen?")
    assert result.allowed
    assert firm in _outgoing(result)
    assert not [m for m in result.mappings if m.category == "person"]


def test_matter_mode_still_masks_the_whole_firm(gateway) -> None:
    result = gateway.prepare_request(purpose="chat_response", sachverhalt="Akte: Werkvertrag",
                                     anwaltliche_anmerkungen="Die Muster Consulting AG hat nicht geliefert.",
                                     known_entities={"gegner": ["Muster Consulting AG"]})
    assert result.allowed and "Muster Consulting" not in _outgoing(result)


# --- Personen bleiben geschuetzt ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, person",
    [
        ("Frau Lena Vogt von der Muster Consulting AG hat angerufen.", "Vogt"),
        ("Die Muster Consulting GmbH, vertreten durch Max Brandhuber, verlangt Zahlung.", "Brandhuber"),
        ("Herr Hochleiter, Geschäftsführer der Muster Solutions GmbH, widerspricht.", "Hochleiter"),
    ],
)
def test_real_persons_next_to_the_firm_stay_protected(gateway, question: str, person: str) -> None:
    result = _general(gateway, question)
    assert result.allowed and person not in _outgoing(result)


@pytest.mark.parametrize("surname", ["Müller", "Brandhuber", "Kowalczyk", "Fischer", "Zweigelt", "Licht"])
def test_rule_never_releases_a_surname(surname: str) -> None:
    text = f"Wie ist die {surname} Consulting GmbH eingetragen?"
    start = text.index(surname)
    firm = DetectedSpan(category="organisation", start=start, end=start + len(f"{surname} Consulting GmbH"),
                        value=f"{surname} Consulting GmbH")
    person = DetectedSpan(category="person", start=start, end=start + len(surname), value=surname)
    assert not _is_generic_word_inside_dropped_firm(person, [firm], [])


def test_rule_needs_a_dropped_firm_around_the_word() -> None:
    text = "Consulting ist ein Beratungsgeschäft."
    person = DetectedSpan(category="person", start=0, end=10, value="Consulting")
    assert not _is_generic_word_inside_dropped_firm(person, [], [])
    firm = DetectedSpan(category="organisation", start=0, end=10, value="Consulting")  # deckungsgleich: keine Firma darum
    assert not _is_generic_word_inside_dropped_firm(person, [firm], [])
    assert text  # Text nur zur Lesbarkeit


# --- dokumentierte Grenzen: ausserhalb einer erkannten Firma unveraendert -------------------------------------------


@pytest.mark.xfail(strict=True, reason="vorbestehend: ohne erkannte Firma bleibt das PROPN-Zweitsignal bestehen "
                   "(keine globale Freigabe von 'Consulting')")
@pytest.mark.parametrize("question", ["Consulting ist ein Beratungsgeschäft.",
                                      "Muster Consulting hat die Rechnung gestellt."])  # ohne Rechtsform: keine Firma
def test_known_limit_generic_word_outside_a_recognised_firm(gateway, question: str) -> None:
    result = _general(gateway, question)
    assert not [m for m in result.mappings if m.category == "person"]


# --- Mandatsbezug, Kurzformen, Kollision, Leck-Pruefung, fail-closed ---------------------------------------------


@pytest.mark.parametrize("question", [
    "Die Muster Consulting GmbH hat uns beauftragt.",
    "Für die Muster Consulting AG prüfen wir gerade die Kündigung.",
    "Unsere Mandantin, die Muster Consulting GmbH, will kündigen.",
])
def test_mandate_firm_with_generic_word_is_fully_protected(gateway, question: str) -> None:
    result = _general(gateway, question)
    assert result.allowed
    payload = _outgoing(result)
    assert "Muster" not in payload and "Consulting" not in payload


def test_public_firm_in_a_general_legal_question_stays_readable(gateway) -> None:
    result = _general(gateway, "Welche Pflichten hat die Muster Consulting AG nach § 241 Abs. 2 BGB?")
    payload = _outgoing(result)
    assert "Muster Consulting AG" in payload and "§ 241 Abs. 2 BGB" in payload


def test_short_form_later_mentions_and_collision(gateway) -> None:
    result = _general(gateway, "Die Muster Consulting GmbH (MCG) hat uns beauftragt. Die MCG zahlt nicht nach § 433 BGB; "
                               "die Muster Consulting GmbH bestreitet.")
    payload = _outgoing(result)
    assert "MCG" not in payload and "Muster Consulting" not in payload and "§ 433 BGB" in payload
    blocked = _general(gateway, "Die Bremer Grundbesitz GmbH (BGB) hat uns beauftragt. Die BGB zahlt nicht nach § 823 BGB.")
    assert not blocked.allowed and blocked.payload is None and blocked.reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]


def test_response_leak_check_still_catches_the_protected_firm(gateway) -> None:
    result = _general(gateway, "Die Muster Consulting GmbH hat uns beauftragt.")
    placeholder = next(m.placeholder for m in result.mappings if m.original_value == "Muster Consulting GmbH")
    assert check_response_placeholder_integrity(f"Antwort zu {placeholder}: die Muster Consulting GmbH haftet.",
                                                 result.mappings)


def test_failure_of_the_rule_blocks(gateway, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("Pruefung nicht moeglich")

    monkeypatch.setattr(detectors_module, "_is_generic_word_inside_dropped_firm", broken)
    result = _general(gateway, "Wie ist die Muster Consulting AG eingetragen?")
    assert not result.allowed and result.payload is None and result.reasons == [PRIVACY_CHECK_FAILED_REASON]


def test_chat_wire_public_firm_readable_mandate_firm_masked(client, recorder) -> None:
    _ask(client, "", "Wie ist die Muster Consulting AG im Handelsregister eingetragen?")
    _ask(client, "", "Die Muster Consulting GmbH hat uns beauftragt.")
    assert len(recorder.requests) == 2
    assert "Muster Consulting AG" in _wire(recorder.requests[0])
    assert "Muster Consulting" not in _wire(recorder.requests[1])
