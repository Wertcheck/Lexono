"""Dashboard-Router für "Aufgaben" (01.09., Referenzbild-Sidebar-Punkt).

Löst einen echten, vorher unbekannten Gap: `app/models/task.py::Task`
(Akten-Aufgaben mit `due_date`/`status`) existierte bereits als
Datenmodell, hatte aber KEINE eigene Dashboard-Seite - nur die reine
Existenz des Modells, keine Web-Route. Bewusst NUR eine lesende Übersicht
(kein CRUD in dieser Iteration, Umfang bleibt klein) - Anlegen/Bearbeiten
von Aufgaben bleibt vorerst der Akten-Detailansicht vorbehalten, falls
dort bereits vorhanden, sonst ein separat zu planender nächster Schritt.

`tasks_badge` liefert NUR die Zahl offener Aufgaben als HTML-Fragment,
per HTMX lazy in die Sidebar geladen (dasselbe Muster wie
monitoring_router.py::budget_badge/update_badge) - kein neuer Router muss
dafür angefasst werden, um diesen Wert in seinen eigenen Kontext
aufzunehmen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth.permissions import require_login
from app.db.session import get_db
from app.models import Matter, Task, User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/tasks", tags=["dashboard-tasks"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _open_tasks(db: Session) -> list[Task]:
    return (
        db.query(Task)
        .options(joinedload(Task.matter).joinedload(Matter.client))
        .filter(Task.status == "open")
        .order_by(Task.due_date.is_(None), Task.due_date.asc())
        .all()
    )


@router.get("", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tasks = _open_tasks(db)
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Aufgaben",
        "tasks": tasks,
    }
    return templates.TemplateResponse(request, "tasks.html", context)


@router.get("/badge", response_class=HTMLResponse)
def tasks_badge(
    request: Request,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    count = db.query(Task).filter(Task.status == "open").count()
    context = {"request": request, "count": count}
    return templates.TemplateResponse(request, "partials/tasks_badge.html", context)
