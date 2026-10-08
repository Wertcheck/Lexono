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


#: Markierungszeilen um den eigentlichen Schreibtext (Prompt-Regel "SCHREIBEN-BEGRENZUNG").
#: Alles AUSSERHALB (Vorbemerkung, Erlaeuterung) ist nicht Teil des Schreibens und wird als
#: Hinweis getrennt angezeigt - ECHTER FUND (Real-E2E 08.10.): bei einer Ueberarbeitung stand
#: ein Einleitungsabsatz im kopierbaren Text; "bitte keinen Einleitungssatz" allein ist nicht
#: verlaesslich genug, die strukturelle Begrenzung dagegen schon.
LETTER_START_MARKER = "=== SCHREIBEN ==="
LETTER_END_MARKER = "=== ENDE SCHREIBEN ==="
_LETTER_START = re.compile(r"^[\s#>*_\-]*={2,}\s*SCHREIBEN\s*={2,}[\s*_]*$", re.IGNORECASE | re.MULTILINE)
_LETTER_END = re.compile(r"^[\s#>*_\-]*={2,}\s*ENDE\s+SCHREIBEN\s*={2,}[\s*_]*$", re.IGNORECASE | re.MULTILINE)


def split_review_notes(text: str | None) -> tuple[str, str]:
    """Liefert `(schreiben, hinweise)`.

    1. Ist das Schreiben mit den Markierungszeilen eingefasst, ist `schreiben` NUR der Text
       dazwischen; Text davor und danach (Vorbemerkungen, Erlaeuterungen) wird den Hinweisen
       vorangestellt.
    2. Zusaetzlich (und auch ohne Markierungen) wird am Hinweisblock mit der festen
       Ueberschrift getrennt, siehe `_split_heading`."""
    if not text:
        return "", ""
    start = _LETTER_START.search(text)
    if start is None:
        letter, notes = _split_heading(_LETTER_END.sub("", text))
        return letter, notes
    before = text[: start.start()].strip()
    remainder = text[start.end() :]
    end = _LETTER_END.search(remainder)
    if end is not None:
        body, after = remainder[: end.start()], remainder[end.end() :]
    else:
        body, after = remainder, ""
    body_letter, body_notes = _split_heading(body)
    after_letter, after_notes = _notes_after_letter(after)
    extra = [part.strip() for part in (before, after_letter) if part.strip()]
    notes_parts = [*extra, *(n for n in (body_notes, after_notes) if n)]
    letter = body_letter.strip("\n")
    if not letter.strip():
        return text, ""
    return letter, "\n\n".join(notes_parts).strip()


def _notes_after_letter(text: str) -> tuple[str, str]:
    """Wie `_split_heading`, aber fuer den Text NACH der Endmarkierung: beginnt er direkt mit
    der Hinweis-Ueberschrift, ist alles danach der Hinweisblock und die Ueberschrift selbst
    wird NICHT noch einmal als Text uebernommen (sonst erschiene sie im Kasten doppelt)."""
    if not text.strip():
        return "", ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _HEADING_LINE.match(line):
            before = "\n".join(lines[:index]).strip()
            return before, "\n".join(lines[index + 1 :]).strip()
    return text.strip(), ""


def _split_heading(text: str) -> tuple[str, str]:

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
