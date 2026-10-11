"""Chat-Rueckfrage end-to-end ueber die echten Endpunkte (12.10.2026): SSE "review", kein Cloud-Aufruf vor der
Entscheidung, Wiederaufnahme mit erneuter vollstaendiger Pruefung, Eigentuemer- und Zustandspruefung.
Ausschliesslich synthetische Daten."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web.chat_router as chat_router_module
from app.models import ChatConversation, ChatMessage
from app.models.privacy_decision import PrivacyDecision
from tests.auth_test_utils import create_test_user, login, login_as_admin
from tests.test_web_chat import (  # noqa: F401 - Fixtures
    FakeClaudeWritingProvider,
    _csrf,
    _parse_sse_events,
    _working_drafting_service,
    client,
    db_session,
)

QUESTION = "Wir bestätigen den Eingang des Schreibens von Richter vom 16.03.2024. Bitte fasse den Stand zusammen."


@pytest.fixture()
def writer(monkeypatch: pytest.MonkeyPatch) -> FakeClaudeWritingProvider:
    fake = FakeClaudeWritingProvider("Zusammenfassung erstellt.")
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(fake))
    return fake


def _send(client: TestClient, csrf: str) -> list[dict]:
    response = client.post(
        "/dashboard/chat/send-stream", data={"csrf_token": csrf, "conversation_id": "", "content": QUESTION}
    )
    assert response.status_code == 200
    return _parse_sse_events(response.text)


def _review(client: TestClient, csrf: str, conversation_id: str, message_id: str, decisions: list[dict]):
    return client.post(
        f"/dashboard/chat/{conversation_id}/privacy-review",
        data={"csrf_token": csrf, "message_id": message_id, "decisions": json.dumps(decisions)},
    )


def test_uncertain_term_pauses_before_any_cloud_call(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    events = _send(client, _csrf(client))
    review = [e for e in events if e["kind"] == "review"]
    assert len(review) == 1 and not [e for e in events if e["kind"] == "done"]
    assert [c["term"] for c in review[0]["candidates"]] == ["Richter"]
    assert writer.received_payloads == []  # nichts hat die Cloud erreicht
    conversation = db_session.query(ChatConversation).filter_by(id=review[0]["conversation_id"]).one()
    roles = [m.role for m in db_session.query(ChatMessage).filter_by(conversation_id=conversation.id)]
    assert roles == ["user"]  # keine Assistenten-Nachricht gespeichert


def test_personal_decision_resumes_with_the_term_pseudonymized(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    review = next(e for e in _send(client, csrf) if e["kind"] == "review")
    response = _review(client, csrf, review["conversation_id"], review["message_id"], [{"term": "Richter", "decision": "personal"}])
    events = _parse_sse_events(response.text)
    assert [e["kind"] for e in events if e["kind"] in ("done", "review")] == ["done"]
    assert len(writer.received_payloads) == 1
    assert "Richter" not in writer.received_payloads[0].model_dump_json()
    decision = db_session.query(PrivacyDecision).one()
    assert decision.decision == "personal" and decision.conversation_id is None  # aktenweit


def test_not_personal_decision_resumes_with_the_term_readable(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    review = next(e for e in _send(client, csrf) if e["kind"] == "review")
    response = _review(client, csrf, review["conversation_id"], review["message_id"], [{"term": "Richter", "decision": "not_personal"}])
    assert [e["kind"] for e in _parse_sse_events(response.text) if e["kind"] in ("done", "review")] == ["done"]
    assert "Richter" in writer.received_payloads[0].model_dump_json()


def test_review_is_only_accepted_for_the_open_last_message_of_an_own_conversation(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    review = next(e for e in _send(client, csrf) if e["kind"] == "review")
    # falsche Nachricht
    events = _parse_sse_events(_review(client, csrf, review["conversation_id"], "unbekannt", [{"term": "Richter", "decision": "not_personal"}]).text)
    assert events[0]["kind"] == "done" and events[0]["message_id"] is None
    # ungueltige Entscheidung
    events = _parse_sse_events(_review(client, csrf, review["conversation_id"], review["message_id"], [{"term": "Richter", "decision": "freigeben"}]).text)
    assert events[0]["message_id"] is None
    assert writer.received_payloads == [] and db_session.query(PrivacyDecision).count() == 0
    # fremder Nutzer
    from tests.auth_test_utils import seed_roles

    other = create_test_user(db_session, seed_roles(db_session)["admin"], "kollege@kanzlei.test")
    client.cookies.clear()
    login(client, other.email)
    response = _review(client, _csrf(client), review["conversation_id"], review["message_id"], [{"term": "Richter", "decision": "not_personal"}])
    assert response.status_code in (403, 404) or "nicht" in response.text.lower()
    assert writer.received_payloads == []
