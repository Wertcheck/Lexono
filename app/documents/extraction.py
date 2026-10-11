"""Textextraktion aus PDFs und gängigen Office-Dokumenten.

Bewusst rein technisch: liest vorhandenen Text aus, ohne ihn zu bewerten,
zu kürzen oder zu interpretieren. Erkennt lediglich, ob genug Text
vorhanden ist oder ob OCR nötig sein könnte (Entscheidung darüber trifft
`app/documents/service.py`, nicht dieses Modul).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document as DocxDocument

# Dateiendungen, aus denen direkt Text extrahiert werden kann (ggf. leer,
# dann evtl. OCR-Kandidat).
SUPPORTED_TEXT_EXTENSIONS = {".pdf", ".docx", ".txt"}
# Dateiendungen, die direkt als Bild gelten und nie eigenen extrahierbaren
# Text haben - immer OCR-Kandidat, falls OCR aktiviert ist.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


@dataclass
class ExtractionResult:
    text: str | None
    # True, wenn kein/zu wenig Text gefunden wurde und die Datei ein
    # Format ist, aus dem OCR grundsätzlich Sinn ergeben könnte.
    needs_ocr: bool
    # True, wenn das Dateiformat von diesem Modul gar nicht unterstützt
    # wird (weder Text noch OCR möglich) - z. B. ein unbekanntes Format.
    unsupported_format: bool = False


#: Anteil der Seitenflaeche, ab dem ein Bild auf einer Seite ohne (nennenswerte) Textebene als gescannte Seite gilt -
#: ein Logo oder eine Unterschrift bleibt deutlich darunter.
_SCANNED_PAGE_IMAGE_AREA_RATIO = 0.5


def _is_scanned_page(page, text: str, min_text_length: int) -> bool:
    """Seite ohne nennenswerten Text, die ueberwiegend aus einem Bild besteht (Scan). Leere Seiten ohne Bild zaehlen
    nicht - dort fehlt kein Inhalt."""
    if len(text.strip()) >= min_text_length:
        return False
    page_area = abs(page.rect)
    if not page_area:
        return False
    image_area = 0.0
    for info in page.get_image_info():
        x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
        image_area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return image_area / page_area >= _SCANNED_PAGE_IMAGE_AREA_RATIO


def _extract_pdf_text(path: Path, min_text_length: int = 20) -> tuple[str, int]:
    """Text aller Seiten und Anzahl gescannter Seiten ohne Textebene."""
    text_parts: list[str] = []
    scanned_pages = 0
    with pymupdf.open(path) as pdf:
        for page in pdf:
            text = page.get_text()
            text_parts.append(text)
            if _is_scanned_page(page, text, min_text_length):
                scanned_pages += 1
    return "\n".join(text_parts), scanned_pages


def _docx_block_lines(container) -> list[str]:
    """Absaetze UND Tabellen in Dokumentreihenfolge. Tabellenzeilen als "Zelle | Zelle | Zelle" - dieselbe Darstellung,
    die die Datenschutzpruefung als Tabellenzeile erkennt (app/privacy/review.py)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    lines: list[str] = []
    for child in container.element.body.iterchildren() if hasattr(container.element, "body") else container._element.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, container).text)
        elif tag == "tbl":
            for row in Table(child, container).rows:
                cells: list[str] = []
                for cell in row.cells:
                    value = " ".join(cell.text.split())
                    if not cells or cells[-1] != value:  # verbundene Zellen nicht doppelt
                        cells.append(value)
                lines.append(" | ".join(cells))
    return lines


def _extract_docx_text(path: Path) -> str:
    """ECHTER FUND (12.10.2026, realistische synthetische Kanzleidokumente): bisher nur `paragraphs` - Tabellen
    (Beteiligten-/Rechnungstabellen), Kopf- und Fusszeilen fehlten vollstaendig im extrahierten Text. Jetzt: Kopfzeile(n),
    Absaetze und Tabellen in Dokumentreihenfolge, Fusszeile(n). Der gesamte Text durchlaeuft wie jeder andere die
    Pseudonymisierung und das Pre-Cloud-Gate."""
    docx_document = DocxDocument(str(path))
    header_lines: list[str] = []
    footer_lines: list[str] = []
    for section in docx_document.sections:
        for part, target in ((section.header, header_lines), (section.footer, footer_lines)):
            if part.is_linked_to_previous and target:
                continue
            for paragraph in part.paragraphs:
                if paragraph.text.strip() and paragraph.text not in target:
                    target.append(paragraph.text)
    body_lines = _docx_block_lines(docx_document)
    return "\n".join([*header_lines, *body_lines, *footer_lines])


def _extract_txt_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_text(
    path: Path, *, min_text_length: int = 20
) -> ExtractionResult:
    """Extrahiert Text aus einer Datei, falls das Format unterstützt wird.

    `min_text_length` steuert, ab wann extrahierter Text als "ausreichend"
    gilt statt als OCR-Kandidat (z. B. verhindert das, dass ein PDF mit nur
    einer eingebetteten Kopfzeile fälschlich als vollständig erfasst gilt).
    """
    suffix = path.suffix.lower()

    if suffix in IMAGE_EXTENSIONS:
        return ExtractionResult(text=None, needs_ocr=True)

    if suffix not in SUPPORTED_TEXT_EXTENSIONS:
        return ExtractionResult(text=None, needs_ocr=False, unsupported_format=True)

    scanned_pages = 0
    if suffix == ".pdf":
        text, scanned_pages = _extract_pdf_text(path, min_text_length)
    elif suffix == ".docx":
        text = _extract_docx_text(path)
    else:  # .txt
        text = _extract_txt_text(path)

    stripped = text.strip()
    if len(stripped) < min_text_length:
        # Nur PDFs koennen sinnvoll per OCR nachbearbeitet werden (Seiten
        # lassen sich rastern) - ein leeres .docx/.txt ist kein OCR-Fall.
        needs_ocr = suffix == ".pdf"
        return ExtractionResult(text=text or None, needs_ocr=needs_ocr)

    if scanned_pages:
        # ECHTER FUND (13.10.2026): ein PDF aus Textseiten UND gescannten Seiten galt mit dem Text der Textseiten als
        # vollstaendig erfasst - der Inhalt der Scan-Seiten fehlte unbemerkt. Unvollstaendiger Text ist keine
        # vollstaendige Verarbeitung: das Dokument ist ein OCR-Fall (bei aktivierter Texterkennung wird das ganze
        # Dokument erkannt, sonst bleibt es "pending" und wird nicht als ausgewertet behandelt).
        return ExtractionResult(text=text, needs_ocr=True)

    return ExtractionResult(text=text, needs_ocr=False)
