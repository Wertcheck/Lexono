"""Tests für `icons.file_type_badge` in app/web/templates/_icons.html
(04.10., Owner-Direktive "EINHEITLICHE DATEIFORMATE UND DATEISYMBOLE IN
DER GESAMTEN ANWENDUNG") - die EINE zentrale Zuordnung von Dateiendung zu
Icon-Form/-Farbe, die von allen sechs Aufrufstellen (Posteingang, Akten,
Mandantendetail, Chat, Entwürfe, Dokumentenvorschau) gemeinsam genutzt
wird. Rendert das Makro direkt über eine eigene Jinja2-Environment (ohne
vollen HTTP-Request/DB-Umweg) - prüft die Zuordnungslogik isoliert."""

from __future__ import annotations

import jinja2
import pytest

from app.web.template_paths import TEMPLATES_DIR


@pytest.fixture(scope="module")
def icons_module():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("_icons.html")
    return template.module


def _render(icons_module, filename: str) -> str:
    return str(icons_module.file_type_badge(filename))


@pytest.mark.parametrize(
    ("filename", "expected_modifier", "expected_title"),
    [
        ("Beschluss.pdf", "pdf", "PDF-Dokument"),
        ("Beschluss.PDF", "pdf", "PDF-Dokument"),  # Gross-/Kleinschreibung
        ("Verfahrensvermerk.xml", "xml", "XML-Datei"),
        ("Vertrag.doc", "word", "Word-Dokument"),
        ("Vertrag.docx", "word", "Word-Dokument"),
        ("Belege.xls", "sheet", "Tabellendokument"),
        ("Belege.xlsx", "sheet", "Tabellendokument"),
        ("Export.csv", "sheet", "Tabellendokument"),
        ("Praesentation.ppt", "slides", "Praesentation"),
        ("Praesentation.pptx", "slides", "Praesentation"),
        ("Notiz.txt", "text", "Textdatei"),
        ("Nachricht.eml", "mail", "E-Mail"),
        ("Nachricht.msg", "mail", "E-Mail"),
        ("Foto.png", "image", "Bilddatei"),
        ("Foto.jpg", "image", "Bilddatei"),
        ("Foto.JPEG", "image", "Bilddatei"),
        ("Scan.tiff", "image", "Bilddatei"),
        ("Sonstiges.xyz", "generic", "XYZ-Datei"),
        ("Ohne_Endung", "unknown", "Unbekannter Dateityp"),
        ("", "unknown", "Unbekannter Dateityp"),
    ],
)
def test_file_type_badge_maps_extension_to_modifier_and_title(
    icons_module, filename: str, expected_modifier: str, expected_title: str
) -> None:
    html = _render(icons_module, filename)
    assert f"file-format-icon--{expected_modifier}" in html
    assert f'title="{expected_title}"' in html


def test_file_type_badge_same_extension_renders_identically_regardless_of_name() -> None:
    """Zwei verschiedene Dateinamen mit identischer Endung muessen exakt
    dasselbe Icon-Markup (Form+Farb-Modifier+Titel) erzeugen - echte
    Konsistenzpruefung, nicht nur "irgendein pdf-Icon"."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)))
    module = env.get_template("_icons.html").module
    first = str(module.file_type_badge("Beschluss_12C345-24.pdf"))
    second = str(module.file_type_badge("Ganz_anderer_Name.pdf"))
    assert first == second


def test_file_type_badge_renders_svg_not_text_chip(icons_module) -> None:
    """ECHTER FUND (04.10.): die Referenz `04_posteingang_nachricht_
    detail.png` zeigt ein Dokumentsymbol, kein Text-Badge - das Makro
    darf daher keine sichtbaren Format-Buchstaben mehr im Element-Text
    rendern (nur noch ueber `title`, siehe obiger Test)."""
    html = _render(icons_module, "Beschluss.pdf")
    assert "<svg" in html
    assert ">PDF<" not in html


def test_file_type_badge_long_filename_does_not_break_markup(icons_module) -> None:
    long_name = "Ein_sehr_langer_Dateiname_der_theoretisch_sehr_breit_waere_" * 3 + ".pdf"
    html = _render(icons_module, long_name)
    assert "file-format-icon--pdf" in html
    # Das Icon-Markup selbst enthaelt den Dateinamen nicht (nur `title`
    # bei Aufrufern, die ihn explizit mitgeben) - lange Namen duerfen das
    # Icon-Markup selbst nicht aufblaehen oder zerbrechen.
    assert html.count("<svg") == 1
