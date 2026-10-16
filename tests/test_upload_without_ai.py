"""§57: Upload und KI-Verarbeitung sind getrennte Vorgaenge - Chat und Schriftsatz-Generator.

Ein Upload speichert und liest das Dokument lokal aus (Extraktion/OCR wie bisher) und loest KEINE KI-Anfrage aus -
serverseitig erzwungen, auch nicht fuer einen mitgetippten Text. Erst eine separate, bewusste Aktion (Frage senden
bzw. "Schriftsatz generieren" ohne Dateien) laeuft durch die unveraenderte zentrale Datenschutzpruefung.

Nachweis auf HTTP-Ebene: echter `AnthropicClaudeWritingProvider` (echte Factory, echtes SDK) gegen den lokalen
Aufzeichnungsserver auf 127.0.0.1 (tests/test_privacy_wire_payload.py), toter Proxy als Riegel, gefaelschter
Schluessel. Belegt sind die unverschluesselten Payloads auf diesem Rechner; TLS zum echten Endpunkt ist NICHT belegt.
Nur synthetische Daten, kein Anthropic-Aufruf."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.chat_router as chat_router_module
import app.web.schriftsatz_router as schriftsatz_router_module
from app.chat.service import UPLOAD_NOTE
from app.db.session import get_db
from app.main import app
from app.models import ApiCallLog, ChatConversation, ChatMessage, Document, Draft, Matter
from app.models.base import Base
from app.models.privacy_decision import PrivacyDecision
from app.web.name_protection import document_text_version
from tests.auth_test_utils import extract_csrf, login_as_admin
from tests.test_web_chat import _parse_sse_events, _working_drafting_service
from tests.test_web_document_name_protection import SachverhaltEcho
from tests.test_web_privacy_review import _pdf_bytes

LINES = [
    "Beschluss des Amtsgerichts Musterstadt vom 03.11.2026, Az. 3 C 117/26.",
    "Der Richter Zweigelt hat den Termin auf den 22.07.2026 verlegt;",
    "Streitwert 4.250,00 EUR nach § 3 ZPO.",
]
QUESTION = "Fasse das angehängte Dokument zusammen."
TYPED = "Was entschied Richter Zweigelt?"  # mitgetippter Text beim Upload - darf NICHT gesendet werden


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def writer() -> SachverhaltEcho:
    return SachverhaltEcho()


@pytest.fixture()
def client(db_session: Session, writer, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    from app.config.settings import Settings

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    settings = Settings(_env_file=None, chat_upload_storage_dir=str(tmp_path / "chat"),
                        schriftsatz_upload_storage_dir=str(tmp_path / "schriftsatz"))
    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer))
    monkeypatch.setattr(schriftsatz_router_module, "get_drafting_service", lambda: _working_drafting_service(writer))
    monkeypatch.setattr(chat_router_module, "get_settings", lambda: settings)
    monkeypatch.setattr(schriftsatz_router_module, "get_settings", lambda: settings)
    try:
        test_client = TestClient(app, raise_server_exceptions=False)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def recorder(monkeypatch, writer) -> Iterator:
    """Echter Provider gegen den Loopback-Recorder - ersetzt den Fake-Writer in beiden Routern."""
    from tests.test_privacy_wire_payload import _provider, _Recorder

    rec = _Recorder()
    for key, value in (("ANTHROPIC_BASE_URL", rec.url), ("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1"),
                       ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                       ("NO_PROXY", "127.0.0.1,localhost")):
        monkeypatch.setenv(key, value)
    service = lambda: _working_drafting_service(_provider())  # noqa: E731
    monkeypatch.setattr(chat_router_module, "get_drafting_service", service)
    monkeypatch.setattr(schriftsatz_router_module, "get_drafting_service", service)
    yield rec
    rec.server.shutdown()
    rec.server.server_close()


def _csrf(client: TestClient, path: str = "/dashboard/chat") -> str:
    return extract_csrf(client.get(path).text)


def _upload(client: TestClient, content: str = TYPED, conversation_id: str = "", name: str = "beschluss.pdf",
            data: bytes | None = None) -> list[dict]:
    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": _csrf(client), "conversation_id": conversation_id, "content": content},
        files={"documents": (name, data if data is not None else _pdf_bytes(LINES), "application/pdf")},
    )
    assert response.status_code == 200
    return _parse_sse_events(response.text)


def _ask(client: TestClient, conversation_id: str, content: str = QUESTION, **extra) -> list[dict]:
    response = client.post("/dashboard/chat/send-stream", data={
        "csrf_token": _csrf(client), "conversation_id": conversation_id, "content": content, **extra})
    return _parse_sse_events(response.text)


def _protect_in_chat_view(client: TestClient, conversation_id: str, document: Document, terms, *, version=None):
    page = client.get(f"/dashboard/chat/{conversation_id}/document/{document.id}").text
    return client.post(
        f"/dashboard/matters/{document.matter_id}/document/{document.id}/protect-names",
        data={"csrf_token": extract_csrf(page), "protected_terms": json.dumps(terms),
              "text_version": version if version is not None else document_text_version(document),
              "return_to": f"chat:{conversation_id}"},
        follow_redirects=False,
    )


def _is_rejection(events: list[dict]) -> bool:
    """Abweisung VOR jeder Persistenz: ein einzelnes "done" ohne Nachricht (chat_router._stream_error_event)."""
    return len(events) == 1 and events[0]["kind"] == "done" and events[0]["message_id"] is None and events[0]["blocked"]


def _wire(request: dict) -> str:
    return json.dumps(json.loads(request["raw"].decode("utf-8")), ensure_ascii=False)


# --- 1. Upload ohne jede ausgehende KI-Anfrage --------------------------------------------------------------------------


def test_chat_upload_sends_no_http_request_and_logs_no_api_call(client, db_session, recorder) -> None:
    events = _upload(client)
    assert recorder.requests == []  # HTTP-Nachweis: nichts hat den Aufzeichnungsserver erreicht
    assert db_session.query(ApiCallLog).count() == 0
    assert [e["kind"] for e in events] == ["start", "uploaded"] and events[1]["text_kept"] is True
    document = db_session.query(Document).one()
    assert "Zweigelt" in document.extracted_text  # lokal ausgelesen und gespeichert ...
    conversation = db_session.query(ChatConversation).one()
    assert document.matter_id == conversation.matter_id  # ... und der Akte der Unterhaltung zugeordnet
    roles = [m.role for m in db_session.query(ChatMessage).filter_by(conversation_id=conversation.id)]
    assert roles == ["upload"]  # keine Nutzernachricht, keine Antwort - der mitgetippte Text ist nicht gespeichert
    assert TYPED not in "".join(m.content for m in db_session.query(ChatMessage))


def test_classic_send_with_a_file_only_uploads(client, db_session, writer) -> None:
    response = client.post(
        "/dashboard/chat/send", follow_redirects=False,
        data={"csrf_token": _csrf(client), "conversation_id": "", "content": TYPED},
        files={"documents": ("beschluss.pdf", _pdf_bytes(LINES), "application/pdf")},
    )
    document = db_session.query(Document).one()
    conversation = db_session.query(ChatConversation).one()
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/dashboard/chat/{conversation.id}/document/{document.id}?uploaded=1&text_not_sent=1#name-protection")
    assert writer.received_payloads == []


def test_upload_without_text_and_the_upload_notice_is_visible(client, db_session, writer) -> None:
    events = _upload(client, content="")
    assert events[1]["text_kept"] is False and "text_not_sent" not in events[1]["target"]
    page = client.get(events[1]["target"].split("#")[0]).text
    assert 'id="upload-notice"' in page and "noch keine KI-Verarbeitung" in page
    assert 'id="document-protect-btn"' in page  # Pruefen und Markieren direkt hier
    chat_page = client.get(f"/dashboard/chat/{events[0]['conversation_id']}").text
    assert "chat-message--upload" in chat_page and "beim Hochladen keine KI-Verarbeitung" in chat_page
    assert writer.received_payloads == []


def test_unreadable_upload_is_reported_locally_without_any_request(client, db_session, recorder) -> None:
    events = _upload(client, data=b"%PDF-1.4 kein gueltiges PDF", name="kaputt.pdf")
    assert events[1]["target"] == f"/dashboard/chat/{events[0]['conversation_id']}"  # kein lesbarer Text
    note = db_session.query(ChatMessage).filter_by(role="upload").one().content
    assert note.startswith(UPLOAD_NOTE) and "„kaputt.pdf“" in note and "Fehler aufgetreten" in note
    assert recorder.requests == []
    # die bewusste Folgefrage bleibt durch die Lesbarkeitspruefung gesperrt (nicht still ohne Dokument gesendet)
    done = [e for e in _ask(client, events[0]["conversation_id"]) if e["kind"] == "done"]
    assert done[0]["blocked"] is True and recorder.requests == []


# --- 2. Markierung nach dem Upload, Cloud erst nach bewusster Aktion -------------------------------------------------


def test_mark_after_upload_then_the_deliberate_question_is_the_first_and_masked_request(
    client, db_session, recorder
) -> None:
    events = _upload(client)
    conversation_id = events[0]["conversation_id"]
    document = db_session.query(Document).one()
    response = _protect_in_chat_view(client, conversation_id, document, ["Zweigelt"])
    assert "name_protection=saved" in response.headers["location"] and recorder.requests == []

    _ask(client, conversation_id)
    assert len(recorder.requests) == 1  # erst die bewusste Aktion erreicht die (Loopback-)Cloud
    wire = _wire(recorder.requests[0])
    assert "Zweigelt" not in wire  # verifizierter Payload: markierter Name nicht im Klartext
    for value in ("03.11.2026", "22.07.2026", "4.250,00 EUR", "§ 3 ZPO"):
        assert value in wire  # Dokumentinhalt (der zuvor hochgeladenen Datei) ist enthalten
    assert UPLOAD_NOTE[:40] not in wire  # der lokale Upload-Hinweis ist kein Gespraechsbeitrag
    assert db_session.query(ApiCallLog).count() == 1


def test_deliberate_question_is_scoped_to_the_uploaded_document_and_reconstructed(client, db_session, writer) -> None:
    events = _upload(client)
    conversation_id = events[0]["conversation_id"]
    document = db_session.query(Document).one()
    _protect_in_chat_view(client, conversation_id, document, ["Zweigelt"])
    done = [e for e in _ask(client, conversation_id) if e["kind"] == "done"][0]
    sent = json.dumps(writer.received_payloads[0].model_dump(), ensure_ascii=False)
    assert "Zweigelt" not in sent and "4.250,00 EUR" in sent
    assert "Zweigelt" in done["content"]  # Rueckfuehrung lokal: der Platzhalter wird wieder zum Namen


def test_second_question_does_not_resend_the_upload_note(client, db_session, writer) -> None:
    conversation_id = _upload(client)[0]["conversation_id"]
    _ask(client, conversation_id)
    _ask(client, conversation_id, "Welche Frist ergibt sich daraus?")
    assert len(writer.received_payloads) == 2
    assert "noch keine KI-Verarbeitung" not in json.dumps(writer.received_payloads[1].model_dump(), ensure_ascii=False)


# --- 3. Fehler- und Manipulationsfaelle --------------------------------------------------------------------------------


def test_stale_document_marking_is_rejected_and_nothing_is_sent(client, db_session, recorder) -> None:
    conversation_id = _upload(client)[0]["conversation_id"]
    document = db_session.query(Document).one()
    response = _protect_in_chat_view(client, conversation_id, document, ["Zweigelt"], version="veraltet")
    assert "name_protection_error" in response.headers["location"]
    assert db_session.query(PrivacyDecision).count() == 0 and recorder.requests == []


def test_invalid_marking_on_the_question_sends_nothing(client, db_session, recorder) -> None:
    conversation_id = _upload(client)[0]["conversation_id"]
    events = _ask(client, conversation_id, protected_terms=json.dumps(["Nichtimtext"]))
    assert _is_rejection(events) and recorder.requests == []


def test_failed_privacy_check_is_fail_closed_after_an_upload(client, db_session, recorder, monkeypatch) -> None:
    conversation_id = _upload(client)[0]["conversation_id"]

    calls = []

    def _broken(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("Pruefung nicht verfuegbar (synthetisch)")

    monkeypatch.setattr("app.privacy.gateway.ClaudePrivacyGateway.prepare_request", _broken)
    _ask(client, conversation_id)
    assert calls  # die zentrale Pruefung lief vor der bewussten Aktion erneut ...
    assert recorder.requests == []  # ... und ihr Scheitern sendet nichts (fail-closed)


def test_upload_with_invalid_marking_stores_nothing(client, db_session, writer) -> None:
    response = client.post("/dashboard/chat/send-stream", files={
        "documents": ("beschluss.pdf", _pdf_bytes(LINES), "application/pdf")},
        data={"csrf_token": _csrf(client), "conversation_id": "", "content": TYPED,
              "protected_terms": json.dumps(["Nichtimtext"])})
    assert _is_rejection(_parse_sse_events(response.text))
    assert db_session.query(Document).count() == 0 and writer.received_payloads == []


def test_upload_message_cannot_be_reviewed_resumed_or_reprocessed(client, db_session, writer) -> None:
    events = _upload(client)
    conversation_id = events[0]["conversation_id"]
    upload_message = db_session.query(ChatMessage).filter_by(role="upload").one()
    # Wiederherstellung nach dem Neuladen: keine Rueckfrage, keine Verarbeitung
    assert client.get(f"/dashboard/chat/{conversation_id}/pending-review").json() == {"state": "none"}
    # Manipulation: Datenschutz-Rueckfrage fuer die Upload-Nachricht "beantworten"
    review = client.post(f"/dashboard/chat/{conversation_id}/privacy-review", data={
        "csrf_token": _csrf(client), "message_id": upload_message.id,
        "decisions": json.dumps([{"term": "Zweigelt", "decision": "not_personal"}])})
    assert "nicht mehr offen" in review.text
    # Wiederholung/Doppelklick: ein erneuter Upload ist wieder nur ein Upload
    _upload(client, conversation_id=conversation_id)
    assert writer.received_payloads == []
    assert db_session.query(PrivacyDecision).count() == 0


def test_upload_scope_never_crosses_into_another_conversation(client, db_session, writer) -> None:
    _upload(client)
    other = [e for e in _ask(client, "", "Fasse das Dokument zusammen.")]
    assert other and all(e["kind"] != "uploaded" for e in other)
    sent = "".join(json.dumps(p.model_dump(), ensure_ascii=False) for p in writer.received_payloads)
    assert "4.250,00" not in sent and "Zweigelt" not in sent  # fremde Akte: Dokument nicht im Kontext


# --- 4. Schriftsatz-Generator -------------------------------------------------------------------------------------------


def _generate(client: TestClient, *, files=None, **fields):
    data = {"csrf_token": _csrf(client, "/dashboard/tools/schriftsatz"), "matter_id": "", "new_matter_title": "",
            "new_client_name": "", "stil": "", "vorlage": "", "attorney_anmerkungen": "", **fields}
    return client.post("/dashboard/tools/schriftsatz/generate", data=data, follow_redirects=False,
                       files=files or {"unbenutzt": ("", b"", "application/octet-stream")})


def test_schriftsatz_upload_only_stores_and_the_deliberate_generate_is_masked(client, db_session, recorder) -> None:
    response = _generate(
        client, new_matter_title="Generatorakte", stil="sachlich", attorney_anmerkungen="Bitte knapp.",
        files={"documents": ("beschluss.pdf", _pdf_bytes(LINES), "application/pdf")},
    )
    assert response.status_code == 200 and recorder.requests == []  # HTTP-Nachweis: Upload ohne Anfrage
    assert db_session.query(Draft).count() == 0 and db_session.query(ApiCallLog).count() == 0
    document = db_session.query(Document).one()
    matter = db_session.query(Matter).filter_by(title="Generatorakte").one()
    assert document.matter_id == matter.id and "Zweigelt" in document.extracted_text
    page = response.text
    assert 'id="upload-notice"' in page and "noch keine KI-Verarbeitung" in page
    assert f'/dashboard/matters/{matter.id}/document/{document.id}#name-protection' in page
    assert f'<option value="{matter.id}" selected>' in page  # Akte fuer die bewusste Generierung vorausgewaehlt
    assert 'value="sachlich"' in page and "Bitte knapp.</textarea>" in page  # Eingaben bleiben erhalten

    from tests.test_web_document_name_protection import _protect

    assert "name_protection=saved" in _protect(client, document, ["Zweigelt"]).headers["location"]
    assert recorder.requests == []

    generated = _generate(client, matter_id=matter.id, stil="sachlich", attorney_anmerkungen="Bitte knapp.")
    assert generated.status_code == 303 and generated.headers["location"].endswith("/edit")
    assert len(recorder.requests) == 1
    wire = _wire(recorder.requests[0])
    assert "Zweigelt" not in wire and "4.250,00 EUR" in wire


def test_schriftsatz_upload_with_invalid_marking_stores_nothing(client, db_session, writer) -> None:
    response = _generate(client, new_matter_title="Generatorakte", protected_terms=json.dumps(["Nichtimtext"]),
                         files={"documents": ("beschluss.pdf", _pdf_bytes(LINES), "application/pdf")})
    assert response.status_code == 303 and "error=" in response.headers["location"]
    assert db_session.query(Document).count() == 0 and writer.received_payloads == []


def test_schriftsatz_upload_never_calls_create_draft(client, db_session, monkeypatch) -> None:
    from app.drafting.service import DraftingService

    def _forbidden(*args, **kwargs):
        raise AssertionError("create_draft darf beim Upload nicht aufgerufen werden")

    monkeypatch.setattr(DraftingService, "create_draft", _forbidden)
    response = _generate(client, new_matter_title="Generatorakte",
                         files={"documents": ("beschluss.pdf", _pdf_bytes(LINES), "application/pdf")})
    assert response.status_code == 200 and db_session.query(Document).count() == 1


# --- 5. Nachtraege aus dem unabhaengigen Review -------------------------------------------------------------------------


def test_pending_review_after_upload_uses_the_same_scope_as_sending(client, db_session, writer) -> None:
    """Review §57 (mittel): die wiederhergestellte Rueckfrage prueft denselben Kontext wie das spaetere Senden."""
    from app.chat.service import ChatService

    conversation_id = _upload(client, data=_pdf_bytes(["Bescheid vom 03.11.2026.", "Unterschrift: Lange"]))[0][
        "conversation_id"]
    conversation = db_session.get(ChatConversation, conversation_id)
    other = Document(matter_id=conversation.matter_id, file_path="x", original_filename="alt.pdf",
                     extracted_text="Anderer Vorgang, Zeuge Richter.", ocr_status="not_needed")
    db_session.add(other)
    db_session.commit()
    seen = []
    original = ChatService._message_scope

    def _spy(self, db, conv, message_id):
        scope = original(self, db, conv, message_id)
        seen.append(scope)
        return scope

    import app.chat.service as service_module

    service_module.ChatService._message_scope = _spy
    try:
        user = ChatService("unbenutzt").record_user_message(db_session, conversation=conversation, content=QUESTION)
        client.get(f"/dashboard/chat/{conversation_id}/pending-review")
    finally:
        service_module.ChatService._message_scope = original
    uploaded = db_session.query(Document).filter(Document.id != other.id).one()
    assert [uploaded.id] in seen and other.id not in sum(seen, [])
    assert user.role == "user" and writer.received_payloads == []


def test_several_uploads_in_a_row_are_all_in_scope(client, db_session, writer) -> None:
    conversation_id = _upload(client, name="erstes.pdf")[0]["conversation_id"]
    _upload(client, conversation_id=conversation_id, name="zweites.pdf",
            data=_pdf_bytes(["Vergleich vom 05.12.2026 über 1.100,00 EUR."]))
    assert writer.received_payloads == []
    _ask(client, conversation_id, "Fasse die beiden Dokumente zusammen.")
    sent = json.dumps(writer.received_payloads[0].model_dump(), ensure_ascii=False)
    assert "4.250,00 EUR" in sent and "1.100,00 EUR" in sent


def test_scope_survives_an_unanswered_question_and_ends_with_any_answer(client, db_session, writer) -> None:
    from app.chat.service import ChatService

    conversation_id = _upload(client)[0]["conversation_id"]
    conversation = db_session.get(ChatConversation, conversation_id)
    service = ChatService("unbenutzt")
    document_id = db_session.query(Document).one().id
    first = service.record_user_message(db_session, conversation=conversation, content=QUESTION)  # z. B. offene Rueckfrage
    second = service.record_user_message(db_session, conversation=conversation, content=QUESTION)
    assert service._message_scope(db_session, conversation, first.id) == [document_id]
    assert service._message_scope(db_session, conversation, second.id) == [document_id]
    service._create_message(db_session, conversation=conversation, role="assistant", content="gesperrt", blocked=True)
    db_session.commit()
    third = service.record_user_message(db_session, conversation=conversation, content="Was ist eine Abmahnung?")
    # auch ein lokaler Sperrhinweis beendet den Upload-Bezug (bestehendes Verhalten: allgemeine Fragen bleiben moeglich)
    assert service._message_scope(db_session, conversation, third.id) == []
