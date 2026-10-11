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


def _extract_pdf_text(path: Path) -> str:
    text_parts: list[str] = []
    with pymupdf.open(path) as pdf:
        for page in pdf:
            text_parts.append(page.get_text())
    return "\n".join(text_parts)


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

    if suffix == ".pdf":
        text = _extract_pdf_text(path)
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

    return ExtractionResult(text=text, needs_ocr=False)
