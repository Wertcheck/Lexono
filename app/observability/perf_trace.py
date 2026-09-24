"""Request-Correlation-Trace fuer Performance-Diagnose (P0 Performance-
Root-Cause-Run, 13.09.).

Misst NUR die Dauer der BEREITS BESTEHENDEN Pipeline-Schritte (Chat-
Routing, Privacy-Gateway, lokale KI-Vorabanalyse, Retrieval, Claude,
Antwortvalidierung, Rekonstruktion) unter einer gemeinsamen, zufaelligen
Trace-ID - erfindet KEINE neuen Pipeline-Schritte und veraendert keinen
bestehenden Kontrollfluss. Protokolliert AUSSCHLIESSLICH Schrittname,
Trace-ID und Dauer in Sekunden - NIEMALS Sachverhalt, Anmerkungen oder
sonstigen Klartextinhalt (CLAUDE.md: "Niemals Secrets in Code oder Logs
schreiben" / keine personenbezogenen Klartextdaten in Performance-Logs).

Optionaler Parameter an bestehenden Methoden (siehe DraftingService.
create_draft, ChatService.send_message) - ohne uebergebene Instanz wird
intern automatisch eine neue erzeugt, bestehende Aufrufer/Tests bleiben
unveraendert."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

_logger = logging.getLogger("lexono.perf")


class PerfTrace:
    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex[:12]
        self._t_start = time.perf_counter()
        self.steps: list[tuple[str, float]] = []

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            duration = time.perf_counter() - t0
            self.steps.append((name, duration))
            _logger.info(
                "PERF trace=%s step=%s duration_s=%.3f", self.trace_id, name, duration
            )

    def total_seconds(self) -> float:
        return time.perf_counter() - self._t_start
