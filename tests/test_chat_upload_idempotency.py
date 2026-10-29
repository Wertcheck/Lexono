"""§59: Idempotenz beim Chat-Upload - Doppelklick, parallele Requests, Wiederholung nach verlorener Antwort oder
Netzwerkfehler, Teilfehler, manipulierte/wiederverwendete Kennungen, bewusst neue Uploads. Upload bleibt reiner Upload
ohne KI (§57); eine spaetere, bewusst gesendete Nachricht ist ein eigener Vorgang und erzeugt genau EINEN Cloud-Aufruf.

HTTP-Nachweis ueber den lokalen Aufzeichnungsserver (echter Provider, toter Proxy, gefaelschter Schluessel).
Nur synthetische Daten, isolierte Datenbanken (SQLite)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.chat.service as chat_service_module
import app.web.chat_router as chat_router_module
from app.chat import upload_operations
from app.db.session import get_db
from app.drafting import schriftsatz_operations
from app.main import app
from app.models import ApiCallLog, ChatConversation, ChatMessage, Document, User
from app.models.base import Base
from app.models.chat_upload_operation import ChatUploadOperation
from tests.auth_test_utils import extract_csrf, extract_upload_token, login_as_admin
from tests.test_upload_without_ai import LINES, QUESTION, TYPED, _ask, _pdf_bytes, _protect_in_chat_view, _wire
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)
from tests.test_web_chat import _parse_sse_events

PDF = _pdf_bytes(LINES)  # einmal erzeugt: eine Wiederholung sendet byte-identische Dateien


def _page(client: TestClient, conversation_id: str = "") -> str:
    return client.get(f"/dashboard/chat/{conversation_id}" if conversation_id else "/dashboard/chat").text


def _stream(client: TestClient, token: str, *, conversation_id: str = "", content: str = TYPED, files=None):
    page = _page(client)
    response = client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": extract_csrf(page), "upload_token": token, "conversation_id": conversation_id,
              "content": content},
        files=files or {"documents": ("beschluss.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 200
    return _parse_sse_events(response.text)


def _classic(client: TestClient, token: str, *, conversation_id: str = "", content: str = TYPED):
    return client.post(
        "/dashboard/chat/send", follow_redirects=False,
        data={"csrf_token": extract_csrf(_page(client)), "upload_token": token, "conversation_id": conversation_id,
              "content": content},
        files={"documents": ("beschluss.pdf", PDF, "application/pdf")},
    )


def _counts(db: Session) -> tuple[int, int, int, int]:
    db.expire_all()
    return (db.query(ChatConversation).count(), db.query(Document).count(),
            db.query(ChatMessage).filter_by(role="upload").count(),
            db.query(ChatMessage).filter(ChatMessage.role != "upload").count())


def _uploaded(events: list[dict]) -> dict:
    [event] = [e for e in events if e["kind"] == "uploaded"]
    return event


def _rejection(events: list[dict]) -> dict:
    [event] = [e for e in events if e["kind"] == "done"]
    assert event["message_id"] is None and event["blocked"] is True and not [e for e in events if e["kind"] == "uploaded"]
    return event


# --- 1. Doppelklick / Wiederholung nach verlorener Antwort ------------------------------------------------------------


def test_double_click_creates_one_conversation_one_document_one_upload_message(client, db_session, recorder) -> None:
    token = extract_upload_token(_page(client))
    first = _uploaded(_stream(client, token))
    second = _uploaded(_stream(client, token))  # Doppelklick bzw. Wiederholung nach verlorener Antwort
    assert second["repeated"] is True and first["repeated"] is False
    assert second["target"] == first["target"] and second["conversation_id"] == first["conversation_id"]
    assert _counts(db_session) == (1, 1, 1, 0)
    assert recorder.requests == [] and db_session.query(ApiCallLog).count() == 0  # Upload ohne KI (§57)


def test_repeat_into_an_existing_conversation_creates_nothing_twice(client, db_session, writer) -> None:
    conversation_id = _uploaded(_stream(client, extract_upload_token(_page(client))))["conversation_id"]
    token = extract_upload_token(_page(client, conversation_id))
    for _ in range(3):
        _stream(client, token, conversation_id=conversation_id)
    assert _counts(db_session) == (1, 2, 2, 0) and writer.received_payloads == []


def test_classic_send_repeated_after_lost_responses_redirects_to_the_same_result(client, db_session, writer) -> None:
    token = extract_upload_token(_page(client))
    locations = {_classic(client, token).headers["location"] for _ in range(3)}
    assert len(locations) == 1 and "uploaded=1" in locations.pop()
    assert _counts(db_session) == (1, 1, 1, 0) and writer.received_payloads == []


def test_every_upload_answer_carries_a_new_token_for_a_deliberate_new_upload(client, db_session, writer) -> None:
    token = extract_upload_token(_page(client))
    first = _uploaded(_stream(client, token))
    assert first["upload_token"] != token
    # bewusst DIESELBE Datei erneut hochladen - mit der neuen ID ein neuer Vorgang
    second = _uploaded(_stream(client, first["upload_token"], conversation_id=first["conversation_id"]))
    assert second["repeated"] is False and _counts(db_session) == (1, 2, 2, 0)


# --- 2. Veraenderte Anfrage / manipulierte Kennungen ------------------------------------------------------------------


@pytest.mark.parametrize("change", ["content", "file", "conversation"])
def test_changed_request_with_a_used_token_is_neither_executed_nor_treated_as_identical(client, db_session, writer,
                                                                                       change) -> None:
    other = _uploaded(_stream(client, extract_upload_token(_page(client))))["conversation_id"]  # zweite, eigene
    token = extract_upload_token(_page(client))
    _uploaded(_stream(client, token))
    kwargs = {
        "content": {"content": "Ganz andere Frage."},
        "file": {"files": {"documents": ("anderes.pdf", _pdf_bytes(["Anderer Inhalt."]), "application/pdf")}},
        "conversation": {"conversation_id": other},  # dieselbe ID in eine ANDERE Unterhaltung
    }[change]
    events = _stream(client, token, **kwargs)
    rejection = _rejection(events)
    assert "unterscheidet sich vom ersten Absenden" in rejection["content"] and "nichts" in rejection["content"]
    assert rejection["upload_token"] != token  # neue ID fuer einen bewusst neuen Upload
    assert _counts(db_session) == (2, 2, 2, 0) and writer.received_payloads == []


@pytest.mark.parametrize("token", ["", "frei-gewaehlt", "a" * 32, "x" * 600])
def test_missing_or_forged_tokens_create_nothing(client, db_session, writer, token) -> None:
    rejection = _rejection(_stream(client, token))
    assert "abgelaufen oder ungültig" in rejection["content"]
    assert _counts(db_session) == (0, 0, 0, 0) and db_session.query(ChatUploadOperation).count() == 0


def test_foreign_expired_and_generator_tokens_are_rejected(client, db_session, writer, monkeypatch) -> None:
    for token in (upload_operations.issue_token("anderer-nutzer"),
                  schriftsatz_operations.issue_token(db_session.query(User).one().id)):  # anderer Zweck (Salt)
        _rejection(_stream(client, token))
    old = extract_upload_token(_page(client))
    monkeypatch.setattr(schriftsatz_operations, "TOKEN_MAX_AGE_SECONDS", -1)
    _rejection(_stream(client, old))
    assert _counts(db_session) == (0, 0, 0, 0)


def test_chat_token_is_not_accepted_by_the_generator(client, db_session, writer) -> None:
    from app.models import Matter

    chat_token = extract_upload_token(_page(client))
    page = client.get("/dashboard/tools/schriftsatz").text
    response = client.post("/dashboard/tools/schriftsatz/generate", follow_redirects=False, data={
        "csrf_token": extract_csrf(page), "operation_token": chat_token, "matter_id": "", "new_matter_title": "X"},
        files={"documents": ("beschluss.pdf", PDF, "application/pdf")})
    assert "error=" in response.headers["location"] and db_session.query(Matter).count() == 0


# --- 3. Teilfehler, Netzwerkfehler, Absturz ---------------------------------------------------------------------------


def test_partial_storage_failure_is_not_repeated_and_names_what_was_stored(client, db_session, writer,
                                                                           monkeypatch) -> None:
    small = _pdf_bytes(["Kurz."])
    monkeypatch.setattr(chat_service_module, "_MAX_UPLOAD_SIZE_BYTES", len(small) + 10)
    files = [("documents", ("klein.pdf", small, "application/pdf")),
             ("documents", ("gross.pdf", PDF + b"x" * 5_000, "application/pdf"))]
    token = extract_upload_token(_page(client))
    failed = _rejection(_stream(client, token, files=files))
    assert "maximale Größe" in failed["content"]
    retry = _rejection(_stream(client, token, files=files))
    assert "bereits fehlgeschlagen" in retry["content"] and "klein.pdf" in retry["content"]
    assert "gross.pdf" not in retry["content"]
    assert _counts(db_session) == (1, 1, 0, 0)  # Unterhaltung + erstes Dokument, keine Upload-Nachricht, nichts doppelt
    conversation_id = db_session.query(ChatConversation).one().id
    # bewusst neuer, korrigierter Upload in DIESELBE Unterhaltung (keine weitere Unterhaltung)
    _uploaded(_stream(client, retry["upload_token"], conversation_id=conversation_id,
                      files={"documents": ("klein.pdf", small, "application/pdf")}))
    assert _counts(db_session) == (1, 2, 1, 0) and writer.received_payloads == []


def test_crash_after_storing_is_not_repeated(client, db_session, writer, monkeypatch) -> None:
    def _crash(self, *args, **kwargs):
        raise RuntimeError("Absturz zwischen Speicherung und Upload-Nachricht (synthetisch)")

    token = extract_upload_token(_page(client))
    monkeypatch.setattr(chat_service_module.ChatService, "record_upload", _crash)
    page = _page(client)
    crashed = client.post("/dashboard/chat/send-stream", files={"documents": ("beschluss.pdf", PDF, "application/pdf")},
                          data={"csrf_token": extract_csrf(page), "upload_token": token, "conversation_id": "",
                                "content": TYPED})
    assert crashed.status_code == 500  # Netzwerkfehler/unklare Antwort aus Sicht des Browsers
    monkeypatch.undo()
    retry = _rejection(_stream(client, token))
    # ohne KI-Anfrage ist ein Absturz eindeutig ein Fehlschlag (Review §59) - nicht "laeuft noch"
    assert "bereits fehlgeschlagen" in retry["content"] and "beschluss.pdf" in retry["content"]
    assert db_session.query(ChatUploadOperation).one().status == "failed"
    assert _counts(db_session) == (1, 1, 0, 0) and writer.received_payloads == []


# --- 4. Upload und bewusste Nachricht bleiben getrennte Vorgaenge ----------------------------------------------------


def test_upload_has_no_cloud_call_and_the_deliberate_question_exactly_one(client, db_session, recorder) -> None:
    token = extract_upload_token(_page(client))
    conversation_id = _uploaded(_stream(client, token))["conversation_id"]
    _uploaded(_stream(client, token))  # Wiederholung des Uploads
    assert recorder.requests == []
    # Das Testdokument nennt "Der Richter Zweigelt" - seit 24.10.2026 (Grenzfall G2) ein Rueckfrage-Signal. Der Name wird
    # deshalb wie im echten Ablauf in der Dokumentansicht geschuetzt; Gegenstand hier ist der Upload-Umfang.
    _protect_in_chat_view(client, conversation_id, db_session.query(Document).order_by(Document.created_at).first(), ["Zweigelt"])
    _ask(client, conversation_id, QUESTION)  # bewusste Aktion: ohne Dateien, ohne Upload-Vorgangs-ID
    assert len(recorder.requests) == 1
    wire = _wire(recorder.requests[0])
    assert "4.250,00 EUR" in wire  # das (einmal) hochgeladene Dokument ist im Kontext
    # eine spaete Wiederholung des Uploads beruehrt die beantwortete Frage nicht und sendet nichts
    assert _uploaded(_stream(client, token))["repeated"] is True
    assert len(recorder.requests) == 1 and _counts(db_session) == (1, 1, 1, 2)


def test_a_message_without_files_does_not_need_or_consume_an_upload_token(client, db_session, writer) -> None:
    token = extract_upload_token(_page(client))
    page = _page(client)
    events = _parse_sse_events(client.post("/dashboard/chat/send-stream", data={
        "csrf_token": extract_csrf(page), "upload_token": "manipuliert", "conversation_id": "", "content": QUESTION,
    }).text)
    assert [e for e in events if e["kind"] == "done"][0]["message_id"] is not None and len(writer.received_payloads) == 1
    assert db_session.query(ChatUploadOperation).count() == 0
    assert _uploaded(_stream(client, token))["repeated"] is False  # die Seiten-ID ist weiterhin unverbraucht


# --- 5. Parallele Requests (eigene Datei-DB, eine Sitzung je Request) ------------------------------------------------


@pytest.fixture()
def file_db(tmp_path) -> Iterator[sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'parallel.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_parallel_identical_upload_requests_create_everything_once(file_db, monkeypatch, tmp_path) -> None:
    from app.config.settings import Settings
    from app.documents.service import DocumentProcessingService

    def _override_get_db() -> Iterator[Session]:
        db = file_db()
        try:
            yield db
        finally:
            db.close()

    original = DocumentProcessingService.process_document

    def _slow(self, *args, **kwargs):
        time.sleep(0.5)  # beide Requests ueberlappen sicher
        return original(self, *args, **kwargs)

    settings = Settings(_env_file=None, chat_upload_storage_dir=str(tmp_path / "chat"))
    monkeypatch.setattr(chat_router_module, "get_settings", lambda: settings)
    polls = []
    original_load = upload_operations.load

    def _counting_load(*args, **kwargs):
        polls.append(1)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(upload_operations, "load", _counting_load)
    monkeypatch.setattr(DocumentProcessingService, "process_document", _slow)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        with file_db() as db:
            client = TestClient(app, raise_server_exceptions=False)
            login_as_admin(db, client)
        page = client.get("/dashboard/chat").text
        data = {"csrf_token": extract_csrf(page), "upload_token": extract_upload_token(page), "conversation_id": "",
                "content": TYPED}
        responses, barrier = [], threading.Barrier(2)

        def _post():
            barrier.wait()
            responses.append(client.post("/dashboard/chat/send-stream", data=data,
                                         files={"documents": ("beschluss.pdf", PDF, "application/pdf")}))

        threads = [threading.Thread(target=_post) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update({k: v for k, v in previous.items() if k is not get_db})
    finals = [_parse_sse_events(r.text)[-1] for r in responses]
    # beide erhalten dasselbe Ergebnis: einer laedt hoch, der andere wartet auf den LAUFENDEN Vorgang (Review §59)
    assert [f["kind"] for f in finals] == ["uploaded", "uploaded"]
    assert sorted(f["repeated"] for f in finals) == [False, True] and finals[0]["target"] == finals[1]["target"]
    assert polls, "der zweite Request hat nicht auf den laufenden Vorgang gewartet (keine Ueberlappung)"
    with file_db() as db:
        assert db.query(ChatConversation).count() == 1 and db.query(Document).count() == 1
        assert db.query(ChatMessage).filter_by(role="upload").count() == 1 and db.query(ApiCallLog).count() == 0


def test_operation_rows_hold_no_content(client, db_session, writer) -> None:
    _stream(client, extract_upload_token(_page(client)))
    row = db_session.query(ChatUploadOperation).one()
    stored = json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns}, ensure_ascii=False)
    for value in ("Zweigelt", "beschluss.pdf", TYPED, "4.250,00"):
        assert value not in stored


# --- 6. Nachtraege aus dem unabhaengigen Review ------------------------------------------------------------------------


def test_classic_send_failure_keeps_file_names_out_of_the_url(client, db_session, writer, monkeypatch) -> None:
    small = _pdf_bytes(["Kurz."])
    monkeypatch.setattr(chat_service_module, "_MAX_UPLOAD_SIZE_BYTES", len(small) + 10)
    files = [("documents", ("Mandantin Haselhorst.pdf", small, "application/pdf")),
             ("documents", ("gross.pdf", PDF + b"x" * 5_000, "application/pdf"))]
    token = extract_upload_token(_page(client))

    def _post():
        return client.post("/dashboard/chat/send", follow_redirects=False, files=files, data={
            "csrf_token": extract_csrf(_page(client)), "upload_token": token, "conversation_id": "", "content": TYPED})

    _post()
    repeated = _post().headers["location"]
    assert "fehlgeschlagen" in repeated and "Haselhorst" not in repeated and "1%20Dokument" in repeated


def test_repeat_after_the_start_event_set_the_conversation_is_still_identical(client, db_session, writer) -> None:
    """Abbruch zwischen "start" (setzt die Unterhaltung im Eingabefeld) und "uploaded": die Wiederholung traegt die
    Unterhaltung des Vorgangs und gilt trotzdem als DERSELBE Upload (keine Doppelanlage nach neuem Token)."""
    token = extract_upload_token(_page(client))
    first = _uploaded(_stream(client, token))
    again = _uploaded(_stream(client, token, conversation_id=first["conversation_id"]))
    assert again["repeated"] is True and _counts(db_session) == (1, 1, 1, 0)


def test_message_with_hash_and_ampersand_survives_the_error_redirect() -> None:
    from urllib.parse import parse_qs, urlparse

    location = chat_router_module._redirect_with_error("abc", "Fehler #1 & mehr").headers["location"]
    assert parse_qs(urlparse(location).query)["error"] == ["Fehler #1 & mehr"]
