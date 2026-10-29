"""§60: offene Fehlerfaelle der Chat-Upload-Idempotenz (§59) - harter Abbruch an jedem Speicherpunkt und
Wiederaufnahme, Lebenszeichen des Besitzers (Zeit allein gibt nie frei), kontrollierter Fehlschlag, keine Dateinamen in
Fehler-URLs, Zurueck-Button mit verbrauchter Kennung.

Ein harter Abbruch (Prozess beendet) wird mit einer BaseException simuliert: sie wird - wie ein Prozessende - von
keinem `except Exception` aufgefangen, der Vorgang bleibt "processing", der eigene Prozess fuehrt ihn danach nicht
mehr aus (Besitzer nachweislich beendet). Nur synthetische Daten, isolierte SQLite-Datenbanken, lokaler
Aufzeichnungsserver: Upload, Abbruch und Wiederaufnahme loesen null KI-Anfragen aus."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.chat.service as chat_service_module
import app.web.chat_router as chat_router_module
from app.chat import process_owner, upload_operations
from app.db.session import get_db
from app.documents import readiness
from app.main import app
from app.models import ApiCallLog, ChatConversation, ChatMessage, Document, Matter
from app.models.base import Base
from app.models.chat_upload_operation import ChatUploadOperation
from tests.auth_test_utils import extract_csrf, extract_upload_token, login_as_admin
from tests.test_chat_upload_idempotency import PDF, _counts, _page, _rejection, _stream, _uploaded
from tests.test_upload_without_ai import QUESTION, TYPED, _ask, _protect_in_chat_view, _wire
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)


class _HardAbort(BaseException):
    """Simulierter Prozessabbruch (kein Exception-Handler faengt ihn)."""


def _abort_stream(client: TestClient, token: str, *, conversation_id: str = "", files=None) -> None:
    """Der Request bricht hart ab: je nach Stelle sieht der Browser eine 500-Antwort oder gar keine."""
    page = _page(client)
    try:
        response = client.post(
            "/dashboard/chat/send-stream",
            data={"csrf_token": extract_csrf(page), "upload_token": token, "conversation_id": conversation_id,
                  "content": TYPED},
            files=files or {"documents": ("beschluss.pdf", PDF, "application/pdf")},
        )
    except BaseException:  # noqa: BLE001 - der simulierte Abbruch selbst
        return
    assert response.status_code == 500, response.text[:300]


def _operation(db: Session) -> ChatUploadOperation:
    db.expire_all()
    return db.query(ChatUploadOperation).one()


def _quick_matters(db: Session) -> int:
    return db.query(Matter).count()


def _files_on_disk(tmp_path: Path) -> int:
    folder = tmp_path / "chat"
    return len(list(folder.iterdir())) if folder.exists() else 0


def _after(original, *, times: int = 1):
    """Fuehrt `original` aus und bricht DANACH hart ab (Commit erfolgt, Antwort/Weiterlauf nicht)."""
    calls = []

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) <= times:
            raise _HardAbort("Prozess beendet (synthetisch)")
        return result

    return wrapper


def _before(*, times: int = 1):
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(1)
        if len(calls) <= times:
            raise _HardAbort("Prozess beendet (synthetisch)")

    return wrapper


# --- 1. Harter Abbruch an jedem Speicherpunkt -> Wiederaufnahme ohne Doppelanlage -----------------------------------

CRASH_POINTS = {
    # nach dem Anlegen der Schnellakte, vor der Unterhaltung
    "nach_akte": lambda mp: mp.setattr(chat_router_module, "create_quick_matter",
                                       _after(chat_router_module.create_quick_matter)),
    # nach dem Commit der Unterhaltung, vor dem ersten Dokument
    "nach_unterhaltung": lambda mp: mp.setattr(chat_service_module.ChatService, "create_conversation",
                                               _after(chat_service_module.ChatService.create_conversation)),
    # Datei geschrieben, Dokument noch nicht committet
    "vor_dokument_commit": lambda mp: mp.setattr(chat_service_module, "compute_sha256", _before()),
    # Dokument committet, Verarbeitung (Text/OCR) abgebrochen
    "waehrend_verarbeitung": lambda mp: mp.setattr(
        "app.documents.service.DocumentProcessingService.process_document", _before()),
    # Dokument fertig, aber am Vorgang noch nicht vermerkt (unklarer Commit-Ausgang)
    "vor_vermerk": lambda mp: mp.setattr(upload_operations, "add_document", _before()),
    # Upload-Nachricht committet, Status noch nicht "done"
    "nach_nachricht": lambda mp: mp.setattr(chat_service_module.ChatService, "record_upload",
                                            _after(chat_service_module.ChatService.record_upload)),
}


@pytest.mark.parametrize("point", sorted(CRASH_POINTS))
def test_hard_abort_at_each_storage_point_resumes_without_duplicates(client, db_session, recorder, monkeypatch,
                                                                     tmp_path, point) -> None:
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS[point](patch)
        _abort_stream(client, token)
    operation = _operation(db_session)
    assert operation.status == "processing" and upload_operations.owner_state(operation) == upload_operations.DEAD

    resumed = _uploaded(_stream(client, token))  # Wiederholung derselben Anfrage nach dem Abbruch
    assert resumed["resumed"] is True
    assert _counts(db_session) == (1, 1, 1, 0)  # 1 Unterhaltung, 1 Dokument, 1 Upload-Nachricht, keine Frage
    assert _quick_matters(db_session) == 1 and _files_on_disk(tmp_path) == 1  # keine verwaiste Kopie
    document = db_session.query(Document).one()
    assert readiness.assess_document(db_session, document).state == readiness.READY  # fertig verarbeitet
    message = db_session.query(ChatMessage).filter_by(role="upload").one()
    assert [link.document_id for link in message.attached_documents] == [document.id]
    assert _operation(db_session).status == "done"

    again = _uploaded(_stream(client, token))  # eine weitere Wiederholung liefert nur das Ergebnis
    assert again["repeated"] is True and again["target"] == resumed["target"]
    assert _counts(db_session) == (1, 1, 1, 0)
    assert recorder.requests == [] and db_session.query(ApiCallLog).count() == 0  # null KI-Anfragen


def test_resume_into_an_existing_conversation_with_two_files(client, db_session, writer, monkeypatch) -> None:
    conversation_id = _uploaded(_stream(client, extract_upload_token(_page(client))))["conversation_id"]
    token = extract_upload_token(_page(client, conversation_id))
    files = [("documents", ("eins.pdf", PDF, "application/pdf")),
             ("documents", ("zwei.pdf", PDF + b"%zweite", "application/pdf"))]
    with monkeypatch.context() as patch:
        patch.setattr(upload_operations, "add_document", _after(upload_operations.add_document))
        _abort_stream(client, token, conversation_id=conversation_id, files=files)
    _uploaded(_stream(client, token, conversation_id=conversation_id, files=files))
    assert _counts(db_session) == (1, 3, 2, 0) and writer.received_payloads == []


def test_deliberate_question_after_a_resumed_upload_is_exactly_one_request(client, db_session, recorder,
                                                                           monkeypatch) -> None:
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS["waehrend_verarbeitung"](patch)
        _abort_stream(client, token)
    conversation_id = _uploaded(_stream(client, token))["conversation_id"]
    assert recorder.requests == []
    # Das Testdokument nennt "Der Richter Zweigelt" - seit 24.10.2026 (Grenzfall G2) ein Rueckfrage-Signal. Der Name wird
    # deshalb wie im echten Ablauf in der Dokumentansicht geschuetzt; Gegenstand hier ist die Wiederaufnahme des Uploads.
    _protect_in_chat_view(client, conversation_id, db_session.query(Document).one(), ["Zweigelt"])
    _ask(client, conversation_id, QUESTION)
    assert len(recorder.requests) == 1 and "4.250,00 EUR" in _wire(recorder.requests[0])


# --- 2. Lebenszeichen: Zeit allein gibt nie frei ---------------------------------------------------------------------


def _foreign_owner(pid_process) -> str:
    state, ticks = process_owner._probe(pid_process.pid)
    assert state == process_owner.ALIVE
    return f"{process_owner._HOST}|{pid_process.pid}|{ticks}|versuch1"


def test_a_living_owner_is_never_taken_over_even_hours_later(client, db_session, writer, monkeypatch) -> None:
    monkeypatch.setattr(chat_router_module, "_REPEAT_WAIT_SECONDS", 0.3)
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_unterhaltung"](patch)
        _abort_stream(client, token)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        operation = _operation(db_session)
        operation.owner = _foreign_owner(child)  # ein anderer, LEBENDER Prozess haelt den Vorgang
        operation.created_at = operation.updated_at = datetime.now(timezone.utc) - timedelta(hours=5)
        db_session.commit()
        waiting = _rejection(_stream(client, token))
        assert "gerade noch verarbeitet" in waiting["content"]
        assert _counts(db_session) == (1, 0, 0, 0) and _operation(db_session).status == "processing"
    finally:
        child.kill()
        child.wait()
    # Besitzer nachweislich beendet -> genau jetzt (nicht nach Zeit) wiederaufnehmbar
    assert _uploaded(_stream(client, token))["resumed"] is True
    assert _counts(db_session) == (1, 1, 1, 0) and writer.received_payloads == []


@pytest.mark.parametrize("owner", ["anderer-rechner|4242|1|v", None, "kaputt"])
def test_an_unverifiable_owner_is_left_alone_and_a_new_upload_is_possible(client, db_session, writer, monkeypatch,
                                                                         owner) -> None:
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_unterhaltung"](patch)
        _abort_stream(client, token)
    operation = _operation(db_session)
    operation.owner = owner
    operation.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()
    rejection = _rejection(_stream(client, token))
    assert "nicht sicher feststellen" in rejection["content"] and "neuer Upload ist möglich" in rejection["content"]
    assert _operation(db_session).status == "processing" and _counts(db_session) == (1, 0, 0, 0)
    conversation_id = db_session.query(ChatConversation).one().id
    # sicherer Neustart: bewusst neuer Upload mit der neuen Kennung
    _uploaded(_stream(client, rejection["upload_token"], conversation_id=conversation_id))
    assert _counts(db_session) == (1, 1, 1, 0)


def test_process_liveness_detects_ended_and_reused_processes() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    owner = _foreign_owner(child)
    assert process_owner.state_of(owner) == process_owner.ALIVE
    child.wait()
    assert process_owner.state_of(owner) == process_owner.DEAD
    host, pid, _ = process_owner.PROCESS.split("|")
    assert process_owner.state_of(f"{host}|{pid}|1|v") == process_owner.DEAD  # gleiche PID, anderer Start = beendet
    assert process_owner.state_of("anderer-rechner|1|1|v") == process_owner.UNKNOWN
    assert process_owner.state_of(f"{host}|{pid}|1") == process_owner.UNKNOWN  # unvollstaendig: nicht pruefbar


# --- 3. Parallele Wiederaufnahme: genau ein Request uebernimmt -------------------------------------------------------


@pytest.fixture()
def file_db(tmp_path) -> Iterator[sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_take_over_has_exactly_one_winner(file_db) -> None:
    from app.models import User

    with file_db() as db:
        user = User(email="qa@kanzlei.test", password_hash="x", display_name="QA")
        db.add(user)
        db.flush()
        # Besitzer = der EIGENE Prozess, Versuch beendet (nicht in der Liste laufender Vorgaenge): ohne Nonce je
        # Versuch waere "alt == neu" und jeder Compare-and-Set gelaenge (unabhaengiges Review §60, mittel)
        db.add(ChatUploadOperation(id="1" * 32, user_id=user.id, status="processing", fingerprint="f" * 64,
                                   owner=process_owner.new_attempt()))
        db.commit()
    results, barrier = [], threading.Barrier(8)

    def _worker():
        with file_db() as db:
            operation = db.get(ChatUploadOperation, "1" * 32)
            barrier.wait()
            results.append(upload_operations.take_over(db, operation))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1


def test_parallel_resume_requests_complete_the_upload_once(file_db, monkeypatch, tmp_path) -> None:
    from app.config.settings import Settings
    from app.documents.service import DocumentProcessingService

    def _override_get_db() -> Iterator[Session]:
        db = file_db()
        try:
            yield db
        finally:
            db.close()

    settings = Settings(_env_file=None, chat_upload_storage_dir=str(tmp_path / "chat"))
    monkeypatch.setattr(chat_router_module, "get_settings", lambda: settings)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        with file_db() as db:
            client = TestClient(app, raise_server_exceptions=False)
            login_as_admin(db, client)
        page = client.get("/dashboard/chat").text
        data = {"csrf_token": extract_csrf(page), "upload_token": extract_upload_token(page), "conversation_id": "",
                "content": TYPED}

        def _post():
            return client.post("/dashboard/chat/send-stream", data=data,
                               files={"documents": ("beschluss.pdf", PDF, "application/pdf")})

        with monkeypatch.context() as patch:
            patch.setattr(DocumentProcessingService, "process_document", _before())
            try:
                assert _post().status_code == 500  # harter Abbruch waehrend der Verarbeitung
            except BaseException as exc:  # noqa: BLE001
                assert isinstance(exc, _HardAbort) or "HardAbort" in repr(exc)
        original = DocumentProcessingService.process_document

        def _slow(self, *args, **kwargs):
            time.sleep(0.5)  # die Wiederaufnahme laeuft sicher, waehrend der zweite Request kommt
            return original(self, *args, **kwargs)

        monkeypatch.setattr(DocumentProcessingService, "process_document", _slow)
        responses, barrier = [], threading.Barrier(2)

        def _parallel():
            barrier.wait()
            responses.append(_post())

        threads = [threading.Thread(target=_parallel) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update({k: v for k, v in previous.items() if k is not get_db})
    from tests.test_web_chat import _parse_sse_events

    finals = [_parse_sse_events(r.text)[-1] for r in responses]
    assert [f["kind"] for f in finals] == ["uploaded", "uploaded"]
    assert sorted(bool(f["resumed"]) for f in finals) == [False, True]  # einer setzt fort, der andere wartet
    with file_db() as db:
        assert db.query(ChatConversation).count() == 1 and db.query(Document).count() == 1
        assert db.query(ChatMessage).filter_by(role="upload").count() == 1 and db.query(ApiCallLog).count() == 0


# --- 4. Kontrollierter Fehlschlag, wenn der Stand nicht stimmig ist --------------------------------------------------


def test_document_deleted_after_the_abort_fails_controlled_without_deleting_anything(client, db_session, writer,
                                                                                    monkeypatch) -> None:
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS["vor_vermerk"](patch)
        _abort_stream(client, token)
    document = db_session.query(Document).one()
    document.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    rejection = _rejection(_stream(client, token))
    assert "nicht sicher fortsetzen" in rejection["content"] and "nichts gelöscht" in rejection["content"]
    assert _operation(db_session).status == "failed"
    assert db_session.query(Document).count() == 1 and db_session.query(ChatConversation).count() == 1  # nichts geloescht
    assert db_session.query(ChatMessage).filter_by(role="upload").count() == 0
    _uploaded(_stream(client, rejection["upload_token"]))  # sicherer Neustart mit neuer Kennung


def test_legacy_operation_without_plan_fails_controlled(client, db_session, writer, monkeypatch) -> None:
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_unterhaltung"](patch)
        _abort_stream(client, token)
    operation = _operation(db_session)
    operation.plan = None
    db_session.commit()
    rejection = _rejection(_stream(client, token))
    assert "nicht sicher fortsetzen" in rejection["content"] and _operation(db_session).status == "failed"
    assert _counts(db_session) == (1, 0, 0, 0)


# --- 5. Keine Dateinamen in Fehler-URLs -----------------------------------------------------------------------------


def _classic_post(client, files, token=None):
    page = _page(client)
    return client.post("/dashboard/chat/send", follow_redirects=False, files=files, data={
        "csrf_token": extract_csrf(page), "upload_token": token or extract_upload_token(page),
        "conversation_id": "", "content": TYPED})


def test_size_error_url_contains_no_file_name(client, db_session, writer, monkeypatch) -> None:
    monkeypatch.setattr(chat_service_module, "_MAX_UPLOAD_SIZE_BYTES", 10)
    response = _classic_post(client, {"documents": ("Mandantin Haselhorst Gutachten.pdf", PDF, "application/pdf")})
    location = response.headers["location"]
    assert "Haselhorst" not in location and "Gutachten" not in location and "maximale" in location.replace("%20", " ")


def test_type_error_url_contains_no_file_name(client, db_session, writer) -> None:
    response = _classic_post(client, {"documents": ("Mandantin Haselhorst.exe", b"MZ", "application/octet-stream")})
    location = response.headers["location"]
    assert "Haselhorst" not in location and ".exe" in location and db_session.query(Document).count() == 0


def test_stream_size_error_still_names_the_file_locally(client, db_session, writer, monkeypatch) -> None:
    monkeypatch.setattr(chat_service_module, "_MAX_UPLOAD_SIZE_BYTES", 10)
    rejection = _rejection(_stream(client, extract_upload_token(_page(client)),
                                   files={"documents": ("gutachten.pdf", PDF, "application/pdf")}))
    assert "maximale Größe" in rejection["content"] and "gutachten.pdf" in rejection["content"]


def test_matter_document_upload_size_error_url_contains_no_file_name(client, db_session, monkeypatch) -> None:
    import app.web.document_actions_router as document_actions

    from app.models import Client

    matter = Matter(client=Client(name="Synthetischer Mandant"), title="Testakte")
    db_session.add(matter)
    db_session.commit()
    monkeypatch.setattr(document_actions, "_MAX_UPLOAD_SIZE_BYTES", 10)
    page = client.get(f"/dashboard/matters/{matter.id}").text
    response = client.post(f"/dashboard/matters/{matter.id}/documents/upload", follow_redirects=False,
                           data={"csrf_token": extract_csrf(page)},
                           files={"documents": ("Mandantin Haselhorst.pdf", PDF, "application/pdf")})
    location = response.headers.get("location", "")
    assert response.status_code == 303 and "error=" in location and "Haselhorst" not in location


# --- 6. Zurueck-Button: verbrauchte Kennung, neue Kennung ohne Wiederholung -------------------------------------------


def test_restored_page_with_a_used_token_gets_a_new_one_without_any_write(client, db_session, writer) -> None:
    page = _page(client)
    used = extract_upload_token(page)
    _uploaded(_stream(client, used))
    before = _counts(db_session), db_session.query(ChatUploadOperation).count()
    fresh = client.get("/dashboard/chat/upload-token")
    assert fresh.status_code == 200 and fresh.headers["cache-control"] == "no-store"
    token = fresh.json()["upload_token"]
    assert token != used and upload_operations.read_token(token, _operation(db_session).user_id)
    assert (_counts(db_session), db_session.query(ChatUploadOperation).count()) == before  # nichts geschrieben
    # die alte (verbrauchte) Kennung mit einem ANDEREN Upload: abgewiesen, nichts angelegt
    other = {"documents": ("anderes.pdf", PDF + b"%anders", "application/pdf")}
    assert "unterscheidet sich" in _rejection(_stream(client, used, files=other))["content"]
    assert _counts(db_session) == before[0]
    _uploaded(_stream(client, token, files=other))  # bewusst neuer Upload mit der neuen Kennung
    assert _counts(db_session) == (2, 2, 2, 0)


def test_chat_pages_are_not_cached_and_restore_a_fresh_token(client) -> None:
    response = client.get("/dashboard/chat")
    assert response.headers["cache-control"] == "no-store"
    assert 'window.addEventListener("pageshow"' in response.text and "/dashboard/chat/upload-token" in response.text


def test_token_endpoint_requires_login(db_session) -> None:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        response = TestClient(app).get("/dashboard/chat/upload-token", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code in (302, 303, 401) and "upload_token" not in response.text


def test_operation_rows_still_hold_no_content(client, db_session, writer) -> None:
    _stream(client, extract_upload_token(_page(client)))
    row = _operation(db_session)
    stored = json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns}, ensure_ascii=False)
    for value in ("Zweigelt", "beschluss.pdf", TYPED, "4.250,00"):
        assert value not in stored


# --- 7. Nachtraege aus dem unabhaengigen Review (§60) ----------------------------------------------------------------


def test_two_resumes_of_an_own_dead_attempt_cannot_both_take_over(client, db_session, writer, monkeypatch) -> None:
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_unterhaltung"](patch)
        _abort_stream(client, token)
    stale = _operation(db_session)
    assert process_owner.is_own(stale.owner) and upload_operations.owner_state(stale) == upload_operations.DEAD
    assert upload_operations.take_over(db_session, stale) is True
    assert upload_operations.take_over(db_session, stale) is False  # derselbe veraltete Stand: verliert


def test_a_conversation_deleted_after_the_abort_is_not_revived(client, db_session, writer, monkeypatch) -> None:
    token = extract_upload_token(_page(client))
    with monkeypatch.context() as patch:
        CRASH_POINTS["vor_vermerk"](patch)
        _abort_stream(client, token)
    conversation = db_session.query(ChatConversation).one()
    db_session.delete(conversation)  # der Nutzer loescht die halbfertige Unterhaltung (hartes Loeschen)
    db_session.commit()
    rejection = _rejection(_stream(client, token))  # dieselbe Seite sendet erneut
    assert "nicht sicher fortsetzen" in rejection["content"] and _operation(db_session).status == "failed"
    assert db_session.query(ChatConversation).count() == 0  # nicht wiederbelebt
    assert db_session.query(ChatMessage).filter_by(role="upload").count() == 0


def test_resume_does_not_record_the_name_marking_twice(client, db_session, writer, monkeypatch) -> None:
    from app.models import AuditEvent

    token = extract_upload_token(_page(client))
    content = "Was entschied Richter Zweigelt?"
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_nachricht"](patch)
        page = _page(client)
        try:
            client.post("/dashboard/chat/send-stream", files={"documents": ("beschluss.pdf", PDF, "application/pdf")},
                        data={"csrf_token": extract_csrf(page), "upload_token": token, "conversation_id": "",
                              "content": content, "protected_terms": json.dumps(["Zweigelt"])})
        except BaseException:  # noqa: BLE001
            pass
    events = lambda: db_session.query(AuditEvent).filter(AuditEvent.event_type.like("%privacy%")).count()  # noqa: E731
    before = events()
    assert before >= 1  # die Markierung wurde im abgebrochenen Versuch bereits gespeichert
    page = _page(client)
    client.post("/dashboard/chat/send-stream", files={"documents": ("beschluss.pdf", PDF, "application/pdf")},
                data={"csrf_token": extract_csrf(page), "upload_token": token, "conversation_id": "",
                      "content": content, "protected_terms": json.dumps(["Zweigelt"])})
    assert _operation(db_session).status == "done" and events() == before


def test_odd_file_name_dots_do_not_leak_name_parts_into_the_url(client, db_session, writer) -> None:
    response = _classic_post(client, {"documents": ("Klage Meier.Meier GmbH", b"x", "application/octet-stream")})
    from urllib.parse import parse_qs, urlparse

    location = response.headers["location"]
    message = parse_qs(urlparse(location).query)["error"][0]
    assert "meier" not in location.lower() and "gmbh" not in location.lower()
    assert message.startswith("Dateityp '?' wird nicht unterstützt")


def test_owner_stores_no_plain_host_name(client, db_session, writer) -> None:
    import socket

    _stream(client, extract_upload_token(_page(client)))
    assert socket.gethostname() not in (_operation(db_session).owner or "")
