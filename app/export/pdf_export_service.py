"""DraftPdfExportService – Export EINES Entwurfs als druckfertiges `.pdf`
(18.09., Owner-Direktive "WEITERARBEITEN" - Referenzabgleich zeigt PDF als
den PRIMÄREN, vorausgewählten Export-Format-Radiobutton auf der
Entwurfsseite, DOCX als zweite Option - im Produkt existierte projektweit
nur der DOCX-Export, `app/export/docx_export_service.py`).

Nutzt `pymupdf` (bereits Projektabhängigkeit, siehe pyproject.toml) statt
einer neuen Bibliothek - identisches Muster wie
`app/document_generator/pdf_export.py::GeneratedDocumentPdfExportService`
(deterministische Zeilenumbrüche via `textwrap.wrap`, siehe dortiger
Docstring für die Begründung gegen `page.insert_textbox`s Neuversuchs-
Schleife). Anders als jener bewusst einfachere Export: `Draft` ist (wie
beim DOCX-Export) das vollwertige, druckfertige Schriftstück - Briefkopf/
Signatur werden deshalb hier ebenfalls eingebunden, über dieselben,
bereits etablierten `app/export/letterhead.py`-Hilfsfunktionen
(`has_letterhead_content`/`has_signature_content`/`image_exists`), NICHT
über eine zweite, docx-spezifische Kopie dieser Prüfungen.

Bewusst NUR einmalig auf Seite 1 (kein pro-Seite wiederholter Kopfbereich
wie im DOCX-Export - PyMuPDFs Low-Level-API hat keinen "echten
Seiten-Header" wie Word; ein einmaliger Briefkopf ist für ein
Anwaltsschreiben ohnehin die uebliche Konvention, siehe Referenz)."""

from __future__ import annotations

import textwrap
from io import BytesIO

import pymupdf

from app.export.letterhead import has_letterhead_content, has_signature_content, image_exists
from app.export.pdf_text import sanitize_for_base14_font
from app.models import Draft, FirmProfile, Matter

PDF_MEDIA_TYPE = "application/pdf"

_PAGE_WIDTH, _PAGE_HEIGHT = 595, 842  # A4 in Punkt
_MARGIN = 56  # ca. 2 cm
_FONT = "helv"
_FONT_SIZE = 11
_LINE_HEIGHT = 15
_CHARS_PER_LINE = 90  # Faustregel für 11pt Helvetica auf A4 mit 2 cm Rand


