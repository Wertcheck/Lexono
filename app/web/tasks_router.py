"""Dashboard-Router für "Aufgaben & Fristen".

Vollstaendig neu aufgebaut (03.10., Owner-Direktive "AUFGABEN & FRISTEN",
Referenzabgleich `18_akte_dokumente_detail.png`) - siehe app/tasks/
service.py fuer die eigentliche Listen-/CRUD-Logik und dessen Modul-
Docstring fuer die echten, beim Bestandsabgleich gefundenen Luecken
(Task wurde projektweit nie erzeugt; Deadline hatte keine Bearbeiten-/
Duplizieren-/Loeschen-/Erledigt-Aktion).

`review_deadline`/`tasks_badge` bleiben UNVERAENDERT (bereits bestehende,
verifizierte Routen - siehe jeweiligen Docstring unten)."""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_login, require_role
from app.db.session import get_db
from app.documents.rendering import document_file_size_label
from app.models import AuditEvent, Deadline, Document, Matter, Task, User
from app.tasks.service import (
    ITEM_TYPES,
    PRIORITY_SUGGESTIONS,
    TaskListItem,
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
from app.web.template_paths import TEMPLATES_DIR

#: Erlaubte Zielwerte fuer die manuelle Fristen-Pruefung (17.09., Owner-
#: Direktive §5/§6/§10 - siehe app/models/deadline.py: "unreviewed /
#: confirmed / rejected - niemals automatisch 'confirmed'"). Der
#: Wertebereich existierte bereits vollstaendig (Datenmodell + Anzeige,
#: siehe _labels.html::deadline_status_tag), nur die tatsaechliche
#: Pruef-AKTION fehlte projektweit - weder im Dashboard noch in der
#: read-only REST-API (app/api/routers/tasks.py) gab es je einen
#: Schreibpfad dafuer. "unreviewed" bewusst NICHT als Zielwert erlaubt
#: (kein sinnvoller manueller "Rueckgaengig"-Fall in dieser Iteration,
#: kleinster korrekter Umfang).
_ALLOWED_REVIEW_TARGETS = {"confirmed", "rejected"}

_ALLOWED_PAGE_SIZES = (10, 20, 50)

router = APIRouter(prefix="/dashboard/tasks", tags=["dashboard-tasks"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _parse_due_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ungültiges Datum.") from exc


def _relative_due_text(due_date: date | None, today: date) -> str | None:
    """Nachvollziehbare relative Zusatzangabe ("in 18 Tagen"/"vor 3 Tagen"/
    "heute") - echte Datumsberechnung, kein hartcodierter Text (ausdrueck-
    liche Vorgabe der Direktive §3.D)."""
    if due_date is None:
        return None
    delta = (due_date - today).days
    if delta == 0:
        return "heute"
    if delta == 1:
        return "morgen"
    if delta == -1:
        return "gestern"
    if delta > 1:
        if delta >= 60:
            months = round(delta / 30)
            return f"in {months} Monat{'en' if months != 1 else ''}"
        return f"in {delta} Tagen"
    overdue = -delta
    if overdue >= 60:
        months = round(overdue / 30)
        return f"vor {months} Monat{'en' if months != 1 else ''}"
    return f"vor {overdue} Tagen"


def _deadline_source_label(deadline: Deadline) -> str:
    """Woher die Frist stammt - direkt aus den bereits bestehenden Quellen-
    feldern abgeleitet (app/models/deadline.py), nichts Erfundenes."""
    if deadline.document_id:
        return "Dokumentanalyse (KI)"
    if deadline.message_id:
        return "E-Mail-Analyse (KI)"
    return "Manuell"


def _creation_audit_event(db: Session, *, entity_type: str, entity_id: str) -> AuditEvent | None:
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.created_at.asc())
        .first()
    )


def _created_by_label(event: AuditEvent | None) -> str | None:
    """Echter, aus dem Audit-Log abgeleiteter Ersteller - KEIN erfundener
    Name, falls kein Audit-Event existiert (z. B. Synthetic-Data-Zeilen
    ohne Audit-Spur) gibt es ehrlich "–" statt einer Annahme."""
    if event is None:
        return None
    if event.actor == "system":
        return "System (automatische Erkennung)"
    return event.actor


def _audit_trail(db: Session, *, entity_type: str, entity_id: str) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.created_at.desc())
        .all()
    )


