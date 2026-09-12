"""Orchestrierung der Ersteinrichtung (Prompt 37).

Reine Ablauflogik, keine Konsolen-Interaktion (siehe app/setup/__init__.py).
Migration und Admin-Anlage werden als Callables injiziert, siehe Docstring
von `run_setup_wizard` für die Begründung (Prozessgrenzen wegen
`lru_cache`d Settings).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .env_writer import build_env_content, write_env_file

logger = logging.getLogger(__name__)


class WizardError(Exception):
    """Fehler während des Setup-Assistenten (z. B. ungültige Eingabe)."""


@dataclass(frozen=True)
class WizardResult:
    env_path: Path
    data_dir: Path
    local_ai_setup_succeeded: bool | None = None


def run_setup_wizard(
    *,
    data_dir: Path,
    admin_email: str,
    admin_password: str | None,
    run_migrations: Callable[[], None],
    create_admin: Callable[[str, str | None], None],
    run_local_ai_setup: Callable[[], bool] | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    force: bool = False,
) -> WizardResult:
    """Führt die Ersteinrichtung durch: Verzeichnisse, `.env`, Migration, Admin.

    `run_migrations`/`create_admin` werden injiziert statt hier direkt
    aufgerufen: in der tatsächlichen Windows-Installation laufen beide als
    SEPARATER Prozessaufruf derselben gebündelten .exe (siehe `run.py`),
    weil `app.config.get_settings()` (`@lru_cache`) und `app.db.session`
    die Konfiguration beim ERSTEN Import im laufenden Prozess einlesen bzw.
    fest verdrahten (Engine-Erzeugung beim Modul-Import). Ein frischer
    Prozess pro Schritt stellt sicher, dass die gerade geschriebene `.env`
    tatsächlich gelesen wird, ohne Cache-Invalidierung über bereits
    importierte Module nachvollziehen zu müssen. Für Tests genügt ein
    einfacher In-Prozess-Callable (siehe tests/test_setup_wizard.py).

    Reihenfolge bewusst: Validierung → Verzeichnisse → `.env` schreiben →
    Migration → Admin-Anlage → (optional) lokale KI. Ein Fehler in einem
    späteren Schritt lässt die vorherigen Ergebnisse (Verzeichnisse, `.env`)
    bestehen - ein erneuter Lauf mit `force=True` kann daran anknüpfen,
    statt bei Null zu beginnen.

    `run_local_ai_setup` (Phase 3, §71): anders als `run_migrations`/
    `create_admin` ist dieser Schritt bewusst NICHT ladungstragend für den
    Setup-Erfolg - Migration/Admin-Anlage MÜSSEN gelingen, damit die
    Anwendung überhaupt startet, die lokale KI-Einrichtung ist dagegen ein
    optionaler, potenziell langsamer (Download) Zusatzschritt (Auftrag §33:
    "Local AI dauerhaft deaktivieren, nur weil ein Testgerät langsam ist"
    ist verboten - das Gegenteil gilt hier ebenso: ein FEHLGESCHLAGENER
    Local-AI-Setup-Versuch darf nicht die gesamte Ersteinrichtung
    scheitern lassen, z. B. bei fehlendem Internetzugang während der
    Installation). Ein Fehlschlag wird daher abgefangen und nur als
    `local_ai_setup_succeeded=False` im Ergebnis vermerkt - der Anwalt/die
    Kanzlei kann die Einrichtung jederzeit später manuell nachholen."""
    _validate_admin_email(admin_email)

    (data_dir / "data").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    session_secret = secrets.token_urlsafe(48)
    content = build_env_content(
        data_dir=data_dir, session_secret=session_secret, host=host, port=port
    )
    env_path = data_dir / ".env"
    write_env_file(env_path, content, force=force)

    run_migrations()
    create_admin(admin_email, admin_password)

    # Erst HIER (nach tatsaechlich erfolgreicher Admin-Anlage) geschrieben -
    # bewusst NICHT gleichzeitig mit `.env` oben, da `.env` bereits VOR
    # Migration/Admin-Anlage existiert und daher allein kein verlaesslicher
    # Beweis fuer "Ersteinrichtung abgeschlossen" ist (siehe
    # OPEN_ISSUES.md: realer Endanwender-Vorfall, `run.py::main()` prueft
    # deshalb inzwischen zusaetzlich die Datenbank). `Start.vbs` kann keine
    # Datenbankabfrage durchfuehren (kein SQLite-Treiber in VBScript) und
    # braucht daher dieses einfache, aber nur bei echtem Erfolg gesetzte
    # Datei-Signal, um zu entscheiden, ob die Konsole beim naechsten Start
    # sichtbar sein muss (Ersteinrichtung evtl. noch unvollstaendig) oder
    # verborgen bleiben darf.
    (data_dir / ".setup_complete").write_text("1", encoding="utf-8")

    local_ai_setup_succeeded: bool | None = None
    if run_local_ai_setup is not None:
        try:
            local_ai_setup_succeeded = run_local_ai_setup()
        except Exception:  # noqa: BLE001 - darf die Ersteinrichtung nie scheitern lassen
            logger.exception("Lokale-KI-Einrichtung während des Setup-Assistenten fehlgeschlagen")
            local_ai_setup_succeeded = False

    return WizardResult(
        env_path=env_path,
        data_dir=data_dir,
        local_ai_setup_succeeded=local_ai_setup_succeeded,
    )


def _validate_admin_email(email: str) -> None:
    if not email or not email.strip():
        raise WizardError("Admin-E-Mail-Adresse darf nicht leer sein.")
    if "@" not in email:
        raise WizardError(f"'{email}' sieht nicht wie eine gültige E-Mail-Adresse aus.")
