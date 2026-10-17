"""Idempotente Chat-Uploads (§59) - duenne Schicht ueber dem Mechanismus aus app/drafting/schriftsatz_operations.py
(§58): eigenes Salt (eine Generator-ID gilt hier nicht und umgekehrt), eigenes Modell mit Chat-Bezug."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.drafting import schriftsatz_operations as _operations
from app.models.chat_upload_operation import ChatUploadOperation

_SALT = "lexono-chat-upload-vorgang-v1"

document_ids = _operations.document_ids
is_stale = _operations.is_stale


def issue_token(user_id: str) -> str:
    return _operations.issue_token(user_id, salt=_SALT)


def read_token(token: str, user_id: str) -> str | None:
    return _operations.read_token(token, user_id, salt=_SALT)


def request_fingerprint(parts: list[str], files: list[tuple[str, bytes]]) -> str:
    return _operations.request_fingerprint(parts, files, salt=_SALT)


def claim(db: Session, operation_id: str, *, user_id: str, fingerprint: str) -> ChatUploadOperation | None:
    return _operations.claim(db, operation_id, user_id=user_id, fingerprint=fingerprint, model=ChatUploadOperation)


def load(db: Session, operation_id: str) -> ChatUploadOperation | None:
    return _operations.load(db, operation_id, model=ChatUploadOperation)


def update(db: Session, operation_id: str, **fields) -> None:
    _operations.update(db, operation_id, model=ChatUploadOperation, **fields)


def add_document(db: Session, operation_id: str, document_id: str) -> None:
    _operations.add_document(db, operation_id, document_id, model=ChatUploadOperation)
