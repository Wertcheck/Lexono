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

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_login, require_role
from app.db.session import get_db
from app.models import PromptTemplate, User
from app.prompt_library.rendering import extract_variables, render_template
from app.prompt_library.schema import PromptTemplateInput
from app.prompt_library.service import PromptTemplateService
from app.prompt_library.system_prompts import SYSTEM_PROMPT_REFERENCES
from app.web.template_paths import TEMPLATES_DIR

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


def _is_curator(user: User) -> bool:
    return bool(user.role and user.role.name.strip().lower() in {"admin", "anwalt"})


@router.get("/manage.json")
def manage_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> JSONResponse:
    """Daten der Verwaltung "Standard-Prompts verwalten" im Chat (28.10.2026): alle Vorlagen in Verwaltungs-
    reihenfolge (aktivierte zuerst), Startseiten-Limit, Icons und ob der Nutzer aendern darf (Admin/Anwalt, wie die
    bestehende Bibliothek). Keine Mandantendaten - reine Textbausteine."""
    from app.prompt_library import start_prompts as sp

    return JSONResponse({
        "items": [sp.serialize(row) for row in sp.ordered_templates(db)],
        "max_start": sp.MAX_START_PROMPTS,
        "limits": {"name": sp.NAME_MAX, "description": sp.DESCRIPTION_MAX, "content": sp.CONTENT_MAX},
        "icons": list(sp.ICONS),
        "can_edit": _is_curator(current_user),
    }, headers={"Cache-Control": "no-store"})


@router.post("/manage")
def manage_save(
    payload: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> JSONResponse:
    """Speichert den gesamten Verwaltungsstand in EINEM Schritt (Anlegen, Bearbeiten, Loeschen, Aktivieren,
    Sortieren). Vollstaendige serverseitige Pruefung vor dem Schreiben - Viererlimit, Pflichtfelder, Laengen,
    Icons; bei einem Fehler bleibt der gespeicherte Stand unveraendert (HTTP 400 mit verstaendlicher Meldung)."""
    import json

    from app.prompt_library import start_prompts as sp

    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return JSONResponse({"error": "Die Änderungen konnten nicht gelesen werden."}, status_code=400)
    try:
        rows = sp.apply_management_save(db, data, actor=current_user.email)
    except sp.PromptManagementError as exc:
        db.rollback()
        return JSONResponse({"error": str(exc), "key": exc.key, "field": exc.field}, status_code=400)
    return JSONResponse({"ok": True, "items": [sp.serialize(row) for row in rows]})


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
