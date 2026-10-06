"""Tests für den periodischen Hintergrund-Aktualisierungs-Check der
Gesetzesbibliothek (app/main.py::`_run_periodic_law_update_check`, 03.10.,
Owner-Direktive "RELIABLE LEGAL KNOWLEDGE UPDATES").

Gleiches Testmuster wie tests/test_main_mail_ingestion.py: die
"while True"-Schleife wird deterministisch nach GENAU einem echten
Durchlauf beendet. Anders als beim Mail-Abruf wartet diese Schleife ZUERST
(`asyncio.sleep`), DANN prueft sie (siehe Docstring in app/main.py, Grund:
verhindert einen sofortigen echten Netzwerkzugriff bei jedem App-/
Test-Start) - der erste `sleep`-Aufruf muss deshalb bewusst NICHT sofort
abbrechen, sondern "normal" zurückkehren, damit der eigentliche
Pruefdurchlauf ueberhaupt ausgefuehrt wird; erst der ZWEITE Aufruf beendet
die Schleife (entspricht einem echten `task.cancel()` nach dem ersten
Zyklus)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.laws.gesetze_im_internet import GesetzeImInternetError
from app.main import _run_periodic_law_update_check
from app.models import Law
from app.models.base import Base


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _settings(**overrides):
    from app.config.settings import Settings

    return Settings(**overrides)


async def _run_one_iteration(settings, monkeypatch, *, session_factory) -> None:
    monkeypatch.setattr(main_module, "SessionLocal", session_factory)

    sleep_calls = {"count": 0}

    async def _fake_sleep(_seconds: float) -> None:
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(main_module.asyncio, "sleep", _fake_sleep)

    task = asyncio.create_task(_run_periodic_law_update_check(settings))
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert sleep_calls["count"] == 2, "Schleife muss genau einen echten Pruefzyklus durchlaufen haben"


def test_disabled_check_does_nothing(monkeypatch) -> None:
    """Standardfall abgeschaltet: kehrt SOFORT zurueck, OHNE jemals zu
    schlafen oder einen Check auszufuehren."""
    settings = _settings(law_update_check_enabled=False)

    sleep_calls = {"count": 0}

    async def _fake_sleep(_seconds: float) -> None:
        sleep_calls["count"] += 1

    monkeypatch.setattr(main_module.asyncio, "sleep", _fake_sleep)

    asyncio.run(_run_periodic_law_update_check(settings))

    assert sleep_calls["count"] == 0


def test_enabled_check_calls_check_law_for_update_for_every_installed_law(
    monkeypatch, db_session: Session
) -> None:
    db_session.add_all(
        [
            Law(code="BGB", title="Bürgerliches Gesetzbuch"),
            Law(code="STGB", title="Strafgesetzbuch"),
        ]
    )
    db_session.commit()
    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    checked_codes: list[str] = []

    def _fake_check(db, law_code):
        checked_codes.append(law_code)

    monkeypatch.setattr(main_module, "check_law_for_update", _fake_check)

    settings = _settings(law_update_check_enabled=True, law_update_check_interval_seconds=1)
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))

    assert sorted(checked_codes) == ["BGB", "STGB"]


def test_check_failure_does_not_crash_the_loop(monkeypatch, db_session: Session) -> None:
    """Ein fehlgeschlagener Pruefzyklus (z. B. DB-Fehler) darf die Schleife
    nicht beenden - naechster Versuch nach der Wartezeit, gleiches Prinzip
    wie beim Mail-Abruf."""
    db_session.add(Law(code="BGB", title="Bürgerliches Gesetzbuch"))
    db_session.commit()
    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    def _fake_check(db, law_code):
        raise GesetzeImInternetError("Status-Abfrage fehlgeschlagen: ConnectError")

    monkeypatch.setattr(main_module, "check_law_for_update", _fake_check)

    settings = _settings(law_update_check_enabled=True, law_update_check_interval_seconds=1)
    # Erwartung: KEINE Exception propagiert nach aussen.
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))


def test_enabled_check_with_no_installed_laws_does_nothing_harmful(
    monkeypatch, db_session: Session
) -> None:
    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    checked_codes: list[str] = []
    monkeypatch.setattr(
        main_module, "check_law_for_update", lambda db, code: checked_codes.append(code)
    )

    settings = _settings(law_update_check_enabled=True, law_update_check_interval_seconds=1)
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))

    assert checked_codes == []
