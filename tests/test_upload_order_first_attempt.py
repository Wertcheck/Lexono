"""Erstversuch "Dokument hochladen + Auftrag" (29.10.2026).

Fehlerbild (in der echten UI reproduziert, QA-Ordner pseudonymisierung-erstversuch-20261029): Datei + "Erstelle einen
Schriftsatz" senden -> nach §57 nur Upload, der Text blieb im Browser -> der Dialog oeffnete als reine Pruefansicht
(kein offener Auftrag) mit GESPERRTEN Optionen; erst ein zweites Senden legte den Auftrag an.

Korrektur: Der Dialog sendet den lokal gehaltenen Auftrag mit dem Bestaetigen (`order`), der Server legt ihn genau
einmal an - nur wenn die juengste Nachricht der Upload ist - und verarbeitet ihn nach Entscheidungen und Freigabe ueber
denselben fail-closed Pfad. Scheitern Entscheidungen/Freigabe, wird der nie verarbeitete Auftrag wieder entfernt.

Ausschliesslich synthetische Dokumente; Cloud = Test-Double (`FakeClaudeWritingProvider`, SIMULATION)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

import app.web.chat_router as chat_router_module
from app.documents import release
from app.models import ChatConversation, ChatMessage
from app.models.privacy_decision import PrivacyDecision
from tests.auth_test_utils import login_as_admin
from tests.test_pseudonymization_center import ANALYSE, _pdf_multipage, _upload, pdf_case  # noqa: F401
from tests.test_schriftsatz_release_resume import AUFTRAG, _doc_of_conversation, writer  # noqa: F401
from tests.test_web_chat import _csrf, _parse_sse_events, client, db_session  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _confirm_with_order(client, conversation_id: str, doc, decisions, *, order: str = AUFTRAG, text_version=None,
                        require_resolved: bool = True) -> list[dict]:
    data = {"csrf_token": _csrf(client), "document_id": doc.id, "order": order,
            "text_version": text_version or release.text_version_of(doc.extracted_text),
            "decisions": json.dumps(decisions)}
    if require_resolved:
        data["require_resolved"] = "1"
    return _parse_sse_events(client.post(f"/dashboard/chat/{conversation_id}/confirm-release-and-generate",
                                         data=data).text)


def _roles(db_session: Session, conversation_id: str) -> list[str]:
    db_session.expire_all()
    return [m.role for m in db_session.query(ChatMessage).filter_by(conversation_id=conversation_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)]


def _user_messages(db_session: Session, conversation_id: str) -> list[ChatMessage]:
    db_session.expire_all()
    return db_session.query(ChatMessage).filter_by(conversation_id=conversation_id, role="user").all()


def test_upload_alone_still_creates_no_order_and_sends_nothing(client, db_session, writer, pdf_case) -> None:
    """§57 unveraendert: der mitgetippte Text wird beim Upload NICHT gesendet und nicht gespeichert."""
    conversation_id, _doc = pdf_case
    assert _roles(db_session, conversation_id) == ["upload"]
    assert writer.received_payloads == []
    assert client.get(f"/dashboard/chat/{conversation_id}/pseudonymization-documents").json()["has_open_order"] is False


def test_first_attempt_confirm_with_order_drafts_exactly_once_pseudonymized(client, db_session, writer, pdf_case) -> None:
    conversation_id, doc = pdf_case
    events = _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "personal"}])
    start = events[0]
    assert start["kind"] == "start" and start["drafting"] is True  # Ladeanzeige: "Schriftsatz wird erstellt"
    assert not [e for e in events if e["kind"] == "review"]
    assert next(e for e in events if e["kind"] == "done").get("blocked") is not True
    assert len(writer.received_payloads) == 1
    sent = writer.received_payloads[0].model_dump_json()
    assert "Richter" not in sent and "Thomas Berger" not in sent  # Pseudonymisierung greift unveraendert
    users = _user_messages(db_session, conversation_id)
    assert [m.content for m in users] == [AUFTRAG]
    assert db_session.query(PrivacyDecision).filter_by(term="Richter").one().decision == "personal"
    assert db_session.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id,
                                                ChatMessage.draft_id.isnot(None)).count() == 1


def test_repeated_confirm_after_success_creates_no_second_order(client, db_session, writer, pdf_case) -> None:
    conversation_id, doc = pdf_case
    _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "personal"}])
    again = _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "personal"}])
    assert again[0]["message_id"] is None and "keinen offenen Auftrag" in again[0]["content"]
    assert len(_user_messages(db_session, conversation_id)) == 1 and len(writer.received_payloads) == 1


def test_order_is_ignored_when_an_open_order_already_exists(client, db_session, writer, pdf_case) -> None:
    """Wurde der Auftrag schon aus dem Eingabefeld gesendet (Gate-Sperre), setzt das Bestaetigen GENAU diesen fort."""
    conversation_id, doc = pdf_case
    blocked = _parse_sse_events(client.post("/dashboard/chat/send-stream", data={
        "csrf_token": _csrf(client), "conversation_id": conversation_id, "content": AUFTRAG}).text)
    assert next(e for e in blocked if e["kind"] == "done").get("blocked") is True
    _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "personal"}],
                        order="Ein ganz anderer Auftrag")
    assert [m.content for m in _user_messages(db_session, conversation_id)] == [AUFTRAG]
    assert len(writer.received_payloads) == 1


def test_stale_text_version_removes_the_new_order_and_a_retry_works_once(client, db_session, writer, pdf_case) -> None:
    conversation_id, doc = pdf_case
    failed = _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "personal"}],
                                 text_version="veraltet")
    assert failed[0]["message_id"] is None and "geändert" in failed[0]["content"]
    assert _roles(db_session, conversation_id) == ["upload"]  # Zustand wie vor dem Klick
    assert writer.received_payloads == [] and db_session.query(PrivacyDecision).count() == 0
    _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "personal"}])
    assert len(_user_messages(db_session, conversation_id)) == 1 and len(writer.received_payloads) == 1


def test_unanswered_question_removes_the_new_order_and_sends_nothing(client, db_session, writer, pdf_case) -> None:
    conversation_id, doc = pdf_case
    events = _confirm_with_order(client, conversation_id, doc, [])
    assert events[0]["message_id"] is None and "offenen Rückfragen" in events[0]["content"]
    assert _roles(db_session, conversation_id) == ["upload"] and writer.received_payloads == []


def test_order_is_refused_when_the_latest_message_is_not_the_upload(client, db_session, writer) -> None:
    """Kein Nachschieben eines Auftrags in eine bereits beantwortete Unterhaltung."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_MANUAL, actor="test")
    conversation_id = _upload(client, "a.pdf", _pdf_multipage(), "application/pdf")
    doc = _doc_of_conversation(db_session, conversation_id)
    _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "personal"}])
    assert len(writer.received_payloads) == 1
    events = _confirm_with_order(client, conversation_id, doc, [], order="Noch ein Auftrag", require_resolved=False)
    assert events[0]["message_id"] is None
    assert len(_user_messages(db_session, conversation_id)) == 1 and len(writer.received_payloads) == 1


