"""Idempotente Chat-Uploads (§59) - duenne Schicht ueber dem Mechanismus aus app/drafting/schriftsatz_operations.py
(§58): eigenes Salt (eine Generator-ID gilt hier nicht und umgekehrt), eigenes Modell mit Chat-Bezug.

§60 Wiederaufnahme nach hartem Abbruch: jeder Vorgang traegt seinen Besitzer (app/chat/process_owner.py). Im EIGENEN
Prozess entscheidet die Liste der hier gerade laufenden Vorgaenge (`running`), sonst das Lebenszeichen des fremden
Prozesses. Zeit allein gibt nie frei.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.chat import process_owner
from app.drafting import schriftsatz_operations as _operations
from app.models.chat_upload_operation import ChatUploadOperation

ALIVE, DEAD, UNKNOWN = process_owner.ALIVE, process_owner.DEAD, process_owner.UNKNOWN

# Referenzzaehler statt Menge: ein unterlegener paralleler Request darf beim Verlassen nicht die Markierung des noch
# laufenden Gewinners entfernen
_running: dict[str, int] = {}
_running_lock = threading.Lock()


@contextmanager
def running(operation_id: str) -> Iterator[None]:
    """Markiert den Vorgang als in DIESEM Prozess laufend - VOR dem Anlegen bzw. der Uebernahme gesetzt (keine Luecke),
    endet auch bei einem harten Abbruch des Requests (finally)."""
    with _running_lock:
        _running[operation_id] = _running.get(operation_id, 0) + 1
    try:
        yield
    finally:
        with _running_lock:
            remaining = _running.get(operation_id, 1) - 1
            if remaining > 0:
                _running[operation_id] = remaining
            else:
                _running.pop(operation_id, None)


def owner_state(operation: ChatUploadOperation) -> str:
    """ALIVE / DEAD / UNKNOWN des Besitzers eines Vorgangs im Zustand "processing"."""
    if process_owner.is_own(operation.owner):
        with _running_lock:
            return ALIVE if _running.get(operation.id) else DEAD
    return process_owner.state_of(operation.owner)


def take_over(db: Session, operation: ChatUploadOperation) -> bool:
    """Neuer Ausfuehrungsversuch (eigene Nonce): genau ein Request gewinnt - auch wenn der beendete Besitzer der eigene
    Prozess war (sonst waere "alt == neu" und jeder Compare-and-Set gelaenge)."""
    return _operations.take_over(db, operation.id, expected_owner=operation.owner,
                                 new_owner=process_owner.new_attempt(), model=ChatUploadOperation)

_SALT = "lexono-chat-upload-vorgang-v1"

document_ids = _operations.document_ids
is_stale = _operations.is_stale


def issue_token(user_id: str) -> str:
    return _operations.issue_token(user_id, salt=_SALT)


def read_token(token: str, user_id: str) -> str | None:
    return _operations.read_token(token, user_id, salt=_SALT)


def request_fingerprint(parts: list[str], files: list[tuple[str, bytes]]) -> str:
    return _operations.request_fingerprint(parts, files, salt=_SALT)


def claim(db: Session, operation_id: str, *, user_id: str, fingerprint: str, plan: str) -> ChatUploadOperation | None:
    return _operations.claim(db, operation_id, user_id=user_id, fingerprint=fingerprint, model=ChatUploadOperation,
                             owner=process_owner.new_attempt(), plan=plan)


def load(db: Session, operation_id: str) -> ChatUploadOperation | None:
    return _operations.load(db, operation_id, model=ChatUploadOperation)


def update(db: Session, operation_id: str, **fields) -> None:
    _operations.update(db, operation_id, model=ChatUploadOperation, **fields)


def add_document(db: Session, operation_id: str, document_id: str) -> None:
    _operations.add_document(db, operation_id, document_id, model=ChatUploadOperation)
