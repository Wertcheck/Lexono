"""Tests für app/firm_profile/practice_areas.py (03.10., Owner-Direktive
"KANZLEIFACHPROFIL UND JURISTISCHE WISSENSSTEUERUNG").

Gleiches Testmuster wie tests/test_firm_profile_service.py - isolierte
In-Memory-SQLite-DB, kein Web-Layer."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.clients.service import PRACTICE_AREA_SUGGESTIONS
from app.firm_profile import (
    InvalidPracticeAreaError,
    get_display_options,
    get_firm_profile,
    get_selected_practice_areas,
    remove_practice_area,
    set_practice_areas,
)
from app.models import AuditEvent, FirmPracticeArea
from app.models.base import Base


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_get_selected_practice_areas_is_empty_by_default(db_session: Session) -> None:
    assert get_selected_practice_areas(db_session) == []


def test_set_practice_areas_persists_multiple_values(db_session: Session) -> None:
    result = set_practice_areas(
        db_session, ["Erbrecht", "Arbeitsrecht"], actor="admin@kanzlei.test"
    )

    assert result == ["Arbeitsrecht", "Erbrecht"]
    assert get_selected_practice_areas(db_session) == ["Arbeitsrecht", "Erbrecht"]


def test_set_practice_areas_is_idempotent_no_duplicate_rows(db_session: Session) -> None:
    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")
    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")

    assert db_session.query(FirmPracticeArea).filter_by(practice_area="Erbrecht").count() == 1


def test_set_practice_areas_removes_deselected_values(db_session: Session) -> None:
    set_practice_areas(db_session, ["Erbrecht", "Familienrecht"], actor="admin@kanzlei.test")

    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")

    assert get_selected_practice_areas(db_session) == ["Erbrecht"]


def test_set_practice_areas_rejects_unknown_value_without_writing_anything(
    db_session: Session,
) -> None:
    with pytest.raises(InvalidPracticeAreaError) as exc_info:
        set_practice_areas(
            db_session, ["Erbrecht", "Voelkerrecht"], actor="admin@kanzlei.test"
        )

    assert "Voelkerrecht" in exc_info.value.invalid_values
    assert get_selected_practice_areas(db_session) == []


def test_set_practice_areas_strips_whitespace_and_deduplicates_input(
    db_session: Session,
) -> None:
    result = set_practice_areas(
        db_session, [" Erbrecht ", "Erbrecht", "Erbrecht  "], actor="admin@kanzlei.test"
    )

    assert result == ["Erbrecht"]


def test_set_practice_areas_writes_an_audit_event(db_session: Session) -> None:
    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")

    events = db_session.query(AuditEvent).filter_by(event_type="firm_practice_areas_updated").all()
    assert len(events) == 1
    assert events[0].actor == "admin@kanzlei.test"
    assert "Erbrecht" in events[0].details


def test_set_practice_areas_does_not_affect_existing_orphaned_rows(db_session: Session) -> None:
    profile = get_firm_profile(db_session)
    db_session.add(FirmPracticeArea(firm_profile_id=profile.id, practice_area="Altes Gebiet"))
    db_session.commit()

    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")

    stored = {row.practice_area for row in db_session.query(FirmPracticeArea).all()}
    assert stored == {"Erbrecht", "Altes Gebiet"}


def test_remove_practice_area_deletes_exactly_one_row(db_session: Session) -> None:
    set_practice_areas(db_session, ["Erbrecht", "Familienrecht"], actor="admin@kanzlei.test")

    remove_practice_area(db_session, "Erbrecht", actor="admin@kanzlei.test")

    assert get_selected_practice_areas(db_session) == ["Familienrecht"]


def test_remove_practice_area_is_idempotent_for_a_missing_value(db_session: Session) -> None:
    # Darf nicht fehlschlagen, obwohl nichts zu entfernen ist.
    remove_practice_area(db_session, "Nie gespeichert", actor="admin@kanzlei.test")
    assert get_selected_practice_areas(db_session) == []


def test_get_display_options_marks_recognized_and_orphaned_values_correctly(
    db_session: Session,
) -> None:
    profile = get_firm_profile(db_session)
    db_session.add(FirmPracticeArea(firm_profile_id=profile.id, practice_area="Erbrecht"))
    db_session.add(FirmPracticeArea(firm_profile_id=profile.id, practice_area="Verwaistes Gebiet"))
    db_session.commit()

    options = get_display_options(db_session)
    by_name = {option.name: option for option in options}

    assert by_name["Erbrecht"].selected is True
    assert by_name["Erbrecht"].recognized is True
    assert by_name["Verwaistes Gebiet"].selected is True
    assert by_name["Verwaistes Gebiet"].recognized is False
    # Alle bekannten Vorschlaege sind immer vorhanden, unabhaengig von der Auswahl.
    assert set(PRACTICE_AREA_SUGGESTIONS).issubset({o.name for o in options})
    not_selected = by_name["Arbeitsrecht"]
    assert not_selected.selected is False
    assert not_selected.recognized is True


def test_firm_profile_deletion_cascades_to_practice_areas(db_session: Session) -> None:
    """Singleton-Konsistenz: wird die `FirmProfile`-Zeile jemals geloescht
    (z. B. ueber einen kuenftigen Reset), duerfen keine verwaisten
    `FirmPracticeArea`-Zeilen zurueckbleiben (cascade="all, delete-orphan",
    siehe app/models/firm_profile.py)."""
    profile = get_firm_profile(db_session)
    set_practice_areas(db_session, ["Erbrecht"], actor="admin@kanzlei.test")

    db_session.delete(profile)
    db_session.commit()

    assert db_session.query(FirmPracticeArea).count() == 0
