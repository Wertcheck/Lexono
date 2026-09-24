"""Dashboard-Router für "Kanzleiwissen" (14.09., UI-Build).

Löst einen echten UI-Gap: `/dashboard/knowledge` war bis hierher ein reiner
PLATZHALTER ("Die Verwaltungsoberfläche für die Kanzlei-Wissensbasis
befindet sich in der finalen Vorbereitung für das v0.2-Update", siehe
app/web/placeholder_router.py) - obwohl die dahinterliegende Funktion und
die Daten längst existieren:

- `KnowledgeItem` + `app/knowledge/service.py::KnowledgeItemService.list_items`
  (Textbausteine/Kanzleiwissen mit Kategorie, Rechtsgebiet, Freigabestatus
  und Gültigkeitszeitraum) - vollständig vorhanden und getestet,
- `Source` (Rechtsquellen mit Fundstelle/Freigabegrad),
- die importierte Gesetzesbibliothek (`Law`/`LawSection`, aktuell 34 Gesetze
  mit über 11.000 Normen) samt eigener, funktionierender Oberfläche unter
  `/dashboard/laws`.

Ein Hauptnavigationspunkt, der auf eine "In Vorbereitung"-Seite führt,
obwohl die Inhalte vorhanden sind, lässt das Produkt unfertig wirken und
verbirgt vorhandenen Wert - genau das schließt dieser Router.

BEWUSST NUR LESEND: kein Anlegen/Bearbeiten/Freigeben von Wissenselementen
hier. Freigabe (`approve`) ist eine fachliche Entscheidung mit
Audit-Relevanz (siehe KnowledgeItemService) und bekommt, wenn sie in die
Oberfläche soll, einen eigenen, bewusst geplanten Schritt - keine
nebenbei gebaute Schreibfunktion. Dieselbe Trennung wie bei
app/web/matters_router.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.permissions import require_login
from app.db.session import get_db
from app.knowledge.service import KnowledgeItemService
from app.models import Law, LawSection, Source, User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/knowledge", tags=["dashboard-knowledge"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)

_knowledge_service = KnowledgeItemService()


@router.get("", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    search: str = "",
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Überblick über das tatsächlich vorhandene Kanzleiwissen.

    Nutzt bewusst den BESTEHENDEN `KnowledgeItemService.list_items`
    (Prompt 22) statt einer eigenen Abfrage - dieselbe Logik, die auch
    sonst im Projekt für Wissenselemente zuständig ist.
    """
    items = _knowledge_service.list_items(db)
    sources = db.query(Source).order_by(Source.title.asc()).all()

    # Freitextsuche über Titel/Inhalt bzw. Titel/Fundstelle - bewusst eine
    # einfache, ehrliche Filterung auf dem bereits geladenen Bestand statt
    # eines zweiten Suchstacks (die semantische Suche hat mit
    # DocumentSearchService/globaler Suche ihren eigenen, anderen Zweck).
    if search:
        needle = search.strip().lower()
        items = [
            item
            for item in items
            if needle in (item.title or "").lower()
            or needle in (item.content or "").lower()
            or needle in (item.category or "").lower()
            or needle in (item.practice_area or "").lower()
        ]
        sources = [
            source
            for source in sources
            if needle in (source.title or "").lower()
            or needle in (source.reference or "").lower()
            or needle in (source.source_type or "").lower()
        ]

    # Zuletzt geändertes zuerst - entspricht der Referenz ("Zuletzt
    # aktualisiert") und ist für eine Wissensbasis die nützlichste
    # Voreinstellung.
    items = sorted(items, key=lambda i: i.updated_at, reverse=True)

    # Gesetzesbibliothek: nur die Kennzahlen + Einstieg. Die vollständige
    # Ansicht hat mit /dashboard/laws bereits eine eigene, funktionierende
    # Oberfläche - hier wird sie verlinkt, nicht nachgebaut.
    law_count = db.query(func.count(Law.id)).scalar() or 0
    section_count = db.query(func.count(LawSection.id)).scalar() or 0

    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Kanzleiwissen",
        "items": items,
        "sources": sources,
        "search": search,
        "law_count": law_count,
        "section_count": section_count,
    }
    return templates.TemplateResponse(request, "knowledge.html", context)
