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

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_or_404
from app.auth.permissions import require_login, require_role
from app.db.session import get_db
from app.models import AuditEvent, Deadline, Matter, Task, User
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

# Obergrenze fuer die Fristenliste (14.09.): in der realen
# Produktionsdatenbank lagen bereits 178 erkannte Fristen - ungebremst
# gerendert entsteht daraus eine unbrauchbare Endlosliste, die mit jeder
# weiteren verarbeiteten Akte waechst. Die Gesamtzahl wird weiterhin
# ehrlich angezeigt (siehe `deadline_total` im Seitenkontext), damit die
# Begrenzung nichts verschleiert.
_MAX_DEADLINES_SHOWN = 50

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


def _open_deadlines(db: Session) -> list[Deadline]:
    """ECHTER FUND (14.09., Gold-Workflow-Pruefung "Fristen/Aufgaben
    erkennen"): diese Seite heisst im Navigationspunkt "Aufgaben & Fristen",
    fragte aber AUSSCHLIESSLICH `Task` ab - und `Task` wird von KEINEM
    Code-Pfad der Anwendung je erzeugt (projektweit geprueft). Gleichzeitig
    lagen in der Produktionsdatenbank 178 real erkannte `Deadline`-
    Datensaetze, erzeugt vom produktiven `DeadlineAnalysisService`.
    Ergebnis: die Fristenuebersicht war strukturell IMMER leer, waehrend
    jede erkannte Frist nur noch ueber die einzelne Aktendetailseite
    auffindbar war. Fuer eine Kanzlei ist eine uebersehene Frist der
    folgenreichste Fehler ueberhaupt - deshalb werden die erkannten Fristen
    hier jetzt tatsaechlich angezeigt.

    Bewusst NUR lesend und bewusst OHNE `rejected`: eine vom Anwalt
    ausdruecklich verworfene Frist soll nicht weiter als offener Punkt
    erscheinen. `unreviewed` UND `confirmed` bleiben sichtbar - eine
    bestaetigte Frist ist der wichtigste Termin ueberhaupt, keine
    erledigte Aufgabe (ein Erledigt-Status existiert fuer Fristen derzeit
    nicht; das waere eine eigene, separat zu planende Funktion)."""
    return (
        db.query(Deadline)
        .options(joinedload(Deadline.matter).joinedload(Matter.client))
        .filter(Deadline.review_status != "rejected")
        # Frueheste zuerst: ueberfaellige und unmittelbar anstehende Fristen
        # stehen damit oben - genau die, die Handlungsbedarf ausloesen.
        # Fristen ohne Datum ganz ans Ende (kein Termin = kein Zeitdruck).
        .order_by(Deadline.due_date.is_(None), Deadline.due_date.asc())
        .limit(_MAX_DEADLINES_SHOWN)
        .all()
    )


@router.get("", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tasks = _open_tasks(db)
    deadlines = _open_deadlines(db)
    deadline_total = (
        db.query(Deadline).filter(Deadline.review_status != "rejected").count()
    )
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Aufgaben & Fristen",
        "tasks": tasks,
        "deadlines": deadlines,
        "deadline_total": deadline_total,
        "today": date.today(),
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }
    return templates.TemplateResponse(request, "tasks.html", context)


@router.post("/{deadline_id}/review")
def review_deadline(
    deadline_id: str,
    status: str = Form(...),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Bestaetigt oder verwirft eine erkannte Frist (17.09., Owner-Direktive
    §5/§6/§10 "Missing core workflow functionality"). ECHTER FUND: der
    "Anwalt prueft"-Schritt des Gold-Workflows fuer Fristen existierte im
    Datenmodell (`Deadline.review_status`, siehe app/models/deadline.py)
    und in der Anzeige (`_labels.html::deadline_status_tag`) bereits
    vollstaendig, aber es gab PROJEKTWEIT keinen einzigen Schreibpfad, der
    ihn tatsaechlich aendert - weder hier noch in der read-only REST-API
    (app/api/routers/tasks.py). Jede erkannte Frist blieb dadurch fuer
    immer "unreviewed", unabhaengig davon, ob der Anwalt sie laengst
    geprueft hatte. Reines FALL-1 "anbinden" - keine neue Architektur,
    kein neues Datenmodell.

    `require_role()` ohne explizite Rolle/Berechtigung (nicht
    `PERM_CLAUDE_CALL`) bewusst: dies ist keine KI-/Kostenaktion, sondern
    eine reine menschliche Pruefentscheidung - jeder eingeloggte Nutzer
    darf sie treffen, GENAU wie das Lesen der Seite selbst. `require_role()`
    statt `require_login` ist hier trotzdem noetig: nur Ersteres prueft
    auch den CSRF-Token (siehe dessen Docstring "prueft IN DIESER
    REIHENFOLGE Login -> CSRF -> Berechtigung") - ein zustandsveraenderndes
    POST OHNE CSRF-Pruefung waere eine echte Sicherheitsluecke."""
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
    # Zaehlt Aufgaben UND offene Fristen (14.09.): der Navigationspunkt
    # heisst "Aufgaben & Fristen", die Zahl daneben muss denselben Umfang
    # abbilden wie die Seite selbst - vorher zaehlte sie nur `Task` und
    # stand deshalb dauerhaft auf 0, obwohl real erkannte Fristen offen waren.
    count = db.query(Task).filter(Task.status == "open").count() + db.query(
        Deadline
    ).filter(Deadline.review_status != "rejected").count()
    context = {"request": request, "count": count}
    return templates.TemplateResponse(request, "partials/tasks_badge.html", context)
