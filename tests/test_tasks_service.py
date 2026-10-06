"""Tests für app/tasks/service.py (03.10., Owner-Direktive "AUFGABEN &
FRISTEN") - reine Service-Ebene (keine HTTP-Schicht), siehe
tests/test_web_tasks.py für die Router-/Template-Tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import AuditEvent, Client, Deadline, Matter, Task
from app.models.base import Base
from app.tasks.service import (
    TaskValidationError,
    count_task_items,
    create_task,
    delete_deadline,
    delete_task,
    duplicate_deadline,
    duplicate_task,
    list_task_items,
    set_deadline_status,
    set_task_status,
    update_deadline,
    update_task,
)


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _matter(db: Session, *, title: str = "Testakte") -> Matter:
    client = Client(name="Testmandant")
    matter = Matter(client=client, title=title)
    db.add_all([client, matter])
    db.commit()
    return matter


# --- create_task ---------------------------------------------------------


def test_create_task_succeeds_with_required_fields(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(
        db_session, matter_id=matter.id, title="Schriftsatz finalisieren", actor="anwalt@kanzlei.test"
    )
    assert task.id is not None
    assert task.status == "open"
    assert db_session.query(Task).count() == 1


def test_create_task_writes_audit_event(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(db_session, matter_id=matter.id, title="Aufgabe", actor="anwalt@kanzlei.test")
    events = db_session.query(AuditEvent).filter_by(entity_id=task.id).all()
    assert len(events) == 1
    assert events[0].event_type == "task_created"
    assert events[0].actor == "anwalt@kanzlei.test"


def test_create_task_rejects_blank_title(db_session: Session) -> None:
    matter = _matter(db_session)
    with pytest.raises(TaskValidationError):
        create_task(db_session, matter_id=matter.id, title="   ", actor="anwalt@kanzlei.test")
    assert db_session.query(Task).count() == 0


def test_create_task_stores_priority_and_due_date(db_session: Session) -> None:
    matter = _matter(db_session)
    due = date.today() + timedelta(days=10)
    task = create_task(
        db_session,
        matter_id=matter.id,
        title="Aufgabe",
        due_date=due,
        priority="Hoch",
        actor="anwalt@kanzlei.test",
    )
    assert task.due_date == due
    assert task.priority == "Hoch"


# --- update_task / set_task_status / duplicate_task / delete_task -------


def test_update_task_changes_fields(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(db_session, matter_id=matter.id, title="Alt", actor="a@kanzlei.test")
    updated = update_task(
        db_session,
        task,
        title="Neu",
        description="Neue Beschreibung",
        due_date=date.today(),
        priority="Niedrig",
        actor="a@kanzlei.test",
    )
    assert updated.title == "Neu"
    assert updated.description == "Neue Beschreibung"
    assert updated.priority == "Niedrig"


def test_update_task_rejects_blank_title(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(db_session, matter_id=matter.id, title="Alt", actor="a@kanzlei.test")
    with pytest.raises(TaskValidationError):
        update_task(
            db_session, task, title="  ", description=None, due_date=None, priority=None, actor="a@kanzlei.test"
        )


def test_set_task_status_marks_done_and_reopens(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(db_session, matter_id=matter.id, title="Aufgabe", actor="a@kanzlei.test")
    set_task_status(db_session, task, status="done", actor="a@kanzlei.test")
    assert task.status == "done"
    set_task_status(db_session, task, status="open", actor="a@kanzlei.test")
    assert task.status == "open"


def test_set_task_status_rejects_invalid_value(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(db_session, matter_id=matter.id, title="Aufgabe", actor="a@kanzlei.test")
    with pytest.raises(TaskValidationError):
        set_task_status(db_session, task, status="archived", actor="a@kanzlei.test")


def test_duplicate_task_creates_independent_copy(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(
        db_session, matter_id=matter.id, title="Original", priority="Mittel", actor="a@kanzlei.test"
    )
    set_task_status(db_session, task, status="done", actor="a@kanzlei.test")
    duplicate = duplicate_task(db_session, task, actor="a@kanzlei.test")
    assert duplicate.id != task.id
    assert duplicate.title == "Original (Kopie)"
    assert duplicate.priority == "Mittel"
    # Eine Kopie einer erledigten Aufgabe ist selbst wieder offen - sie
    # repraesentiert einen NEUEN, noch zu erledigenden Vorgang.
    assert duplicate.status == "open"
    assert db_session.query(Task).count() == 2


def test_delete_task_removes_it_and_logs_audit_event_first(db_session: Session) -> None:
    matter = _matter(db_session)
    task = create_task(db_session, matter_id=matter.id, title="Weg damit", actor="a@kanzlei.test")
    task_id = task.id
    delete_task(db_session, task, actor="a@kanzlei.test")
    assert db_session.query(Task).filter_by(id=task_id).first() is None
    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Task", entity_id=task_id, event_type="task_deleted")
        .first()
    )
    assert event is not None


# --- Deadline CRUD ---------------------------------------------------------


def _deadline(db: Session, matter: Matter, **kwargs) -> Deadline:
    kwargs.setdefault("review_status", "confirmed")
    deadline = Deadline(matter_id=matter.id, **kwargs)
    db.add(deadline)
    db.commit()
    return deadline


def test_update_deadline_changes_fields(db_session: Session) -> None:
    matter = _matter(db_session)
    deadline = _deadline(db_session, matter, source_text="Alt", due_date=date.today())
    updated = update_deadline(
        db_session,
        deadline,
        source_text="Neu",
        due_date=date.today() + timedelta(days=5),
        priority="Hoch",
        actor="a@kanzlei.test",
    )
    assert updated.source_text == "Neu"
    assert updated.priority == "Hoch"


def test_update_deadline_rejects_blank_source_text(db_session: Session) -> None:
    matter = _matter(db_session)
    deadline = _deadline(db_session, matter, source_text="Alt", due_date=date.today())
    with pytest.raises(TaskValidationError):
        update_deadline(
            db_session, deadline, source_text="   ", due_date=None, priority=None, actor="a@kanzlei.test"
        )


def test_set_deadline_status_marks_done(db_session: Session) -> None:
    matter = _matter(db_session)
    deadline = _deadline(db_session, matter, source_text="Frist", due_date=date.today())
    assert deadline.status == "open"
    set_deadline_status(db_session, deadline, status="done", actor="a@kanzlei.test")
    assert deadline.status == "done"


def test_duplicate_deadline_creates_confirmed_copy(db_session: Session) -> None:
    matter = _matter(db_session)
    deadline = _deadline(
        db_session, matter, source_text="Original-Frist", due_date=date.today(), priority="Hoch"
    )
    duplicate = duplicate_deadline(db_session, deadline, actor="a@kanzlei.test")
    assert duplicate.id != deadline.id
    assert "Original-Frist" in duplicate.source_text
    assert duplicate.priority == "Hoch"
    # Eine vom Anwalt selbst erzeugte Kopie ist bereits die menschliche
    # Pruefung - nicht "unreviewed" (gleiche Regel wie beim manuellen
    # Anlegen, siehe deadline_actions_router.py).
    assert duplicate.review_status == "confirmed"


def test_delete_deadline_removes_it(db_session: Session) -> None:
    matter = _matter(db_session)
    deadline = _deadline(db_session, matter, source_text="Weg", due_date=date.today())
    deadline_id = deadline.id
    delete_deadline(db_session, deadline, actor="a@kanzlei.test")
    assert db_session.query(Deadline).filter_by(id=deadline_id).first() is None


# --- list_task_items / count_task_items (vereinheitlichte Liste) --------


def test_list_task_items_combines_tasks_and_deadlines(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(db_session, matter_id=matter.id, title="Eine Aufgabe", actor="a@kanzlei.test")
    _deadline(db_session, matter, source_text="Eine Frist", due_date=date.today())

    items = list_task_items(db_session)

    assert len(items) == 2
    assert {item.item_type for item in items} == {"task", "deadline"}


def test_list_task_items_filters_by_item_type(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(db_session, matter_id=matter.id, title="Aufgabe", actor="a@kanzlei.test")
    _deadline(db_session, matter, source_text="Frist", due_date=date.today())

    only_tasks = list_task_items(db_session, item_type="task")
    only_deadlines = list_task_items(db_session, item_type="deadline")

    assert [i.item_type for i in only_tasks] == ["task"]
    assert [i.item_type for i in only_deadlines] == ["deadline"]


def test_list_task_items_filters_by_priority(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(db_session, matter_id=matter.id, title="Hoch", priority="Hoch", actor="a@kanzlei.test")
    create_task(db_session, matter_id=matter.id, title="Niedrig", priority="Niedrig", actor="a@kanzlei.test")

    items = list_task_items(db_session, priority="Hoch")

    assert len(items) == 1
    assert items[0].title == "Hoch"


def test_list_task_items_filters_by_matter(db_session: Session) -> None:
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B")
    create_task(db_session, matter_id=matter_a.id, title="In A", actor="a@kanzlei.test")
    create_task(db_session, matter_id=matter_b.id, title="In B", actor="a@kanzlei.test")

    items = list_task_items(db_session, matter_id=matter_a.id)

    assert [i.title for i in items] == ["In A"]


def test_list_task_items_search_matches_task_title_and_deadline_text(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(db_session, matter_id=matter.id, title="Findbare Aufgabe", actor="a@kanzlei.test")
    create_task(db_session, matter_id=matter.id, title="Andere Aufgabe", actor="a@kanzlei.test")
    _deadline(db_session, matter, source_text="Findbare Frist", due_date=date.today())

    items = list_task_items(db_session, search="Findbar")

    assert len(items) == 2
    assert {i.title for i in items} == {"Findbare Aufgabe", "Findbare Frist"}


def test_list_task_items_empty_search_returns_nothing(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(db_session, matter_id=matter.id, title="Etwas", actor="a@kanzlei.test")

    items = list_task_items(db_session, search="Nichts davon passt")

    assert items == []


def test_list_task_items_default_status_shows_only_open(db_session: Session) -> None:
    matter = _matter(db_session)
    open_task = create_task(db_session, matter_id=matter.id, title="Offen", actor="a@kanzlei.test")
    done_task = create_task(db_session, matter_id=matter.id, title="Erledigt", actor="a@kanzlei.test")
    set_task_status(db_session, done_task, status="done", actor="a@kanzlei.test")

    items = list_task_items(db_session)

    assert [i.title for i in items] == ["Offen"]


def test_list_task_items_status_done_shows_only_done(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(db_session, matter_id=matter.id, title="Offen", actor="a@kanzlei.test")
    done_task = create_task(db_session, matter_id=matter.id, title="Erledigt", actor="a@kanzlei.test")
    set_task_status(db_session, done_task, status="done", actor="a@kanzlei.test")

    items = list_task_items(db_session, status="done")

    assert [i.title for i in items] == ["Erledigt"]


def test_list_task_items_excludes_rejected_deadlines_regardless_of_status(db_session: Session) -> None:
    matter = _matter(db_session)
    _deadline(db_session, matter, source_text="Verworfen", due_date=date.today(), review_status="rejected")

    assert list_task_items(db_session, status="open") == []
    assert list_task_items(db_session, status="done") == []
    assert list_task_items(db_session, status="all") == []


def test_list_task_items_sorts_by_due_date_ascending_and_descending(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(
        db_session, matter_id=matter.id, title="Spät", due_date=date.today() + timedelta(days=30), actor="a@kanzlei.test"
    )
    create_task(
        db_session, matter_id=matter.id, title="Früh", due_date=date.today() + timedelta(days=1), actor="a@kanzlei.test"
    )

    asc = list_task_items(db_session, sort="due_asc")
    desc = list_task_items(db_session, sort="due_desc")

    assert [i.title for i in asc] == ["Früh", "Spät"]
    assert [i.title for i in desc] == ["Spät", "Früh"]


def test_list_task_items_items_without_due_date_sort_last_both_directions(db_session: Session) -> None:
    matter = _matter(db_session)
    create_task(db_session, matter_id=matter.id, title="Ohne Datum", actor="a@kanzlei.test")
    create_task(
        db_session, matter_id=matter.id, title="Mit Datum", due_date=date.today(), actor="a@kanzlei.test"
    )

    asc = list_task_items(db_session, sort="due_asc")
    desc = list_task_items(db_session, sort="due_desc")

    assert asc[-1].title == "Ohne Datum"
    assert desc[-1].title == "Ohne Datum"


def test_list_task_items_pagination_after_filtering(db_session: Session) -> None:
    matter = _matter(db_session)
    for i in range(15):
        create_task(
            db_session,
            matter_id=matter.id,
            title=f"Hoch-Aufgabe {i:02d}",
            priority="Hoch",
            due_date=date.today() + timedelta(days=i),
            actor="a@kanzlei.test",
        )
    create_task(db_session, matter_id=matter.id, title="Andere Prioritaet", priority="Niedrig", actor="a@kanzlei.test")

    total = count_task_items(db_session, priority="Hoch")
    page_1 = list_task_items(db_session, priority="Hoch", page=1, page_size=10, sort="due_asc")
    page_2 = list_task_items(db_session, priority="Hoch", page=2, page_size=10, sort="due_asc")

    assert total == 15
    assert len(page_1) == 10
    assert len(page_2) == 5
    assert {i.id for i in page_1}.isdisjoint({i.id for i in page_2})
