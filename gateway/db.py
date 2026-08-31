"""SQLAlchemy-Engine/Session fuer den Gateway - unabhaengig von app/db/
(eigener Deployment-Kontext, siehe gateway/__init__.py)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from gateway.config import GatewaySettings
from gateway.models import Base


def build_engine(settings: GatewaySettings):
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
