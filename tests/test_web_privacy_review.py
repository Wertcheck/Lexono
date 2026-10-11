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


# --- Neuladen (12.10.2026): offene Rueckfrage sicher wiederherstellen, kein Doppelversand -----------------------------


def test_pending_review_is_restored_after_reload_without_any_cloud_call(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    review = next(e for e in _send(client, _csrf(client)) if e["kind"] == "review")
    restored = client.get(f"/dashboard/chat/{review['conversation_id']}/pending-review").json()
    assert restored["state"] == "review"
    assert restored["message_id"] == review["message_id"]
    assert [c["term"] for c in restored["candidates"]] == ["Richter"]
    assert writer.received_payloads == []


def test_restored_review_resumes_and_is_then_closed(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    review = next(e for e in _send(client, csrf) if e["kind"] == "review")
    restored = client.get(f"/dashboard/chat/{review['conversation_id']}/pending-review").json()
    response = _review(client, csrf, restored["conversation_id"], restored["message_id"], [{"term": "Richter", "decision": "personal"}])
    assert [e["kind"] for e in _parse_sse_events(response.text) if e["kind"] in ("done", "review")] == ["done"]
    assert len(writer.received_payloads) == 1 and "Richter" not in writer.received_payloads[0].model_dump_json()
    assert client.get(f"/dashboard/chat/{review['conversation_id']}/pending-review").json()["state"] == "none"
    # Erneutes Absenden derselben (jetzt beantworteten) Rueckfrage loest keine zweite Cloud-Anfrage aus.
    again = _review(client, csrf, review["conversation_id"], review["message_id"], [{"term": "Richter", "decision": "personal"}])
    assert _parse_sse_events(again.text)[0]["message_id"] is None
    assert len(writer.received_payloads) == 1


def test_message_in_processing_is_never_offered_or_released_twice(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    review = next(e for e in _send(client, csrf) if e["kind"] == "review")
    assert chat_router_module._claim_message(db_session, review["message_id"])  # simuliert eine laufende Verarbeitung
    try:
        assert client.get(f"/dashboard/chat/{review['conversation_id']}/pending-review").json() == {"state": "processing"}
        events = _parse_sse_events(_review(client, csrf, review["conversation_id"], review["message_id"], [{"term": "Richter", "decision": "not_personal"}]).text)
        assert events[0]["message_id"] is None and "bereits verarbeitet" in events[0]["content"]
        assert writer.received_payloads == []
    finally:
        chat_router_module._release_message(db_session, review["message_id"])


def test_pending_review_of_a_foreign_conversation_is_denied(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    review = next(e for e in _send(client, _csrf(client)) if e["kind"] == "review")
    from tests.auth_test_utils import seed_roles

    other = create_test_user(db_session, seed_roles(db_session)["admin"], "fremd@kanzlei.test")
    client.cookies.clear()
    login(client, other.email)
    response = client.get(f"/dashboard/chat/{review['conversation_id']}/pending-review")
    assert response.status_code != 200 or response.json().get("state") != "review"


# --- Fundstelle im Dokument (12.10.2026) -------------------------------------------------------------------------------


def _pdf_bytes(lines: list[str]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((50, y), line)
        y += 16
    data = doc.tobytes()
    doc.close()
    return data


def test_review_links_to_the_highlighted_finding_in_the_attached_document(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    csrf = _csrf(client)
    pdf = _pdf_bytes(["Entwurf (synthetisch)", "Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024."])
    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": csrf, "conversation_id": "", "content": "Bitte fasse das Dokument zusammen."},
        files={"documents": ("entwurf.pdf", pdf, "application/pdf")},
    )
    review = next(e for e in _parse_sse_events(response.text) if e["kind"] == "review")
    candidate = next(c for c in review["candidates"] if c["term"] == "Richter")
    assert candidate["document_url"].startswith(f"/dashboard/chat/{review['conversation_id']}/document/")
    page = client.get(candidate["document_url"].split("#")[0])
    assert page.status_code == 200
    assert '<mark class="pii-highlight pii-highlight--review" id="review-mark"' in page.text
    assert writer.received_payloads == []


def test_review_without_a_document_finding_has_no_link(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    review = next(e for e in _send(client, _csrf(client)) if e["kind"] == "review")
    assert all(c["document_url"] is None for c in review["candidates"])


def test_document_preview_marks_review_terms_safely() -> None:
    from app.chat.document_preview import build_document_preview

    text = "Eingang des Schreibens von Richter vom 16.03.2024. Der Schiedsrichter <b>entschied</b>."
    html = build_document_preview(text, review_terms=frozenset({"Richter"})).highlighted_html
    assert html.count("pii-highlight--review") == 1  # ganzes Wort, nicht "Schiedsrichter"
    assert 'id="review-mark"' in html and "&lt;b&gt;" in html and "<b>" not in html
    assert "pii-highlight--review" not in build_document_preview(text).highlighted_html
    absent = build_document_preview(text, review_terms=frozenset({"Fuerst"})).highlighted_html
    assert "pii-highlight--review" not in absent


def test_interrupted_processing_is_never_resumed_automatically(client, db_session: Session, writer) -> None:
    """Abgestuerzter Prozess: Sperre bleibt liegen und ist aelter als die Frist. Unklar, ob bereits gesendet wurde -
    weder Rueckfrage noch Fortsetzung, keine Cloud-Anfrage (fail-closed)."""
    from datetime import datetime, timedelta, timezone

    from app.models.chat_message_claim import ChatMessageClaim

    login_as_admin(db_session, client)
    csrf = _csrf(client)
    review = next(e for e in _send(client, csrf) if e["kind"] == "review")
    db_session.add(ChatMessageClaim(
        message_id=review["message_id"], claimed_at=datetime.now(timezone.utc) - timedelta(hours=1), owner="abgestuerzt"
    ))
    db_session.commit()
    assert client.get(f"/dashboard/chat/{review['conversation_id']}/pending-review").json() == {"state": "interrupted"}
    events = _parse_sse_events(_review(client, csrf, review["conversation_id"], review["message_id"], [{"term": "Richter", "decision": "personal"}]).text)
    assert events[0]["message_id"] is None and "unterbrochen" in events[0]["content"]
    assert writer.received_payloads == []



def test_uploaded_pdf_with_heading_and_wrapped_agent_is_asked_before_any_cloud_call(client, db_session: Session, writer) -> None:
    """Echter Upload-Pfad (Extraktion + Dokumentaufbereitung), Befund im installierten Build 12.10.2026."""
    login_as_admin(db_session, client)
    pdf = _pdf_bytes([
        "Zeugenaussage Richter",
        "Der Zeuge schildert den Ablauf der Abnahme am 12.03.2026.",
        "Die Unterlagen wurden am 12.03.2026 durch",
        "Lange geprueft und dem Auftraggeber uebergeben.",
    ])
    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": _csrf(client), "conversation_id": "", "content": "Bitte fasse das Protokoll zusammen."},
        files={"documents": ("protokoll.pdf", pdf, "application/pdf")},
    )
    review = next(e for e in _parse_sse_events(response.text) if e["kind"] == "review")
    assert {"Richter", "Lange"} <= {c["term"] for c in review["candidates"]}
    assert writer.received_payloads == []