def _selected_item_context(db: Session, *, selected: str, selected_type: str) -> dict | None:
    if selected_type == "task":
        task = db.get(Task, selected)
        if task is None:
            return None
        created_event = _creation_audit_event(db, entity_type="Task", entity_id=task.id)
        return {
            "item_type": "task",
            "id": task.id,
            "title": task.title,
            "due_date": task.due_date,
            "priority": task.priority,
            "status": task.status,
            "matter": task.matter,
            "description": task.description,
            "source_label": "Manuell",
            "created_by": _created_by_label(created_event),
            "created_at": task.created_at,
            "document": None,
            "document_size": None,
            "task": task,
            "deadline": None,
            "audit_events": _audit_trail(db, entity_type="Task", entity_id=task.id),
        }
    if selected_type == "deadline":
        deadline = db.get(Deadline, selected)
        if deadline is None:
            return None
        created_event = _creation_audit_event(db, entity_type="Deadline", entity_id=deadline.id)
        document = db.get(Document, deadline.document_id) if deadline.document_id else None
        return {
            "item_type": "deadline",
            "id": deadline.id,
            "title": deadline.source_text or "Frist ohne Bezeichnung",
            "due_date": deadline.due_date,
            "priority": deadline.priority,
            "status": deadline.status,
            "matter": deadline.matter,
            "description": deadline.reasoning,
            "source_label": _deadline_source_label(deadline),
            "created_by": _created_by_label(created_event),
            "created_at": deadline.created_at,
            "document": document,
            # ECHTE DATEIGROESSE (07.10., Owner-Direktive "AUFGABEN & FRISTEN
            # - REFERENZABGLEICH"): wiederverwendet exakt denselben, bereits
            # bestehenden Helfer wie die Mandanten-Detailseite/Chat-
            # Dokumentvorschau (app/documents/rendering.py::
            # document_file_size_label) - liest die ECHTE Dateigroesse vom
            # Dateisystem, KEIN neues Datenbankfeld/keine Migration noetig.
            "document_size": document_file_size_label(document.file_path) if document else None,
            "task": None,
            "deadline": deadline,
            "audit_events": _audit_trail(db, entity_type="Deadline", entity_id=deadline.id),
        }
    return None


def _build_page_numbers(page: int, total_pages: int) -> list[int | str]:
    """Identische Logik wie app/web/clients_router.py::_build_page_numbers
    (kompakte Seitenzahl-Liste mit "…"-Ellipsen) - bewusst hier dupliziert
    statt in ein gemeinsames Modul extrahiert, um das bereits verifizierte
    Verhalten der Mandanten-/Akten-Seiten in dieser Aufgabe nicht
    anzutasten (kleinstes robustes Risiko, etabliertes Muster)."""
    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    window = {1, total_pages, page - 1, page, page + 1}
    window = {p for p in window if 1 <= p <= total_pages}
    result: list[int | str] = []
    for p in range(1, total_pages + 1):
        if p in window:
            result.append(p)
        elif result and result[-1] != "…":
            result.append("…")
    return result


def _open_matters(db: Session) -> list[Matter]:
    """Fuer das "Alle Akten"-Filter-Dropdown - alle nicht geloeschten Akten,
    gleiches Muster wie matters_list.html's Mandanten-Filter (alle
    moeglichen Werte, nicht nur die mit vorhandenen Aufgaben/Fristen)."""
    return (
        db.query(Matter)
        .filter(Matter.deleted_at.is_(None))
        .order_by(Matter.title.asc())
        .all()
    )


