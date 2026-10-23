"""Reproduktion + Regression des Schriftsatz-Stalls nach einer Dokument-Freigabe (23.10.2026).

Befund (Root-Cause, zwei unabhaengige Code-Traces bestaetigt): Bei Freigabemodus "auto_review"
(Standard fuer Neuinstallationen) blockiert bei einem Schriftsatz-Auftrag auf ein Dokument mit
unsicherem Begriff zuerst das Dokument-Gate (`_documents_gate` -> Hinweis-Nachricht, KEIN
"review"-Ereignis). Die anschliessende Dokument-Freigabe (`POST .../release`) schrieb bisher nur
eine `DocumentRelease`-Zeile und leitete in die Dokumentansicht zurueck - NICHTS nahm den
unterbrochenen Schriftsatz wieder auf. Ergebnis fuer die Nutzerin: "nach der Antwort passiert
nichts, kein Schriftsatz".

Diese Datei belegt den Stall (Reproduktion) und sichert danach die Wiederaufnahme ueber den neuen
Endpunkt ab. Ausschliesslich synthetische Daten, kein Cloud-Aufruf (Test-Double
`FakeClaudeWritingProvider`), keine Produktiv-Datenbank.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web.chat_router as chat_router_module
from app.documents import release
from app.models import ChatMessage
from app.models.document_release import DocumentRelease
from tests.auth_test_utils import create_test_user, extract_upload_token, login, login_as_admin, seed_roles
from tests.test_web_chat import (  # noqa: F401 - Fixtures
    FakeClaudeWritingProvider,
    _csrf,
    _parse_sse_events,
    _working_drafting_service,
    client,
    db_session,
)

# Auftrag MIT Erstellungs-Verb + Formell-Dokument-Substantiv -> Routing "formulate_draft"
# (siehe app/chat/service.py _DRAFTING_TRIGGER_PATTERN). Der unsichere Begriff "Richter" steckt
# im angehaengten Dokument, nicht im Auftragstext.
AUFTRAG = "Bitte erstelle einen Schriftsatz an das Gericht mit dem aktuellen Sachstand."
DOKUMENT = "Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024."


@pytest.fixture()
def writer(monkeypatch: pytest.MonkeyPatch) -> FakeClaudeWritingProvider:
    fake = FakeClaudeWritingProvider("Sehr geehrte Damen und Herren, [Entwurf].")
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(fake))
    return fake


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


def _upload(client: TestClient) -> dict:
    """§57: erst nur hochladen (kein KI-Aufruf)."""
    files = {"documents": ("sachstand.pdf", _pdf_bytes(["Sachstand (synthetisch)", DOKUMENT]), "application/pdf")}
    upload = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream", files=files,
        data={"upload_token": extract_upload_token(client.get("/dashboard/chat").text),
              "csrf_token": _csrf(client), "conversation_id": "", "content": AUFTRAG},
    ).text)
    assert [e["kind"] for e in upload] == ["start", "uploaded"], upload
    return upload[0]


def _send_order(client: TestClient, conversation_id: str) -> list[dict]:
    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": _csrf(client), "conversation_id": conversation_id, "content": AUFTRAG},
    )
    assert response.status_code == 200
    return _parse_sse_events(response.text)


def test_reproduction_order_on_uncertain_document_is_blocked_at_the_gate_not_as_review(
    client, db_session: Session, writer
) -> None:
    """Teil 1 des Belegs: im auto_review-Modus blockiert das Dokument-Gate den Auftrag mit einer
    Hinweis-Nachricht (KEIN "review"-Ereignis, KEIN Cloud-Aufruf)."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]

    events = _send_order(client, conversation_id)

    assert not [e for e in events if e["kind"] == "review"], "erwartet: Gate blockt, kein review"
    done = next(e for e in events if e["kind"] == "done")
    assert done.get("blocked") is True
    assert "freigeben" in done["content"].lower() or "pseudonymisierung" in done["content"].lower()
    assert writer.received_payloads == []  # nichts hat die Cloud erreicht


