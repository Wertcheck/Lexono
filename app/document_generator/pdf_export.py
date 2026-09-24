"""GeneratedDocumentPdfExportService – Export EINES generierten Dokuments
als einfaches, textbasiertes `.pdf` (Block 3, 20.08.).

Nutzt `pymupdf` (bereits Projektabhängigkeit, siehe pyproject.toml/
app/documents/ocr.py) statt einer neuen Bibliothek. Bewusst DETERMINISTISCHE
Zeilenumbrüche via `textwrap.wrap` statt `page.insert_textbox`s "passt der
Text in die Box?"-Rückgabewert: Letzteres würde bei einem pathologisch
langen, ununterbrochenen Absatz eine Neuversuchs-/Seitenumbruch-Schleife
ohne garantierten Abbruch erfordern - `write_line` hier erzeugt IMMER
höchstens eine neue Seite pro Zeile, terminiert also garantiert.

Kein Briefkopf/Logo (anders als der DOCX-Export, app/document_generator/
docx_export.py) - bewusst einfacher gehalten, das PDF ist die schlanke
Alternative für reinen Text-/Archivzweck. Word bleibt der vollwertige,
druckfertige Export mit Briefkopf/Unterschrift."""

from __future__ import annotations

import textwrap
from io import BytesIO

import pymupdf

from app.export.pdf_text import sanitize_for_base14_font
from app.models import GeneratedDocument, Matter

PDF_MEDIA_TYPE = "application/pdf"

_PAGE_WIDTH, _PAGE_HEIGHT = 595, 842  # A4 in Punkt
_MARGIN = 56  # ca. 2 cm
_FONT = "helv"
_FONT_SIZE = 11
_LINE_HEIGHT = 15
_CHARS_PER_LINE = 90  # Faustregel für 11pt Helvetica auf A4 mit 2 cm Rand


class GeneratedDocumentPdfExportService:
    def export(self, document: GeneratedDocument, matter: Matter) -> BytesIO:
        pdf = pymupdf.open()
        state = {"page": pdf.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT), "y": float(_MARGIN)}

        def write_line(text: str, *, size: float = _FONT_SIZE) -> None:
            if state["y"] + _LINE_HEIGHT > _PAGE_HEIGHT - _MARGIN:
                state["page"] = pdf.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
                state["y"] = float(_MARGIN)
            state["page"].insert_text(
                (_MARGIN, state["y"]), sanitize_for_base14_font(text), fontsize=size, fontname=_FONT
            )
            state["y"] += _LINE_HEIGHT * (size / _FONT_SIZE)

        write_line(document.title, size=16)
        state["y"] += 6
        write_line(
            f"Akte: {matter.title} · Erstellt {document.created_at.strftime('%d.%m.%Y')}", size=9
        )
        state["y"] += 14

        for block in document.content.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            for line in textwrap.wrap(block, width=_CHARS_PER_LINE) or [""]:
                write_line(line)
            state["y"] += 8  # Absatzabstand

        buffer = BytesIO(pdf.write())
        pdf.close()
        buffer.seek(0)
        return buffer
