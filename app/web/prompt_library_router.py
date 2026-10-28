"""Dashboard-Router für 'Kanzleiwissen & KI' -> 'Standard-Prompts'
(Schritt 3, Teil 2): die tatsächlich verwendeten System-Prompts (read-only,
zur Transparenz) + eine editierbare Kanzlei-Prompt-Bibliothek mit
Platzhalter-Variablen wie {Mandant}/{Frist}/{Dokumententext}.

Rechte: Lesen + Rendern (Platzhalter befüllen, zum Kopieren) für alle drei
Rollen (kein Kostenrisiko, keine Mandantendaten beteiligt - reine
Textbausteine). Anlegen/Ändern/Löschen bewusst auf Admin/Anwalt beschränkt
(analog zur Rechte-Matrix, Prompt 26: Mitarbeiter bearbeitet Entwürfe
manuell, kuratiert aber keine KI-Textbausteine)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import PermissionDeniedError, require_login, require_role
from app.db.session import get_db
from app.models import PromptTemplate, User
from app.prompt_library import home_service
from app.prompt_library.home import CONTENT_MAX, DESCRIPTION_MAX, HOME_PROMPT_LIMIT, NAME_MAX
from app.prompt_library.icons import PROMPT_ICONS
from app.prompt_library.rendering import extract_variables, render_template
from app.prompt_library.schema import PromptTemplateInput
from app.prompt_library.service import PromptTemplateService
from app.prompt_library.system_prompts import SYSTEM_PROMPT_REFERENCES
from app.web.template_paths import TEMPLATES_DIR

def require_role_get(*roles: str):
    """GET-Variante von `require_role` (kein CSRF-Formularfeld bei reinem Lesen)."""

    def dependency(current_user: User = Depends(require_login)) -> User:
        name = current_user.role.name.strip().lower() if current_user.role else ""
        if name not in {r.lower() for r in roles}:
            raise PermissionDeniedError()
        return current_user

    return dependency


router = APIRouter(prefix="/dashboard/library/prompts", tags=["dashboard-prompt-library"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("", response_class=HTMLResponse)
def prompt_library_page(
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    is_curator = bool(
        current_user.role and current_user.role.name.strip().lower() in {"admin", "anwalt"}
    )
    templates_with_variables = [
        (template, extract_variables(template.content))
        for template in PromptTemplateService().list_templates(db)
    ]
    context = {
        "request": request,
        "active_nav": "Standard-Prompts",
        "current_user": current_user,
        "is_curator": is_curator,
        "system_prompts": SYSTEM_PROMPT_REFERENCES,
        "templates_with_variables": templates_with_variables,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "error": error,
    }
    return templates.TemplateResponse(request, "prompt_library.html", context)


@router.post("")
def create_template(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    content: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    try:
        data = PromptTemplateInput(name=name, description=description or None, content=content)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/dashboard/library/prompts?error={exc}", status_code=303
        )
    PromptTemplateService().create_template(db, data, actor=current_user.email)
    return RedirectResponse(url="/dashboard/library/prompts", status_code=303)


@router.get("/manage/state")
def home_state(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role_get("admin", "anwalt")),
) -> JSONResponse:
    """Aktueller Zustand für das Modal "Standard-Prompts verwalten" (Chat-Startseite)."""
    return JSONResponse(
        {
            "prompts": [home_service.serialize(p) for p in home_service.list_prompts(db)],
            "limit": HOME_PROMPT_LIMIT,
            "icons": PROMPT_ICONS,
            "max": {"name": NAME_MAX, "description": DESCRIPTION_MAX, "content": CONTENT_MAX},
        }
    )


@router.post("/manage/state")
def save_home_state(
    payload: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> JSONResponse:
    """Speichert den gesamten Arbeitszustand des Modals atomar (siehe home_service.save_state)."""
    try:
        data = json.loads(payload)
    except ValueError:
        return JSONResponse({"errors": [{"index": None, "field": "payload", "message": "Ungültige Daten."}]}, status_code=422)
    if not isinstance(data, dict):
        return JSONResponse({"errors": [{"index": None, "field": "payload", "message": "Ungültige Daten."}]}, status_code=422)
    try:
        prompts = home_service.save_state(db, data, actor=current_user.email)
    except home_service.PromptStateError as exc:
        db.rollback()
        return JSONResponse({"errors": exc.errors}, status_code=422)
    return JSONResponse({"prompts": [home_service.serialize(p) for p in prompts]})


@router.get("/{template_id}/edit", response_class=HTMLResponse)
def edit_template_page(
    template_id: str,
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    is_curator = bool(
        current_user.role and current_user.role.name.strip().lower() in {"admin", "anwalt"}
    )
    if not is_curator:
        from app.auth.permissions import PermissionDeniedError

        raise PermissionDeniedError("Nur Admin/Anwalt können Kanzlei-Prompts bearbeiten")

    template = get_or_404(db, PromptTemplate, template_id, "Prompt-Vorlage")
    context = {
        "request": request,
        "active_nav": "Standard-Prompts",
        "current_user": current_user,
        "template": template,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "error": error,
    }
    return templates.TemplateResponse(request, "prompt_library_edit.html", context)


@router.post("/{template_id}")
def update_template(
    template_id: str,
    name: str = Form(...),
    description: str = Form(""),
    content: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    template = get_or_404(db, PromptTemplate, template_id, "Prompt-Vorlage")
    try:
        data = PromptTemplateInput(name=name, description=description or None, content=content)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/dashboard/library/prompts/{template_id}/edit?error={exc}", status_code=303
        )
    PromptTemplateService().update_template(db, template, data, actor=current_user.email)
    return RedirectResponse(url="/dashboard/library/prompts", status_code=303)


@router.post("/{template_id}/delete")
def delete_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    template = get_or_404(db, PromptTemplate, template_id, "Prompt-Vorlage")
    PromptTemplateService().delete_template(db, template, actor=current_user.email)
    return RedirectResponse(url="/dashboard/library/prompts", status_code=303)


@router.post("/{template_id}/render", response_class=HTMLResponse)
async def render_template_preview(
    template_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> HTMLResponse:
    """Befüllt die Platzhalter eines Kanzlei-Prompts mit den vom Nutzer
    eingegebenen Werten - reine Textverarbeitung zum Kopieren, kein
    Claude-Aufruf, keine Persistierung. Die Variablenfelder sind dynamisch
    (abhängig von den Platzhaltern der jeweiligen Vorlage) - deshalb wird
    das Formular hier bewusst per `request.form()` statt fester
    `Form(...)`-Parameter gelesen (Starlette cached den Formular-Body,
    daher kein Konflikt mit dem `csrf_token`-Feld, das bereits die
    `require_role()`-Dependency konsumiert hat).

    Bewusst werden LEER gelassene Felder NICHT als leerer String
    eingesetzt - der Platzhalter bleibt dann sichtbar im Vorschautext
    stehen (`{Frist}`), statt eine unvollständig ausgefüllte Vorlage
    unbemerkt als scheinbar fertigen Text erscheinen zu lassen (CLAUDE.md:
    "Unsicherheit explizit markieren, nicht verschweigen")."""
    template = get_or_404(db, PromptTemplate, template_id, "Prompt-Vorlage")
    variable_names = extract_variables(template.content)

    form_data = await request.form()
    variables = {
        name: str(form_data[name])
        for name in variable_names
        if name in form_data and str(form_data[name]).strip()
    }
    rendered = render_template(template.content, variables)

    context = {"request": request, "template": template, "rendered": rendered}
    return templates.TemplateResponse(request, "partials/prompt_render_result.html", context)
