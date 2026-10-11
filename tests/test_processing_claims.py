"""Prozessuebergreifende Doppelversand-Sperre (12.10.2026, app/chat/processing_claims.py).

Echte Datei-SQLite-Datenbank, echte parallele Prozesse bzw. Threads: genau ein Halter je Nachricht."""

from __future__ import annotations

import multiprocessing
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chat import processing_claims
from app.models import ChatConversation, ChatMessage, Client, Matter, User
from app.models.base import Base
from app.models.chat_message_claim import ChatMessageClaim
from tests.claim_worker_util import claim_in_process


@pytest.fixture()
def file_db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'claims.db').as_posix()}"
    engine = create_engine(url, connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = User(email="a@kanzlei.test", password_hash="x")
    client = Client(name="Synthetischer Mandant")
    matter = Matter(client=client, title="Akte")
    session.add_all([user, client, matter])
    session.flush()
    conversation = ChatConversation(matter_id=matter.id, user_id=user.id, title="Chat")
    session.add(conversation)
    session.flush()
    message = ChatMessage(conversation_id=conversation.id, role="user", content="Synthetische Frage")
    session.add(message)
    session.commit()
    yield url, session, message.id
    session.close()
    engine.dispose()


def test_exactly_one_of_several_processes_obtains_the_claim(file_db) -> None:
    url, _, message_id = file_db
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=claim_in_process, args=(url, message_id, start, results)) for _ in range(4)]
    for worker in workers:
        worker.start()
    start.set()
    outcomes = [results.get(timeout=120) for _ in workers]
    for worker in workers:
        worker.join(timeout=60)
    assert outcomes.count(True) == 1, outcomes
    assert all(o is False or o is True or str(o).startswith("error:") for o in outcomes)


def test_exactly_one_of_several_threads_obtains_the_claim(file_db) -> None:
    url, _, message_id = file_db
    engine = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False})
    barrier = threading.Barrier(6)
    outcomes: list = []

    def worker() -> None:
        session = sessionmaker(bind=engine)()
        barrier.wait()
        try:
            outcomes.append(processing_claims.claim(session, message_id))
        except Exception as exc:  # noqa: BLE001
            outcomes.append(f"error:{type(exc).__name__}")
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    engine.dispose()
    assert outcomes.count(True) == 1, outcomes


def test_release_allows_a_new_claim_and_states_are_reported(file_db) -> None:
    _, session, message_id = file_db
    assert processing_claims.state(session, message_id) == "free"
    assert processing_claims.claim(session, message_id) is True
    assert processing_claims.state(session, message_id) == "processing"
    assert processing_claims.claim(session, message_id) is False
    processing_claims.release(session, message_id)
    assert processing_claims.state(session, message_id) == "free"
    assert processing_claims.claim(session, message_id) is True


def test_stale_claim_is_reported_as_interrupted_and_never_reclaimed(file_db) -> None:
    _, session, message_id = file_db
    session.add(ChatMessageClaim(message_id=message_id, claimed_at=datetime.now(timezone.utc) - timedelta(hours=2), owner="x"))
    session.commit()
    assert processing_claims.state(session, message_id) == "interrupted"
    assert processing_claims.claim(session, message_id) is False  # keine automatische Uebernahme (fail-closed)


def test_claim_fails_closed_on_a_read_only_database(file_db, tmp_path) -> None:
    r"""Zweites Windows-Konto (12.10.2026): Standard-ACL von C:\ProgramData\Lexono\data\kanzlei_ai.db gibt anderen
    Benutzern nur Lesezugriff. Ein Prozess ohne Schreibrecht darf die Sperre NIE erhalten (keine Verarbeitung, kein
    Cloud-Aufruf) - der Fehler wird nicht als "frei" oder "erhalten" umgedeutet."""
    import os
    import stat

    from sqlalchemy.exc import OperationalError

    url, session, message_id = file_db
    session.close()
    db_file = url.replace("sqlite:///", "")
    os.chmod(db_file, stat.S_IREAD)
    try:
        engine = create_engine(url)
        ro_session = sessionmaker(bind=engine)()
        with pytest.raises(OperationalError):
            processing_claims.claim(ro_session, message_id)
        assert processing_claims.state(ro_session, message_id) == "free"  # nichts wurde beansprucht
        ro_session.close()
        engine.dispose()
    finally:
        os.chmod(db_file, stat.S_IREAD | stat.S_IWRITE)
