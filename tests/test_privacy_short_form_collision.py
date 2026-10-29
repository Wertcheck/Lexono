"""Option C (24.10.2026): Firmen-Kurzform = zitierte Gesetzesabkuerzung ("Bremer Grundbesitz GmbH (BGB) ... Die BGB
verweigert ... § 823 BGB"). Nicht sicher aufloesbar - maskieren liesse dieselbe Zeichenfolge im Normzitat lesbar (die
unveraenderte Leck-Pruefung blockiert), lesbar lassen gaebe die Initialen der Firma preis. Lexono blockiert deshalb VOR
jeder Payload und bittet um Umformulierung; gewoehnliche Gesetzeszitate und Kurzformen ohne Kollision bleiben
unveraendert.

Ausschliesslich synthetische Daten. Cloud-Aufrufe nur gegen den lokalen Loopback-Recorder (Leitungsebene) bzw. Test-Doubles."""

from __future__ import annotations

import json
import logging

import pytest

import app.privacy.gateway as gateway_module
from app.privacy.api_logger import categorize_block_reasons, friendly_block_message
from app.privacy.detectors import detect_all, short_form_statute_collisions
from app.privacy.gateway import (
    PRIVACY_CHECK_FAILED_REASON,
    SHORT_FORM_STATUTE_COLLISION_REASON,
    ClaudePrivacyGateway,
)
from app.privacy.pseudonymizer import Pseudonymizer
from app.privacy.review import PrivacyDecisions
from app.privacy.security_check import SecurityCheckService
from tests.auth_test_utils import extract_csrf
from tests.test_upload_without_ai import _ask, _pdf_bytes, _upload, _wire, client, db_session, recorder, writer  # noqa: F401

COLLISION = "Die Bremer Grundbesitz GmbH (BGB) zahlt nicht. Die BGB verweigert die Zahlung. Nach § 823 BGB haftet sie."
REWORDED = "Die Bremer Grundbesitz GmbH zahlt nicht. Die Gesellschaft verweigert die Zahlung. Nach § 823 BGB haftet sie."
PROTECTED_VALUES = ("Bremer", "Grundbesitz", "BGB")


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _run(gateway, text: str, mode: str = "mask", **fields):
    return gateway.prepare_request(purpose="formulate_draft", sachverhalt=text, known_entities={}, review_mode=mode,
                                   **fields)


def _outgoing(result) -> str:
    return json.dumps(result.payload.model_dump(), ensure_ascii=False)


# --- Kollision: blockiert vor jeder Payload ------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["mask", "ask"])
def test_collision_is_blocked_before_any_payload(gateway, mode: str) -> None:
    result = _run(gateway, COLLISION, mode)
    assert not result.allowed and result.payload is None
    assert result.reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]
    assert not result.review_required  # keine Rueckfrage, die an der Blockade nichts aendern koennte


def test_block_message_asks_for_rewording_without_protected_values(gateway) -> None:
    result = _run(gateway, COLLISION)
    message = friendly_block_message(result.reasons)
    assert "die Gesellschaft" in message and "nicht an die KI übermittelt" in message
    assert categorize_block_reasons(result.reasons) == "short_form_statute_collision"
    for value in PROTECTED_VALUES:
        assert value not in message and value not in " ".join(result.reasons)


def test_log_names_no_protected_value(gateway, caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="lexono"):
        _run(gateway, COLLISION)
    privacy_logs = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("lexono"))
    assert "Kurzform" in privacy_logs
    for value in PROTECTED_VALUES:
        assert value not in privacy_logs


@pytest.mark.parametrize(
    "text, fields",
    [
        # Kurzform im Sachverhalt, Gesetzeszitat in einem anderen Feld
        ("Die Bremer Grundbesitz GmbH (BGB) zahlt nicht.", {"quellenverweise": ["§ 823 BGB"]}),
        ("Die Bremer Grundbesitz GmbH (BGB) zahlt nicht.", {"anwaltliche_anmerkungen": "Prüfe § 280 Abs. 1 BGB."}),
        # ausdrueckliche Definition statt passender Initialen
        ("Die Nordbau Holding GmbH (nachfolgend: BGB) zahlt nicht. Die BGB verweigert. Es gilt § 433 BGB.", {}),
        # andere Gesetze
        ("Die Hamburger Gasversorgung GmbH (HGB) ist Kaufmann nach § 1 HGB. Die HGB schuldet 4.000 EUR.", {}),
        ("Die Zentrale Prüf Organisation GmbH (ZPO) beruft sich auf § 286 ZPO.", {}),
    ],
)
def test_collision_variants_are_blocked(gateway, text: str, fields: dict) -> None:
    result = _run(gateway, text, **fields)
    assert not result.allowed and result.payload is None
    assert result.reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]


