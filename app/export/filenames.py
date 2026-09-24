"""safe_download_filename – gemeinsame Dateinamen-Bereinigung für alle
Export-/Download-Routen (19.09., echter Fund waehrend der vollstaendigen
Dokument-Lifecycle-Verifikation).

ECHTER FUND: `app/web/drafts_router.py` (PDF- UND DOCX-Export) und
`app/web/document_generator_router.py` filterten Dateinamen bisher je
EIGENSTAENDIG per `c.isalnum() or c in " -_"` - ein Halbgeviertstrich (–)
im Aktentitel (z. B. "Einspruch Steuerbescheid 2025 – sabine", ECHT in
der Produktions-DB per Live-Test bestätigt) ist WEDER alphanumerisch
NOCH in der erlaubten Zeichenmenge " -_" enthalten (die dort enthaltene
"-" ist ein gewöhnlicher ASCII-Bindestrich, kein Halbgeviertstrich) -
das Zeichen wurde dadurch stillschweigend GELÖSCHT statt ersetzt,
Ergebnis: ein Dateiname mit einem hässlichen doppelten Leerzeichen
("...2025  sabine_v2.pdf" statt "...2025 - sabine_v2.pdf"). Funktional
unschädlich (Datei lässt sich weiterhin öffnen), aber sichtbar
unsauber - dieselbe Zeichenklasse wie der bereits behobene PDF-Text-
Korruptionsfehler (app/export/pdf_text.py), hier aber im Dateinamen statt
im Dokumentinhalt.

Bewusst als GEMEINSAME Funktion statt einer dritten, unabhängigen Kopie -
alle drei bisherigen Aufrufstellen nutzen jetzt diese eine Implementierung."""

from __future__ import annotations

from app.export.pdf_text import sanitize_for_base14_font


def safe_download_filename(text: str, *, fallback: str = "Dokument") -> str:
    """Bereinigt `text` für die Verwendung als Dateiname in einem
    `Content-Disposition`-Header. Ersetzt erst typografische Zeichen
    (Halbgeviertstrich etc., siehe `sanitize_for_base14_font`) durch
    ASCII-Äquivalente, entfernt danach alles, was kein Buchstabe/Zahl/
    Leerzeichen/Bindestrich/Unterstrich ist, und fällt bei einem leeren
    Ergebnis auf `fallback` zurück statt einen leeren Dateinamen zu
    erzeugen."""
    normalized = sanitize_for_base14_font(text)
    kept = "".join(c for c in normalized if c.isalnum() or c in " -_")
    return kept.strip() or fallback
