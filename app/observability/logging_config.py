"""Zentrale Logging-Konfiguration (Prompt 32).

Bislang (Prompts 01-31) konfigurierte KEIN Modul das Python-Logging
zentral - nur `app/ingestion/watcher.py` rief `logging.getLogger(__name__)`
auf, ohne dass jemals ein Handler/Format/Level gesetzt wurde (Python gibt
in diesem Fall nur eine minimale Default-Warnmeldung aus, INFO-Logs gehen
schlicht verloren). `configure_logging()` (aufgerufen aus der
`lifespan`-Funktion in app/main.py) behebt das zentral für die gesamte
Anwendung.

GRUNDREGEL (durchgängig seit dem Security Review, Prompt 27/31): Logs
dürfen NIEMALS personenbezogene oder vertrauliche Mandanteninhalte
enthalten - nur IDs, Kategorien, technische Fehlertypen. Diese Regel wird
hier NICHT automatisch/laufzeitseitig erzwungen (ein generischer Filter,
der "verdächtigen" Text erkennt, wäre unzuverlässig und würde falsche
Sicherheit vortäuschen) - sie ist eine Entwickler-Disziplin, die durch
Code-Review UND eine strukturelle Testprüfung abgesichert wird (siehe
tests/test_logging_config.py).
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Verhindert Mehrfach-Konfiguration (z. B. bei Tests, die die App mehrfach
# importieren/instanziieren) - `logging.basicConfig`-artige Idempotenz.
_configured = False


def configure_logging(*, log_level: str = "INFO", log_file_path: str | None = None) -> None:
    """Konfiguriert den Root-Logger einmalig für den gesamten Prozess.

    - Immer: Konsole (stdout) - ausreichend für Entwicklung und für
      Container-/Systemd-artige Betriebsumgebungen, die stdout ohnehin
      sammeln.
    - Optional: eine rotierende Log-Datei (`log_file_path`), sinnvoll für
      einen dauerhaft laufenden Windows-Dienst ohne externe Log-
      Aggregation. Rotation bei 5 MB, 5 Generationen aufbewahrt - verhindert
      unbegrenztes Wachstum ohne manuelle Pflege.
    """
    global _configured
    install_access_log_query_filter()
    if _configured:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file_path:
        path = Path(log_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Drittanbieter-Bibliotheken standardmäßig auf WARNING drosseln -
    # sonst überflutet z. B. watchdog/urllib3 die Logs auf INFO-Ebene mit
    # technischen Details, die für den Kanzleibetrieb irrelevant sind.
    for noisy_logger_name in ("watchdog", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)

    _configured = True


class StripQueryStringFilter(logging.Filter):
    """Entfernt die Query-Parameter aus Zeilen des uvicorn-Zugriffsprotokolls (Datenschutz-Pruefung §27).

    Suchbegriffe (`?q=`, `?search=`) enthalten oft Namen; sie landeten bisher im Klartext in `app.log`. Pfad, Methode und
    Status bleiben fuer den Betrieb erhalten."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0] + "?[entfernt]", *args[3:])
        return True


def install_access_log_query_filter() -> None:
    """Idempotent; aufgerufen beim Anwendungsstart (lifespan), also nach der Logging-Konfiguration durch uvicorn."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, StripQueryStringFilter) for f in access_logger.filters):
        access_logger.addFilter(StripQueryStringFilter())


def reset_logging_configuration_for_tests() -> None:
    """NUR für Tests: erlaubt, `configure_logging` in einem neuen Testfall
    erneut auszuführen (z. B. um einen anderen log_level zu prüfen)."""
    global _configured
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    _configured = False