def test_collision_is_detected_without_ner_and_with_a_known_matter_party() -> None:
    """Fehlende Konfiguration: ohne Presidio-NER erkennt die Rechtsform-Regel die Firma - die Blockade greift trotzdem."""
    bare = ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(), security_check=SecurityCheckService())
    assert bare.prepare_request(purpose="formulate_draft", sachverhalt=COLLISION).reasons == [
        SHORT_FORM_STATUTE_COLLISION_REASON]
    known = bare.prepare_request(purpose="formulate_draft", sachverhalt=COLLISION,
                                 known_entities={"gegner": ["Bremer Grundbesitz GmbH"]})
    assert known.reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]


def test_unclear_collision_check_fails_closed(gateway, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("Pruefung nicht moeglich")

    monkeypatch.setattr(gateway_module, "short_form_statute_collisions", broken)
    result = _run(gateway, "Die Nordbauholding GmbH (NBH) zahlt nach § 433 BGB nicht.")
    assert not result.allowed and result.payload is None and result.reasons == [PRIVACY_CHECK_FAILED_REASON]


def test_resubmitting_the_same_text_is_blocked_again_and_rewording_passes(gateway) -> None:
    assert _run(gateway, COLLISION).reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]
    assert _run(gateway, COLLISION).reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]
    result = _run(gateway, REWORDED)
    assert result.allowed
    payload = _outgoing(result)
    assert "§ 823 BGB" in payload and "Die Gesellschaft verweigert" in payload
    assert "Bremer" not in payload and "Grundbesitz" not in payload


# --- keine Kollision: unveraendert ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, readable",
    [
        ("Der Schädiger haftet nach § 823 Abs. 1 BGB.", "§ 823 Abs. 1 BGB"),
        ("Es gelten §§ 433, 437 BGB sowie § 1 StVO.", "§§ 433, 437 BGB"),
        ("Nach dem Bürgerlichen Gesetzbuch (BGB) gilt § 433 BGB. Das BGB regelt den Kauf.", "Das BGB regelt"),
        ("Es gilt die Straßenverkehrs-Ordnung (StVO); nach § 1 StVO ist Rücksicht geboten.", "§ 1 StVO"),
        ("Art. 3 GG und § 286 ZPO sind zu beachten.", "Art. 3 GG"),
    ],
)
@pytest.mark.parametrize("mode", ["mask", "ask"])
def test_ordinary_statute_citations_still_pass(gateway, text: str, readable: str, mode: str) -> None:
    result = _run(gateway, text, mode)
    assert result.allowed, result.reasons
    assert readable in _outgoing(result)


def test_short_form_without_statute_collision_stays_protected(gateway) -> None:
    result = _run(gateway, "Die Nordbauholding GmbH (NBH) legt Einspruch ein. Die NBH zahlt nach § 433 BGB nicht.")
    assert result.allowed
    payload = _outgoing(result)
    assert "NBH" not in payload and "Nordbauholding" not in payload and "§ 433 BGB" in payload


def test_several_firms_citations_and_short_forms(gateway) -> None:
    no_collision = ("Die Nordbauholding GmbH (NBH) und die Kran-Logistik AG (KLA) streiten. Die NBH fordert, die KLA "
                    "verweigert. Es gelten § 823 BGB, § 1 StVO und § 286 ZPO.")
    result = _run(gateway, no_collision)
    assert result.allowed
    payload = _outgoing(result)
    for value in ("NBH", "KLA", "Nordbauholding", "Kran-Logistik"):
        assert value not in payload
    for citation in ("§ 823 BGB", "§ 1 StVO", "§ 286 ZPO"):
        assert citation in payload
    # eine der beiden Kurzformen kollidiert -> die ganze Anfrage wird blockiert
    one_collision = no_collision.replace("Kran-Logistik AG (KLA)", "Bremer Grundbesitz GmbH (BGB)").replace("KLA", "BGB")
    assert _run(gateway, one_collision).reasons == [SHORT_FORM_STATUTE_COLLISION_REASON]


def test_short_form_equal_to_an_uncited_statute_is_no_collision(gateway) -> None:
    """"HGB" als Kurzform, zitiert wird nur das BGB: keine Kollision - die Kurzform bleibt geschuetzt."""
    result = _run(gateway, "Die Hamburger Gasversorgung GmbH (HGB) zahlt nicht. Die HGB verweigert. Es gilt § 433 BGB.")
    assert result.allowed
    payload = _outgoing(result)
    assert "HGB" not in payload and "§ 433 BGB" in payload


def test_detector_function_reports_only_real_collisions() -> None:
    def collisions(text: str) -> set[str]:
        organisations = [(s.category, s.value) for s in detect_all(text, None) if s.category == "organisation"]
        return short_form_statute_collisions(text, organisations)

    assert collisions(COLLISION) == {"BGB"}
    assert collisions("Die Nordbauholding GmbH (NBH) zahlt nach § 433 BGB nicht.") == set()
    assert collisions("Die Alpha Bau GmbH (AG) beruft sich auf § 433 BGB.") == set()  # Rechtsform, keine Kurzform
    assert collisions("Die Alpha Bau GmbH (XYZ) beruft sich auf § 433 BGB.") == set()  # passt nicht zum Namen
    assert collisions("Die Gesetzbuch Verlag GmbH (GVG) beruft sich auf § 13 GVG.") == set()  # Gesetzeswort im Namen


