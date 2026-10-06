"""TaskService – vereinheitlichte Listen-/CRUD-Logik fuer `Task` und
`Deadline` auf der Dashboard-Seite "Aufgaben & Fristen" (03.10., Owner-
Direktive "AUFGABEN & FRISTEN", Referenzabgleich
`18_akte_dokumente_detail.png`).

ECHTER FUND beim Bestandsabgleich: `Task` existierte bereits als
Datenmodell, wurde aber projektweit von KEINEM Code-Pfad je erzeugt (0
Zeilen in der echten Produktions-DB) - es gab keinerlei Anlegen-/
Bearbeiten-/Loeschen-Route. `Deadline` hatte nur eine Anlegen-Route
(app/web/deadline_actions_router.py, akten-scoped) und eine reine
Pruef-Aktion (app/web/tasks_router.py::review_deadline) - Bearbeiten/
Duplizieren/Loeschen/Erledigt-Markieren fehlten fuer beide Modelle
VOLLSTAENDIG. Dieses Modul schliesst genau diese Luecke ("Fall 1:
anbinden" - keine neue Architektur, keine neuen Kern-Datenmodelle,
nur zwei kleine additive Spalten siehe Migration schritt3_023).

Die Listenansicht der Seite zeigt Aufgaben UND Fristen gemeinsam
(gleiche Spalten: Titel/Typ/Akte/Mandant/Faellig/Prioritaet/Status).
Da beide aus unterschiedlichen Tabellen stammen, kombiniert
`list_task_items` zwei getrennte, gefilterte Queries zu einer
gemeinsamen Python-Liste (`TaskListItem`), statt eine komplexe
UNION-SQL-Abfrage zu bauen - bei den hier realistischen Datenmengen
(dreistellige Zeilenzahl, siehe `_MAX_DEADLINES_SHOWN`-Vorgaenger in
tasks_router.py) ist das klar lesbarer und wartbarer als eine neue,
bruechige SQL-Konstruktion, und vermeidet jede DB-Dialekt-Kopplung.
Sortierung/Pagination finden entsprechend NACH dem Zusammenfuehren in
Python statt (siehe `list_task_items`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models import AuditEvent, Deadline, Matter, Task

# Vorschlagswerte fuer die Prioritaet (Task.priority/Deadline.priority) -
# bewusst freier String statt DB-Enum, gleiches Muster wie
# app/clients/service.py::CLIENT_TYPE_SUGGESTIONS.
PRIORITY_SUGGESTIONS = ("Hoch", "Mittel", "Niedrig")

# Typen, die die vereinheitlichte Liste tatsaechlich darstellen kann.
# "Termin" erscheint in der Referenz (`18_akte_dokumente_detail.png`,
# Zeile "Gerichtstermin vorbereiten"), hat aber KEIN eigenes Datenmodell
# (kein Appointment/Termin-Modell existiert) - ein neues Kernmodell nur
# fuer die visuelle Vollstaendigkeit eines einzelnen Referenzbilds waere
# eine eigenstaendige Produktentscheidung, die diese Direktive ausdruecklich
# untersagt ("Triff keine eigenstaendigen Produktentscheidungen"). Bewusst
# NICHT nachgebaut - siehe OPEN_ISSUES.md fuer die offene Dokumentation
# dieses Gaps.
ITEM_TYPES = ("task", "deadline")

_ALLOWED_SORT_OPTIONS = ("due_asc", "due_desc")
_ALLOWED_PAGE_SIZES = (10, 20, 50)
_ALLOWED_STATUS_FILTERS = ("open", "done", "all")


class TaskValidationError(Exception):
    """Pflichtfeld fehlt oder ungueltiger Wert - von create_task/
    update_task/create geworfen."""


@dataclass(frozen=True)
class TaskListItem:
    """Vereinheitlichte Zeile der Liste - entweder aus `Task` oder aus
    `Deadline` gespeist. Genau eines von `task`/`deadline` ist gesetzt."""

    id: str
    item_type: str  # "task" | "deadline"
    title: str
    due_date: date | None
    priority: str | None
    status: str  # normalisiert: "open" | "done"
    matter: Matter
    task: Task | None = None
    deadline: Deadline | None = None


def _normalize_priority(priority: str | None) -> str | None:
    priority = (priority or "").strip()
    return priority or None


def _build_task_query(
    db: Session, *, search: str | None, priority: str | None, matter_id: str | None, status: str
):
    query = db.query(Task).options(joinedload(Task.matter).joinedload(Matter.client))
    if search:
        query = query.filter(Task.title.ilike(f"%{search}%"))
    if priority:
        query = query.filter(Task.priority == priority)
    if matter_id:
        query = query.filter(Task.matter_id == matter_id)
    if status == "open":
        query = query.filter(Task.status == "open")
    elif status == "done":
        query = query.filter(Task.status == "done")
    return query


def _build_deadline_query(
    db: Session, *, search: str | None, priority: str | None, matter_id: str | None, status: str
):
    # Eine vom Anwalt ausdruecklich verworfene Frist (`review_status ==
    # "rejected"`) bleibt wie bisher (siehe tasks_router.py::_open_deadlines,
    # Vorgaengerlogik) grundsaetzlich ausgeblendet - das gilt unabhaengig vom
    # Erledigt-Status.
    query = (
        db.query(Deadline)
        .options(joinedload(Deadline.matter).joinedload(Matter.client))
        .filter(Deadline.review_status != "rejected")
    )
    if search:
        query = query.filter(
            or_(
                Deadline.source_text.ilike(f"%{search}%"),
                Deadline.reasoning.ilike(f"%{search}%"),
            )
        )
    if priority:
        query = query.filter(Deadline.priority == priority)
    if matter_id:
        query = query.filter(Deadline.matter_id == matter_id)
    if status == "open":
        query = query.filter(Deadline.status == "open")
    elif status == "done":
        query = query.filter(Deadline.status == "done")
    return query


def _task_title(task: Task) -> str:
    return task.title


def _deadline_title(deadline: Deadline) -> str:
    # Eine manuell/automatisch erkannte Frist hat keinen eigenen "Titel"
    # im Datenmodell (siehe app/models/deadline.py) - `source_text` ist das
    # naechstliegende echte Feld dafuer (Bezeichnung bei manueller Anlage,
    # erkannter Kontext-Satz bei KI-Erkennung). Kein erfundener Titel.
    return deadline.source_text or "Frist ohne Bezeichnung"


def _fetch_items(
    db: Session,
    *,
    search: str | None,
    item_type: str | None,
    priority: str | None,
    matter_id: str | None,
    status: str,
) -> list[TaskListItem]:
    items: list[TaskListItem] = []
    if item_type in (None, "task"):
        for task in _build_task_query(
            db, search=search, priority=priority, matter_id=matter_id, status=status
        ).all():
            items.append(
                TaskListItem(
                    id=task.id,
                    item_type="task",
                    title=_task_title(task),
                    due_date=task.due_date,
                    priority=task.priority,
                    status=task.status,
                    matter=task.matter,
                    task=task,
                )
            )
    if item_type in (None, "deadline"):
        for deadline in _build_deadline_query(
            db, search=search, priority=priority, matter_id=matter_id, status=status
        ).all():
            items.append(
                TaskListItem(
                    id=deadline.id,
                    item_type="deadline",
                    title=_deadline_title(deadline),
                    due_date=deadline.due_date,
                    priority=deadline.priority,
                    status=deadline.status,
                    matter=deadline.matter,
                    deadline=deadline,
                )
            )
    return items


def list_task_items(
    db: Session,
    *,
    search: str | None = None,
    item_type: str | None = None,
    priority: str | None = None,
    matter_id: str | None = None,
    status: str = "open",
    sort: str = "due_asc",
    page: int = 1,
    page_size: int = 10,
) -> list[TaskListItem]:
    if status not in _ALLOWED_STATUS_FILTERS:
        status = "open"
    if sort not in _ALLOWED_SORT_OPTIONS:
        sort = "due_asc"
    if page_size not in _ALLOWED_PAGE_SIZES:
        page_size = 10
    if page < 1:
        page = 1

    items = _fetch_items(
        db, search=search, item_type=item_type, priority=priority, matter_id=matter_id, status=status
    )
    # Faellig-ohne-Datum ans Ende, unabhaengig von der Sortierrichtung - ein
    # fehlendes Datum ist weder "am frühesten" noch "am spätesten faellig",
    # es fehlt schlicht die Information (gleiches Prinzip wie bereits in
    # list_clients/list_matters: `Deadline.due_date.is_(None)` zuerst im
    # SQL-`order_by` - hier in Python nachgebildet, da ueber zwei Tabellen
    # gemergt wird).
    reverse = sort == "due_desc"
    items.sort(
        key=lambda item: (
            item.due_date is None,
            item.due_date or date.max,
        ),
        reverse=False,
    )
    if reverse:
        with_date = [i for i in items if i.due_date is not None]
        without_date = [i for i in items if i.due_date is None]
        with_date.reverse()
        items = with_date + without_date

    start = (page - 1) * page_size
    return items[start : start + page_size]


def count_task_items(
    db: Session,
    *,
    search: str | None = None,
    item_type: str | None = None,
    priority: str | None = None,
    matter_id: str | None = None,
    status: str = "open",
) -> int:
    if status not in _ALLOWED_STATUS_FILTERS:
        status = "open"
    return len(
        _fetch_items(
            db, search=search, item_type=item_type, priority=priority, matter_id=matter_id, status=status
        )
    )


def _log_audit(
    db: Session, *, entity_type: str, entity_id: str, event_type: str, actor: str, details: str | None = None
) -> None:
    db.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            actor=actor,
            details=details,
        )
    )


# --- Task CRUD ---------------------------------------------------------


def create_task(
    db: Session,
    *,
    matter_id: str,
    title: str,
    description: str | None = None,
    due_date: date | None = None,
    priority: str | None = None,
    actor: str,
) -> Task:
    title = title.strip()
    if not title:
        raise TaskValidationError("Titel ist ein Pflichtfeld.")

    task = Task(
        matter_id=matter_id,
        title=title,
        description=(description or "").strip() or None,
        due_date=due_date,
        priority=_normalize_priority(priority),
    )
    db.add(task)
    db.flush()
    _log_audit(
        db,
        entity_type="Task",
        entity_id=task.id,
        event_type="task_created",
        actor=actor,
        details=f"Aufgabe angelegt: {title}",
    )
    db.commit()
    return task


def update_task(
    db: Session,
    task: Task,
    *,
    title: str,
    description: str | None,
    due_date: date | None,
    priority: str | None,
    actor: str,
) -> Task:
    title = title.strip()
    if not title:
        raise TaskValidationError("Titel ist ein Pflichtfeld.")

    task.title = title
    task.description = (description or "").strip() or None
    task.due_date = due_date
    task.priority = _normalize_priority(priority)
    _log_audit(
        db,
        entity_type="Task",
        entity_id=task.id,
        event_type="task_updated",
        actor=actor,
        details=f"Aufgabe bearbeitet: {title}",
    )
    db.commit()
    return task


def set_task_status(db: Session, task: Task, *, status: str, actor: str) -> Task:
    if status not in ("open", "done"):
        raise TaskValidationError("Ungueltiger Status.")
    previous = task.status
    task.status = status
    _log_audit(
        db,
        entity_type="Task",
        entity_id=task.id,
        event_type="task_status_changed",
        actor=actor,
        details=f"Status geaendert: {previous} -> {status}",
    )
    db.commit()
    return task


def duplicate_task(db: Session, task: Task, *, actor: str) -> Task:
    duplicate = Task(
        matter_id=task.matter_id,
        title=f"{task.title} (Kopie)",
        description=task.description,
        due_date=task.due_date,
        priority=task.priority,
        status="open",
    )
    db.add(duplicate)
    db.flush()
    _log_audit(
        db,
        entity_type="Task",
        entity_id=duplicate.id,
        event_type="task_created",
        actor=actor,
        details=f"Dupliziert von Aufgabe {task.id}",
    )
    db.commit()
    return duplicate


def delete_task(db: Session, task: Task, *, actor: str) -> None:
    # Audit-Event VOR dem Loeschen (gleiches Muster wie
    # app/clients/service.py::delete_client) - danach existiert die Aufgabe
    # nicht mehr, der Log-Eintrag ist der einzige verbleibende Beleg.
    _log_audit(
        db,
        entity_type="Task",
        entity_id=task.id,
        event_type="task_deleted",
        actor=actor,
        details=f"Aufgabe geloescht: {task.title}",
    )
    db.delete(task)
    db.commit()


# --- Deadline CRUD (Ergaenzung zu app/web/deadline_actions_router.py) ---


def update_deadline(
    db: Session,
    deadline: Deadline,
    *,
    source_text: str,
    due_date: date | None,
    priority: str | None,
    actor: str,
) -> Deadline:
    label = source_text.strip()
    if not label:
        raise TaskValidationError("Bezeichnung ist ein Pflichtfeld.")

    deadline.source_text = label
    deadline.due_date = due_date
    deadline.priority = _normalize_priority(priority)
    _log_audit(
        db,
        entity_type="Deadline",
        entity_id=deadline.id,
        event_type="deadline_updated",
        actor=actor,
        details=f"Frist bearbeitet: {label}",
    )
    db.commit()
    return deadline


def set_deadline_status(db: Session, deadline: Deadline, *, status: str, actor: str) -> Deadline:
    if status not in ("open", "done"):
        raise TaskValidationError("Ungueltiger Status.")
    previous = deadline.status
    deadline.status = status
    _log_audit(
        db,
        entity_type="Deadline",
        entity_id=deadline.id,
        event_type="deadline_status_changed",
        actor=actor,
        details=f"Status geaendert: {previous} -> {status}",
    )
    db.commit()
    return deadline


def duplicate_deadline(db: Session, deadline: Deadline, *, actor: str) -> Deadline:
    duplicate = Deadline(
        matter_id=deadline.matter_id,
        source_text=f"{_deadline_title(deadline)} (Kopie)",
        due_date=deadline.due_date,
        priority=deadline.priority,
        # Dieselbe Begruendung wie beim manuellen Anlegen
        # (deadline_actions_router.py): eine vom Anwalt selbst erzeugte
        # Kopie ist bereits die menschliche Pruefung, nicht "unreviewed".
        review_status="confirmed",
        status="open",
    )
    db.add(duplicate)
    db.flush()
    _log_audit(
        db,
        entity_type="Deadline",
        entity_id=duplicate.id,
        event_type="deadline_added_manually",
        actor=actor,
        details=f"Dupliziert von Frist {deadline.id}",
    )
    db.commit()
    return duplicate


def delete_deadline(db: Session, deadline: Deadline, *, actor: str) -> None:
    _log_audit(
        db,
        entity_type="Deadline",
        entity_id=deadline.id,
        event_type="deadline_deleted",
        actor=actor,
        details=f"Frist geloescht: {_deadline_title(deadline)}",
    )
    db.delete(deadline)
    db.commit()
