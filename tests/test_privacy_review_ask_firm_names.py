"""Rueckfrage-Stufe (review_mode="ask") bei Allgemeinwoertern in Firmennamen (24./25.10.2026). Haelt die zuvor nur manuell
gemessene Pruefung dauerhaft fest: "Consulting" in "Muster Consulting AG" loest in keinem Kontext eine Rueckfrage aus;
oeffentliche Firmen bleiben im Chat ohne Akte lesbar, Firmen mit Mandatsbezug und im Aktenmodus werden vollstaendig
pseudonymisiert. Geprueft wird der Rueckfrage-Status UND der tatsaechlich weitergegebene Text (Payload bzw. Leitung).

Ausschliesslich synthetische Daten. Cloud-Aufrufe nur gegen den lokalen Loopback-Recorder."""

from __future__ import annotations

import json

import pytest

from app.privacy.gateway import ClaudePrivacyGateway
from tests.test_upload_without_ai import _ask, _pdf_bytes, _upload, _wire, client, db_session, recorder, writer  # noqa: F401


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _ask_mode(gateway, text: str, *, general: bool, known_entities=None):
    return gateway.prepare_request(
        purpose="chat_response", sachverhalt="Allgemeine Frage ohne Aktenbezug." if general else "Akte: Werkvertrag",
        anwaltliche_anmerkungen=text, known_entities=known_entities,
        skip_general_knowledge_pseudonymization=general, review_mode="ask",
    )


def _sent(result) -> str:
    assert result.payload is not None, "keine Payload - Rueckfrage oder Blockade statt Weitergabe"
    return json.dumps(result.payload.model_dump(), ensure_ascii=False)


def _no_question(result) -> None:
    assert not result.review_required, [c.term for c in result.review_candidates]
    assert result.review_candidates == [] or result.review_candidates is None
    assert result.allowed, result.reasons


# --- Gateway (echter Erkennungs- und Rueckfragepfad) --------------------------------------------------------------


def test_general_chat_public_firm_is_readable_without_a_question(gateway) -> None:
    result = _ask_mode(gateway, "Wie ist die Muster Consulting AG im Handelsregister eingetragen?", general=True)
    _no_question(result)
    sent = _sent(result)
    assert "Muster Consulting AG" in sent
    assert not result.mappings  # nichts pseudonymisiert


@pytest.mark.parametrize("text", [
    "Die Muster Consulting AG hat uns beauftragt.",
    "Für die Muster Consulting AG prüfen wir gerade die Kündigung.",
    "Unsere Mandantin, die Muster Consulting AG, will kündigen.",
])
def test_general_chat_mandate_firm_is_fully_pseudonymized_without_a_question(gateway, text: str) -> None:
    result = _ask_mode(gateway, text, general=True)
    _no_question(result)
    sent = _sent(result)
    assert "Muster" not in sent and "Consulting" not in sent
    assert [(m.category, m.original_value) for m in result.mappings] == [("organisation", "Muster Consulting AG")]


def test_matter_mode_firm_is_fully_pseudonymized_without_a_question(gateway) -> None:
    result = _ask_mode(gateway, "Die Muster Consulting AG hat nicht geliefert.", general=False)
    _no_question(result)
    sent = _sent(result)
    assert "Muster" not in sent and "Consulting" not in sent


def test_matter_mode_known_party_keeps_its_role(gateway) -> None:
    result = _ask_mode(gateway, "Die Muster Consulting AG hat nicht geliefert.", general=False,
                       known_entities={"gegner": ["Muster Consulting AG"]})
    _no_question(result)
    assert "Muster Consulting" not in _sent(result)
    assert [m.category for m in result.mappings] == ["gegner"]


def test_a_real_uncertain_person_next_to_the_firm_is_still_asked(gateway) -> None:
    """Gegenprobe: die Rueckfrage selbst ist nicht abgeschaltet - nur "Consulting" ist kein unsicherer Begriff."""
    result = _ask_mode(gateway, "Zeuge: Schuetz von der Muster Consulting AG.", general=True)
    assert result.review_required and result.payload is None
    terms = {c.term for c in result.review_candidates}
    assert "Schuetz" in terms and "Consulting" not in terms


# --- Chat auf Leitungsebene (SSE-Ereignisse + Loopback-Empfaenger) -------------------------------------------------


def _kinds(events: list[dict]) -> list[str]:
    return [e["kind"] for e in events]


def test_chat_without_matter_wire_and_no_review_event(client, recorder) -> None:
    public = _ask(client, "", "Wie ist die Muster Consulting AG im Handelsregister eingetragen?")
    mandate = _ask(client, "", "Die Muster Consulting AG hat uns beauftragt.")
    assert "review" not in _kinds(public) and "review" not in _kinds(mandate)
    assert len(recorder.requests) == 2
    assert "Muster Consulting AG" in _wire(recorder.requests[0])
    assert "Muster" not in _wire(recorder.requests[1]) and "Consulting" not in _wire(recorder.requests[1])


def test_chat_in_matter_context_wire_and_no_review_event(client, recorder) -> None:
    conversation_id = _upload(client, data=_pdf_bytes(["Vergleich vom 05.12.2026 über 1.100,00 EUR."]))[0][
        "conversation_id"]
    events = _ask(client, conversation_id, "Die Muster Consulting AG hat nicht geliefert.")
    assert "review" not in _kinds(events)
    assert len(recorder.requests) == 1
    wire = _wire(recorder.requests[0])
    assert "Muster" not in wire and "Consulting" not in wire and "1.100,00 EUR" in wire
