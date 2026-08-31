"""In-Prozess-Sliding-Window-Rate-Limiter pro Kanzlei-Credential.

Bewusst kein Redis/externer Store (siehe ARCHITECTURE.md §70: Monolith/
kleiner Gateway-Service ist für den ersten Pilot ausreichend, ein
einzelner Gateway-Prozess reicht für ein Pilot-Kanzlei-Volumen). Eine
künftige Mehrprozess-/Mehrinstanz-Bereitstellung bräuchte einen geteilten
Store - hier bewusst als offener Folgepunkt dokumentiert, nicht vorab
gebaut (§21 des Auftrags: keine unnötige Neuentwicklung)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit_per_minute: int, now: float | None = None) -> bool:
        """Gibt `True` zurück und zählt den Aufruf, wenn `key` das
        Sliding-Window-Limit (letzte 60 Sekunden) nicht überschreitet -
        sonst `False`, ohne zu zählen."""
        current_time = now if now is not None else time.monotonic()
        window_start = current_time - 60.0
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < window_start:
                hits.popleft()
            if len(hits) >= limit_per_minute:
                return False
            hits.append(current_time)
            return True
