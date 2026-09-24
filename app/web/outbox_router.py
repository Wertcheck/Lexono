"""Dashboard-Router für den Postausgang (Prompt 25, Zugriffsschutz Prompt 26).

GRUNDREGEL (CLAUDE.md, wörtlich): "Keine automatische externe
Kommunikation (insb. E-Mail-Versand) ohne explizite Freigabe." Dieser
Router hat KEINE Versandfähigkeit - siehe app/outbox/service.py.
`mark_sent` bestätigt nur eine manuelle, bereits AUSSERHALB des Systems
erfolgte Handlung. NUR Anwalt/Admin dürfen das (Rechte-Matrix,
PERM_OUTBOX_MARK_SENT) - Mitarbeiter dürfen den Postausgang lesen, aber
nicht als versendet markieren.

Getrennt von app/web/drafts_router.py gehalten (eigene, klar abgegrenzte
Zuständigkeit), analog zur bestehenden Modultrennung (Inbox/Drafts/Outbox
je eigener Router).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_or_404
from app.auth.permissions import (
    PERM_OUTBOX_MARK_SENT,
    has_permission,
    require_login,
    require_role,
)
from app.db.session import get_db
from app.models import Draft, Matter, OutboxEntry, User
from app.outbox.service import OutboxService
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/outbox", tags=["dashboard-outbox"])

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("", response_class=HTMLResponse)
def outbox_list_page(
    request: Request,
    status: str = "pending",
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    # Mandant mitladen (14.09.) - die Liste zeigt ihn jetzt als eigene
    # Spalte; ohne dieses dritte `joinedload` waere das eine N+1-Abfrage
    # pro Zeile.
    query = db.query(OutboxEntry).options(
        joinedload(OutboxEntry.draft).joinedload(Draft.matter).joinedload(Matter.client)
    )
    if status in ("pending", "sent"):
        query = query.filter(OutboxEntry.status == status)
    entries = query.order_by(OutboxEntry.created_at.desc()).all()

    context = {
        "request": request,
        "active_nav": "Postausgang",
        "entries": entries,
        "active_status": status,
        "error": error,
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "can_mark_sent": has_permission(current_user, PERM_OUTBOX_MARK_SENT),
    }
    return templates.TemplateResponse(request, "outbox_list.html", context)


@router.post("/{entry_id}/mark-sent")
def mark_sent(
    entry_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_OUTBOX_MARK_SENT)),
) -> RedirectResponse:
    """Bestätigt manuell, dass der Anwalt bereits (außerhalb dieses
    Systems) versendet hat. Löst selbst nichts aus. NUR Anwalt/Admin."""
    entry = get_or_404(db, OutboxEntry, entry_id, "Postausgang-Eintrag")
    try:
        OutboxService().mark_as_sent(entry, db, actor=current_user.email)
    except ValueError as exc:
        # Z. B. Doppelklick oder zwei parallel geöffnete Tabs - bereits
        # als versendet markiert. Sauberer Hinweis statt Serverfehler.
        return RedirectResponse(url=f"/dashboard/outbox?error={exc}", status_code=303)
    return RedirectResponse(url="/dashboard/outbox", status_code=303)
