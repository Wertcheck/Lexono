"""§51: manuell markierte Namen ("Namen schützen" im Chat-Eingabefeld) - Erweiterung der bestehenden Datenschutz-
Rueckfrage, keine zweite Rueckfrage: beim Senden wird daraus dieselbe Entscheidung "Als Person behandeln".

Echte Endpunkte (send-stream, privacy-review, pending-review), echter Gateway (Presidio), synthetische Daten. Der
HTTP-Nachweis laeuft ueber den echten Anthropic-Provider gegen den lokalen Aufzeichnungsserver aus
tests/test_privacy_wire_payload.py (kein echter Aufruf, gefaelschter Schluessel)."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web.chat_router as chat_router_module
from app.models import AuditEvent, ChatConversation, ChatMessage, Matter
from app.models.privacy_decision import PrivacyDecision, PrivacyFeedbackStat
from tests.auth_test_utils import login_as_admin
from tests.test_web_chat import (  # noqa: F401 - Fixtures
    FakeClaudeWritingProvider,
    _csrf,
    _parse_sse_events,
    _working_drafting_service,
    client,
    db_session,
)

P10 = "Der Richter Zweigelt hat den Termin auf den 22.07.2026 verlegt; Streitwert 4.250,00 EUR nach § 3 ZPO."
LEGAL = ["22.07.2026", "4.250,00 EUR", "§ 3 ZPO", "Termin", "Streitwert"]


@pytest.fixture()
def writer(monkeypatch: pytest.MonkeyPatch) -> FakeClaudeWritingProvider:
    fake = FakeClaudeWritingProvider("Antwort erstellt.")
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(fake))
    return fake


def _send(client: TestClient, csrf: str, content: str, terms=None, conversation_id: str = "") -> list[dict]:
    data = {"csrf_token": csrf, "conversation_id": conversation_id, "content": content}
    if terms is not None:
        data["protected_terms"] = terms if isinstance(terms, str) else json.dumps(terms)
    response = client.post("/dashboard/chat/send-stream", data=data)
    assert response.status_code == 200
    return _parse_sse_events(response.text)


def _sent_text(writer: FakeClaudeWritingProvider, index: int = -1) -> str:
    """Alle Felder der Payload (Sachverhalt, Anweisung, Verlauf ...) als lesbarer Text."""
    return json.dumps(writer.received_payloads[index].model_dump(), ensure_ascii=False)


def _done(events: list[dict]) -> dict:
    return next(e for e in events if e["kind"] == "done")


# --- Ausgangsfall P10 im Chat ohne Akte ---------------------------------------------------------------------------


def test_unmarked_judge_reaches_the_cloud_payload_in_clear_text(client, db_session: Session, writer) -> None:
    """Gegenprobe (dokumentierte Grenze §50): ohne Markierung erkennt nichts den Namen - deshalb die Erweiterung."""
    login_as_admin(db_session, client)
    _send(client, _csrf(client), P10)
    assert "Zweigelt" in _sent_text(writer)


def test_marked_judge_in_chat_without_matter_is_pseudonymized(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    events = _send(client, _csrf(client), P10, ["Zweigelt"])
    assert _done(events)["message_id"] is not None
    sent = _sent_text(writer)
    assert "Zweigelt" not in sent and "[PERSON_01]" in sent
    for value in LEGAL:
        assert value in sent
    conversation = db_session.query(ChatConversation).filter_by(id=events[0]["conversation_id"]).one()
    matter = db_session.query(Matter).filter_by(id=conversation.matter_id).one()  # automatische Schnellakte
    decision = db_session.query(PrivacyDecision).one()
    assert (decision.term, decision.decision, decision.matter_id, decision.conversation_id) == (
        "Zweigelt", "personal", matter.id, None)
    audit = db_session.query(AuditEvent).filter_by(event_type="privacy_decision_recorded").one()
    assert "manuell" in audit.details and "Zweigelt" not in audit.details  # Audit ohne Klartext
    stat = db_session.query(PrivacyFeedbackStat).one()
    assert "Zweigelt" not in stat.term_hmac and "zweigelt" not in stat.term_hmac


def test_marking_persists_for_later_messages_of_the_conversation(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    first = _send(client, csrf, P10, ["Zweigelt"])
    second = _send(client, csrf, "Bitte fasse zusammen, was Zweigelt angeordnet hat.",
                   conversation_id=first[0]["conversation_id"])
    assert not [e for e in second if e["kind"] == "review"]
    assert len(writer.received_payloads) == 2
    assert "Zweigelt" not in _sent_text(writer, 1)  # auch im Verlauf und in der neuen Nachricht


def test_marking_of_an_automatically_detected_name_is_harmless(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    _send(client, _csrf(client), "Herr Gustav Wendelin bat um Rückruf zum Termin am 22.07.2026.", ["Wendelin"])
    sent = _sent_text(writer)
    assert "Wendelin" not in sent and "Gustav" not in sent and "22.07.2026" in sent


def test_several_names_repetitions_and_spellings_share_their_placeholder(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    text = ("Der Vorsitzende Zweigelt lud den Zeugen Brummelbeck. Nach Zweigelts Hinweis widersprach Brummelbeck; "
            "ZWEIGELT setzte die Frist bis 30.11.2026.")
    events = _send(client, _csrf(client), text, ["Zweigelt", "Brummelbeck"])
    assert not [e for e in events if e["kind"] == "review"]
    sent = _sent_text(writer)
    for secret in ("Zweigelt", "ZWEIGELT", "Brummelbeck"):
        assert secret not in sent
    judge = re.search(r"Vorsitzende (\[PERSON_\d+\]) lud", sent).group(1)
    witness = re.search(r"Zeugen (\[PERSON_\d+\])", sent).group(1)  # vorher las die NER diese Stelle als Ort
    assert judge != witness
    assert f"Nach {judge}s Hinweis widersprach {witness};" in sent  # Genitiv und Wiederholung: derselbe Platzhalter
    assert f"{witness}; {judge} setzte die Frist bis 30.11.2026." in sent  # Versalien: derselbe Platzhalter


@pytest.mark.xfail(strict=True, reason="§51 vorbestehende Grenze (auch auf 3994019 mit einer Rueckfrage-Entscheidung): "
                   "ein als Person entschiedener Name wird auch kleingeschrieben weitergetragen ('wartete lange')")
def test_lowercase_homograph_is_not_masked(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    _send(client, _csrf(client), "Herr Lange wartete lange auf den Termin.", ["Lange"])
    sent = _sent_text(writer)
    assert "Lange" not in sent and "wartete lange auf den Termin" in sent


# --- Markierung darf nicht verloren gehen oder auf falsche Stellen zeigen ---------------------------------------------


@pytest.mark.parametrize("terms,content,expected", [
    (["Zweigelt"], "Der Richter hat den Termin verlegt.", "kommt in der Nachricht nicht mehr vor"),  # Text geaendert
    (["Richter Zweigelt"], P10, "ohne Anrede, Titel oder Rolle"),  # §52: Rolle gehoert nicht in den Platzhalter
    (["von"], P10, "Bitte nur Namen markieren"),  # §52: Namenszusatz allein ist kein Name
    (["Zweig"], P10, "kommt in der Nachricht nicht mehr vor"),  # nur Teil eines Wortes
    (["0170"], P10, "Ungültiger Begriff"),
    ([f"Name{chr(97 + i)}" for i in range(21)], P10, "Ungültige Namensmarkierung"),
    ("kein json", P10, "Ungültige Namensmarkierung"),
    ([{"term": "Zweigelt"}], P10, "Ungültige Namensmarkierung"),
])
def test_invalid_or_stale_marking_sends_nothing(client, db_session: Session, writer, terms, content, expected) -> None:
    login_as_admin(db_session, client)
    events = _send(client, _csrf(client), content, terms)
    assert events[0]["kind"] == "done" and events[0]["message_id"] is None and expected in events[0]["content"]
    assert writer.received_payloads == []
    assert db_session.query(PrivacyDecision).count() == 0
    assert db_session.query(ChatMessage).count() == 0


def test_classic_form_fallback_applies_the_marking_too(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": _csrf(client), "conversation_id": "", "content": P10, "protected_terms": '["Zweigelt"]'},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert len(writer.received_payloads) == 1 and "Zweigelt" not in _sent_text(writer)


def test_classic_form_fallback_refuses_a_stale_marking(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    response = client.post(
        "/dashboard/chat/send",
        data={"csrf_token": _csrf(client), "conversation_id": "", "content": "Ohne Namen.", "protected_terms": '["Zweigelt"]'},
        follow_redirects=False,
    )
    assert writer.received_payloads == [] and db_session.query(ChatMessage).count() == 0
    # Fehlermeldung steht in der Redirect-URL (Zugriffsprotokoll, Browserverlauf) - ohne den Namen
    assert "error=" in response.headers["location"] and "Zweigelt" not in response.headers["location"]


def test_server_error_messages_never_contain_the_marked_term(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    for terms, content in ((["Zweigelt"], "Ohne Namen."), (["Zweig"], P10), (["Hans Zweigelt"], P10)):
        events = _send(client, _csrf(client), content, terms)
        assert events[0]["message_id"] is None and "Zweig" not in events[0]["content"]


# --- Zusammenspiel mit der bestehenden Rueckfrage, Abbruch, Neuladen, Fehler ------------------------------------------

QUESTION_WITH_CANDIDATE = (
    "Wir bestätigen den Eingang des Schreibens von Richter vom 16.03.2024. Richter Zweigelt bat um Stellungnahme."
)


def test_marking_and_existing_review_work_together_and_survive_reload(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    events = _send(client, csrf, QUESTION_WITH_CANDIDATE, ["Zweigelt"])
    review = next(e for e in events if e["kind"] == "review")
    assert [c["term"] for c in review["candidates"]] == ["Richter"]  # bestehende Rueckfrage unveraendert
    assert writer.received_payloads == []  # Abbruch an dieser Stelle: nichts hat die Cloud erreicht
    # Neuladen: offene Rueckfrage wird wiederhergestellt, die Markierung gilt weiter (gespeichert, nicht im Browser)
    restored = client.get(f"/dashboard/chat/{review['conversation_id']}/pending-review").json()
    assert restored["state"] == "review" and [c["term"] for c in restored["candidates"]] == ["Richter"]
    response = client.post(
        f"/dashboard/chat/{review['conversation_id']}/privacy-review",
        data={"csrf_token": csrf, "message_id": review["message_id"],
              "decisions": json.dumps([{"term": "Richter", "decision": "personal"}])},
    )
    assert [e["kind"] for e in _parse_sse_events(response.text) if e["kind"] in ("done", "review")] == ["done"]
    sent = _sent_text(writer)
    assert "Zweigelt" not in sent and "Richter" not in sent and "16.03.2024" in sent


def test_failure_while_applying_the_marking_blocks_the_cloud_call(client, db_session: Session, writer, monkeypatch) -> None:
    """Fail-closed: scheitert die Anwendung der Entscheidungen, geht nichts hinaus (keine Umgehung bei Fehlern)."""
    import app.privacy.gateway as gateway_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("simulierter Fehler")

    monkeypatch.setattr(gateway_module, "detect_decision_terms", broken)
    login_as_admin(db_session, client)
    try:
        _send(client, _csrf(client), P10, ["Zweigelt"])
    except RuntimeError:
        pass  # der Fehler darf auch bis zum Testclient durchschlagen - entscheidend ist: kein Versand
    assert writer.received_payloads == []


# --- Andere Pfade derselben Akte ---------------------------------------------------------------------------------


def test_matter_decision_applies_to_the_draft_review_engine(db_session: Session) -> None:
    from tests.test_review_engine import FakeReviewProvider, _draft, _engine

    draft = _draft(db_session, "Der Richter Zweigelt hat den Termin auf den 22.07.2026 verlegt.")
    db_session.add(PrivacyDecision(matter_id=draft.matter_id, conversation_id=None, term="Zweigelt", decision="personal"))
    db_session.commit()
    provider = FakeReviewProvider()
    _engine(provider).review_draft(draft.id, db_session)
    sent = provider.received_payloads[0].anonymisierter_sachverhalt
    assert "Zweigelt" not in sent and "22.07.2026" in sent


def test_matter_decision_applies_to_drafts_without_chat_decisions(db_session: Session) -> None:
    from app.models import Client

    writer = FakeClaudeWritingProvider("Entwurf.")
    matter = Matter(client=Client(name="Corinna Haselhorst"), title="Haselhorst - Mietsache")
    db_session.add(matter)
    db_session.flush()
    db_session.add(PrivacyDecision(matter_id=matter.id, conversation_id=None, term="Zweigelt", decision="personal"))
    db_session.commit()
    _working_drafting_service(writer).create_draft(
        matter.id, "formulate_draft", db_session,
        attorney_anmerkungen="Bitte an Richter Zweigelt schreiben, Termin 22.07.2026.",
    )
    assert writer.received_payloads and "Zweigelt" not in _sent_text(writer)


# --- Nachweis auf HTTP-Ebene ---------------------------------------------------------------------------------------


def test_marked_name_never_appears_in_the_http_request(client, db_session: Session, monkeypatch) -> None:
    from tests.test_privacy_wire_payload import _provider, _Recorder

    recorder = _Recorder()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", recorder.url)
    monkeypatch.setenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    try:
        monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(_provider()))
        login_as_admin(db_session, client)
        events = _send(client, _csrf(client), P10, ["Zweigelt"])
        assert _done(events)["message_id"] is not None
        assert len(recorder.requests) == 1
        wire = recorder.requests[0]["raw"].decode("utf-8")
        decoded = json.dumps(json.loads(wire), ensure_ascii=False)
        assert "Zweigelt" not in wire and "Zweigelt" not in decoded
        for value in ("22.07.2026", "4.250,00 EUR", "§ 3 ZPO"):
            assert value in decoded
    finally:
        recorder.server.shutdown()
        recorder.server.server_close()


# --- UI ----------------------------------------------------------------------------------------------------------


def test_chat_page_offers_the_marking_next_to_the_existing_review(client, db_session: Session) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/chat").text
    assert 'id="chat-protect-btn"' in page and 'name="protected_terms"' in page
    assert "Als Person behandeln" in page and "Kein personenbezogenes Datum" in page and "Unsicher" in page
