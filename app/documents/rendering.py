"""Echtes visuelles Rendering von Dokumentseiten fuer den Dokumentviewer
(20.09., Owner-Direktive "PRIORITAETSERGAENZUNG: ECHTER DOKUMENTVIEWER").

WICHTIG (Abgrenzung, siehe Direktive - "ORIGINALDATEI vs. VISUELLE
DOKUMENTANSICHT vs. EXTRAHIERTER INHALT vs. EDITOR" muessen sauber
getrennt bleiben): dieses Modul ist STRIKT getrennt von
`app/documents/extraction.py` (Textextraktion fuer KI/Suche/
Pseudonymisierung/PII-Vorschau). Extraktion liefert NIEMALS die visuelle
Ansicht - Layout, Tabellen, Bilder gingen dabei verloren, genau das
verbietet die Direktive ausdruecklich ("nicht einfach DOCX-Text
extrahieren und als HTML ausgeben"). Dieses Modul rendert stattdessen
echte Seiten-Bilder aus der TATSAECHLICH gespeicherten Originaldatei
(`document.file_path`) - die Originaldatei selbst wird dabei nie
veraendert (nur gelesen).

Technischer Kern (waehrend dieser Sitzung verifiziert): PyMuPDF
(`pymupdf.open()`) kann PDF- UND DOCX-Dateien direkt oeffnen und ueber
`page.get_pixmap()` als Bild rastern - dieselbe API fuer beide Formate,
getestet an synthetischen mehrseitigen Testdokumenten (PDF und DOCX,
inkl. Tabelle/Seitenumbruch/mehreren Ueberschriften - alle Seiten korrekt
gerendert). Kein LibreOffice/keine externe Konvertierung noetig - PyMuPDF
ist bereits ein produktiv genutzter Projekt-Dependency (siehe
app/documents/extraction.py, app/export/pdf_export_service.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

#: Formate, die PyMuPDF direkt seitenweise als Bild rastern kann - fuer
#: diese Formate zeigt der Viewer eine echte Seitenansicht mit
#: Seitennavigation/Thumbnails/Zoom.
RENDERABLE_PAGE_EXTENSIONS = {".pdf", ".docx"}
#: Die Datei IST bereits ein Bild - kein Rendering noetig, direkte
#: Anzeige der Originaldatei.
DIRECT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
#: Reiner Text - eigene einfache Textansicht statt Bild-Rendering (ein
#: Bild einer Textdatei waere unnoetig und unscharf).
TEXT_VIEW_EXTENSIONS = {".txt"}

_MIN_DPI = 40
_MAX_DPI = 300
#: Standard-Aufloesung fuer die grosse Hauptansicht - hoch genug fuer
#: gute Lesbarkeit bis ~200% Zoom (Zoom selbst skaliert das bereits
#: gerenderte Bild clientseitig per CSS, siehe app_document_viewer.js).
DEFAULT_PAGE_DPI = 150
#: Niedrige Aufloesung fuer die Thumbnail-Leiste - Thumbnails muessen nur
#: als Miniatur erkennbar sein, keine hohe Aufloesung noetig.
THUMBNAIL_DPI = 45


class DocumentRenderError(Exception):
    """Die Datei behauptet ein unterstuetztes Format zu sein, liess sich
    aber tatsaechlich nicht oeffnen/rendern (fehlt, beschaedigt, kein
    valides PDF/DOCX trotz Dateiendung). Der Router faengt dies ab und
    zeigt einen ehrlichen Fallback statt eines Absturzes."""


def clamp_dpi(dpi: int) -> int:
    return max(_MIN_DPI, min(_MAX_DPI, dpi))


def get_page_count(path: Path) -> int:
    try:
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception as exc:  # PyMuPDF wirft diverse eigene Fehlerklassen
        raise DocumentRenderError(
            f"Datei konnte nicht geoeffnet werden: {exc}"
        ) from exc


def render_page_png(path: Path, page_number: int, *, dpi: int = DEFAULT_PAGE_DPI) -> bytes:
    """`page_number` ist 1-basiert (wie in der UI angezeigt: "Seite 1")."""
    dpi = clamp_dpi(dpi)
    try:
        with pymupdf.open(path) as doc:
            if page_number < 1 or page_number > doc.page_count:
                raise DocumentRenderError(
                    f"Seite {page_number} existiert nicht "
                    f"(Dokument hat {doc.page_count} Seite(n))."
                )
            page = doc[page_number - 1]
            pixmap = page.get_pixmap(dpi=dpi)
            return pixmap.tobytes("png")
    except DocumentRenderError:
        raise
    except Exception as exc:
        raise DocumentRenderError(f"Seite konnte nicht gerendert werden: {exc}") from exc


@dataclass
class ViewerMode:
    #: "pages" (echtes Seiten-Rendering, PDF/DOCX), "image" (Originaldatei
    #: ist bereits ein Bild), "text" (reiner Text), "unsupported" (ehrlicher
    #: Fallback - weder Rendering noch Bild noch Text moeglich).
    kind: str
    page_count: int | None = None


def determine_viewer_mode(path: Path) -> ViewerMode:
    """Bestimmt AUSSCHLIESSLICH anhand der echten Dateiendung von
    `document.file_path` (nicht `original_filename` - siehe Begruendung
    bei `icons.file_type_badge`, dasselbe Prinzip: `original_filename`
    ist per "Umbenennen" frei/ohne Endungspruefung editierbar, `file_path`
    behaelt die tatsaechliche Upload-Endung dauerhaft)."""
    suffix = path.suffix.lower()

    if suffix in RENDERABLE_PAGE_EXTENSIONS:
        if not path.is_file():
            return ViewerMode(kind="unsupported")
        try:
            count = get_page_count(path)
        except DocumentRenderError:
            return ViewerMode(kind="unsupported")
        return ViewerMode(kind="pages", page_count=count)

    if suffix in DIRECT_IMAGE_EXTENSIONS:
        return ViewerMode(kind="image")

    if suffix in TEXT_VIEW_EXTENSIONS:
        return ViewerMode(kind="text")

    return ViewerMode(kind="unsupported")
