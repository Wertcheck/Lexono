"""Dashboard-Router für die digitale Gesetzesbibliothek (20.08.), unter
`/dashboard/laws` (siehe Moduldocstring app/laws/__init__.py für die
§ 5 UrhG-Einordnung).

Lesezugriff für alle drei Rollen (`require_login`, wie jede andere
Referenz-/Übersichtsseite) - es gibt bewusst KEINE Anlege-/Bearbeiten-UI
hier: Inhalte entstehen ausschließlich über den kontrollierten Fixture-
Import (app/laws/service.py, scripts/import_law_fixtures.py), nie durch
Nutzereingabe im Dashboard.

Drei GET-Routen rendern dieselbe Split-Pane-Vorlage
(templates/law_library.html) mit wachsendem Kontext, plus eine HTMX-
Partial-Route für die Schnellsuche in der Seitenleiste - gleiches Muster
wie app/web/clients_router.py (_list_page_context/_client_detail_context)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import require_login
from app.db.session import get_db
from app.laws.service import (
    get_law_by_code,
    get_law_stats,
    get_laws,
    get_sections,
    import_all_fixtures,
)
from app.models import Law, LawSection, User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/laws", tags=["dashboard-laws"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _ensure_seeded(db: Session) -> None:
    """Lazy Bootstrap (20.08.): "damit die Bibliothek nicht leer startet"
    (ausdrückliche Vorgabe) - importiert die mitgelieferten Fixtures GENAU
    EINMAL, wenn noch kein einziges Gesetzeswerk existiert. Bewusst NICHT
    im FastAPI-Lifespan-Hook (app/main.py) verankert: dieses Projekt seedet
    an keiner anderen Stelle automatisch beim Prozessstart (Rollen/Admin
    entstehen ausschließlich über explizite Skripte, siehe
    scripts/create_admin.py) - ein stiller DB-Schreibzugriff bei JEDEM
    App-Start wäre ein Bruch mit diesem Prinzip. Der Check hier ist
    dagegen an den tatsächlichen Seitenaufruf gebunden, günstig (ein
    COUNT), und komplett idempotent (import_all_fixtures ist ein Upsert)."""
    if db.query(Law).count() == 0:
        import_all_fixtures(db)


def _library_context(
    request: Request,
    db: Session,
    current_user: User,
    *,
    law_code: str | None,
    section_id: str | None,
    search: str,
) -> dict:
    laws = get_laws(db)
    selected_law = get_law_by_code(db, law_code) if law_code else None
    if law_code and selected_law is None:
        raise HTTPException(status_code=404, detail="Gesetzeswerk nicht gefunden")

    sections = get_sections(db, law_code, search=search) if selected_law else []

    selected_section = None
    if section_id:
        selected_section = next((s for s in sections if s.id == section_id), None)
        if selected_section is None:
            # Kann per section_id auch ausserhalb der aktuell gefilterten
            # `sections`-Liste liegen (Suchbegriff aktiv) - noch ein
            # direkter Versuch, bevor 404 ausgeloest wird.
            selected_section = (
                db.query(LawSection)
                .filter_by(id=section_id, law_code=law_code)
                .first()
            )
        if selected_section is None:
            raise HTTPException(status_code=404, detail="Paragraph nicht gefunden")

    # Echte Kennzahlen je Gesetz (14.09., seit 26.09. in
    # app/laws/service.py::get_law_stats geteilt mit der neuen
    # Kanzleiwissen-Katalogtabelle) - Version/Stand/Umfang lassen sich aus
    # dem vorhandenen Datenmodell WAHRHEITSGEMAESS ableiten
    # (Groessenangaben in MB dagegen nicht, die werden bewusst nicht
    # erfunden).
    law_stats = get_law_stats(db)

    return {
        "request": request,
        "active_nav": "Kanzleiwissen",
        "current_user": current_user,
        "laws": laws,
        "law_stats": law_stats,
        "selected_law": selected_law,
        "sections": sections,
        "selected_section": selected_section,
        "search": search,
    }


@router.get("", response_class=HTMLResponse)
def law_library_overview(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    _ensure_seeded(db)
    context = _library_context(
        request, db, current_user, law_code=None, section_id=None, search=q
    )
    return templates.TemplateResponse(request, "law_library.html", context)


@router.get("/{law_code}", response_class=HTMLResponse)
def law_library_law_selected(
    law_code: str,
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    _ensure_seeded(db)
    context = _library_context(
        request, db, current_user, law_code=law_code, section_id=None, search=q
    )
    return templates.TemplateResponse(request, "law_library.html", context)


@router.get("/{law_code}/sections-partial", response_class=HTMLResponse)
def law_library_sections_partial(
    law_code: str,
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """HTMX-Ziel der Schnellsuche in der Seitenleiste (Live-Filter ohne
    vollen Seiten-Reload) - liefert NUR die Paragraphenliste, kein
    komplettes Layout."""
    selected_law = get_law_by_code(db, law_code)
    if selected_law is None:
        raise HTTPException(status_code=404, detail="Gesetzeswerk nicht gefunden")
    sections = get_sections(db, law_code, search=q)
    context = {
        "request": request,
        "selected_law": selected_law,
        "sections": sections,
        "selected_section": None,
        "search": q,
    }
    return templates.TemplateResponse(request, "partials/law_sections_list.html", context)


@router.get("/{law_code}/{section_id}", response_class=HTMLResponse)
def law_library_section_selected(
    law_code: str,
    section_id: str,
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    _ensure_seeded(db)
    context = _library_context(
        request, db, current_user, law_code=law_code, section_id=section_id, search=q
    )
    return templates.TemplateResponse(request, "law_library.html", context)
