"""SQLAlchemy-Engine/Session fuer den Gateway - unabhaengig von app/db/
(eigener Deployment-Kontext, siehe gateway/__init__.py)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from gateway.config import GatewaySettings
from gateway.models import Base


def _ensure_sqlite_directory_exists(database_url: str) -> None:
    """Wie app/db/session.py::_ensure_sqlite_directory_exists - SQLite legt
    das Verzeichnis der DB-Datei NICHT selbst an, ein frischer
    Gateway-Start mit einem noch nicht existierenden `gateway_data/`-Ordner
    würde sonst mit `unable to open database file` fehlschlagen."""
    if not database_url.startswith("sqlite:///"):
        return
    db_path = Path(database_url.removeprefix("sqlite:///"))
    if db_path.parent and str(db_path.parent) not in ("", "."):
        db_path.parent.mkdir(parents=True, exist_ok=True)


def build_engine(settings: GatewaySettings):
    _ensure_sqlite_directory_exists(settings.database_url)
    connect_args = (
        {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    )
    return create_engine(settings.database_url, connect_args=connect_args)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def build_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def iter_session(session_factory: sessionmaker) -> Iterator[Session]:
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
