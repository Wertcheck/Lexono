"""Dashboard-Router für den Rich-Text-Dokumenten-Editor (04.10., Owner-
Direktive "LEXONO - Dokumenten-Editor produktionsnah implementieren und
vollständig in den Chat-Workflow integrieren").

Bewusst eine EIGENE Datei statt app/web/drafts_router.py zu erweitern
(dieselbe Begründung wie app/web/schriftsatz_router.py-Moduldocstring:
"keine unnötigen Umbauten außerhalb des erforderlichen Umfangs" - die
bereits 700+ Zeilen lange Entwurfsprüfungs-/Freigabeseite bleibt
unberührt). `draft_detail.html` (die Entwurfsprüfung: Versionen,
Freigabe, Export, Review-Findings, Audit-Log) bleibt der "Viewer" -
dieser Router liefert ausschließlich den neuen "Editor" dazu, über
`/dashboard/drafts/{id}/edit`. Beide verweisen gegenseitig aufeinander
(siehe draft_detail.html/draft_editor.html).

Autosave/"Als Vorlage speichern"/Verwerfen eines KI-Vorschlags laufen
bewusst NICHT über volle Seiten-Redirects (anders als drafts_router.py -
siehe dessen Moduldocstring), sondern als schlanke JSON-Antworten für
AJAX-Aufrufe aus dem Editor (app_draft_editor.js) - der Editor selbst ist
eine Single-Page-artige Oberfläche (Statusleiste "gespeichert/speichert/
fehlgeschlagen" würde bei einem vollen Reload pro Tastendruck-Pause nicht
funktionieren). CSRF bleibt dabei UNVERÄNDERT Pflicht (`csrf_token` als
Form-Feld, siehe app/auth/permissions.py::require_role) - nur das
Antwortformat ist neu, nicht die Absicherung.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_or_404
from app.auth.permissions import (
    PERM_CLAUDE_CALL,
    PERM_DRAFT_APPROVE,
    PERM_DRAFT_MANUAL_EDIT,
    has_permission,
    require_login,
    require_role,
)
from app.db.session import get_db
from app.document_generator.template_service import DocumentTemplateService
from app.drafting.editor_service import AI_SUGGESTIONS, LEGAL_REVIEW_DISCLAIMER, EditorService
from app.drafting.versioning import AI_SUGGESTION_DISCARDED_STATUS, resolve_visible_draft
from app.export.letterhead import (
    address_and_contact_lines,
    has_letterhead_content,
    has_signature_content,
    image_exists,
)
from app.firm_profile import get_firm_profile
from app.models import Draft, Matter, User
from app.prompt_library.service import PromptTemplateService
from app.privacy.api_logger import friendly_block_message
from app.web.service_factory import (
    WritingProviderNotConfiguredError,
    get_editor_service,
    get_editor_service_for_ai_edit,
)
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/drafts", tags=["dashboard-draft-editor"])

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _load_chain_by_id(draft: Draft, db: Session) -> dict[str, Draft]:
    """Siehe app/web/drafts_router.py::_load_version_chain - hier wird nur
    die `{id: Draft}`-Abbildung derselben Entwurfslinie gebraucht (für
    `resolve_visible_draft`), keine vollständige, geordnete Kette."""
    all_matter_drafts = db.query(Draft).filter(Draft.matter_id == draft.matter_id).all()
    return {d.id: d for d in all_matter_drafts}


@router.get("/{draft_id}/edit", response_class=HTMLResponse)
def draft_editor_page(
    draft_id: str,
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    draft = get_or_404(db, Draft, draft_id, "Entwurf")

    # Ein verworfener KI-Vorschlag ist kein gültiger Editor-Einstiegspunkt
    # mehr (siehe resolve_visible_draft) - z. B. wenn ein Nutzer einen
    # bereits verworfenen Link erneut öffnet (Browser-Zurück nach
    # "Verwerfen"). Leitet ehrlich auf die zuletzt aktive Version um,
    # statt den verworfenen Stand trotzdem zu zeigen.
    if draft.status == AI_SUGGESTION_DISCARDED_STATUS:
        visible = resolve_visible_draft(draft, _load_chain_by_id(draft, db))
        if visible.id != draft.id:
            return RedirectResponse(url=f"/dashboard/drafts/{visible.id}/edit", status_code=303)

    matter = db.query(Matter).options(joinedload(Matter.client)).filter_by(id=draft.matter_id).first()
    prompt_templates = PromptTemplateService().list_templates(db)
    document_templates = DocumentTemplateService().list_templates(db)

    firm_profile = get_firm_profile(db)

    context = {
        "request": request,
        "active_nav": "Entwürfe zur Prüfung",
        "draft": draft,
        "matter": matter,
        "ai_suggestions": AI_SUGGESTIONS,
        "legal_review_disclaimer": LEGAL_REVIEW_DISCLAIMER,
        "prompt_templates": prompt_templates,
        "document_templates": document_templates,
        # Briefkopf-/Signatur-Vorschau: dieselben Helper wie draft_detail/Export
        # (das Template rendert sie nur, wenn diese Werte im Kontext stehen).
        "firm_profile": firm_profile,
        "show_letterhead": has_letterhead_content(firm_profile),
        "show_signature_block": has_signature_content(firm_profile),
        "firm_logo_exists": image_exists(firm_profile.logo_path),
        "firm_signature_exists": image_exists(firm_profile.signature_path),
        "firm_contact_lines": address_and_contact_lines(firm_profile),
        "error": error,
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "can_claude_call": has_permission(current_user, PERM_CLAUDE_CALL),
        "can_manual_edit": has_permission(current_user, PERM_DRAFT_MANUAL_EDIT),
        "can_approve": has_permission(current_user, PERM_DRAFT_APPROVE),
    }
    return templates.TemplateResponse(request, "draft_editor.html", context)


@router.post("/{draft_id}/autosave")
def autosave_draft(
    draft_id: str,
    content: str = Form(...),
    subject: str = Form(""),
    recipient: str = Form(""),
    content_format: str = Form("html"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_DRAFT_MANUAL_EDIT)),
    service: EditorService = Depends(get_editor_service),
) -> JSONResponse:
    """Autosave - KEIN neuer Versionssprung (siehe EditorService.
    autosave_draft). Antwortet ehrlich mit `saved: false`, wenn der
    Entwurf nicht mehr im Status "draft" ist (z. B. zwischenzeitlich in
    einem anderen Tab freigegeben/abgelehnt wurde) - der Editor zeigt das
    dann als "nicht gespeichert, Entwurf eingefroren" an, statt fälschlich
    einen Erfolg zu melden (CLAUDE.md: "Keine Fake-Vollständigkeit")."""
    draft = get_or_404(db, Draft, draft_id, "Entwurf")
    was_draft_status = draft.status == "draft"
    updated = service.autosave_draft(
        db,
        draft=draft,
        content=content,
        subject=subject.strip() or None,
        recipient=recipient.strip() or None,
        content_format=content_format,
        actor=current_user.email,
    )
    saved = was_draft_status
    return JSONResponse(
        {
            "saved": saved,
            "status": updated.status,
            "last_autosaved_at": (
                updated.last_autosaved_at.isoformat() if updated.last_autosaved_at else None
            ),
        }
    )


@router.post("/{draft_id}/ai-edit")
def apply_ai_edit(
    draft_id: str,
    instruction_text: str = Form(...),
    purpose: str = Form(...),
    selected_text: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLAUDE_CALL)),
    service: EditorService = Depends(get_editor_service_for_ai_edit),
) -> JSONResponse:
    """Löst EINE auswahlbewusste KI-Bearbeitung aus (siehe
    EditorService.apply_ai_suggestion) - erzeugt bei Erfolg eine neue,
    eingefrorene Draft-Version (nie eine Überschreibung der aktuellen),
    meldet sie aber dem Editor NUR als "Vorschlag" zurück (`accepted:
    false` im zurückgegebenen JSON gibt es bewusst nicht - Annahme/
    Verwerfen ist ein rein clientseitiger Navigations-/Discard-Schritt,
    siehe app_draft_editor.js und `discard_ai_edit` unten)."""
    draft = get_or_404(db, Draft, draft_id, "Entwurf")

    try:
        result = service.apply_ai_suggestion(
            db,
            draft=draft,
            instruction_text=instruction_text,
            purpose=purpose,
            actor=current_user.email,
            selected_text=selected_text or None,
        )
    except WritingProviderNotConfiguredError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=503)

    if not result.drafting_result.success or result.new_draft is None:
        safe_message = friendly_block_message(result.drafting_result.blocked_reasons)
        return JSONResponse(
            {"success": False, "error": f"KI-Bearbeitung blockiert: {safe_message}"},
            status_code=422,
        )

    return JSONResponse(
        {
            "success": True,
            "new_draft_id": result.new_draft.id,
            "new_draft_version": result.new_draft.version,
            "content": result.new_draft.content,
            "content_format": result.new_draft.content_format,
        }
    )


@router.post("/{draft_id}/ai-edit/discard")
def discard_ai_edit(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_DRAFT_MANUAL_EDIT)),
    service: EditorService = Depends(get_editor_service),
) -> JSONResponse:
    """Verwirft EINEN per `apply_ai_edit` erzeugten Vorschlag (`draft_id`
    hier = die ID der NEU erzeugten Vorschlagsversion, nicht die des
    Entwurfs, von dem aus der Vorschlag ausgelöst wurde) - siehe
    EditorService.discard_ai_suggestion. Die Zeile bleibt in der
    Historie erhalten (siehe dort), nur ihr Status ändert sich."""
    draft = get_or_404(db, Draft, draft_id, "Entwurf")
    service.discard_ai_suggestion(db, draft=draft, actor=current_user.email)
    return JSONResponse({"discarded": True})


@router.post("/{draft_id}/save-as-template")
def save_as_template(
    draft_id: str,
    name: str = Form(...),
    category: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_DRAFT_MANUAL_EDIT)),
    service: EditorService = Depends(get_editor_service),
) -> JSONResponse:
    """"Als Vorlage speichern" - echte Wiederverwendung von
    `DocumentTemplateService` (siehe EditorService.save_as_template)."""
    draft = get_or_404(db, Draft, draft_id, "Entwurf")
    if not name.strip():
        return JSONResponse({"success": False, "error": "Name darf nicht leer sein."}, status_code=422)
    template = service.save_as_template(
        db, draft=draft, name=name.strip(), category=category.strip() or None, actor=current_user.email
    )
    return JSONResponse({"success": True, "template_id": template.id, "template_name": template.name})
