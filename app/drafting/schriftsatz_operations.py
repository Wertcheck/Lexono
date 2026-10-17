"""Idempotente Vorgaenge im Schriftsatz-Generator (§58), siehe app/models/schriftsatz_operation.py.

- `issue_token`: jede ausgelieferte Generator-Seite bekommt eine NEUE Vorgangs-ID (uuid4), signiert mit dem
  Session-Schluessel und an den Nutzer gebunden (itsdangerous, wie das Session-Cookie). Eine selbst gewaehlte,
  fremde oder abgelaufene ID wird abgewiesen - serverseitig, keine reine UI-Sperre.
- `claim`: legt den Vorgang atomar an (Primaerschluessel). Gibt None fuer den EINEN Request zurueck, der den Vorgang
  ausfuehrt, sonst den bereits bestehenden Vorgang (Wiederholung).
- Fortschritt (Akte, Dokument-IDs, Entwurf, Status) wird in einer EIGENEN kurzen Sitzung geschrieben (wie
  app/chat/processing_claims.py): ein Commit hier schreibt nie fremde offene Aenderungen der Request-Sitzung mit.

§59: derselbe Mechanismus fuer den Chat-Upload (app/chat/upload_operations.py) - Modell und Salt sind Parameter, die
Voreinstellungen bleiben die des Schriftsatz-Generators. Eigene Salts je Zweck: eine Generator-ID gilt nie im Chat.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.chat_upload_operation import ChatUploadOperation
from app.models.schriftsatz_operation import STATUS_PROCESSING, SchriftsatzOperation

Operation = SchriftsatzOperation | ChatUploadOperation

_SALT = "lexono-schriftsatz-vorgang-v1"
# Laenger als eine Arbeitssitzung am Formular, kuerzer als "beliebig alt": eine sehr alte Seite muss neu geladen werden
TOKEN_MAX_AGE_SECONDS = 24 * 60 * 60
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
# Juenger: laeuft vermutlich noch (bitte warten); aelter: vermutlich abgebrochen (wie app/chat/processing_claims.py)
STALE_AFTER = timedelta(minutes=15)


def _serializer(salt: str) -> URLSafeTimedSerializer:
    # Prozessweiter Session-Schluessel aus der gecachten Konfiguration - dieselbe Quelle wie das Session-Cookie,
    # unabhaengig von einer pro Request erzeugten Settings-Instanz (sonst wechselte im Entwicklungsbetrieb der
    # zufaellige Schluessel zwischen Seite und Absenden)
    return URLSafeTimedSerializer(get_settings().resolved_session_secret_key, salt=salt)


def issue_token(user_id: str, *, salt: str = _SALT) -> str:
    return _serializer(salt).dumps({"op": uuid.uuid4().hex, "user": user_id})


def read_token(token: str, user_id: str, *, salt: str = _SALT) -> str | None:
    """Vorgangs-ID oder None (fehlend, manipuliert, abgelaufen, fuer einen anderen Nutzer ausgestellt)."""
    if not token or len(token) > 512:
        return None
    try:
        payload = _serializer(salt).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except BadSignature:  # umfasst SignatureExpired
        return None
    if not isinstance(payload, dict) or payload.get("user") != user_id:
        return None
    operation_id = payload.get("op")
    return operation_id if isinstance(operation_id, str) and _OPERATION_ID.match(operation_id) else None


def request_fingerprint(parts: list[str], files: list[tuple[str, bytes]], *, salt: str = _SALT) -> str:
    """HMAC-SHA256 ueber Formularfelder und Dateien (Name + Inhalts-Hash). Mit dem Session-Schluessel verkettet, damit
    der gespeicherte Wert keine Rueckschluesse auf Inhalte erlaubt (kein ungesalzener Hash von Mandanteninhalten)."""
    key = hashlib.sha256((salt + "|fingerprint|" + get_settings().resolved_session_secret_key).encode()).digest()
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for part in parts:
        mac.update(part.encode("utf-8"))
        mac.update(b"\0")
    for name, content in files:
        mac.update(name.encode("utf-8"))
        mac.update(b"\1")
        mac.update(hashlib.sha256(content).digest())
    return mac.hexdigest()


def is_stale(operation: Operation) -> bool:
    created = operation.created_at
    if created is None:
        return True
    if created.tzinfo is None:  # SQLite liefert naive Zeitstempel (UTC gespeichert)
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > STALE_AFTER


def _short_session(db: Session) -> Session:
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)()


def claim(db: Session, operation_id: str, *, user_id: str, fingerprint: str, model=SchriftsatzOperation, **fields):
    """None genau fuer den EINEN Request, der den Vorgang anlegt; sonst der bestehende Vorgang (frisch gelesen)."""
    session = _short_session(db)
    try:
        session.add(model(id=operation_id, user_id=user_id, status=STATUS_PROCESSING, fingerprint=fingerprint,
                          **fields))
        session.commit()
        return None
    except IntegrityError:
        session.rollback()
        existing = session.get(model, operation_id)
        if existing is None:  # theoretisch: Zeile zwischenzeitlich entfernt - nicht ausfuehren (fail-closed)
            return model(id=operation_id, user_id="", status=STATUS_PROCESSING, fingerprint="", **fields)
        session.expunge(existing)
        return existing
    finally:
        session.close()


def load(db: Session, operation_id: str, *, model=SchriftsatzOperation) -> Operation | None:
    """Frisch gelesener Vorgang (eigene kurze Sitzung - sieht den Commit eines parallelen Requests)."""
    session = _short_session(db)
    try:
        operation = session.get(model, operation_id)
        if operation is not None:
            session.expunge(operation)
        return operation
    finally:
        session.close()


def update(db: Session, operation_id: str, *, model=SchriftsatzOperation, **fields) -> None:
    session = _short_session(db)
    try:
        operation = session.get(model, operation_id)
        if operation is not None:
            for name, value in fields.items():
                setattr(operation, name, value)
            session.commit()
    finally:
        session.close()


def add_document(db: Session, operation_id: str, document_id: str, *, model=SchriftsatzOperation) -> None:
    """Fortlaufend nach JEDEM gespeicherten Dokument - eine Wiederholung nach einem Teilfehler kennt die Dokumente."""
    session = _short_session(db)
    try:
        operation = session.get(model, operation_id)
        if operation is not None:
            operation.document_ids = json.dumps(document_ids(operation) + [document_id])
            session.commit()
    finally:
        session.close()


def document_ids(operation: Operation) -> list[str]:
    try:
        value = json.loads(operation.document_ids or "[]")
    except ValueError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