def test_reproduction_release_alone_does_not_resume_the_draft(client, db_session: Session, writer) -> None:
    """Teil 2 des Belegs (der eigentliche Fehler): nach der Dokument-Freigabe wird KEIN Entwurf
    erzeugt und nichts wiederaufgenommen - der Auftrag ist verloren."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]
    _send_order(client, conversation_id)  # -> blockiert am Gate

    # Dokument der Konversation ermitteln und regulaer freigeben (wie es die Nutzerin tut).
    from app.models import ChatMessageDocument, Document

    doc_id = (
        db_session.query(ChatMessageDocument.document_id)
        .join(ChatMessage, ChatMessage.id == ChatMessageDocument.message_id)
        .filter(ChatMessage.conversation_id == conversation_id)
        .scalar()
    )
    assert doc_id is not None
    doc = db_session.get(Document, doc_id)
    matter_id = doc.matter_id
    text_version = release.text_version_of(doc.extracted_text)

    resp = client.post(
        f"/dashboard/matters/{matter_id}/document/{doc_id}/release",
        data={"csrf_token": _csrf(client), "text_version": text_version,
              "return_to": f"chat:{conversation_id}"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    # Freigabe wurde gespeichert ...
    assert db_session.query(DocumentRelease).filter_by(document_id=doc_id).count() == 1
    # ... aber KEIN Entwurf erzeugt und KEINE Assistenten-Nachricht mit draft_id vorhanden.
    assistant_drafts = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id, ChatMessage.role == "assistant",
                ChatMessage.draft_id.isnot(None))
        .count()
    )
    assert assistant_drafts == 0, "REPRODUKTION: Freigabe allein nimmt den Schriftsatz nicht wieder auf"
    assert writer.received_payloads == []  # es wurde nie generiert


# --- Fix: "Pseudonymisierung bestätigen und Schriftsatz erstellen" ----------------------------------------------------


def _doc_of_conversation(db_session: Session, conversation_id: str):
    from app.models import ChatMessageDocument, Document

    doc_id = (
        db_session.query(ChatMessageDocument.document_id)
        .join(ChatMessage, ChatMessage.id == ChatMessageDocument.message_id)
        .filter(ChatMessage.conversation_id == conversation_id)
        .scalar()
    )
    return db_session.get(Document, doc_id)


def _confirm(client: TestClient, conversation_id: str, doc, *, text_version: str | None = None):
    return client.post(
        f"/dashboard/chat/{conversation_id}/confirm-release-and-generate",
        data={"csrf_token": _csrf(client), "document_id": doc.id,
              "text_version": text_version if text_version is not None else release.text_version_of(doc.extracted_text)},
    )


def test_confirm_release_and_generate_produces_the_draft_when_nothing_is_uncertain(
    client, db_session: Session, writer
) -> None:
    """Manueller Modus, Dokument OHNE unsicheren Begriff: der Auftrag haengt nur an der fehlenden Freigabe.
    Ein Klick gibt frei UND erzeugt den Entwurf (SIMULATION der Cloud ueber das Test-Double)."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_MANUAL, actor="test")
    files = {"documents": ("sachstand.pdf",
                           _pdf_bytes(["Sachstand (synthetisch)",
                                       "Die Abnahme der Bauleistung erfolgte am 12.03.2026 vollstaendig."]),
                           "application/pdf")}
    upload = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream", files=files,
        data={"upload_token": extract_upload_token(client.get("/dashboard/chat").text),
              "csrf_token": _csrf(client), "conversation_id": "", "content": AUFTRAG},
    ).text)
    conversation_id = upload[0]["conversation_id"]
    blocked = _send_order(client, conversation_id)
    assert next(e for e in blocked if e["kind"] == "done").get("blocked") is True  # Gate: Freigabe erforderlich

    doc = _doc_of_conversation(db_session, conversation_id)
    events = _parse_sse_events(_confirm(client, conversation_id, doc).text)

    done = next(e for e in events if e["kind"] == "done")
    assert done.get("blocked") is not True and not [e for e in events if e["kind"] == "review"]
    assert len(writer.received_payloads) == 1  # genau eine Cloud-(Simulations-)Anfrage
    assert db_session.query(DocumentRelease).filter_by(document_id=doc.id).count() == 1
    assistant_draft = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id, ChatMessage.role == "assistant",
                ChatMessage.draft_id.isnot(None))
        .count()
    )
    assert assistant_draft == 1  # Entwurf im Editor verfuegbar (draft_id gesetzt)