@router.get("", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    q: str = "",
    item_type: str = "",
    priority: str = "",
    matter_id: str = "",
    status: str = "open",
    sort: str = "due_asc",
    page: int = 1,
    page_size: int = 10,
    selected: str = "",
    selected_type: str = "",
    error: str | None = None,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    item_type_value = item_type if item_type in ITEM_TYPES else None
    priority_value = priority or None
    matter_id_value = matter_id or None
    if page_size not in _ALLOWED_PAGE_SIZES:
        page_size = 10

    total_count = count_task_items(
        db,
        search=q or None,
        item_type=item_type_value,
        priority=priority_value,
        matter_id=matter_id_value,
        status=status,
    )
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(max(page, 1), total_pages)

    items: list[TaskListItem] = list_task_items(
        db,
        search=q or None,
        item_type=item_type_value,
        priority=priority_value,
        matter_id=matter_id_value,
        status=status,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    today = date.today()
    due_texts = {item.id: _relative_due_text(item.due_date, today) for item in items}

    selected_item = None
    if selected and selected_type:
        selected_item = _selected_item_context(db, selected=selected, selected_type=selected_type)
        if selected_item is not None:
            selected_item["due_text"] = _relative_due_text(selected_item["due_date"], today)

    # Query-Strings fuer Zeilen-/Pagination-/Tab-Links (03.10.): echtes
    # `urlencode` statt manueller String-Verkettung (wie z. B. in
    # clients_list.html/matters_list.html) - fuer eine NEU gebaute Seite
    # kein Grund, denselben technisch unsauberen, dort nur aus Konsistenz-
    # gruenden unveraenderten Ansatz zu wiederholen.
    def _qs(**overrides: object) -> str:
        params = {
            "q": q,
            "item_type": item_type,
            "priority": priority,
            "matter_id": matter_id,
            "status": status,
            "sort": sort,
            "page_size": page_size,
            "page": page,
        }
        params.update(overrides)
        return urlencode({k: v for k, v in params.items() if v not in (None, "")})

    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Aufgaben & Fristen",
        # Top-Level-Navigationsseite wie "Akten" (siehe matters_router.py
        # fuer dasselbe Muster) - unterdrueckt den sonst von base.html auf
        # jeder Unterseite ergaenzten "Zurueck"-Pfeil (03.10., Owner-
        # Direktive "AUFGABEN & FRISTEN: PRAEZISE VISUELLE KORREKTUR",
        # per Soll-Ist-Vergleich mit der Referenz bewiesen: die Referenz
        # zeigt dort KEINEN Zurueck-Pfeil). Anders als bei Akten gibt es
        # hier KEINE separate Detail-ROUTE mit demselben `active_nav`-Wert,
        # die den Pfeil weiterhin braeuchte (das Detailpanel laeuft ueber
        # denselben `/dashboard/tasks`-Pfad per Query-Parameter) - deshalb
        # hier unbedingt, nicht wie bei Akten auf die Listen-Route
        # beschraenkt. #}
        "hide_back_link": True,
        "items": items,
        "due_texts": due_texts,
        "today": today,
        "search": q,
        "item_type": item_type,
        "priority": priority,
        "matter_id": matter_id,
        "status": status,
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "priority_suggestions": PRIORITY_SUGGESTIONS,
        "matters": _open_matters(db),
        "selected_item": selected_item,
        "selected": selected,
        "selected_type": selected_type,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "error": error,
        "base_qs": _qs(),
        "qs_tab_list": _qs(status="open", item_type="", page=1),
        "qs_tab_fristen": _qs(status="open", item_type="deadline", page=1),
        "qs_tab_erledigt": _qs(status="done", page=1),
        "qs_prev_page": _qs(page=page - 1),
        "qs_next_page": _qs(page=page + 1),
        "page_numbers": _build_page_numbers(page, total_pages),
        "qs_for_page": {p: _qs(page=p) for p in _build_page_numbers(page, total_pages) if isinstance(p, int)},
        "is_tab_fristen": status != "done" and item_type == "deadline",
        "is_tab_erledigt": status == "done",
    }
    return templates.TemplateResponse(request, "tasks.html", context)


@router.post("/create")
def create_task_action(
    request: Request,
    matter_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    due_date: str = Form(""),
    priority: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    try:
        task = create_task(
            db,
            matter_id=matter.id,
            title=title,
            description=description,
            due_date=_parse_due_date(due_date),
            priority=priority,
            actor=current_user.email,
        )
    except TaskValidationError as exc:
        return RedirectResponse(url=f"/dashboard/tasks?error={exc}", status_code=303)
    return RedirectResponse(
        url=f"/dashboard/tasks?selected={task.id}&selected_type=task", status_code=303
    )


@router.post("/task/{task_id}/update")
def update_task_action(
    task_id: str,
    title: str = Form(...),
    description: str = Form(""),
    due_date: str = Form(""),
    priority: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    task = get_or_404(db, Task, task_id, "Aufgabe")
    try:
        update_task(
            db,
            task,
            title=title,
            description=description,
            due_date=_parse_due_date(due_date),
            priority=priority,
            actor=current_user.email,
        )
    except TaskValidationError as exc:
        return RedirectResponse(url=f"/dashboard/tasks?error={exc}", status_code=303)
    return RedirectResponse(
        url=f"/dashboard/tasks?selected={task.id}&selected_type=task", status_code=303
    )


@router.post("/task/{task_id}/status")
def set_task_status_action(
    task_id: str,
    status: str = Form(...),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    task = get_or_404(db, Task, task_id, "Aufgabe")
    try:
        set_task_status(db, task, status=status, actor=current_user.email)
    except TaskValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"/dashboard/tasks?selected={task.id}&selected_type=task", status_code=303
    )


@router.post("/task/{task_id}/duplicate")
def duplicate_task_action(
    task_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    task = get_or_404(db, Task, task_id, "Aufgabe")
    duplicate = duplicate_task(db, task, actor=current_user.email)
    return RedirectResponse(
        url=f"/dashboard/tasks?selected={duplicate.id}&selected_type=task", status_code=303
    )


@router.post("/task/{task_id}/delete")
def delete_task_action(
    task_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    task = get_or_404(db, Task, task_id, "Aufgabe")
    delete_task(db, task, actor=current_user.email)
    return RedirectResponse(url="/dashboard/tasks", status_code=303)


@router.post("/deadline/{deadline_id}/update")
def update_deadline_action(
    deadline_id: str,
    source_text: str = Form(...),
    due_date: str = Form(""),
    priority: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    deadline = get_or_404(db, Deadline, deadline_id, "Frist")
    try:
        update_deadline(
            db,
            deadline,
            source_text=source_text,
            due_date=_parse_due_date(due_date),
            priority=priority,
            actor=current_user.email,
        )
    except TaskValidationError as exc:
        return RedirectResponse(url=f"/dashboard/tasks?error={exc}", status_code=303)
    return RedirectResponse(
        url=f"/dashboard/tasks?selected={deadline.id}&selected_type=deadline", status_code=303
    )


@router.post("/deadline/{deadline_id}/status")
def set_deadline_status_action(
    deadline_id: str,
    status: str = Form(...),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    deadline = get_or_404(db, Deadline, deadline_id, "Frist")
    try:
        set_deadline_status(db, deadline, status=status, actor=current_user.email)
    except TaskValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"/dashboard/tasks?selected={deadline.id}&selected_type=deadline", status_code=303
    )


@router.post("/deadline/{deadline_id}/duplicate")
def duplicate_deadline_action(
    deadline_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    deadline = get_or_404(db, Deadline, deadline_id, "Frist")
    duplicate = duplicate_deadline(db, deadline, actor=current_user.email)
    return RedirectResponse(
        url=f"/dashboard/tasks?selected={duplicate.id}&selected_type=deadline", status_code=303
    )


@router.post("/deadline/{deadline_id}/delete")
def delete_deadline_action(
    deadline_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    deadline = get_or_404(db, Deadline, deadline_id, "Frist")
    delete_deadline(db, deadline, actor=current_user.email)
    return RedirectResponse(url="/dashboard/tasks", status_code=303)


@router.post("/{deadline_id}/review")
def review_deadline(
    deadline_id: str,
    status: str = Form(...),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Bestaetigt oder verwirft eine erkannte Frist (17.09., Owner-Direktive
    §5/§6/§10 "Missing core workflow functionality"). UNVERAENDERT aus der
    vorherigen Fassung dieser Datei - siehe dortigen Docstring-Verlauf in
    der Git-Historie fuer die volle Herleitung."""
    if status not in _ALLOWED_REVIEW_TARGETS:
        raise HTTPException(status_code=400, detail="Ungueltiger Pruefstatus.")

    deadline = get_or_404(db, Deadline, deadline_id, "Frist")
    previous_status = deadline.review_status
    deadline.review_status = status
    db.add(
        AuditEvent(
            entity_type="Deadline",
            entity_id=deadline.id,
            event_type="deadline_review_status_changed",
            actor=current_user.email,
            details=f"Pruefstatus geaendert: {previous_status} -> {status}",
        )
    )
    db.commit()

    return RedirectResponse(url="/dashboard/tasks", status_code=303)


@router.get("/badge", response_class=HTMLResponse)
def tasks_badge(
    request: Request,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Zaehlt offene Aufgaben UND offene Fristen (unveraendertes Prinzip,
    # jetzt ueber Deadline.status statt review_status != rejected allein -
    # eine bestaetigte, aber bereits erledigte Frist soll nicht mehr als
    # "offener Punkt" in der Badge-Zahl erscheinen).
    count = db.query(Task).filter(Task.status == "open").count() + db.query(
        Deadline
    ).filter(Deadline.review_status != "rejected", Deadline.status == "open").count()
    context = {"request": request, "count": count}
    return templates.TemplateResponse(request, "partials/tasks_badge.html", context)
