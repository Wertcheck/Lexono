"""Atomare, prozessuebergreifende Sperre gegen Doppelversand je Chat-Nutzernachricht (12.10.2026).

Siehe app/models/chat_message_claim.py. Die Sperre nutzt eine EIGENE kurze Datenbanksitzung, damit ein Commit nie
fremde, noch offene Aenderungen der Request-Sitzung mitschreibt und ein Fehler hier den Request nicht verunreinigt.

Zustaende einer Nutzernachricht aus Sicht der Sperre:
- "free"        keine Sperre - eine Verarbeitung darf beginnen.
- "processing"  gesperrt, juenger als `STALE_AFTER` - laeuft vermutlich gerade.
- "interrupted" gesperrt, aelter als `STALE_AFTER` - Prozess vermutlich abgestuerzt. UNKLAR, ob bereits ein
                Cloud-Aufruf erfolgte: KEINE automatische Wiederaufnahme (fail-closed). Der Anwalt sendet die
                Nachricht bewusst neu (neue Nachricht = neue, vollstaendig gepruefte Verarbeitung).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.chat_message_claim import ChatMessageClaim

STALE_AFTER = timedelta(minutes=15)
_OWNER = f"pid{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _short_session(db: Session) -> Session:
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)()


def claim(db: Session, message_id: str) -> bool:
    """True genau fuer den EINEN Aufrufer, der die Sperre anlegt (Primaerschluessel-Eindeutigkeit der Datenbank)."""
    session = _short_session(db)
    try:
        session.add(ChatMessageClaim(message_id=message_id, claimed_at=datetime.now(timezone.utc), owner=_OWNER))
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False
    finally:
        session.close()


def release(db: Session, message_id: str | None) -> None:
    if message_id is None:
        return
    session = _short_session(db)
    try:
        session.query(ChatMessageClaim).filter(ChatMessageClaim.message_id == message_id).delete(
            synchronize_session=False
        )
        session.commit()
    finally:
        session.close()


def state(db: Session, message_id: str) -> str:
    session = _short_session(db)
    try:
        row = session.get(ChatMessageClaim, message_id)
    finally:
        session.close()
    if row is None:
        return "free"
    claimed_at = row.claimed_at if row.claimed_at.tzinfo else row.claimed_at.replace(tzinfo=timezone.utc)
    return "interrupted" if datetime.now(timezone.utc) - claimed_at > STALE_AFTER else "processing"
