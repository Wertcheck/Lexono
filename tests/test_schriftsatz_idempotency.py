"""§58: Idempotenz im Schriftsatz-Generator - F5, erneutes Absenden, Doppelklick, parallele Requests, Wiederholung
nach unklarem Ergebnis. Eine Wiederholung DESSELBEN Vorgangs (gleiche, serverseitig ausgestellte Vorgangs-ID) legt
keine weitere Akte, keine Dokumente und keinen Entwurf an und loest keine KI-Anfrage aus; ein bewusster neuer Vorgang
(neue Seite = neue ID) bleibt moeglich. Upload bleibt reiner Upload (§57).

HTTP-Nachweis ueber den lokalen Aufzeichnungsserver (echter Provider, toter Proxy, gefaelschter Schluessel).
Nur synthetische Daten, isolierte Datenbanken."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.web.schriftsatz_router as schriftsatz_router_module
from app.db.session import get_db
from app.drafting import schriftsatz_operations
from app.main import app
from app.models import ApiCallLog, Client, Document, Draft, Matter
from app.models.base import Base
from app.models.schriftsatz_operation import STATUS_FAILED, STATUS_PROCESSING, SchriftsatzOperation
from tests.auth_test_utils import extract_csrf, extract_operation_token, login_as_admin
from tests.test_upload_without_ai import LINES, _generate, _pdf_bytes, _wire
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)

TITLE = "Idempotenzakte"
PDF = _pdf_bytes(LINES)  # einmal erzeugt: ein echtes F5 sendet byte-identische Dateien (pymupdf-PDFs variieren je Aufruf)


def _files(name: str = "beschluss.pdf", data: bytes | None = None) -> dict:
    return {"documents": (name, data if data is not None else PDF, "application/pdf")}


def _token(client: TestClient, path: str = "/dashboard/tools/schriftsatz") -> str:
    return extract_operation_token(client.get(path).text)


def _counts(db: Session) -> tuple[int, int, int]:
    db.expire_all()
    return (db.query(Matter).filter_by(title=TITLE).count(), db.query(Document).count(), db.query(Draft).count())


# --- 1. F5 / erneutes Absenden / verlorene Antwort ---------------------------------------------------------------------


def test_f5_after_upload_creates_no_second_matter_or_document_and_no_request(client, db_session, recorder) -> None:
    token = _token(client)
    first = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    assert first.status_code == 200 and 'id="upload-notice"' in first.text
    # F5 = der Browser sendet DENSELBEN POST erneut (auch: Antwort ging verloren, Nutzer sendet nochmals ab)
    for _ in range(3):
        again = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
        assert again.status_code == 200 and 'id="upload-repeated"' in again.text
    assert _counts(db_session) == (1, 1, 0)
    document = db_session.query(Document).one()
    assert f"/document/{document.id}#name-protection" in again.text  # Ergebnis des ersten Requests
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    assert f'<option value="{matter.id}" selected>' in again.text
    assert recorder.requests == [] and db_session.query(ApiCallLog).count() == 0  # Upload ohne KI (§57)


def test_resubmit_into_an_existing_matter_creates_no_duplicate_documents(client, db_session, writer) -> None:
    matter = Matter(client=Client(name="Synthetischer Mandant"), title=TITLE, status="open")
    db_session.add(matter)
    db_session.commit()
    token = _token(client)
    for _ in range(2):
        _generate(client, matter_id=matter.id, files=_files(), operation_token=token)
    assert _counts(db_session) == (1, 1, 0) and writer.received_payloads == []


def test_a_new_deliberate_upload_of_the_same_file_is_still_possible(client, db_session, writer) -> None:
    first = _generate(client, new_matter_title=TITLE, files=_files())
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    # die Ergebnisseite traegt eine NEUE Vorgangs-ID: bewusst dieselbe Datei nochmals hochladen
    second_token = extract_operation_token(first.text)
    _generate(client, matter_id=matter.id, files=_files(), operation_token=second_token)
    assert _counts(db_session) == (1, 2, 0) and writer.received_payloads == []


def test_every_page_gets_its_own_operation_id(client) -> None:
    ids = {schriftsatz_operations.read_token(_token(client), _admin_id(client)) for _ in range(3)}
    assert len(ids) == 3 and None not in ids


def _admin_id(client: TestClient) -> str:
    from app.models import User

    db = next(app.dependency_overrides[get_db]())
    return db.query(User).filter_by(email="admin@kanzlei.test").one().id


# --- 2. Bewusste Generierung danach ------------------------------------------------------------------------------------


def test_deliberate_generate_after_upload_runs_once_even_when_resubmitted(client, db_session, recorder) -> None:
    upload = _generate(client, new_matter_title=TITLE, files=_files())
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    token = extract_operation_token(upload.text)
    first = _generate(client, matter_id=matter.id, operation_token=token)
    assert first.status_code == 303 and first.headers["location"].endswith("/edit")
    assert len(recorder.requests) == 1  # die bewusste Aktion erreicht die (Loopback-)Cloud genau einmal
    assert "4.250,00 EUR" in _wire(recorder.requests[0])
    again = _generate(client, matter_id=matter.id, operation_token=token)  # Doppelklick/F5 auf "generieren"
    assert again.status_code == 303 and again.headers["location"] == first.headers["location"]
    assert len(recorder.requests) == 1 and _counts(db_session) == (1, 1, 1)


def test_generate_with_a_new_matter_is_not_duplicated_by_a_double_click(client, db_session, writer) -> None:
    token = _token(client)
    first = _generate(client, new_matter_title=TITLE, operation_token=token)
    second = _generate(client, new_matter_title=TITLE, operation_token=token)
    assert first.headers["location"] == second.headers["location"]
    assert _counts(db_session) == (1, 0, 1) and len(writer.received_payloads) == 1


# --- 3. Fehler zwischen Upload, Aktenzuordnung und Antwort ------------------------------------------------------------


def test_crash_during_processing_is_not_repeated_and_creates_nothing_twice(client, db_session, writer,
                                                                          monkeypatch) -> None:
    from app.documents.service import DocumentProcessingService

    original = DocumentProcessingService.process_document

    def _crash(self, *args, **kwargs):
        raise RuntimeError("Absturz (synthetisch)")

    token = _token(client)
    monkeypatch.setattr(DocumentProcessingService, "process_document", _crash)
    crashed = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    assert crashed.status_code == 500  # Antwort mit unklarem Ergebnis
    monkeypatch.setattr(DocumentProcessingService, "process_document", original)
    retry = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    # §61 P2: der Besitzer des abgebrochenen Uploads ist nachweislich beendet (Request vorbei) -> der Upload wird
    # fortgesetzt (keine Cloud), ohne Doppelanlage; vorher meldete die Wiederholung nur zeitabhaengig "bitte warten"
    assert retry.status_code == 200 and 'id="upload-notice"' in retry.text
    assert _counts(db_session) == (1, 1, 0) and writer.received_payloads == []
    operation = db_session.query(SchriftsatzOperation).one()
    db_session.refresh(operation)
    assert operation.status == "done"
    document = db_session.query(Document).one()
    assert "Zweigelt" in (document.extracted_text or "")  # die abgebrochene Verarbeitung wurde lokal nachgeholt
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    assert f'<option value="{matter.id}" selected>' in retry.text


def test_partial_failure_keeps_the_matter_and_is_not_repeated(client, db_session, writer, monkeypatch) -> None:
    monkeypatch.setattr(schriftsatz_router_module, "_MAX_UPLOAD_SIZE_BYTES", 2_000)
    token = _token(client)
    files = [("documents", ("klein.pdf", _pdf_bytes(["Kurz."]), "application/pdf")),
             ("documents", ("gross.pdf", b"%PDF-1.4 " + b"x" * 5_000, "application/pdf"))]
    if len(files[0][1][1]) > 2_000:
        pytest.skip("synthetisches Mini-PDF groesser als die Testgrenze")
    failed = _generate(client, new_matter_title=TITLE, files=files, operation_token=token)
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    assert failed.status_code == 303 and "error=" in failed.headers["location"]
    assert f"matter_id={matter.id}" in failed.headers["location"]  # nur die ID, keine Formularinhalte
    assert "Idempotenz" not in failed.headers["location"]
    retry = _generate(client, new_matter_title=TITLE, files=files, operation_token=token)
    assert "bereits fehlgeschlagen" in retry.text and "klein.pdf" in retry.text
    assert _counts(db_session) == (1, 1, 0)
    assert db_session.query(SchriftsatzOperation).one().status == STATUS_FAILED
    page = client.get(failed.headers["location"]).text
    assert f'<option value="{matter.id}" selected>' in page


# --- 4. Manipulation der Vorgangs-ID ----------------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "frei-gewaehlt", "a" * 32, "x" * 600])
def test_missing_or_forged_operation_id_creates_nothing(client, db_session, writer, token) -> None:
    response = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token or " ")
    assert response.status_code == 303 and "error=" in response.headers["location"]
    assert _counts(db_session) == (0, 0, 0) and db_session.query(SchriftsatzOperation).count() == 0


def test_operation_id_of_another_user_and_expired_ids_are_rejected(client, db_session, writer, monkeypatch) -> None:
    foreign = schriftsatz_operations.issue_token("anderer-nutzer")
    response = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=foreign)
    assert "error=" in response.headers["location"] and _counts(db_session) == (0, 0, 0)
    old = _token(client)
    monkeypatch.setattr(schriftsatz_operations, "TOKEN_MAX_AGE_SECONDS", -1)
    response = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=old)
    assert "error=" in response.headers["location"] and _counts(db_session) == (0, 0, 0)


# --- 5. Parallele Requests (eigene Datei-DB, eine Sitzung je Request) ------------------------------------------------


@pytest.fixture()
def file_db(tmp_path) -> Iterator[sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'parallel.db'}", connect_args={"check_same_thread": False,
                                                                                 "timeout": 30})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_parallel_claims_have_exactly_one_winner(file_db) -> None:
    from app.models import User

    with file_db() as db:
        user = User(email="qa@kanzlei.test", password_hash="x", display_name="QA")
        db.add(user)
        db.commit()
        user_id = user.id
    results, barrier = [], threading.Barrier(8)

    def _worker():
        with file_db() as db:
            barrier.wait()
            results.append(schriftsatz_operations.claim(db, "0" * 32, user_id=user_id, kind="upload",
                                                        fingerprint="f" * 64))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(None) == 1 and len(results) == 8


def test_parallel_identical_upload_requests_create_one_matter_and_one_document(file_db, monkeypatch, tmp_path) -> None:
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

    settings = Settings(_env_file=None, schriftsatz_upload_storage_dir=str(tmp_path / "uploads"))
    monkeypatch.setattr(schriftsatz_router_module, "get_settings", lambda: settings)
    monkeypatch.setattr(DocumentProcessingService, "process_document", _slow)
    polls = []
    original_load = schriftsatz_operations.load

    def _counting_load(*args, **kwargs):
        polls.append(1)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(schriftsatz_operations, "load", _counting_load)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        with file_db() as db:
            client = TestClient(app, raise_server_exceptions=False)
            login_as_admin(db, client)
        previous_overrides = dict(app.dependency_overrides)
        page = client.get("/dashboard/tools/schriftsatz").text
        data = {"csrf_token": extract_csrf(page), "operation_token": extract_operation_token(page), "matter_id": "",
                "new_matter_title": TITLE}
        responses, barrier = [], threading.Barrier(2)

        def _post():
            barrier.wait()
            responses.append(client.post("/dashboard/tools/schriftsatz/generate", data=data, files=_files(),
                                         follow_redirects=False))

        threads = [threading.Thread(target=_post) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update({k: v for k, v in previous_overrides.items() if k is not get_db})
    assert sorted(r.status_code for r in responses) == [200, 200]
    # §61 P2: der zweite Request wartet auf den LAUFENDEN ersten (Ueberlappung belegt) und zeigt dessen Ergebnis
    assert polls, "der zweite Request hat nicht auf den laufenden Vorgang gewartet (keine Ueberlappung)"
    assert sum('id="upload-repeated"' in r.text for r in responses) == 1
    with file_db() as db:
        assert db.query(Matter).filter_by(title=TITLE).count() == 1 and db.query(Document).count() == 1
        assert db.query(Draft).count() == 0 and db.query(ApiCallLog).count() == 0


# --- 6. Nachtraege aus dem unabhaengigen Review ------------------------------------------------------------------------


def test_changed_request_with_an_old_operation_id_is_neither_redirected_nor_executed(client, db_session,
                                                                                     writer) -> None:
    """Zurueck-Button: alte Seite (alte ID), Anmerkungen bewusst geaendert - kein stilles Umleiten auf den alten
    Entwurf, keine Ausfuehrung, neue Seite mit erhaltenen Eingaben."""
    token = _token(client)
    first = _generate(client, new_matter_title=TITLE, attorney_anmerkungen="Bitte knapp.", operation_token=token)
    assert first.status_code == 303 and len(writer.received_payloads) == 1
    changed = _generate(client, new_matter_title=TITLE, attorney_anmerkungen="Bitte ausführlich.",
                        operation_token=token)
    assert changed.status_code == 200 and "Formular wurde seit dem ersten Absenden geändert" in changed.text
    assert "Bitte ausführlich.</textarea>" in changed.text
    assert extract_operation_token(changed.text) != token  # neue ID fuer den bewussten neuen Vorgang
    assert len(writer.received_payloads) == 1 and _counts(db_session) == (1, 0, 1)


def test_changed_files_with_an_old_operation_id_upload_nothing(client, db_session, writer) -> None:
    token = _token(client)
    _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    other = _generate(client, new_matter_title=TITLE, operation_token=token,
                      files=_files("anderes.pdf", _pdf_bytes(["Anderer Inhalt."])))
    assert "Formular wurde seit dem ersten Absenden geändert" in other.text and "erneut wählen" in other.text
    assert _counts(db_session) == (1, 1, 0) and writer.received_payloads == []


def test_long_interrupted_operation_says_so_without_repeating(client, db_session, writer) -> None:
    from datetime import datetime, timedelta, timezone

    token = _token(client)
    operation_id = schriftsatz_operations.read_token(token, _admin_id(client))
    first = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    assert first.status_code == 200
    operation = db_session.get(SchriftsatzOperation, operation_id)
    # Altbestand bzw. Besitzer auf einem anderen Rechner: nicht pruefbar -> auch nach einer Stunde NICHT uebernommen
    operation.status, operation.owner = STATUS_PROCESSING, None
    operation.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()
    retry = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    assert "wurde unterbrochen" in retry.text and "nicht sicher feststellen" in retry.text
    assert "beschluss.pdf" in retry.text and _counts(db_session) == (1, 1, 0)
    db_session.refresh(operation)
    assert operation.status == STATUS_PROCESSING


def test_repeat_does_not_show_deleted_documents(client, db_session, writer) -> None:
    from datetime import datetime, timezone

    token = _token(client)
    _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    document = db_session.query(Document).one()
    document.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    again = _generate(client, new_matter_title=TITLE, files=_files(), operation_token=token)
    assert document.id not in again.text and _counts(db_session) == (1, 1, 0)


def test_generator_pages_are_not_cached_and_error_urls_are_encoded_without_file_names(client, db_session,
                                                                                    monkeypatch) -> None:
    assert client.get("/dashboard/tools/schriftsatz").headers["cache-control"] == "no-store"
    monkeypatch.setattr(schriftsatz_router_module, "_MAX_UPLOAD_SIZE_BYTES", 10)
    response = _generate(client, new_matter_title=TITLE, files=_files("Mandantin Haselhorst & Co #1.pdf"))
    location = response.headers["location"]
    assert "Haselhorst" not in location and "error=Eine+Datei" in location
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    assert location.endswith(f"&matter_id={matter.id}")
