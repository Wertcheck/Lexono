"""Briefkopf und Unterzeichner: aus dem Briefkopfprofil, nie vom Sprachmodell.

Das Modell kennt die Kanzleidaten bewusst nicht und soll weder einen Briefkopf noch eine
Unterschriftszeile schreiben (Prompt-Regel "BRIEFKOPF UND UNTERSCHRIFT"). Briefkopf und Unterzeichner
stammen aus dem Briefkopfprofil des Entwurfs (`Draft.letterhead_ref`, app/firm_profile/letterheads.py)
und werden von der Anwendung angezeigt/exportiert - genau EINMAL, im Chat, im Editor und im Export.
Der gespeicherte Entwurf und der Chat-Text enthalten deshalb nur das Schreiben selbst; schreibt das
Modell trotzdem einen der frueheren Einsetz-Hinweise ("[Kanzlei einsetzen]" /
"[Unterzeichner einsetzen]"), wird er hier entfernt. Die Pruefpunkte werden dem tatsaechlichen
Zustand des Briefkopfprofils angepasst (nichts als offen melden, was vorhanden ist; konkret melden,
was fehlt)."""

from __future__ import annotations

import re
from typing import Any

from app.drafting.review_notes import REVIEW_NOTES_HEADING, split_review_notes
from app.export.letterhead import has_letterhead_content, has_signature_content

FIRM_PLACEHOLDER = "[Kanzlei einsetzen]"
SIGNATORY_PLACEHOLDER = "[Unterzeichner einsetzen]"

_PLACEHOLDER_LINES = {FIRM_PLACEHOLDER, SIGNATORY_PLACEHOLDER}


def strip_firm_placeholders(text: str) -> str:
    """Entfernt eigenstaendige Briefkopf-/Unterzeichner-Hinweiszeilen (samt folgender Leerzeile)."""
    if not text:
        return text
    kept: list[str] = []
    skip_blank = False
    for line in text.split("\n"):
        if line.strip() in _PLACEHOLDER_LINES:
            skip_blank = True
            continue
        if skip_blank and not line.strip():
            skip_blank = False
            continue
        skip_blank = False
        kept.append(line)
    return "\n".join(kept).strip("\n")


_FIRM_NOTE = re.compile(r"kanzlei-?briefkopf|kanzleiname|briefkopf|kanzlei einsetzen", re.IGNORECASE)
_SIGNATORY_NOTE = re.compile(r"unterzeichner|unterschrift", re.IGNORECASE)
_STATE_WORDS = re.compile(r"einsetz|offen|fehl|belassen|ergänz|nicht (?:angegeben|vorhanden)", re.IGNORECASE)


def compose_letter_notes(text: str, letterhead: Any) -> str:
    """Chat-/Ergebnistext: das Schreiben OHNE Briefkopf/Unterzeichner-Hinweise plus die auf den
    tatsaechlichen Briefkopf-Zustand abgestimmten Pruefpunkte (ausserhalb des Schreibens)."""
    letter, notes = split_review_notes(text)
    letter = strip_firm_placeholders(letter)
    firm_ok = bool(has_letterhead_content(letterhead))
    signatory_ok = bool(has_signature_content(letterhead))
    kept: list[str] = []
    for line in notes.splitlines():
        stateful = bool(_STATE_WORDS.search(line))
        if stateful and _FIRM_NOTE.search(line):
            continue
        if stateful and _SIGNATORY_NOTE.search(line):
            continue
        kept.append(line)
    pending = []
    if not firm_ok:
        pending.append("Briefkopf")
    if not signatory_ok:
        pending.append("Unterzeichner")
    if pending:
        kept.append(
            f"- {' und '.join(pending)} sind im gewählten Briefkopfprofil nicht hinterlegt - "
            "bitte unter Einstellungen → Briefköpfe ergänzen."
        )
    new_notes = "\n".join(line for line in kept if line.strip())
    if not new_notes:
        return letter
    return f"{letter}\n\n{REVIEW_NOTES_HEADING}\n{new_notes}"
