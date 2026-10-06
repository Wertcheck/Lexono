"""Dashboard-Router für "Kanzleiwissen" (14.09., UI-Build; 26.09. erweitert
um die Kategorie-Navigation + den echten Rechtsquellen-Manager "Gesetze &
Normen", Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION").

Löste ursprünglich einen echten UI-Gap: `/dashboard/knowledge` war bis
14.09. ein reiner PLATZHALTER, obwohl die dahinterliegenden Daten
(`KnowledgeItem`, `Source`, `Law`/`LawSection`) längst existierten.

26.09.: die Referenz zeigt Kanzleiwissen als Kategorie-Uebersicht mit
einer Rechtsquellen-Verwaltungstabelle unter "Gesetze & Normen" - genau
DAS wird hier ergaenzt, nicht neu erfunden. Genau FUENF Kacheln
("KANZLEIWISSEN FINAL POLISH + APP-SHELL KORREKTUR"-Folgedirektive,
gleiches Datum): Gesetze & Normen/Rechtsprechung/Vorlagen & Muster/
Fachwissen/Interne Dokumente. Zwei vorherige Kacheln wurden in
Folgerunden DERSELBEN Sitzung bewusst wieder entfernt - "Favoriten" (kein
Datenmodell-Gegenstueck) und zuletzt auch "Alle Inhalte" (war zu einer
eigenen, unnoetigen zweiten Dashboard-Ebene geworden, siehe DECISIONS.md
fuer beide Kurskorrekturen):
- "Gesetze & Normen" nutzt den bereits bestehenden Katalog
  (app/laws/catalog.py) + die bestehenden Law/LawSection-Tabellen +
  den bestehenden offiziellen Import (app/laws/gesetze_im_internet.py,
  bisher nur per CLI erreichbar) - jetzt zusaetzlich per Toggle in der
  Weboberflaeche auslösbar (app/laws/install_service.py fuer den
  Hintergrund-Download mit echtem Fortschritt).
- "Rechtsprechung"/"Interne Dokumente" nutzen den bereits bestehenden
  `Source.source_type` (siehe app/sources/schema.py fuer
  `ALLOWED_SOURCE_TYPES`). "Rechtsprechung" filtert exakt darauf,
  "Interne Dokumente" zeigt bewusst ALLE UEBRIGEN sechs Quellentypen
  (nicht nur "Interne Leitlinie") - andernfalls waeren z. B. "Gesetz"-
  Quellen (real mit Produktionsdaten belegt) seit dem Entfernen von
  "Alle Inhalte" ohne jede Kachel unerreichbar, siehe DECISIONS.md.
- "Vorlagen & Muster" nutzt das bereits bestehende `DocumentTemplate`.
- "Fachwissen" nutzt das bereits bestehende `KnowledgeItem` (vorher
  "Textbausteine" genannt - dieselbe Tabelle, keine zweite).
- "Favoriten" existiert bewusst NICHT (mehr) als eigene Kachel - es gibt
  KEIN Gegenstueck im Datenmodell, und die zweite Referenzrunde verlangt
  die Kachel explizit zu entfernen statt sie (wie in der ersten Runde)
  nur ehrlich leer darzustellen, siehe .agentic/DECISIONS.md fuer die
  volle Begruendung dieser Kurskorrektur.

KURSKORREKTUR (26.09., Owner-Direktive "AUTONOMOUS PRODUCT GAP AUDIT" -
volle Begruendung in DECISIONS.md): der Docstring-Absatz hier lautete
bisher woertlich "BEWUSST WEITERHIN NUR LESEND fuer Textbausteine/
Quellen (kein Anlegen/Bearbeiten/Freigeben hier)". Ein systematischer
Audit fand dabei einen echten, betrieblich relevanten Gap: `Source`
(Rechtsprechung/Interne Dokumente) UND `KnowledgeItem` (Fachwissen)
wurden PROJEKTWEIT ausschliesslich ueber den Synthetic-Data-Generator
erzeugt (`app/synthetic_data/generator.py`) - `SourceService.
import_source`/`KnowledgeItemService.import_item` (beide bereits
VOLLSTAENDIG gebaut, inkl. Freigabe-/Veraltet-/Deaktivieren-Workflow und
Audit-Log) hatten KEINEN einzigen Web-Aufrufer. Ein echter Anwender
konnte damit NIE einen Rechtsprechungs-Eintrag, eine interne Leitlinie
oder einen Textbaustein anlegen - drei von fuenf Kanzleiwissen-
Kategorien waren dauerhaft leere Vitrinen ohne jeden Befuellungsweg.
Jetzt behoben: einfache Erfassungsformulare + Freigabe-/Veraltet-/
Deaktivieren-Aktionen direkt in den jeweiligen Kategorie-Panels, die
AUSSCHLIESSLICH die bereits bestehenden Service-Methoden aufrufen -
keine neue Geschaeftslogik, kein neues Datenmodell. Der Gesetze-Toggle
bleibt wie zuvor die einzige rein TECHNISCHE Verfuegbarkeits-Umschaltung
(kein Freigabe-Workflow noetig, da amtlich importiert)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_login, require_role
from app.db.session import get_db
from app.firm_profile import get_selected_practice_areas
from app.knowledge.schema import KnowledgeItemImport
from app.knowledge.service import KnowledgeItemService
from app.laws.catalog import get_catalog, get_catalog_entry_for_code
from app.laws.install_service import check_law_for_update, get_progress, start_install
from app.laws.service import get_law_stats, toggle_law_active
from app.models import DocumentTemplate, KnowledgeItem, Law, LawSection, Source, User
from app.sources.schema import ALLOWED_SOURCE_TYPES, SourceImport
from app.sources.service import SourceService
from app.web.service_factory import get_document_search_service
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/knowledge", tags=["dashboard-knowledge"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)

#: `get_document_search_service()` ist bereits ein `@lru_cache`-Singleton
#: (siehe service_factory.py) - kein teures Embedding-Modell wird hier
#: geladen, das passiert weiterhin lazy erst beim ersten echten
#: `embed()`-Aufruf. Beide Services brauchen die Suchanbindung nur fuer
#: `approve()` (Indexierung freigegebener Eintraege), `list_items`/
#: `import_item`/`import_source` funktionieren unveraendert auch ohne.
_knowledge_service = KnowledgeItemService(search_service=get_document_search_service())
_source_service = SourceService(search_service=get_document_search_service())

#: Kategorie, in die ein neu erfasster `Source`-Eintrag je nach
#: `source_type` faellt (siehe knowledge_panel.html-Docstring-Fund oben:
#: "internal" zeigt bewusst ALLE Nicht-Rechtsprechung-Typen).
def _category_for_source_type(source_type: str) -> str:
    return "case_law" if source_type == "Rechtsprechung" else "internal"

# Kategorien wie in der neuesten Referenz (26.09., Owner-Direktive
# "KANZLEIWISSEN REFERENCE-MATCH / PRODUCT-COMPLETION PASS") - EXAKT
# sechs Kategorien in dieser Reihenfolge, "icon" verweist auf ein
# bestehendes Makro aus _icons.html (kein neues Icon-Set).
#
# ECHTER KORREKTUR-FUND (26.09., zweite Referenzrunde): die vorige Runde
# hatte "Favoriten" als siebte Kachel bewusst SICHTBAR gelassen (mit
# ehrlichem "noch nicht verfügbar"-Hinweis statt erfundener Inhalte, siehe
# DECISIONS.md) - die NEUE Referenz zeigt aber nur noch sechs Kacheln
# OHNE Favoriten ueberhaupt. Das ist keine Kehrtwende bei der Kernregel
# "keine Fake-Inhalte" (weiterhin gueltig), sondern eine explizite, engere
# Produktentscheidung DIESER Direktive: die Kachel wird komplett entfernt
# statt nur ehrlich leer dargestellt. "Alle Dokumente" -> "Alle Inhalte"
# (Direktive §5: Kanzleiwissen enthaelt nicht nur Dokumente) - dieselbe
# Funktion/Zaehlung dahinter, nur die Beschriftung ist semantisch
# korrigiert.
#: Kein "Alle Inhalte" mehr (26.09., Owner-Direktive "KANZLEIWISSEN FINAL
#: POLISH + APP-SHELL KORREKTUR" §3): die Kachel war zu einer eigenen,
#: zweiten Dashboard-Ebene geworden (Gesetzesbibliothek-Zusammenfassung +
#: eine Textbausteine-Tabelle, die 1:1 die "Fachwissen"-Tabelle
#: duplizierte + eine gemischte "Rechtsquellen"-Liste) statt eines
#: einzelnen produktiven Sammelzustands - genau die von der Direktive
#: verbotene "unnoetige Zwischenebene". Jede der fuenf verbleibenden
#: Kategorien hat bereits eine eigene, vollstaendige, echte Ansicht -
#: ein zusaetzlicher Sammelfilter bringt keinen neuen Produktwert.
#: "laws" ("Gesetze & Normen") ist jetzt die Standardkategorie (Direktive
#: §5: "das ist die Referenzansicht").
_CATEGORIES = [
    ("laws", "Gesetze & Normen", "book"),
    ("case_law", "Rechtsprechung", "scale"),
    ("templates", "Vorlagen & Muster", "printer"),
    ("expertise", "Fachwissen", "graduation_cap"),
    ("internal", "Interne Dokumente", "users"),
]
_VALID_CATEGORIES = {key for key, _, _ in _CATEGORIES}
_DEFAULT_CATEGORY = "laws"

# Kurze, nicht-technische Erklaerung je Kategorie (26.09., Direktive §6) -
# unter dem Titel/Untertitel im Seitenkopf, erklaert das Produktprinzip
# ("der Benutzer entscheidet, was lokal verfuegbar ist"), keine internen
# Begriffe (kein Presidio/Ollama/Claude/Gateway).
_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "case_law": "Manuell erfasste Gerichtsentscheidungen und Rechtsprechung Ihrer Kanzlei.",
    "laws": (
        "Wählen Sie aus, welche Rechtsquellen Sie lokal in Lexono verfügbar machen "
        "möchten. Die ausgewählten Inhalte stehen anschließend für Recherche und "
        "KI-gestützte Schriftsatzerstellung zur Verfügung."
    ),
    "templates": "Wiederverwendbare Schriftsatz- und Dokumentvorlagen Ihrer Kanzlei.",
    "expertise": "Freigegebene Textbausteine und Formulierungen für die Schriftsatzerstellung.",
    "internal": "Interne Leitlinien und Rechtsquellen Ihrer Kanzlei.",
}


def _category_counts(db: Session) -> dict[str, int]:
    """Echte Zaehler je Kategorie-Kachel - NIE aus der Referenz kopiert
    (dort z. B. "Gesetze & Normen 18", tatsaechlich real z. B. 34+)."""
    items_count = len(_knowledge_service.list_items(db))
    case_law_count = (
        db.query(func.count(Source.id)).filter(Source.source_type == "Rechtsprechung").scalar() or 0
    )
    internal_count = (
        db.query(func.count(Source.id)).filter(Source.source_type != "Rechtsprechung").scalar() or 0
    )
    templates_count = db.query(func.count(DocumentTemplate.id)).scalar() or 0
    law_count = db.query(func.count(Law.id)).scalar() or 0
    return {
        "case_law": case_law_count,
        "laws": law_count,
        "templates": templates_count,
        "expertise": items_count,
        "internal": internal_count,
    }


def _law_catalog_rows(db: Session) -> list[dict]:
    """Baut je Katalogeintrag die Zeile fuer die "Gesetze & Normen"-Tabelle
    - merged den echten Katalog (app/laws/catalog.py) mit dem echten
    lokalen Zustand (installiert? aktiv? gerade ein Download/Fehler laut
    app/laws/install_service.py?). GENAU diese Merge-Logik ist der Kern
    von Direktive §19 ("Server-Katalog und lokaler Zustand muessen
    getrennt modelliert werden")."""
    law_stats = get_law_stats(db)
    laws_by_code = {law.code: law for law in db.query(Law).all()}
    rows = []
    for entry in get_catalog():
        law = laws_by_code.get(entry.code)
        progress = get_progress(entry.code)
        stats = law_stats.get(entry.code)
        rows.append(
            {
                "code": entry.code,
                "slug": entry.slug,
                "title": entry.title,
                "law": law,
                "progress": progress,
                "sections_count": stats["count"] if stats else 0,
                "stand": stats["stand"] if stats else None,
                "size_bytes": (law.source_size_bytes if law else None),
                # Automatisierte Aktualisierung (03.10., Owner-Direktive
                # "RELIABLE LEGAL KNOWLEDGE UPDATES") - reine Anzeigefelder,
                # dieselbe Merge-Logik wie die bereits bestehenden Felder
                # oben, keine zweite Kopie der Zustandsermittlung.
                "last_checked_at": (law.last_checked_at if law else None),
                "last_check_status": (law.last_check_status if law else None),
                "last_check_error": (law.last_check_error if law else None),
            }
        )
    return rows


def _build_knowledge_context(
    request: Request, db: Session, current_user: User, *, category: str, search: str
) -> dict:
    """Gemeinsamer Kontext fuer die volle Seite UND das HTMX-Kategorie-
    Panel (26.09.) - EINE Stelle statt zweier abweichender Kopien der
    Filter-/Zaehl-Logik."""
    active_category = category if category in _VALID_CATEGORIES else _DEFAULT_CATEGORY

    items = _knowledge_service.list_items(db)
    sources = db.query(Source).order_by(Source.title.asc()).all()

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
    items = sorted(items, key=lambda i: i.updated_at, reverse=True)

    # Kanzleifachprofil-Relevanz (03.10., Owner-Direktive "KANZLEIFACHPROFIL
    # UND JURISTISCHE WISSENSSTEUERUNG" §4.3) - NUR fuer `KnowledgeItem`
    # ("Fachwissen"), da dies das EINZIGE Modell ist, das bereits ein
    # echtes, befuelltes `practice_area`-Feld traegt (`Law`/`Source` haben
    # keines - eine Zuordnung dort wuerde erfunden, nicht abgeleitet, siehe
    # PROJECT_STATE.md fuer die volle Begruendung). Stabiler Sortier-
    # Zusatzschritt: passende Eintraege zuerst, jeweils weiterhin nach
    # Aktualitaet geordnet - NICHTS wird ausgeblendet/ausgeschlossen.
    firm_practice_areas = get_selected_practice_areas(db)
    if firm_practice_areas:
        matching_areas_lower = {area.lower() for area in firm_practice_areas}
        items = sorted(
            items,
            key=lambda i: 0
            if (i.practice_area or "").strip().lower() in matching_areas_lower
            else 1,
        )

    case_law_sources = [s for s in sources if s.source_type == "Rechtsprechung"]
    # ECHTER FUND (26.09., "APP-SHELL KORREKTUR" §3/§18 "keine bestehende
    # Funktionalitaet verloren"): `ALLOWED_SOURCE_TYPES` (app/sources/
    # schema.py) hat SIEBEN Werte, aber nur "Rechtsprechung" hatte eine
    # eigene Kachel - die anderen sechs (u. a. "Gesetz", real mit
    # Produktionsdaten belegt) waren bisher NUR ueber die jetzt entfernte
    # "Alle Inhalte"-Sammelansicht ("Rechtsquellen"-Tabelle) erreichbar.
    # Ohne diese Korrektur wuerden sie beim Entfernen von "Alle Inhalte"
    # unsichtbar - "Interne Dokumente" ist bewusst die breiteste,
    # generischste der fuenf verbleibenden Kacheln und uebernimmt daher
    # ALLE Nicht-Rechtsprechung-Quellentypen (nicht nur "Interne
    # Leitlinie") - die Tabelle zeigt den echten Typ je Zeile (siehe
    # knowledge_panel.html), keine falsche Kategorisierung.
    internal_sources = [s for s in sources if s.source_type != "Rechtsprechung"]

    templates_qs = db.query(DocumentTemplate).order_by(DocumentTemplate.name.asc()).all()
    if search:
        needle = search.strip().lower()
        templates_qs = [
            t
            for t in templates_qs
            if needle in (t.name or "").lower() or needle in (t.category or "").lower()
        ]

    law_rows = _law_catalog_rows(db)
    if search:
        needle = search.strip().lower()
        law_rows = [
            row
            for row in law_rows
            if needle in row["title"].lower() or needle in row["code"].lower()
        ]

    law_count = db.query(func.count(Law.id)).scalar() or 0
    section_count = db.query(func.count(LawSection.id)).scalar() or 0

    return {
        "request": request,
        "current_user": current_user,
        "active_nav": "Kanzleiwissen",
        "categories": _CATEGORIES,
        "active_category": active_category,
        "category_description": _CATEGORY_DESCRIPTIONS.get(
            active_category, _CATEGORY_DESCRIPTIONS[_DEFAULT_CATEGORY]
        ),
        "category_counts": _category_counts(db),
        "items": items,
        "sources": sources,
        "case_law_sources": case_law_sources,
        "internal_sources": internal_sources,
        "document_templates": templates_qs,
        "law_rows": law_rows,
        "search": search,
        "law_count": law_count,
        "section_count": section_count,
        "firm_practice_areas": firm_practice_areas,
        "firm_practice_areas_lower": {area.lower() for area in firm_practice_areas},
        # 26.09., "AUTONOMOUS PRODUCT GAP AUDIT": gleiches Muster wie
        # document_templates_router.py::_is_curator - Anlegen/Freigeben
        # von Rechtsquellen/Textbausteinen auf Admin/Anwalt beschraenkt,
        # in der Vorlage geprueft statt direkter Rollen-String-Vergleiche.
        "can_curate_knowledge": bool(
            current_user.role and current_user.role.name.strip().lower() in {"admin", "anwalt"}
        ),
        "source_types": sorted(ALLOWED_SOURCE_TYPES - {"Rechtsprechung"}),
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }


@router.get("", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    category: str = _DEFAULT_CATEGORY,
    search: str = "",
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Kanzleiwissen-Uebersicht mit Kategorie-Navigation (26.09.) - die
    Referenz `43_Kanzleiwissen_Gesetze.png` zeigt die Kategorie-Kacheln
    als eigentliche Seiten-Navigation, nicht als Dekoration (Direktive
    §10)."""
    context = _build_knowledge_context(request, db, current_user, category=category, search=search)
    return templates.TemplateResponse(request, "knowledge.html", context)


@router.get("/panel", response_class=HTMLResponse)
def knowledge_panel(
    request: Request,
    category: str = _DEFAULT_CATEGORY,
    search: str = "",
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """HTMX-Partial fuer den Kategorie-Wechsel (26.09.) - liefert den
    Panel-Inhalt ALS regulaeres Swap-Ziel plus die Kategorie-Kacheln und
    den Untertitel als Out-of-Band-Swaps (`oob=True`), damit die aktive
    Kategorie ueberall konsistent bleibt (ECHTER FUND waehrend eigener
    Visual-QA: ohne die OOB-Swaps blieb die zuvor aktive Kachel optisch
    aktiv, obwohl der Inhalt bereits gewechselt hatte). Kein voller Reload
    (gleiches Muster wie app/web/router.py::inbox_list_partial)."""
    context = _build_knowledge_context(request, db, current_user, category=category, search=search)
    context["oob"] = True
    return templates.TemplateResponse(request, "partials/knowledge_panel_response.html", context)


@router.post("/laws/{law_code}/toggle", response_class=HTMLResponse)
def toggle_or_install_law(
    law_code: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> HTMLResponse:
    """EINE Aktion, die je nach echtem lokalem Zustand unterschiedlich
    wirkt (26.09., Direktive §4/§5/§15 - "Der Toggle ist eine echte
    Produktfunktion"):
    - Gesetz lokal installiert -> flippt `is_active` (sofort, synchron,
      kein Download noetig, siehe app/laws/service.py::toggle_law_active).
    - Gesetz nicht installiert (oder vorheriger Versuch fehlgeschlagen)
      -> startet einen ECHTEN Hintergrund-Download+Import
      (app/laws/install_service.py::start_install) und liefert SOFORT die
      Zeile im Zwischenzustand "Wird heruntergeladen …" zurueck - niemals
      "Installiert", bevor der Download wirklich abgeschlossen ist."""
    law = db.query(Law).filter_by(code=law_code).first()
    progress = get_progress(law_code)
    if law is not None and (progress is None or progress.status != "error"):
        toggle_law_active(db, law_code, active=not law.is_active)
    else:
        start_install(law_code)
    return _render_law_row(request, db, law_code)


@router.get("/laws/{law_code}/row", response_class=HTMLResponse)
def law_row(
    law_code: str,
    request: Request,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Polling-Ziel waehrend eines laufenden Downloads (26.09., Direktive
    §14: echter Fortschritt statt einer festen Animation) - die Zeile
    selbst entscheidet per `hx-trigger`, ob sie sich weiter aktualisiert
    (siehe partials/law_catalog_row.html), daher genuegt ein einfaches
    GET ohne Zustandsaenderung hier."""
    return _render_law_row(request, db, law_code)


@router.post("/laws/{law_code}/check", response_class=HTMLResponse)
def check_law_update(
    law_code: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> HTMLResponse:
    """Manuelle Aktualisierungspruefung (03.10., Owner-Direktive "RELIABLE
    LEGAL KNOWLEDGE UPDATES" Phase D, Betriebsform 1: "manuelle Prüfung
    ... durch eine berechtigte Aktion") - ein einzelner, kurzer HEAD-
    Request (siehe app/laws/install_service.py::check_law_for_update),
    laeuft synchron innerhalb des Requests (kein Hintergrund-Thread/
    Polling noetig, da deutlich schneller als der eigentliche Download).
    Laedt NIEMALS den vollen Gesetzesinhalt und AENDERT NIE die
    gespeicherten Normen - nur `Law.last_checked_at`/`last_check_status`."""
    check_law_for_update(db, law_code)
    return _render_law_row(request, db, law_code)


@router.post("/laws/{law_code}/update", response_class=HTMLResponse)
def apply_law_update(
    law_code: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> HTMLResponse:
    """Uebernimmt eine zuvor erkannte neue Fassung (03.10., Owner-Direktive
    "RELIABLE LEGAL KNOWLEDGE UPDATES" Phase D, Betriebsform 1) - startet
    denselben, bereits bestehenden Hintergrund-Download+Import wie die
    Erstinstallation (`start_install`, jetzt mit Validierung vor der
    Übernahme + ETag-Nachführung, siehe app/laws/install_service.py), NUR
    fuer ein bereits lokal installiertes Gesetz. Liefert SOFORT die Zeile
    im Zwischenzustand zurueck (dasselbe Polling-Muster wie der
    Erstinstallations-Toggle)."""
    start_install(law_code)
    return _render_law_row(request, db, law_code)


def _render_law_row(request: Request, db: Session, law_code: str) -> HTMLResponse:
    entry = get_catalog_entry_for_code(law_code)
    if entry is None:
        return HTMLResponse(status_code=404, content="Unbekanntes Gesetzeswerk")
    law_stats = get_law_stats(db)
    law = db.query(Law).filter_by(code=law_code).first()
    progress = get_progress(law_code)
    stats = law_stats.get(law_code)
    row = {
        "code": entry.code,
        "slug": entry.slug,
        "title": entry.title,
        "law": law,
        "progress": progress,
        "sections_count": stats["count"] if stats else 0,
        "stand": stats["stand"] if stats else None,
        "size_bytes": (law.source_size_bytes if law else None),
        "last_checked_at": (law.last_checked_at if law else None),
        "last_check_status": (law.last_check_status if law else None),
        "last_check_error": (law.last_check_error if law else None),
    }
    return templates.TemplateResponse(
        request,
        "partials/law_catalog_row.html",
        {"request": request, "row": row, "csrf_token": getattr(request.state, "csrf_token", "")},
    )


# --- Manuelle Erfassung: Rechtsprechung/Interne Dokumente (Source) und
# Fachwissen (KnowledgeItem) - siehe Moduldocstring-Kurskorrektur oben.
# Anlegen/Freigeben/Veraltet-Markieren/Deaktivieren auf Admin/Anwalt
# beschraenkt, identisches Rechte-Zuschnittsmuster wie die bereits
# bestehende Kanzlei-Mustertexte-/Standard-Prompts-Kuratierung
# (document_templates_router.py/prompt_library_router.py) - ein
# Mitarbeiter nutzt kuratierte Inhalte, kuratiert aber nicht selbst.


@router.post("/sources")
def create_source(
    request: Request,
    title: str = Form(...),
    source_type: str = Form(...),
    reference: str = Form(""),
    url: str = Form(""),
    document_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    """Erfasst eine neue Rechtsquelle (Direktive §21: "keine
    Fake-Rechtsprechung" - deshalb ausschliesslich manuelle Erfassung
    durch den Anwalt selbst, ueber den bereits bestehenden
    `ManualSourceProvider`, KEINE automatisierte/erfundene Quelle)."""
    category = _category_for_source_type(source_type if source_type in ALLOWED_SOURCE_TYPES else "Sonstiges")
    try:
        data = SourceImport(
            title=title,
            source_type=source_type,
            reference=reference or None,
            url=url or None,
            document_date=date.fromisoformat(document_date) if document_date else None,
            notes=notes or None,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/dashboard/knowledge?category={category}&error={exc}", status_code=303
        )
    _source_service.import_source(data, db, actor=current_user.email)
    return RedirectResponse(url=f"/dashboard/knowledge?category={category}", status_code=303)


@router.post("/sources/{source_id}/approve")
def approve_source_action(
    source_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    source = get_or_404(db, Source, source_id, "Rechtsquelle")
    _source_service.approve_source(source, db, actor=current_user.email)
    return RedirectResponse(
        url=f"/dashboard/knowledge?category={_category_for_source_type(source.source_type)}",
        status_code=303,
    )


@router.post("/sources/{source_id}/mark-outdated")
def mark_source_outdated_action(
    source_id: str,
    reason: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    source = get_or_404(db, Source, source_id, "Rechtsquelle")
    category = _category_for_source_type(source.source_type)
    try:
        _source_service.mark_outdated(source, db, actor=current_user.email, reason=reason)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/dashboard/knowledge?category={category}&error={exc}", status_code=303
        )
    return RedirectResponse(url=f"/dashboard/knowledge?category={category}", status_code=303)


@router.post("/items")
def create_knowledge_item(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    category: str = Form(""),
    practice_area: str = Form(""),
    source: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    """Erfasst einen neuen Fachwissen-Textbaustein - identisches
    Freigabe-Prinzip wie bei Rechtsquellen (`approval_status="pending"`
    bis explizit freigegeben, siehe KnowledgeItemService)."""
    try:
        data = KnowledgeItemImport(
            title=title,
            content=content,
            category=category or None,
            practice_area=practice_area or None,
            source=source or None,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/dashboard/knowledge?category=expertise&error={exc}", status_code=303
        )
    _knowledge_service.import_item(data, db, actor=current_user.email)
    return RedirectResponse(url="/dashboard/knowledge?category=expertise", status_code=303)


@router.post("/items/{item_id}/approve")
def approve_knowledge_item_action(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    item = get_or_404(db, KnowledgeItem, item_id, "Textbaustein")
    _knowledge_service.approve(item, db, actor=current_user.email)
    return RedirectResponse(url="/dashboard/knowledge?category=expertise", status_code=303)


@router.post("/items/{item_id}/deactivate")
def deactivate_knowledge_item_action(
    item_id: str,
    reason: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "anwalt")),
) -> RedirectResponse:
    item = get_or_404(db, KnowledgeItem, item_id, "Textbaustein")
    try:
        _knowledge_service.deactivate(item, db, actor=current_user.email, reason=reason)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/dashboard/knowledge?category=expertise&error={exc}", status_code=303
        )
    return RedirectResponse(url="/dashboard/knowledge?category=expertise", status_code=303)
