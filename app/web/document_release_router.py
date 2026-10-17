"""Datenschutz & Dokumentfreigabe (§62) - Einstellung, Anzeigepraeferenz, manuelle Freigabe, Pruefung der
Pseudonymisierung. Fachlogik in app/documents/release.py (serverseitige Durchsetzung beim Kontextaufbau).

Berechtigungen (bestehendes Modell, keine neue Rolle):
- Seite ansehen, Pruefseite oeffnen, eigene Anzeigepraeferenz: jede angemeldete Rolle.
- Freigabemodus aendern und Dokumente manuell freigeben: `PERM_PRIVACY_DECISION_MANAGE` (Admin, Anwalt) - dasselbe
  Recht wie fuer gespeicherte Datenschutz-Entscheidungen (§52).
Alle Routen sind lokal: kein Cloud-Aufruf, auch nicht auf der Pruefseite (zentrale Pruefung lokal, ohne Versand).
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import PERM_PRIVACY_DECISION_MANAGE, has_permission, require_login, require_role
from app.db.session import get_db
from app.documents import release
from app.models import ChatConversation, Document, Matter, User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(tags=["dashboard-document-release"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)

SETTINGS_PATH = "/dashboard/privacy/document-release"


def _matter_document(db: Session, matter_id: str, document_id: str) -> tuple[Matter, Document]:
    """Aktenisolation: Dokument muss zur (nicht geloeschten) Akte der URL gehoeren."""
    matter = db.get(Matter, matter_id)
    if matter is None or matter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Akte nicht gefunden")
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.matter_id == matter.id, Document.deleted_at.is_(None))
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht in dieser Akte")
    return matter, document


def _document_target(db: Session, matter: Matter, document: Document, return_to: str, user: User) -> str:
    """Rueckkehr nur in die Dokumentansicht der Akte oder eines EIGENEN Chats DIESER Akte (wie §56 protect-names)."""
    target = f"/dashboard/matters/{matter.id}/document/{document.id}"
    if return_to.startswith("chat:"):
        conversation = db.get(ChatConversation, return_to[len("chat:"):])
        if conversation is not None and conversation.matter_id == matter.id and conversation.user_id == user.id:
            target = f"/dashboard/chat/{conversation.id}/document/{document.id}"
    return target


@router.get(SETTINGS_PATH, response_class=HTMLResponse)
def document_release_settings(
    request: Request,
    saved: str = "",
    error: str = "",
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    mode = release.get_mode(db)
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Einstellungen",
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "mode": mode,
        "mode_label": release.MODE_LABELS[mode],
        "modes": [(key, release.MODE_LABELS[key], release.MODE_DESCRIPTIONS[key]) for key in release.MODES],
        "default_mode": release.DEFAULT_MODE,
        "can_manage": has_permission(current_user, PERM_PRIVACY_DECISION_MANAGE),
        "show_notice": release.show_auto_release_notice(current_user),
        "saved": saved == "1",
        "error": error[:200],
    }
    return templates.TemplateResponse(request, "document_release.html", context)


@router.post(f"{SETTINGS_PATH}/mode")
def change_document_release_mode(
    mode: str = Form(...),
    current_user: User = Depends(require_role(permission=PERM_PRIVACY_DECISION_MANAGE)),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        release.set_mode(db, mode, actor=current_user.email)
    except release.ReleaseError as exc:
        return RedirectResponse(url=f"{SETTINGS_PATH}?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url=f"{SETTINGS_PATH}?saved=1", status_code=303)


@router.post(f"{SETTINGS_PATH}/notice")
def change_auto_release_notice(
    show: str = Form("0"),
    return_to: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Nur die informative Meldung "automatisch freigegeben" - Freigabemodus, Pruefung und Verarbeitung bleiben
    unveraendert; Warnungen, Blockaden und erforderliche Freigaben werden nie ausgeblendet."""
    release.set_auto_release_notice(db, current_user, show=show == "1")
    # nur interne Ruecksprungziele (keine offene Weiterleitung)
    target = return_to if return_to.startswith("/dashboard/") and "//" not in return_to else f"{SETTINGS_PATH}?saved=1"
    return RedirectResponse(url=target, status_code=303)


@router.post("/dashboard/matters/{matter_id}/document/{document_id}/release")
def release_document(
    matter_id: str,
    document_id: str,
    text_version: str = Form(""),
    return_to: str = Form(""),
    current_user: User = Depends(require_role(permission=PERM_PRIVACY_DECISION_MANAGE)),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter, document = _matter_document(db, matter_id, document_id)
    target = _document_target(db, matter, document, return_to, current_user)
    try:
        release.release_manually(db, document, text_version=text_version, user=current_user)
    except release.ReleaseError as exc:
        return RedirectResponse(url=f"{target}?release_error={quote(str(exc))}#document-release", status_code=303)
    return RedirectResponse(url=f"{target}?released=1#document-release", status_code=303)


@router.get("/dashboard/matters/{matter_id}/document/{document_id}/pseudonymisierung", response_class=HTMLResponse)
def review_pseudonymization(
    request: Request,
    matter_id: str,
    document_id: str,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Human in the Loop: zeigt LOKAL, was die zentrale Pruefung aus dem Dokument machen wuerde (Platzhalter und
    zugeordnete Originalwerte) - dieselbe Pruefung wie beim Versand, Modus "mask", ohne Versand und ohne Protokoll der
    Inhalte. Rueckmeldung ueber die bestehenden Wege (Namen schuetzen, Qualitaetsmeldung); nichts lockert Regeln."""
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.privacy.decisions import load_decisions
    from app.privacy.gateway import ClaudePrivacyGateway

    matter, document = _matter_document(db, matter_id, document_id)
    status = release.release_status(db, document)
    mappings, pseudonymized, blocked_reasons = [], "", []
    if (document.extracted_text or "").strip():
        result = ClaudePrivacyGateway().prepare_request(
            purpose="formulate_draft", sachverhalt=document.extracted_text,
            known_entities=RuleBasedLocalAIProvider()._build_known_entities(matter.id, matter, db),
            privacy_decisions=load_decisions(db, matter_id=matter.id, conversation_id=None), review_mode="mask",
        )
        mappings = sorted({(m.placeholder, m.category, m.original_value) for m in result.mappings})
        pseudonymized = result.payload.anonymisierter_sachverhalt if result.allowed and result.payload else ""
        blocked_reasons = [] if result.allowed else result.reasons
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Akten",
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "matter": matter,
        "document": document,
        "status": status,
        "mappings": mappings,
        "pseudonymized": pseudonymized,
        "blocked_reasons": blocked_reasons,
        "can_manage": has_permission(current_user, PERM_PRIVACY_DECISION_MANAGE),
    }
    return templates.TemplateResponse(request, "document_pseudonymization.html", context)
