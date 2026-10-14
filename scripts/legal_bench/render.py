"""Erzeugt aus einem Korpusfall (benchmarks/legal_e2e_v1) echte Dateien: TXT, DOCX, PDF mit Textebene und Scan-PDF
(nur Bild, ohne Textebene - erfordert OCR). Deterministisch; die Dateien werden zur Laufzeit in ein Arbeitsverzeichnis
geschrieben und nicht versioniert (Quelle der Wahrheit ist der Text im Korpus)."""

from __future__ import annotations

from pathlib import Path

SCAN_DPI = 200


def _pdf(paragraphs: list[str], path: Path) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y, bottom = 60.0, 790.0
    for paragraph in paragraphs:
        # passt der Absatz nicht mehr auf die Seite, schreibt insert_textbox nichts (Rueckgabe < 0): neue Seite
        rest = page.insert_textbox(fitz.Rect(60, y, 535, bottom), paragraph, fontsize=11, fontname="helv")
        if rest < 0:
            page = doc.new_page(width=595, height=842)
            y = 60.0
            rest = page.insert_textbox(fitz.Rect(60, y, 535, bottom), paragraph, fontsize=11, fontname="helv")
            if rest < 0:
                raise ValueError("Absatz laenger als eine Seite")
        y = bottom - rest + 8
    doc.save(str(path))


def _scan(paragraphs: list[str], path: Path, dpi: int = SCAN_DPI) -> None:
    """Rendert den Text als PDF und rastert jede Seite: das Ergebnis enthaelt nur Bilder (wie ein Scanner)."""
    import pymupdf as fitz

    source = path.with_suffix(".source.pdf")
    _pdf(paragraphs, source)
    src = fitz.open(str(source))
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        target = out.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, pixmap=pix)
    out.save(str(path))
    src.close()
    source.unlink()


def _docx(paragraphs: list[str], path: Path) -> None:
    import docx

    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))


def render_document(document: dict, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    paragraphs = document["paragraphs"]
    fmt = document["format"]
    name = Path(document["filename"]).stem
    if fmt == "txt":
        path = outdir / f"{name}.txt"
        path.write_text("\n".join(paragraphs) + "\n", encoding="utf-8")
    elif fmt == "docx":
        path = outdir / f"{name}.docx"
        _docx(paragraphs, path)
    elif fmt == "pdf":
        path = outdir / f"{name}.pdf"
        _pdf(paragraphs, path)
    elif fmt == "scan":
        path = outdir / f"{name}.pdf"
        _scan(paragraphs, path)
    else:  # pragma: no cover - der Validator laesst keine anderen Formate zu
        raise ValueError(f"unbekanntes Format {fmt!r}")
    return path


def render_case(case: dict, outdir: Path) -> dict[str, Path]:
    return {document["id"]: render_document(document, outdir / case["id"]) for document in case["documents"]}
