"""Zuletzt ausgefuehrter automatischer E-Mail-Abruf (Posteingang-Kopfbereich "Letzter Abruf", 26.10.).

Bewusst nur im Prozessspeicher: der Wert beschreibt, was DIESE laufende Lexono-Instanz zuletzt getan hat
(app/main.py::_run_periodic_mail_ingestion). Nach einem Neustart gibt es ehrlich noch keinen Abruf - kein
gespeicherter, moeglicherweise veralteter Zeitpunkt, kein erfundener Wert.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class MailPollStatus:
    at: datetime
    ok: bool
    new_messages: int


_lock = threading.Lock()
_last: MailPollStatus | None = None


def record_poll(ok: bool, new_messages: int = 0) -> None:
    global _last
    with _lock:
        _last = MailPollStatus(at=datetime.now(timezone.utc), ok=ok, new_messages=new_messages)


def last_poll() -> MailPollStatus | None:
    with _lock:
        return _last


def reset() -> None:
    """Nur fuer Tests."""
    global _last
    with _lock:
        _last = None
