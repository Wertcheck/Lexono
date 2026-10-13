"""Datenschutz-Entscheidungsvorlage fuer den Pilot (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §26). Nur synthetische Daten.

Reproduziert und behoben: (1) eine geloeschte Akte (Soft-Delete) lieferte weiterhin Sachverhalt und Dokumente fuer
Cloud-Anfragen; (2) abgelehnte Fristvorschlaege gingen weiter an die Cloud; (3) der Audit-Eintrag zum Loeschen einer
Unterhaltung enthielt deren Titel (oft aus der ersten Nachricht); (4) Intake-Watcher-Logs enthielten den vollen Pfad mit
Dateinamen; (5) die Loeschfrist-Einstellung wirkte wie eine funktionierende automatische Loeschung."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.chat.service import ChatService
from app.models import Client, Deadline, Document, Matter, Role, User
from app.models.base import Base
from tests.test_chat_service import _drafting_service

_SECRET_FACT = "Synthetischer Sachverhalt Lindenweg Vierzehn"


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _matter(db: Session) -> Matter:
    client = Client(name="Synthetische Mandantin")
    matter = Matter(client=client, title="Synthetische Akte")
    db.add_all([client, matter])
    db.flush()
    db.add(Document(matter_id=matter.id, file_path="synthetisch.txt", original_filename="synthetisch.txt",
                    extracted_text=_SECRET_FACT, mime_type="text/plain"))
    db.commit()
    return matter


def test_deleted_matter_yields_no_cloud_context(db: Session) -> None:
    matter = _matter(db)
    provider = RuleBasedLocalAIProvider()
    assert _SECRET_FACT in provider.prepare_draft_context(matter.id, db).sachverhalt  # Gegenprobe: aktiv = Kontext
    matter.deleted_at = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(ValueError, match="gelöscht"):
        provider.prepare_draft_context(matter.id, db)


def test_chat_on_deleted_matter_sends_nothing_to_the_cloud(db: Session, tmp_path: Path) -> None:
    role = Role(name="anwalt")
    db.add(role)
    db.commit()
    user = User(email="anwalt@kanzlei.test", password_hash="x", role_id=role.id)
    db.add(user)
    db.commit()
    matter = _matter(db)
    chat = ChatService(tmp_path / "uploads")
    conversation = chat.create_conversation(db, user=user, matter_id=matter.id, title="Frage", actor=user.email)
    matter.deleted_at = datetime.now(timezone.utc)
    db.commit()
    drafting, writer = _drafting_service()
    reply = chat.send_message(db, conversation=conversation, content="Fasse den Sachverhalt zusammen.",
                              drafting_service=drafting, actor=user.email)
    # fail-closed: der Chat zeigt eine blockierte Antwort, die Cloud erhaelt nichts
    assert reply.blocked and writer.received_payloads == []
    assert _SECRET_FACT not in reply.content


def test_rejected_deadline_suggestion_is_not_sent(db: Session) -> None:
    matter = _matter(db)
    db.add_all([
        Deadline(matter_id=matter.id, source_text="Frist zur Stellungnahme bis 30.11.2026", due_date=date(2026, 11, 30)),
        Deadline(matter_id=matter.id, source_text="Termin am 15.12.2026", due_date=date(2026, 12, 15),
                 review_status="rejected"),
    ])
    db.commit()
    punkte = RuleBasedLocalAIProvider()._build_argumentationspunkte(matter.id, db)
    assert any("30.11.2026" in p for p in punkte)
    assert not any("15.12.2026" in p for p in punkte)


@pytest.mark.parametrize("error", ["intake", "oserror"])
def test_watcher_logs_do_not_contain_the_file_name(tmp_path: Path, error: str) -> None:
    """Auch nicht im Traceback (Review §26): IntakeError und OSError tragen den vollen Pfad im Fehlertext."""
    from app.ingestion.intake import IntakeError
    from app.ingestion.watcher import _NewFileEventHandler

    target = tmp_path / "Mueller_Kuendigung.pdf"

    class _Intake:
        def ingest_file(self, path, db):
            if error == "intake":
                raise IntakeError(f"Datei wurde nicht rechtzeitig stabil: {path}")
            raise FileNotFoundError(2, "No such file", str(path))

    class _Retry:
        def record_failure(self, db, **kwargs):
            pass

    handler = _NewFileEventHandler.__new__(_NewFileEventHandler)
    handler._session_factory = lambda: type("S", (), {"close": lambda self: None})()
    handler._intake_service = _Intake()
    handler._retry_service = _Retry()
    handler._on_ingested = None
    stream = io.StringIO()
    log_handler = logging.StreamHandler(stream)
    logger = logging.getLogger("app.ingestion.watcher")
    # andere Tests koennen Logger per logging.config deaktivieren bzw. den Level heben - hier explizit setzen
    previous = (logger.disabled, logger.level)
    logger.disabled, logger.level = False, logging.INFO
    logger.addHandler(log_handler)
    try:
        handler._handle_new_file(target)
    finally:
        logger.removeHandler(log_handler)
        logger.disabled, logger.level = previous
    output = stream.getvalue()
    assert output and "Mueller" not in output and str(tmp_path) not in output


def test_retention_setting_says_it_has_no_effect_yet() -> None:
    template = (Path(__file__).resolve().parents[1] / "app/web/templates/settings.html").read_text(encoding="utf-8")
    assert "Noch ohne Wirkung" in template and "Automatische Löschfrist" not in template