# --- Rueckfrage und Wiederaufnahme ---------------------------------------------------------------------------------


def test_ask_mode_blocks_first_and_asks_after_rewording(gateway) -> None:
    uncertain = " Zeuge: Schuetz."
    blocked = _run(gateway, COLLISION + uncertain, "ask")
    assert blocked.reasons == [SHORT_FORM_STATUTE_COLLISION_REASON] and not blocked.review_required
    asked = _run(gateway, REWORDED + uncertain, "ask")
    assert asked.review_required and "Schuetz" in {c.term for c in asked.review_candidates}
    resumed = _run(gateway, REWORDED + uncertain, "ask", privacy_decisions=PrivacyDecisions(personal=frozenset({"Schuetz"})))
    assert resumed.allowed
    payload = _outgoing(resumed)
    assert "Schuetz" not in payload and "§ 823 BGB" in payload


# --- Chat: kein Aufruf auf Leitungsebene ------------------------------------------------------------------------------


def _conversation_with_harmless_document(client) -> str:
    return _upload(client, data=_pdf_bytes(["Vergleich vom 05.12.2026 über 1.100,00 EUR."]))[0]["conversation_id"]


def _done(events: list[dict]) -> dict:
    return [e for e in events if e["kind"] == "done"][0]


def test_chat_collision_in_the_document_sends_nothing(client, recorder) -> None:
    conversation_id = _upload(client, data=_pdf_bytes([COLLISION]))[0]["conversation_id"]
    done = _done(_ask(client, conversation_id, "Fasse das Dokument zusammen."))
    assert done["blocked"] is True and "die Gesellschaft" in done["content"]
    assert recorder.requests == []
    _ask(client, conversation_id, "Fasse das Dokument zusammen.")  # erneute Uebermittlung
    assert recorder.requests == []


def test_chat_typed_collision_is_blocked_and_rewording_sends_once(client, recorder, caplog) -> None:
    conversation_id = _conversation_with_harmless_document(client)
    with caplog.at_level(logging.DEBUG, logger="lexono"):
        done = _done(_ask(client, conversation_id, COLLISION))
    assert done["blocked"] is True and "die Gesellschaft" in done["content"]
    for value in PROTECTED_VALUES:
        assert value not in done["content"]
        assert value not in " ".join(r.getMessage() for r in caplog.records if r.name.startswith("lexono"))
    assert recorder.requests == []
    _ask(client, conversation_id, COLLISION)  # unveraendert erneut gesendet -> wieder blockiert
    assert recorder.requests == []
    _ask(client, conversation_id, REWORDED)
    assert len(recorder.requests) == 1
    wire = _wire(recorder.requests[0])
    assert "§ 823 BGB" in wire and "Bremer" not in wire and "Grundbesitz" not in wire


def test_chat_resumes_through_the_privacy_review_after_rewording(client, recorder) -> None:
    conversation_id = _conversation_with_harmless_document(client)
    assert _done(_ask(client, conversation_id, COLLISION + " Zeuge: Schuetz."))["blocked"] is True
    assert recorder.requests == []
    events = _ask(client, conversation_id, REWORDED + " Zeuge: Schuetz.")
    review = [e for e in events if e["kind"] == "review"]
    assert len(review) == 1 and "Schuetz" in [c["term"] for c in review[0]["candidates"]]
    assert recorder.requests == []
    page = client.get(f"/dashboard/chat/{conversation_id}").text
    client.post(f"/dashboard/chat/{conversation_id}/privacy-review", data={
        "csrf_token": extract_csrf(page), "message_id": review[0]["message_id"],
        "decisions": json.dumps([{"term": c["term"], "decision": "personal"} for c in review[0]["candidates"]]),
    })
    assert len(recorder.requests) == 1
    wire = _wire(recorder.requests[0])
    assert "Schuetz" not in wire and "§ 823 BGB" in wire


def test_chat_without_matter_keeps_the_documented_general_knowledge_behaviour(client, recorder) -> None:
    """Bestehende Architekturentscheidung (unveraendert): ohne Akte/Mandant/Dokument pseudonymisiert Lexono Firmennamen
    NICHT (`_GENERAL_KNOWLEDGE_SKIP_CATEGORIES`) - dann gibt es keine geschuetzte Kurzform und keine Kollision.
    Dokumentiertes Restrisiko, siehe Bericht 24.10.2026."""
    _ask(client, "", COLLISION)
    assert len(recorder.requests) == 1


# --- dokumentierte Grenze (vorbestehend, unveraendert) --------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Kurzformen mit Kleinbuchstaben ('StVO') gelten nicht als Firmen-Kurzform "
                   "(_SHORT_FORM_DEFINITION) - weder geschuetzt noch als Kollision erkannt")
def test_known_limit_mixed_case_short_form(gateway) -> None:
    result = _run(gateway, "Die Stadtverkehr Oberhausen GmbH (StVO) zahlt nicht. Die StVO verweigert. Es gilt § 1 StVO.")
    assert not result.allowed or "Die StVO verweigert" not in _outgoing(result)
