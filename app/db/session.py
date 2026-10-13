"""Engine- und Session-Erzeugung.

Nutzt ausschliesslich `settings.database_url` (siehe app/config/settings.py).
Kein Modul ausserhalb dieser Datei soll eine eigene Engine erzeugen oder
SQLite-/PostgreSQL-spezifische Verbindungsdetails kennen.
"""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()


def _ensure_sqlite_directory_exists(database_url: str) -> None:
    """Legt das Verzeichnis der SQLite-Datei an, falls es fehlt.

    Nur fuer den Prototyp-Fall relevant (sqlite:///./data/...). Bei
    PostgreSQL o. ae. ist diese Funktion ein No-Op.
    """
    if not database_url.startswith("sqlite:///"):
        return
    db_path = Path(database_url.removeprefix("sqlite:///"))
    if db_path.parent and str(db_path.parent) not in ("", "."):
        db_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_directory_exists(_settings.database_url)

# SQLite braucht in Multithread-Kontexten (z. B. FastAPI) diesen zusaetzlichen
# Connect-Parameter; PostgreSQL und andere Datenbanken nicht. Das ist der
# einzige Ort im Projekt, an dem zwischen den beiden unterschieden wird -
# Modelle und Geschaeftslogik bleiben davon unberuehrt.
_connect_args = (
    {"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {}
)

# hide_parameters (Datenschutz-Pruefung §27): ein Datenbankfehler schrieb die gespeicherten Werte (Namen, E-Mail-
# Adressen) als "[parameters: ...]" in Fehlermeldung und Log; die SQL-Anweisung selbst bleibt sichtbar.
engine = create_engine(_settings.database_url, connect_args=_connect_args, hide_parameters=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI-Dependency fuer eine Datenbank-Session pro Request.

    Wird ab dem Prompt eingesetzt, der die ersten API-Endpunkte mit
    Datenbankzugriff einfuehrt (Prompt 21). Hier bereits bereitgestellt,
    damit spaetere Module nicht erneut Session-Handling definieren muessen.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
