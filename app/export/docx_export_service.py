"""DraftDocxExportService – Export EINES Entwurfs als formatierte `.docx`
(Schriftsatz-Generator, 20.08.; Briefkopf-/Signatur-Verwaltung, Nachtrag
20.08.).

Getrennt von `MatterExportService` (app/export/service.py, ZIP-Export der
GESAMTEN Akte für Auskunftsersuchen/Archivierung) - dieser Service liefert
gezielt EIN druckfertiges Word-Dokument für genau eine Entwurfsversion.

Briefkopf-/Signatur-Aufbau (Logo/Anschrift/Unterschrift) lebt seit dem
Dokumentengenerator (Block 3, 20.08.) in app/export/letterhead.py - EINE
gemeinsame Implementierung statt einer zweiten, fast identischen Kopie in
app/document_generator/docx_export.py. Optional (`firm_profile=None` oder
ein leerer Datensatz möglich): solange auf der Kanzlei-Profilseite
(/dashboard/settings/profile) kein Kanzleiname eingetragen wurde, bleibt
der Export ohne Briefkopf (ehrlicher als ein Platzhalter-Absender).
"""

from __future__ import annotations

from io import BytesIO

from docx import Document as DocxDocument
from docx.shared import Pt

from app.export.html_content import ContentBlock, parse_html_content
from app.export.letterhead import add_signature_block, build_header, has_letterhead_content, has_signature_content
from app.models import Draft, FirmProfile, Matter

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DraftDocxExportService:
    def export_draft(
        self, draft: Draft, matter: Matter | None, firm_profile: FirmProfile | None = None
    ) -> BytesIO:
        """`matter=None` (17.09., Datenintegritaets-Fund - real in der
        Produktions-DB gefunden: ein Draft/Document-Paar mit `matter_id`,
        die auf keine existierende Matter mehr zeigt, vermutlich Rest eines
        aelteren manuellen Demo-Daten-Aufraeumens VOR der heutigen, bereits
        korrekten `reset_demo_data`-Kaskade): degradiert ehrlich auf
        "Schriftsatz" als Titel, statt mit einem `AttributeError` (`None`
        hat kein `.title`) abzustuerzen - identisches Verhalten wie das
        bereits etablierte `{{ draft.matter.title if draft.matter else ...
        }}`-Muster in drafts_list.html/draft_detail.html, hier nur auch auf
        den bisher ungeschuetzten Export-Pfad uebertragen."""
        document = DocxDocument()

        style = document.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)

        if has_letterhead_content(firm_profile):
            build_header(document, firm_profile)

        document.add_heading((matter.title if matter else None) or "Schriftsatz", level=1)
        meta = document.add_paragraph()
        meta.add_run(
            f"Entwurf Version {draft.version} · Stand "
            f"{draft.updated_at.strftime('%d.%m.%Y')}"
        ).italic = True

        if draft.content_format == "html":
            # ECHTER FUND (05.10., siehe app/export/html_content.py-
            # Moduldocstring): der bisherige Klartext-Pfad unten gab bei
            # einem Editor-Entwurf den rohen HTML-Quelltext aus - hier
            # werden Absatz-/Listenstruktur und Fett/Kursiv/Unterstrichen
            # ueber `python-docx`s native Run-API tatsaechlich umgesetzt.
            self._write_html_blocks(document, parse_html_content(draft.content))
        else:
            # Leerzeilen als Absatzgrenzen - der Entwurfstext selbst ist
            # reiner Fließtext ohne eigene Formatierungssyntax (siehe
            # Draft.content, UNVERAENDERT fuer jeden bestehenden Entwurf).
            for block in draft.content.split("\n\n"):
                block = block.strip()
                if block:
                    document.add_paragraph(block)

        if has_signature_content(firm_profile):
            add_signature_block(document, firm_profile)

        buffer = BytesIO()
        document.save(buffer)
        buffer.seek(0)
        return buffer

    @staticmethod
    def _write_html_blocks(document: DocxDocument, blocks: list[ContentBlock]) -> None:
        """Rendert die bereits geparste Blockfolge (siehe
        app/export/html_content.py) - `<br>` (literales "\\n" in einem
        Run) wird als Word-Zeilenumbruch INNERHALB desselben Absatzes
        ueber `run.add_break()` umgesetzt (kein neuer Absatz - ein
        harter Zeilenumbruch ist in Word etwas anderes als ein neuer
        Absatz, siehe python-docx-Doku `WD_BREAK.LINE`)."""
        from docx.enum.text import WD_BREAK

        for block in blocks:
            if block.kind == "li_bullet":
                paragraph = document.add_paragraph(style="List Bullet")
            elif block.kind == "li_number":
                paragraph = document.add_paragraph(style="List Number")
            else:
                paragraph = document.add_paragraph()
            for inline_run in block.runs:
                segments = inline_run.text.split("\n")
                for i, segment in enumerate(segments):
                    if i > 0:
                        paragraph.add_run().add_break(WD_BREAK.LINE)
                    if not segment:
                        continue
                    run = paragraph.add_run(segment)
                    run.bold = inline_run.bold
                    run.italic = inline_run.italic
                    run.underline = inline_run.underline or bool(inline_run.href)
