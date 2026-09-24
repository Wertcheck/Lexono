"""sanitize_for_base14_font – schützt vor einem echten, stillen
Zeichen-Korruptions-Fund in beiden PyMuPDF-basierten PDF-Exporten
(`app/export/pdf_export_service.py`, `app/document_generator/
pdf_export.py`, 19.09.).

ECHTER FUND (per direktem Rendering-Test, nicht vermutet): die dort
verwendeten PDF-Standard-14-Schriften ("helv"/"hebo", `PyMuPDF.
Page.insert_text`) unterstützen NUR eine eingeschränkte Zeichenmenge.
Umlaute/ß/§/°/© rendern korrekt - aber der Halbgeviertstrich (–),
Geviertstrich (—), typografische An-/Abführungszeichen (' ' " "),
Auslassungspunkte (…), das Aufzählungszeichen (•) UND das Euro-Zeichen
(€) werden OHNE Fehler/Warnung durch einen falschen Mittelpunkt-
Platzhalter (·) ersetzt - für ein deutsches Kanzleischreiben (das
routinemäßig Euro-Beträge und von Claude typografisch korrekt gesetzte
Gedankenstriche/Anführungszeichen enthält) ein echter, bisher
unentdeckter Korruptionsfehler im druckfertigen Ergebnis.

Der DOCX-Export ist NICHT betroffen (OOXML/Word unterstützt vollen
Unicode) - dieser Fund betrifft ausschließlich die beiden PDF-Exporte.

Bewusst KEINE eingebettete TrueType-Schrift als Fix (groessere
Architekturaenderung: Schrift-Datei mitliefern, PyInstaller-Bündelung,
Lizenzpruefung) - stattdessen die kleinstmoegliche, korrekte Korrektur:
betroffene Zeichen vor dem Schreiben durch bedeutungsgleiche, von den
Standard-14-Schriften tatsaechlich unterstuetzte Zeichen ersetzen. Kein
Informationsverlust in der Bedeutung (nur die Typografie wird schlichter,
z. B. Halbgeviertstrich -> Bindestrich, "EUR" statt €-Symbol - Betraege
bleiben unveraendert lesbar)."""

from __future__ import annotations

_REPLACEMENTS: dict[str, str] = {
    "–": "-",  # – Halbgeviertstrich (EN DASH)
    "—": "-",  # — Geviertstrich (EM DASH)
    "‘": "'",  # ' typografisches linkes einfaches Anführungszeichen
    "’": "'",  # ' typografisches rechtes einfaches Anführungszeichen
    "‚": ",",  # ‚ tiefgestelltes einfaches Anführungszeichen
    "“": '"',  # " typografisches linkes doppeltes Anführungszeichen
    "”": '"',  # " typografisches rechtes doppeltes Anführungszeichen
    "„": '"',  # „ deutsches tiefgestelltes doppeltes Anführungszeichen
    "…": "...",  # … Auslassungspunkte
    "•": "-",  # • Aufzählungszeichen
    "€": "EUR",  # € Euro-Zeichen (kein Glyph in den Standard-14-Schriften)
}


def sanitize_for_base14_font(text: str) -> str:
    """Ersetzt Zeichen, die die PDF-Standard-14-Schriften ("helv"/"hebo")
    nicht korrekt darstellen können, durch bedeutungsgleiche Alternativen.
    Alles andere (inkl. Umlaute/ß/§/°/©, die bereits korrekt rendern)
    bleibt unverändert."""
    for original, replacement in _REPLACEMENTS.items():
        if original in text:
            text = text.replace(original, replacement)
    return text