def test_confirm_release_then_asks_about_uncertain_term_then_draft(client, db_session: Session, writer) -> None:
    """auto_review mit unsicherem Begriff: der Klick gibt frei und nimmt wieder auf; das fail-closed Gateway fragt
    nun nach "Richter" (Rueckfrage), und erst nach der Entscheidung entsteht der Entwurf. Kein Gateway-Bypass."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]
    _send_order(client, conversation_id)  # Gate blockt

    doc = _doc_of_conversation(db_session, conversation_id)
    events = _parse_sse_events(_confirm(client, conversation_id, doc).text)

    # Nach der Freigabe: KEIN stiller Versand, sondern die faellige Person-Rueckfrage.
    review = next(e for e in events if e["kind"] == "review")
    assert [c["term"] for c in review["candidates"]] == ["Richter"]
    assert writer.received_payloads == []  # weiterhin nichts an die Cloud, bis entschieden ist
    db_session.expire_all()  # frischer Blick nach dem Streaming-Request
    assert db_session.query(DocumentRelease).filter_by(document_id=doc.id).count() == 1  # Freigabe gespeichert

    # Entscheidung -> Wiederaufnahme ueber den bestehenden Pfad -> Entwurf.
    answer = client.post(
        f"/dashboard/chat/{conversation_id}/privacy-review",
        data={"csrf_token": _csrf(client), "message_id": review["message_id"],
              "decisions": json.dumps([{"term": "Richter", "decision": "personal"}])},
    )
    done_events = _parse_sse_events(answer.text)
    assert [e["kind"] for e in done_events if e["kind"] in ("done", "review")] == ["done"]
    assert len(writer.received_payloads) == 1
    assert "Richter" not in writer.received_payloads[0].model_dump_json()  # pseudonymisiert


def test_confirm_release_rejects_a_document_of_another_matter(client, db_session: Session, writer) -> None:
    """Aktenisolation: ein Dokument einer FREMDEN Akte kann ueber diese Unterhaltung nicht freigegeben/generiert
    werden."""
    from app.models import Client as ClientModel, Document, Matter

    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]
    _send_order(client, conversation_id)

    other = Matter(client=ClientModel(name="Fremd-Mandant (synthetisch)"), title="Fremde Akte",
                   reference_number="2027/000")
    db_session.add(other)
    db_session.commit()
    foreign = Document(matter_id=other.id, original_filename="fremd.txt", mime_type="text/plain",
                       file_path="synthetic/fremd.txt", extracted_text="Fremder Text.", ocr_status="not_needed")
    db_session.add(foreign)
    db_session.commit()

    events = _parse_sse_events(_confirm(client, conversation_id, foreign).text)
    assert events[0]["message_id"] is None and "nicht zu dieser Unterhaltung" in events[0]["content"]
    assert writer.received_payloads == []
    assert db_session.query(DocumentRelease).filter_by(document_id=foreign.id).count() == 0


def test_confirm_release_rejects_a_stale_text_version_without_generating(client, db_session: Session, writer) -> None:
    """Veralteter Textstand: die Freigabe wird abgewiesen, NICHTS wird generiert, und die Nachricht bleibt offen
    (erneuter Versuch moeglich)."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]
    _send_order(client, conversation_id)
    doc = _doc_of_conversation(db_session, conversation_id)

    events = _parse_sse_events(_confirm(client, conversation_id, doc, text_version="veraltet").text)
    assert events[0]["message_id"] is None and "geändert" in events[0]["content"]
    assert writer.received_payloads == []
    assert db_session.query(DocumentRelease).filter_by(document_id=doc.id).count() == 0
    # Nachricht wieder frei -> ein korrekter zweiter Versuch gelangt bis zur Rueckfrage.
    retry = _parse_sse_events(_confirm(client, conversation_id, doc).text)
    assert any(e["kind"] == "review" for e in retry)