def test_parallel_record_keeps_only_the_first_order(client, db_session, writer, pdf_case) -> None:
    """Zwei gleichzeitige Klicks, die beide noch keinen Auftrag sahen: nur der erste bleibt bestehen."""
    conversation_id, _doc = pdf_case
    conversation = db_session.get(ChatConversation, conversation_id)
    from app.models import User

    user = db_session.query(User).filter_by(email="admin@kanzlei.test").one()
    assert chat_router_module._record_upload_order(db_session, conversation, AUFTRAG, user) == ""
    # Zweiter Aufruf, der - wie ein paralleler Request - noch den Upload als juengste Nachricht gelesen hat
    latest_upload = db_session.query(ChatMessage).filter_by(conversation_id=conversation_id, role="upload").one()
    real_query = db_session.query

    def fake_query(model, *rest):
        query = real_query(model, *rest)
        if model is ChatMessage and not rest:
            class Wrapped:
                def __init__(self, q):
                    self._q = q

                def filter_by(self, **kw):
                    inner = self._q.filter_by(**kw)
                    if kw == {"conversation_id": conversation_id}:
                        class Latest:
                            def order_by(self, *a):
                                return self

                            def first(self):
                                return latest_upload
                        return Latest()
                    return inner

                def __getattr__(self, name):
                    return getattr(self._q, name)
            return Wrapped(query)
        return query

    db_session.query = fake_query
    try:
        second_problem = chat_router_module._record_upload_order(db_session, conversation, AUFTRAG, user)
    finally:
        db_session.query = real_query
    assert second_problem == "Diese Anfrage wird bereits verarbeitet."
    assert len(_user_messages(db_session, conversation_id)) == 1
    assert writer.received_payloads == []


def test_start_event_marks_analysis_orders_as_not_drafting(client, db_session, writer) -> None:
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client, "sachstand.pdf", _pdf_multipage(), "application/pdf", content=ANALYSE)
    doc = _doc_of_conversation(db_session, conversation_id)
    events = _confirm_with_order(client, conversation_id, doc, [{"term": "Richter", "decision": "not_personal"}],
                                 order=ANALYSE)
    assert events[0]["kind"] == "start" and events[0]["drafting"] is False
    assert len(writer.received_payloads) == 1


def test_frontend_sends_the_pending_order_and_shows_a_real_loading_state() -> None:
    """Statische Absicherung der Verdrahtung (die Bedienbarkeit selbst ist per Browser-QA mit echten Klicks belegt)."""
    center = (ROOT / "app/web/static/js/pseudonymization_center.js").read_text(encoding="utf-8")
    chat = (ROOT / "app/web/templates/chat.html").read_text(encoding="utf-8")
    assert 'form.append("order", state.pendingOrder)' in center
    assert 'state.mode = "release";\n        state.pendingOrder = readUnsentOrder();' in center
    assert "pseudo-question__view-hint" in center  # gesperrte Pruefansicht erklaert sich an der Frage
    assert 'next.kind === "status"' in center and "progress.status(" in center and "readWithIdleLimit" in center
    assert "window.LexonoChatProgress = chatProgress;" in chat
    assert 'chatProgress.error(next.content)' in chat
    # keine erfundenen Fortschrittsprozente in der Ladeanzeige
    progress_block = chat.split("var chatProgress = (function () {", 1)[1].split("window.LexonoChatProgress", 1)[0]
    assert "%" not in progress_block
