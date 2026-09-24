"""Dashboard-Router für den Dokumenten-/Schriftsatz-Generator (Block 3,
20.08.), unter `/dashboard/tools/dokumentgenerator`.

Getrennt vom bestehenden KI-Schriftsatz-Generator
(app/web/schriftsatz_router.py, `/dashboard/tools/schriftsatz` - lässt
einen Reply-Entwurf per Claude AUS DEM POSTEINGANGSKONTEXT
SCHREIBEN): dieser Router füllt stattdessen eine vom Anwalt selbst
verfasste, wiederverwendbare Vorlage (app/models/document_template.py)
mit den Falldaten EINER Akte - deterministische Textverarbeitung, KEIN
KI-/Cloud-Aufruf (siehe app/document_generator/__init__.py, DSGVO-
Begründung). Rechte: Generieren/Bearbeiten/Exportieren für ALLE drei
Rollen (`require_role()` ohne Rolleneinschränkung, analog zu
`render_template_preview` in app/web/prompt_library_router.py) - reine
Textverarbeitung ohne Kostenrisiko, keine Daten verlassen die Maschine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_login, require_role
from app.db.session import get_db
from app.document_generator.docx_export import DOCX_MEDIA_TYPE, GeneratedDocumentDocxExportService
from app.document_generator.pdf_export import PDF_MEDIA_TYPE, GeneratedDocumentPdfExportService
from app.document_generator.service import generate_from_template, get_unresolved_placeholders, update_content
from app.document_generator.template_service import DocumentTemplateService
from app.export.filenames import safe_download_filename
from app.firm_profile.service import get_firm_profile
from app.models import DocumentTemplate, GeneratedDocument, Matter, User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/tools/dokumentgenerator", tags=["dashboard-document-generator"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _safe_filename(text: str) -> str:
    """Wie `safe_download_filename` (app/export/filenames.py, gemeinsame
    Zeichenbereinigung inkl. Halbgeviertstrich-Korrektur seit 19.09.),
    hier zusätzlich mit Leerzeichen -> Unterstrich - unveraendertes,
    eigenstaendiges Dateinamensformat dieses Routers."""
    return "_".join(safe_download_filename(text, fallback="dokument").split())


@router.get("", response_class=HTMLResponse)
def document_generator_page(
    request: Request,
    matter_id: str = "",
    template_id: str = "",
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    document_templates = DocumentTemplateService().list_templates(db)
    open_matters = (
        db.query(Matter).filter(Matter.status == "open").order_by(Matter.updated_at.desc()).all()
    )
    preselected_matter_id = (
        matter_id if matter_id and any(m.id == matter_id for m in open_matters) else ""
    )
    preselected_template_id = (
        template_id
        if template_id and any(t.id == template_id for t in document_templates)
        else ""
    )
    recent_documents = (
        db.query(GeneratedDocument).order_by(GeneratedDocument.created_at.desc()).limit(20).all()
    )
    context = {
        "request": request,
        "active_nav": "Dokumentengenerator",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "document_templates": document_templates,
        "open_matters": open_matters,
        "preselected_matter_id": preselected_matter_id,
        "preselected_template_id": preselected_template_id,
        "recent_documents": recent_documents,
        "error": error,
    }
    return templates.TemplateResponse(request, "document_generator.html", context)


@router.post("/generate")
def generate_document_action(
    template_id: str = Form(...),
    matter_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    template = db.get(DocumentTemplate, template_id)
    matter = db.get(Matter, matter_id)
    if template is None or matter is None:
        return RedirectResponse(
            url="/dashboard/tools/dokumentgenerator?error=Vorlage oder Akte nicht gefunden",
            status_code=303,
        )
    result = generate_from_template(db, template, matter, actor=current_user.email)
    return RedirectResponse(
        url=f"/dashboard/tools/dokumentgenerator/{result.document.id}", status_code=303
    )


@router.get("/{document_id}", response_class=HTMLResponse)
def document_review_page(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    document = get_or_404(db, GeneratedDocument, document_id, "Generiertes Dokument")
    context = {
        "request": request,
        "active_nav": "Dokumentengenerator",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "document": document,
        "matter": document.matter,
        "unresolved_placeholders": get_unresolved_placeholders(document),
    }
    return templates.TemplateResponse(request, "document_review.html", context)


@router.post("/{document_id}/save")
def save_document_action(
    document_id: str,
    content: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    document = get_or_404(db, GeneratedDocument, document_id, "Generiertes Dokument")
    update_content(db, document, content, actor=current_user.email)
    return RedirectResponse(url=f"/dashboard/tools/dokumentgenerator/{document_id}", status_code=303)


@router.post("/{document_id}/export/docx")
def export_document_docx(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> StreamingResponse:
    document = get_or_404(db, GeneratedDocument, document_id, "Generiertes Dokument")
    firm_profile = get_firm_profile(db)
    buffer = GeneratedDocumentDocxExportService().export(document, document.matter, firm_profile)
    filename = f"{_safe_filename(document.title)}.docx"
    return StreamingResponse(
        buffer,
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{document_id}/export/pdf")
def export_document_pdf(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> StreamingResponse:
    document = get_or_404(db, GeneratedDocument, document_id, "Generiertes Dokument")
    buffer = GeneratedDocumentPdfExportService().export(document, document.matter)
    filename = f"{_safe_filename(document.title)}.pdf"
    return StreamingResponse(
        buffer,
        media_type=PDF_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