def test_confirm_release_does_not_generate_twice_for_a_message_in_processing(
    client, db_session: Session, writer
) -> None:
    """Doppelklick/parallele Verarbeitung: ist die Nachricht bereits beansprucht, wird weder freigegeben noch
    generiert."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]
    _send_order(client, conversation_id)
    doc = _doc_of_conversation(db_session, conversation_id)

    # Die OFFENE Nutzernachricht (nicht die blockierte Hinweis-Nachricht) ist bereits in Verarbeitung.
    user_order = (
        db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation_id, role="user")
        .order_by(ChatMessage.created_at.desc())
        .first()
    )
    assert chat_router_module._claim_message(db_session, user_order.id)
    try:
        events = _parse_sse_events(_confirm(client, conversation_id, doc).text)
        assert events[0]["message_id"] is None and "bereits verarbeitet" in events[0]["content"]
        assert writer.received_payloads == []
        db_session.expire_all()
        assert db_session.query(DocumentRelease).filter_by(document_id=doc.id).count() == 0
    finally:
        chat_router_module._release_message(db_session, user_order.id)


def test_confirm_release_reports_missing_ki_config_understandably(
    client, db_session: Session, writer, monkeypatch
) -> None:
    """Fehlende Cloud-KI-Konfiguration: das Dokument wird freigegeben, aber statt eines Scheinerfolgs erscheint eine
    verstaendliche Meldung, dass die KI nicht verfuegbar ist (kein Entwurf)."""
    from app.web.service_factory import WritingProviderNotConfiguredError

    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_MANUAL, actor="test")
    files = {"documents": ("sachstand.pdf",
                           _pdf_bytes(["Sachstand", "Die Abnahme erfolgte am 12.03.2026 vollstaendig."]),
                           "application/pdf")}
    upload = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream", files=files,
        data={"upload_token": extract_upload_token(client.get("/dashboard/chat").text),
              "csrf_token": _csrf(client), "conversation_id": "", "content": AUFTRAG},
    ).text)
    conversation_id = upload[0]["conversation_id"]
    _send_order(client, conversation_id)
    doc = _doc_of_conversation(db_session, conversation_id)

    def _no_provider():
        raise WritingProviderNotConfiguredError("nicht konfiguriert")

    monkeypatch.setattr(chat_router_module, "get_drafting_service", _no_provider)
    done = next(e for e in _parse_sse_events(_confirm(client, conversation_id, doc).text) if e["kind"] == "done")
    assert done.get("blocked") is True
    assert "ki" in done["content"].lower() or "nicht" in done["content"].lower()
    assert writer.received_payloads == []


# --- P2/P3/P4 UI-Markup (zentrierte Dialoge, Freigabe-Button) ---------------------------------------------------------


def test_chat_page_contains_the_centered_hitl_dialog(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/chat").text
    assert 'id="privacy-review-dialog"' in page and 'class="modal modal--medium"' in page
    assert 'id="privacy-review-dialog-body"' in page


def test_document_view_uses_a_pseudonymization_dialog_and_offers_confirm_and_generate(
    client, db_session: Session, writer
) -> None:
    """P2: Pseudonymisierung im zentrierten Dialog statt in der quetschenden Seitenspalte (kompakter Status +
    Button). P4: im Chat-Kontext der kombinierte Button "Pseudonymisierung bestätigen und Schriftsatz erstellen"."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]
    doc = _doc_of_conversation(db_session, conversation_id)

    page = client.get(f"/dashboard/chat/{conversation_id}/document/{doc.id}").text
    # P2: Dialog + kompakter Oeffnen-Button vorhanden; die alte, dauerhaft sichtbare Seitenspalte (<aside>) ist weg.
    assert 'id="pseudonymization-dialog"' in page
    assert "data-pseudonymization-open" in page and "Pseudonymisierung anzeigen" in page
    assert '<aside class="chat-context-pane"' not in page
    # Die Details (reale erkannte Kategorien) liegen jetzt im Dialog-Body.
    assert 'class="modal__body chat-context-pane"' in page
    # P4: kombinierter Button mit der Konversations-/Dokument-Zuordnung.
    assert "data-confirm-release-generate" in page
    assert f'data-conversation-id="{conversation_id}"' in page
    assert f'data-document-id="{doc.id}"' in page
    assert "Pseudonymisierung bestätigen und Schriftsatz erstellen" in page


