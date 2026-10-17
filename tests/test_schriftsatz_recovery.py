"""§61 P2: harter Abbruch im Schriftsatz-Generator (in §60 offen geblieben).

- Upload (keine Cloud): nach einem harten Abbruch an jedem Speicherpunkt setzt eine Wiederholung DENSELBEN Vorgang
  fort, sobald sein Besitzer nachweislich beendet ist - ohne Doppelanlage von Akte, Dokument oder Datei.
- Generierung (Cloud): nie automatisch wiederholt - ob die KI-Anfrage schon gesendet wurde, ist unklar. Eine Wiederholung
  schlaegt kontrolliert fehl (kein zweiter Request), die Akte bleibt vorausgewaehlt, ein bewusster Neustart ist moeglich.
- Zeit allein gibt nie frei; ein nicht pruefbarer Besitzer bleibt unangetastet.

Ein harter Abbruch wird mit einer BaseException simuliert (kein `except Exception` faengt sie, wie ein Prozessende).
Nur synthetische Daten, isolierte SQLite-Datenbanken, lokaler Aufzeichnungsserver."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web.schriftsatz_router as schriftsatz_router_module
from app.chat import process_owner
from app.documents import readiness
from app.drafting import schriftsatz_operations
from app.models import ApiCallLog, Document, Draft, Matter
from app.models.schriftsatz_operation import SchriftsatzOperation
from tests.auth_test_utils import extract_csrf
from tests.test_schriftsatz_idempotency import TITLE, _counts, _files, _token
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)


class _HardAbort(BaseException):
    """Simulierter Prozessabbruch (kein Exception-Handler faengt ihn)."""


def _after(original):
    calls = []

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            raise _HardAbort("Prozess beendet (synthetisch)")
        return result

    return wrapper


def _before():
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _HardAbort("Prozess beendet (synthetisch)")

    return wrapper


def _post(client: TestClient, token: str, *, files=None, **fields):
    page = client.get("/dashboard/tools/schriftsatz").text
    data = {"csrf_token": extract_csrf(page), "operation_token": token, "matter_id": "", "new_matter_title": TITLE,
            "new_client_name": "", "stil": "", "vorlage": "", "attorney_anmerkungen": "", **fields}
    return client.post("/dashboard/tools/schriftsatz/generate", data=data, follow_redirects=False,
                       files=files or {"unbenutzt": ("", b"", "application/octet-stream")})


def _abort(client: TestClient, token: str, **kwargs) -> None:
    try:
        response = _post(client, token, **kwargs)
    except BaseException:  # noqa: BLE001 - der simulierte Abbruch selbst
        return
    assert response.status_code == 500, response.text[:200]


def _operation(db: Session) -> SchriftsatzOperation:
    db.expire_all()
    return db.query(SchriftsatzOperation).one()


def _files_on_disk(tmp_path: Path) -> int:
    folder = tmp_path / "schriftsatz"
    return len(list(folder.iterdir())) if folder.exists() else 0


CRASH_POINTS = {
    "nach_akte": lambda mp: mp.setattr(schriftsatz_router_module, "create_quick_matter",
                                       _after(schriftsatz_router_module.create_quick_matter)),
    "vor_dokument_commit": lambda mp: mp.setattr(schriftsatz_router_module, "compute_sha256", _before()),
    "waehrend_verarbeitung": lambda mp: mp.setattr(
        "app.documents.service.DocumentProcessingService.process_document", _before()),
    "vor_vermerk": lambda mp: mp.setattr(schriftsatz_operations, "add_document", _before()),
}


@pytest.mark.parametrize("point", sorted(CRASH_POINTS))
def test_hard_abort_of_an_upload_resumes_without_duplicates(client, db_session, recorder, monkeypatch, tmp_path,
                                                            point) -> None:
    token = _token(client)
    with monkeypatch.context() as patch:
        CRASH_POINTS[point](patch)
        _abort(client, token, files=_files())
    operation = _operation(db_session)
    assert operation.status == "processing"
    assert schriftsatz_operations.owner_state(operation) == schriftsatz_operations.DEAD

    from app.models import AuditEvent

    resumed = _post(client, token, files=_files())  # dieselbe Anfrage erneut (F5 / erneutes Absenden)
    assert db_session.query(AuditEvent).filter_by(event_type="schriftsatz_operation_resumed").count() == 1
    assert resumed.status_code == 200 and 'id="upload-notice"' in resumed.text
    assert _counts(db_session) == (1, 1, 0) and _files_on_disk(tmp_path) == 1  # keine Doppelanlage, keine Kopie
    document = db_session.query(Document).one()
    assert readiness.assess_document(db_session, document).state == readiness.READY
    assert _operation(db_session).status == "done"

    again = _post(client, token, files=_files())
    assert 'id="upload-repeated"' in again.text and _counts(db_session) == (1, 1, 0)
    assert recorder.requests == [] and db_session.query(ApiCallLog).count() == 0  # null KI-Anfragen


def test_hard_abort_after_the_cloud_request_is_never_repeated(client, db_session, recorder, monkeypatch) -> None:
    from app.drafting.service import DraftingService

    token = _token(client)
    with monkeypatch.context() as patch:
        patch.setattr(DraftingService, "create_draft", _after(DraftingService.create_draft))
        _abort(client, token, stil="sachlich")
    assert len(recorder.requests) == 1 and db_session.query(Draft).count() == 1  # Anfrage ist bereits erfolgt

    retry = _post(client, token, stil="sachlich")
    assert retry.status_code == 200 and "Generierung wurde unterbrochen" in retry.text
    assert "nicht automatisch wiederholt" in retry.text
    assert len(recorder.requests) == 1 and db_session.query(Draft).count() == 1  # KEIN zweiter Cloud-Aufruf
    operation = _operation(db_session)
    assert operation.status == "failed"
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    assert f'<option value="{matter.id}" selected>' in retry.text  # bewusster Neustart in derselben Akte
    third = _post(client, token, stil="sachlich")
    assert "bereits fehlgeschlagen" in third.text and len(recorder.requests) == 1


def test_hard_abort_before_the_cloud_request_is_also_not_repeated(client, db_session, recorder, monkeypatch) -> None:
    token = _token(client)
    with monkeypatch.context() as patch:
        patch.setattr(schriftsatz_router_module, "get_drafting_service", _before())
        _abort(client, token)
    retry = _post(client, token)
    assert "Generierung wurde unterbrochen" in retry.text and recorder.requests == []
    assert db_session.query(Draft).count() == 0 and _counts(db_session) == (1, 0, 0)
    assert _operation(db_session).status == "failed"
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    assert f'<option value="{matter.id}" selected>' in retry.text


def test_generate_abort_right_after_creating_the_quick_matter_still_preselects_it(client, db_session, recorder,
                                                                                  monkeypatch) -> None:
    """Review §61 P2 (mittel): Abbruch zwischen dem Anlegen der Schnellakte und ihrem Vermerk am Vorgang - die
    Fehlerseite waehlt die (geplante) Akte trotzdem vor, ein bewusster Neustart legt keine zweite an."""
    from app.models import AuditEvent

    token = _token(client)
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_akte"](patch)
        _abort(client, token)
    assert _operation(db_session).matter_id is None  # Vermerk fehlt
    retry = _post(client, token)
    matter = db_session.query(Matter).filter_by(title=TITLE).one()
    assert f'<option value="{matter.id}" selected>' in retry.text and recorder.requests == []
    assert db_session.query(AuditEvent).filter_by(event_type="schriftsatz_operation_interrupted").count() == 1
    fresh = _post(client, _token(client), matter_id=matter.id)  # bewusster Neustart in DIESER Akte
    assert fresh.status_code == 303 and db_session.query(Matter).filter_by(title=TITLE).count() == 1
    assert len(recorder.requests) == 1


def test_broken_plan_fails_controlled_instead_of_endless_errors(client, db_session, writer, monkeypatch) -> None:
    token = _token(client)
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_akte"](patch)
        _abort(client, token, files=_files())
    operation = _operation(db_session)
    operation.plan = "{kaputt"
    db_session.commit()
    first = _post(client, token, files=_files())
    assert first.status_code == 200 and "nicht sicher fortsetzen" in first.text
    assert _operation(db_session).status == "failed"
    assert "bereits fehlgeschlagen" in _post(client, token, files=_files()).text


def test_a_living_owner_is_never_taken_over_even_hours_later(client, db_session, writer, monkeypatch) -> None:
    monkeypatch.setattr(schriftsatz_router_module, "_REPEAT_WAIT_SECONDS", 0.3)
    token = _token(client)
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_akte"](patch)
        _abort(client, token, files=_files())
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        state, ticks = process_owner._probe(child.pid)
        operation = _operation(db_session)
        operation.owner = f"{process_owner._HOST}|{child.pid}|{ticks}|versuch1"
        operation.created_at = datetime.now(timezone.utc) - timedelta(hours=5)
        db_session.commit()
        waiting = _post(client, token, files=_files())
        assert "gerade noch verarbeitet" in waiting.text and _counts(db_session) == (1, 0, 0)
    finally:
        child.kill()
        child.wait()
    resumed = _post(client, token, files=_files())  # Besitzer nachweislich beendet -> jetzt (nicht nach Zeit)
    assert 'id="upload-notice"' in resumed.text and _counts(db_session) == (1, 1, 0)


def test_unverifiable_owner_is_left_alone(client, db_session, writer, monkeypatch) -> None:
    token = _token(client)
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_akte"](patch)
        _abort(client, token, files=_files())
    operation = _operation(db_session)
    operation.owner = "anderer-rechner|4242|1|v"
    operation.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()
    response = _post(client, token, files=_files())
    assert "nicht sicher feststellen" in response.text and _operation(db_session).status == "processing"
    assert _counts(db_session) == (1, 0, 0)


def test_document_deleted_after_the_abort_fails_controlled(client, db_session, writer, monkeypatch) -> None:
    token = _token(client)
    with monkeypatch.context() as patch:
        CRASH_POINTS["waehrend_verarbeitung"](patch)
        _abort(client, token, files=_files())
    document = db_session.query(Document).one()
    document.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    response = _post(client, token, files=_files())
    assert response.status_code == 303 and "error=" in response.headers["location"]
    assert _operation(db_session).status == "failed"
    assert db_session.query(Document).count() == 1 and db_session.query(Matter).filter_by(title=TITLE).count() == 1


def test_two_resumes_of_an_own_dead_attempt_cannot_both_take_over(client, db_session, writer, monkeypatch) -> None:
    token = _token(client)
    with monkeypatch.context() as patch:
        CRASH_POINTS["nach_akte"](patch)
        _abort(client, token, files=_files())
    stale = _operation(db_session)
    assert schriftsatz_operations.take_over_operation(db_session, stale) is True
    assert schriftsatz_operations.take_over_operation(db_session, stale) is False


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_schriftsatz_owner_and_plan_migration_is_additive_and_reversible(tmp_path) -> None:
    import sqlite3

    from alembic import command

    from tests.test_migrations import _alembic_config_for

    db_path = tmp_path / "schriftsatz_owner.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    columns = {row[1]: row[3] for row in con.execute("pragma table_info(schriftsatz_operations)").fetchall()}
    con.close()
    assert columns["owner"] == 0 and columns["plan"] == 0
    command.downgrade(cfg, "schritt3_034")
    con = sqlite3.connect(str(db_path))
    columns = {row[1] for row in con.execute("pragma table_info(schriftsatz_operations)").fetchall()}
    con.close()
    assert "owner" not in columns and "plan" not in columns and "fingerprint" in columns