class DraftPdfExportService:
    def export_draft(
        self, draft: Draft, matter: Matter | None, firm_profile: FirmProfile | None = None
    ) -> BytesIO:
        """`matter=None`: identische, bereits etablierte Degradierung wie
        `DraftDocxExportService.export_draft` (siehe dortiger Docstring) -
        ein Draft ohne (mehr) existierende Matter darf den Export nicht
        mit einem `AttributeError` abbrechen lassen."""
        pdf = pymupdf.open()
        state = {"page": pdf.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT), "y": float(_MARGIN)}

        def new_page() -> None:
            state["page"] = pdf.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
            state["y"] = float(_MARGIN)

        def write_line(text: str, *, size: float = _FONT_SIZE, bold: bool = False) -> None:
            if state["y"] + _LINE_HEIGHT > _PAGE_HEIGHT - _MARGIN:
                new_page()
            state["page"].insert_text(
                (_MARGIN, state["y"]),
                sanitize_for_base14_font(text),
                fontsize=size,
                fontname=_FONT if not bold else "hebo",
            )
            state["y"] += _LINE_HEIGHT * (size / _FONT_SIZE)

        def write_wrapped(text: str, *, size: float = _FONT_SIZE) -> None:
            for line in textwrap.wrap(text, width=_CHARS_PER_LINE) or [""]:
                write_line(line, size=size)

        if has_letterhead_content(firm_profile):
            self._write_letterhead(pdf, state, firm_profile, new_page=new_page)

        write_line((matter.title if matter else None) or "Schriftsatz", size=16, bold=True)
        state["y"] += 6
        write_line(
            f"Entwurf Version {draft.version} · Stand {draft.updated_at.strftime('%d.%m.%Y')}",
            size=9,
        )
        state["y"] += 14

        for block in draft.content.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            write_wrapped(block)
            state["y"] += 8  # Absatzabstand

        if has_signature_content(firm_profile):
            self._write_signature(pdf, state, firm_profile, new_page=new_page)

        buffer = BytesIO(pdf.write())
        pdf.close()
        buffer.seek(0)
        return buffer

    @staticmethod
    def _write_letterhead(pdf, state: dict, firm_profile: FirmProfile, *, new_page) -> None:
        page = state["page"]
        if image_exists(firm_profile.logo_path):
            logo_height = 45.0
            rect = pymupdf.Rect(
                (_PAGE_WIDTH - 140) / 2, state["y"], (_PAGE_WIDTH + 140) / 2, state["y"] + logo_height
            )
            page.insert_image(rect, filename=firm_profile.logo_path, keep_proportion=True)
            # ECHTER FUND (20.09., beim realen Export-Test mit einem echten
            # Logo-Bild sichtbar): `state["y"]` ist die BASELINE der
            # folgenden Textzeile, nicht deren obere Kante - bei nur 6pt
            # Abstand ragte der Aufstrich der naechsten Zeile (Kanzleiname,
            # fontsize 12, Aufstrich ca. 9-10pt) sichtbar in die untere
            # Kante der Logo-Box hinein. 14pt Abstand laesst dafuer genug
            # Raum. Regressionsgeschuetzt durch
            # test_export_service_logo_does_not_overlap_the_firm_name_line
            # (tests/test_draft_pdf_export.py) - real bestaetigt, dass der
            # Test OHNE diesen Fix fehlschlaegt (94.16 < 101.0).
            state["y"] += logo_height + 14

        firm_name = sanitize_for_base14_font(firm_profile.firm_name.strip())
        if firm_name:
            width = pymupdf.get_text_length(firm_name, fontname="hebo", fontsize=12)
            page.insert_text(
                ((_PAGE_WIDTH - width) / 2, state["y"]), firm_name, fontsize=12, fontname="hebo"
            )
            state["y"] += _LINE_HEIGHT

        address_line = ", ".join(
            part
            for part in (
                firm_profile.street,
                " ".join(p for p in (firm_profile.postal_code, firm_profile.city) if p) or None,
            )
            if part
        )
        contact_line = " · ".join(
            part for part in (firm_profile.phone, firm_profile.email, firm_profile.website) if part
        )
        for line in (address_line, contact_line):
            if not line:
                continue
            line = sanitize_for_base14_font(line)
            width = pymupdf.get_text_length(line, fontname=_FONT, fontsize=9)
            page.insert_text(((_PAGE_WIDTH - width) / 2, state["y"]), line, fontsize=9, fontname=_FONT)
            state["y"] += 12

        state["y"] += 4
        page.draw_line(
            (_MARGIN, state["y"]), (_PAGE_WIDTH - _MARGIN, state["y"]), color=(0.79, 0.84, 0.88), width=0.75
        )
        state["y"] += 20

    @staticmethod
    def _write_signature(pdf, state: dict, firm_profile: FirmProfile, *, new_page) -> None:
        state["y"] += 14
        if image_exists(firm_profile.signature_path):
            signature_height = 45.0
            if state["y"] + signature_height > _PAGE_HEIGHT - _MARGIN:
                new_page()
            rect = pymupdf.Rect(_MARGIN, state["y"], _MARGIN + 130, state["y"] + signature_height)
            state["page"].insert_image(rect, filename=firm_profile.signature_path, keep_proportion=True)
            state["y"] += signature_height + 4

        signatory_name = sanitize_for_base14_font((firm_profile.signatory_name or "").strip())
        if signatory_name:
            if state["y"] + _LINE_HEIGHT > _PAGE_HEIGHT - _MARGIN:
                new_page()
            state["page"].insert_text(
                (_MARGIN, state["y"]), signatory_name, fontsize=_FONT_SIZE, fontname=_FONT
            )
            state["y"] += _LINE_HEIGHT