# --- Autorisierung + Anti-Doppelauftrag (Review 23.10.2026) -----------------------------------------------------------


def test_confirm_release_requires_privacy_decision_manage_permission(client, db_session: Session, writer) -> None:
    """Autorisierung: Ohne PERM_PRIVACY_DECISION_MANAGE (z. B. Rolle "mitarbeiter") wird der Endpunkt abgewiesen,
    bevor irgendeine Freigabe oder Generierung stattfindet."""
    # Konversation + Dokument als Admin anlegen.
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_AUTO_REVIEW, actor="test")
    conversation_id = _upload(client)["conversation_id"]
    doc = _doc_of_conversation(db_session, conversation_id)

    # Als Mitarbeiter (ohne Freigaberecht) anmelden und denselben Endpunkt aufrufen.
    staff = create_test_user(db_session, seed_roles(db_session)["mitarbeiter"], "mitarbeiter@kanzlei.test")
    client.cookies.clear()
    login(client, staff.email)
    resp = client.post(
        f"/dashboard/chat/{conversation_id}/confirm-release-and-generate",
        data={"csrf_token": _csrf(client), "document_id": doc.id,
              "text_version": release.text_version_of(doc.extracted_text)},
    )
    assert resp.status_code in (403, 404)
    assert writer.received_payloads == []
    assert db_session.query(DocumentRelease).filter_by(document_id=doc.id).count() == 0


def test_confirm_release_refuses_when_the_order_is_already_completed(client, db_session: Session, writer) -> None:
    """Keine Doppelerzeugung: Ist der Auftrag bereits erfolgreich erledigt (letzte Nachricht = Entwurf), lehnt ein
    erneuter Klick ab, statt einen zweiten Schriftsatz zu erzeugen."""
    login_as_admin(db_session, client)
    release.set_mode(db_session, release.MODE_MANUAL, actor="test")
    files = {"documents": ("sachstand.pdf",
                           _pdf_bytes(["Sachstand", "Die Abnahme erfolgte am 12.03.2026 vollstaendig."]),
                           "application/pdf")}
    upload = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream", files=files,
        data={"upload_token": extract_upload_token(client.get("/dashboard/chat").text),
              "csrf_token": _csrf(client), "conversation_id": "", "content": AUFTRAG},
    ).text)
    conversation_id = upload[0]["conversation_id"]
    _send_order(client, conversation_id)
    doc = _doc_of_conversation(db_session, conversation_id)

    first = _parse_sse_events(_confirm(client, conversation_id, doc).text)
    assert next(e for e in first if e["kind"] == "done").get("blocked") is not True
    assert len(writer.received_payloads) == 1  # genau ein Entwurf erzeugt

    # Zweiter Klick: der Auftrag ist erledigt (letzte Nachricht ist der erfolgreiche Entwurf).
    second = _parse_sse_events(_confirm(client, conversation_id, doc).text)
    assert second[0]["message_id"] is None and "kein" in second[0]["content"].lower()
    assert len(writer.received_payloads) == 1  # KEINE zweite Generierung
