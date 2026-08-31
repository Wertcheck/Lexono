"""Dashboard-Router (Prompt 22 – Dashboard-Inbox).

Serverseitig gerendert (Jinja2 + HTMX, siehe ARCHITECTURE.md §4/Entscheidung
Prompt 22). Bewusst getrennt von `app/api/` (JSON-API): dieser Router liefert
HTML fuer Menschen im Browser, `app/api/` liefert JSON fuer Programme/
zukuenftige Integrationen. Beide teilen sich dieselbe `get_db`-Abhaengigkeit
und dieselben SQLAlchemy-Modelle, aber keine Code-Duplikation der
Query-Logik ist hier bewusst in Kauf genommen: die Web-Views brauchen andere
Joins (z. B. `Message.matter` fuer die Akten-Tab-Badges) als die schlanken
API-Listen-Endpunkte.

WICHTIG: dieselbe Grundregel wie im gesamten Projekt gilt auch hier - noch
keine Authentifizierung (folgt Prompt 26), siehe Sidebar-Fussnote in
base.html.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_or_404
from app.auth.permissions import require_login
from app.db.session import get_db
from app.models import Document, Message, User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

templates = Jinja2Templates(directory=TEMPLATES_DIR)

_FILTER_OPTIONS: list[tuple[str, str]] = [
    ("all", "Alle"),
    ("unmatched", "Nicht zugeordnet"),
    ("inbound", "Eingehend"),
    ("outbound", "Ausgehend"),
]
_VALID_FILTER_KEYS = {key for key, _ in _FILTER_OPTIONS}


def _apply_filter(query, filter_key: str):
    """Wendet den gewaehlten Inbox-Filter auf die Query an.

    Unbekannte/fehlende Filter-Keys fallen sicher auf "all" zurueck, statt
    einen Fehler zu werfen - ein manipulierter Query-Parameter darf die
    Ansicht bestenfalls auf "alle Nachrichten" zuruecksetzen, nie zu einem
    Serverfehler fuehren.
    """
    if filter_key == "unmatched":
        return query.filter(Message.matter_id.is_(None))
    if filter_key == "inbound":
        return query.filter(Message.direction == "inbound")
    if filter_key == "outbound":
        return query.filter(Message.direction == "outbound")
    return query


def _load_messages(db: Session, filter_key: str) -> list[Message]:
    query = db.query(Message).options(joinedload(Message.matter))
    query = _apply_filter(query, filter_key)
    return query.order_by(Message.created_at.desc()).limit(100).all()


def _load_detail_context(db: Session, message_id: str) -> dict:
    message = get_or_404(db, Message, message_id, "Nachricht")
    documents = (
        db.query(Document).filter(Document.message_id == message_id).all()
    )
    return {"message": message, "documents": documents}


@router.get("", response_class=HTMLResponse)
def dashboard_root(
    request: Request, current_user: User = Depends(require_login)
) -> HTMLResponse:
    """`/dashboard` selbst zeigt keine eigene Seite - leitet auf die Chat-
    Startseite weiter (UI-Überarbeitung: Chat ist die zentrale
    Arbeitsoberfläche, siehe app/web/chat_router.py), vormals der
    Posteingang."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/dashboard/chat")


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(
    request: Request,
    filter: str = "all",  # noqa: A002 - passender, konsistenter Query-Param-Name
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    filter_key = filter if filter in _VALID_FILTER_KEYS else "all"
    messages = _load_messages(db, filter_key)
    total_count = db.query(Message).count()
    unmatched_count = db.query(Message).filter(Message.matter_id.is_(None)).count()

    context = {
        "request": request,
        "active_nav": "Posteingang",
        "messages": messages,
        "filter_options": _FILTER_OPTIONS,
        "active_filter": filter_key,
        "total_count": total_count,
        "unmatched_count": unmatched_count,
        "message": None,
        "documents": [],
        "active_message_id": None,
        "current_user": current_user,
        # Fuer partials/onboarding_banner.html (nur bei leerem Posteingang
        # sichtbar) - dessen Formulare posten seit 20.08. echt gegen
        # app/web/settings_router.py, brauchen also einen echten CSRF-Token.
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }
    return templates.TemplateResponse(request, "inbox.html", context)


@router.get("/inbox/list", response_class=HTMLResponse)
def inbox_list_partial(
    request: Request,
    filter: str = "all",  # noqa: A002
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """HTMX-Partial: nur die gefilterte Nachrichtenliste, fuer den
    Filter-Tab-Wechsel ohne vollen Seiten-Reload."""
    filter_key = filter if filter in _VALID_FILTER_KEYS else "all"
    messages = _load_messages(db, filter_key)
    context = {
        "request": request,
        "messages": messages,
        "active_message_id": None,
    }
    return templates.TemplateResponse(request, "partials/message_list.html", context)


@router.get("/inbox/{message_id}", response_class=HTMLResponse)
def inbox_message_page(
    request: Request,
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """Volle Seite mit vorausgewaehlter Nachricht - ermoeglicht direktes
    Verlinken/Neuladen eines einzelnen Postfach-Eintrags (siehe
    `hx-push-url` in partials/message_row.html)."""
    detail_context = _load_detail_context(db, message_id)
    messages = _load_messages(db, "all")
    total_count = db.query(Message).count()
    unmatched_count = db.query(Message).filter(Message.matter_id.is_(None)).count()

    context = {
        "request": request,
        "active_nav": "Posteingang",
        "messages": messages,
        "filter_options": _FILTER_OPTIONS,
        "active_filter": "all",
        "total_count": total_count,
        "unmatched_count": unmatched_count,
        "active_message_id": message_id,
        "current_user": current_user,
        **detail_context,
    }
    return templates.TemplateResponse(request, "inbox.html", context)


@router.get("/inbox/{message_id}/detail", response_class=HTMLResponse)
def inbox_message_detail_partial(
    request: Request,
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """HTMX-Partial: nur das Detail-Panel, fuer den Klick auf eine
    Nachrichten-Zeile ohne vollen Seiten-Reload."""
    detail_context = _load_detail_context(db, message_id)
    context = {"request": request, **detail_context}
    return templates.TemplateResponse(
        request, "partials/message_detail.html", context
    )
