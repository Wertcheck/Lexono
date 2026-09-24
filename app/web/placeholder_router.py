"""Ehrliche Platzhalterseiten für noch nicht gebaute Dashboard-Bereiche
(Prompt 48, erweitert um die juristische Menüstruktur aus Prompt 49).

Grundprinzip unverändert seit Prompt 48: eine Sidebar-Navigation soll
vollständig nutzbar wirken (kein "bald"-Badge, kein nicht anklickbares
Element), ohne Funktionen vorzutäuschen, die es nicht gibt. Jeder Eintrag
hier ist ein ECHTER, klickbarer Link auf eine ehrliche "in Vorbereitung"-
Seite mit einer konkreten, auf den jeweiligen Bereich zugeschnittenen
Beschreibung - kein 404, kein totes Element, aber auch keine vorgetäuschte
Fach-Funktion.

Prompt 49 (juristische Menüstruktur) hat bewusst KEINE der hier gelisteten
Funktionen tatsächlich implementiert (ausdrücklicher Auftragsumfang: "nur
Navigation/Platzhalter, keine neue Backend-Funktionen") - das gilt auch für
Bereiche, für die es dem Namen nach schon verwandte Bausteine im System
gibt (z. B. Fristen-Extraktion, Prompt 10), aber keine dedizierte
Dashboard-Seite. Echte, bereits gebaute Bereiche (Posteingang, Entwürfe,
Postausgang, Fehler, Systemstatus, Backup & Export, Nutzerverwaltung,
Konto-Bereich) werden NICHT hier, sondern in ihren eigenen bestehenden
Routern geführt - siehe app/web/account_router.py für den neuen
Profil-/Einstellungen-Bereich.

Bewusst EIN generischer, dict-getriebener Router statt vieler fast
identischer Funktionen (Prompt 48 hatte noch vier Handfunktionen - bei
jetzt 14 Platzhaltern wäre das reine Wiederholung ohne Mehrwert).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth.permissions import require_login
from app.models import User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard", tags=["dashboard-placeholder"])

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# URL-Suffix (nach "/dashboard") -> (Sidebar-Label/active_nav, Beschreibungstext).
_PLACEHOLDER_PAGES: dict[str, tuple[str, str]] = {
    "/recent": (
        "Letzte Akten",
        "Eine Schnellübersicht der zuletzt bearbeiteten Akten befindet sich in der "
        "finalen Vorbereitung für das v0.2-Update.",
    ),
    # "/matters" ("Akten") ist seit der UI/UX-Ueberarbeitung (13.09.) KEIN
    # Platzhalter mehr - siehe app/web/matters_router.py (echte,
    # rein lesende Akten-Uebersicht + Detailansicht mit Dokumenten/
    # Aufgaben/Fristen/Kommunikation/Chat-Unterhaltungen/Entwuerfen).
    "/documents": (
        "Dokumenten-Viewer",
        "Eine dedizierte Dokumentenansicht mit Vorschau befindet sich in der finalen "
        "Vorbereitung für das v0.2-Update.",
    ),
    "/archive": (
        "Archiv",
        "Eine Übersicht abgeschlossener/archivierter Akten befindet sich in der "
        "finalen Vorbereitung für das v0.2-Update.",
    ),
    # "/tools/schriftsatz" ("Schriftsatz-Generator") ist seit 20.08. KEIN
    # Platzhalter mehr - siehe app/web/schriftsatz_router.py (echte
    # Entwurfserstellung über DraftingService.create_draft, inkl.
    # Drag&Drop-Dokumenten-Upload und DOCX-Export der fertigen Entwürfe).
    "/tools/fristen": (
        "Fristen-Check",
        "Eine dedizierte Übersicht erkannter Fristen (aufbauend auf der bestehenden "
        "Fristenerkennung, Prompt 10) befindet sich in der finalen Vorbereitung für "
        "das v0.2-Update.",
    ),
    "/tools/zeitleiste": (
        "Zeitleiste",
        "Ein chronologischer Zeitleisten-Generator für Aktenverläufe befindet sich in "
        "der finalen Vorbereitung für das v0.2-Update.",
    ),
    "/tools/beleg-extraktion": (
        "Beleg-Extraktion",
        "Eine automatisierte Extraktion strukturierter Daten aus Belegen befindet "
        "sich in der finalen Vorbereitung für das v0.2-Update.",
    ),
    "/sources": (
        "Rechtsquellen",
        "Eine durchsuchbare Übersicht konfigurierter Rechtsquellen befindet sich in "
        "der finalen Vorbereitung für das v0.2-Update.",
    ),
    # "/library/mustertexte" ("Kanzlei-Mustertexte") ist seit dem
    # Dokumenten-/Schriftsatz-Generator (Block 3, 20.08.) KEIN Platzhalter
    # mehr - siehe app/web/document_templates_router.py (echte
    # Vorlagenverwaltung mit Platzhalter-Referenz) und
    # app/web/document_generator_router.py (Generieren/Vorschau/Export).
    # "/library/prompts" ("Standard-Prompts") ist seit Schritt 3 KEIN
    # Platzhalter mehr - siehe app/web/prompt_library_router.py (echte
    # Prompt-Bibliothek: read-only Referenz der tatsächlichen System-Prompts
    # + editierbare Kanzlei-Prompts mit Platzhalter-Variablen).
    # "/knowledge" ("Kanzleiwissen") ist seit 14.09. KEIN Platzhalter mehr -
    # siehe app/web/knowledge_router.py (echte Uebersicht ueber Textbausteine,
    # Rechtsquellen und die importierte Gesetzesbibliothek; die Daten und der
    # KnowledgeItemService existierten laengst, nur die Oberflaeche fehlte).
    "/history/analysen": (
        "Gespeicherte Analysen",
        "Ein durchsuchbares Verlaufsarchiv früherer KI-Analysen befindet sich in der "
        "finalen Vorbereitung für das v0.2-Update.",
    ),
    # "/account/profile" ("Kanzlei-Profil & Briefkopf") ist seit 20.08.
    # KEIN Platzhalter mehr - siehe app/web/settings_router.py
    # (echte Seite jetzt unter /dashboard/settings/profile, verlinkt aus
    # templates/account_overview.html).
    "/account/license": (
        "System & Lizenz",
        "Eine Verbrauchs-/Lizenzübersicht befindet sich in der finalen Vorbereitung "
        "für das v0.2-Update. Die aktuelle Kostenauslastung ist schon heute unter "
        "Systemstatus (nur Admin) einsehbar.",
    ),
    # "/clients" ("Mandantendatenbank") ist seit 20.08. KEIN Platzhalter
    # mehr - siehe app/web/clients_router.py (echte CRM-Uebersicht:
    # Suche/Filter, CSV-/Excel-Import, Detailansicht, DSGVO-Datenauszug/
    # Archivierung).
}


def _make_route(path_suffix: str, label: str, description: str) -> None:
    @router.get(path_suffix, response_class=HTMLResponse, name=f"placeholder{path_suffix}")
    def _placeholder_page(
        request: Request, current_user: User = Depends(require_login)
    ) -> HTMLResponse:
        context = {
            "request": request,
            "current_user": current_user,
            "active_nav": label,
            "placeholder_title": label,
            "placeholder_description": description,
        }
        return templates.TemplateResponse(request, "placeholder.html", context)


for _path_suffix, (_label, _description) in _PLACEHOLDER_PAGES.items():
    _make_route(_path_suffix, _label, _description)

# Hinweis: "/dashboard/settings" war frueher ein Redirect hierher (Prompt 49,
# Ruecksicht auf einen noch aelteren Prompt-48-Link) - seit 20.08. ist
# "/dashboard/settings" eine ECHTE, bedienbare Seite (app/web/settings_router.py),
# kein Platzhalter/Redirect mehr.
