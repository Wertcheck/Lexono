"""Trennung von Schreiben und "Offene Prüfpunkte / Hinweise".

ECHTER FUND (Real-E2E 08.10., installierter Build 5e5f960, Drafting aus
analysierten Dokumenten): Claude schrieb offene Punkte INLINE in den
kopierbaren Schriftsatz ("[Offener Prüfpunkt: ...]" mitten im Text,
"[PRÜFPUNKT: ...]" in Anträgen/Anlagen/Unterschrift, "(Hinweis: ...)" unter
dem Mandantenschreiben). Der Schriftsatz war damit weder kopier- noch
versendbar, die Hinweise nicht vom eigentlichen Schreiben getrennt.

Strukturelle Lösung statt Nachbearbeitung einzelner Formulierungen: der
Systemprompt verlangt die Prüfpunkte AUSSCHLIESSLICH in einem eigenen
Schlussblock mit fester Überschriftzeile (`REVIEW_NOTES_HEADING`); diese
Funktion trennt Schreiben und Hinweise an genau dieser Zeile. Das Schreiben
(Draft/Editor/Export/Kopieren) enthält danach nie die Hinweise, die UI zeigt
sie in einem eigenen Kasten.
"""

from __future__ import annotations

import re

#: Feste Überschrift des Hinweisblocks (Anzeige-Text, auch im Prompt genannt).
REVIEW_NOTES_HEADING = "OFFENE PRÜFPUNKTE / HINWEISE – NICHT BESTANDTEIL DES SCHREIBENS"

# Bewusst NUR der volle Präfix "offene prüfpunkte / hinweise": eine normale
# Analyse darf weiter einen Abschnitt "Offene Prüfpunkte" im Fließtext haben
# (dort sind sie Inhalt der Antwort, nicht Beiwerk zu einem Schreiben).
_HEADING_LINE = re.compile(r"^[\s#>*_\-]*offene\s+prüfpunkte\s*/\s*hinweise\b", re.IGNORECASE)
_SEPARATOR_LINE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def split_review_notes(text: str | None) -> tuple[str, str]:
    """Liefert `(schreiben, hinweise)`.

    Getrennt wird an der ERSTEN Zeile, die mit der festen Überschrift beginnt
    (Markdown-Zeichen davor werden toleriert). `hinweise` ist der Text nach
    dieser Zeile ohne die Überschrift selbst. Ohne Überschrift oder wenn vor
    ihr kein Text steht, bleibt alles beim Schreiben und `hinweise` ist leer
    (nie eine leere Nachricht erzeugen)."""
    if not text:
        return "", ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _HEADING_LINE.match(line):
            body = lines[:index]
            while body and (not body[-1].strip() or _SEPARATOR_LINE.match(body[-1])):
                body.pop()
            if not body:
                return text, ""
            notes = "\n".join(lines[index + 1 :]).strip()
            return "\n".join(body), notes
    return text, ""
