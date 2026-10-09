"""Kanzleidaten lokal in Schreiben einsetzen (Briefkopf/Unterzeichner).

Die KI schreibt fuer Absender und Unterschrift feste Einsetz-Hinweise
("[Kanzlei einsetzen]", "[Unterzeichner einsetzen]"; siehe
WRITING_SYSTEM_PROMPT) - sie kennt die Kanzleidaten bewusst nicht und darf sie nicht
erfinden. Die echten Angaben stehen zentral im Kanzlei-Profil (`FirmProfile`). Dieses
Einsetzen passiert NACH der Rueckuebersetzung und rein lokal: die Kanzleidaten gehen
nie an die Cloud. Ist im Profil nichts hinterlegt, bleibt der Hinweis stehen (ehrlich
statt erfundener Absender)."""

from __future__ import annotations

import re

from app.drafting.review_notes import REVIEW_NOTES_HEADING, split_review_notes
from app.export.letterhead import address_and_contact_lines, has_letterhead_content, has_signature_content
from app.models import FirmProfile

FIRM_PLACEHOLDER = "[Kanzlei einsetzen]"
SIGNATORY_PLACEHOLDER = "[Unterzeichner einsetzen]"


def fill_firm_placeholders(text: str, firm_profile: FirmProfile | None) -> str:
    if firm_profile is None or not text:
        return text
    firm_name = (firm_profile.firm_name or "").strip()
    if firm_name and FIRM_PLACEHOLDER in text:
        address = address_and_contact_lines(firm_profile)[:1]
        text = text.replace(FIRM_PLACEHOLDER, "\n".join([firm_name, *address]))
    signatory = (firm_profile.signatory_name or "").strip()
    if signatory and SIGNATORY_PLACEHOLDER in text:
        text = text.replace(SIGNATORY_PLACEHOLDER, signatory)
    return text


def strip_firm_placeholders(text: str, firm_profile: FirmProfile | None) -> str:
    """Fuer den GESPEICHERTEN Entwurf: Editor und Export rendern Briefkopf und Unterzeichner
    selbst aus dem Kanzlei-Profil (app/export/letterhead.py). Stuende derselbe Hinweis (oder
    nach `fill_firm_placeholders` die eingesetzten Daten) zusaetzlich im Text, erschiene der
    Briefkopf doppelt. Deshalb entfaellt die jeweilige Hinweiszeile genau dann, wenn das
    Profil den Teil tatsaechlich liefert; sonst bleibt sie als ehrlicher Hinweis stehen."""
    if firm_profile is None or not text:
        return text
    drop = set()
    if has_letterhead_content(firm_profile):
        drop.add(FIRM_PLACEHOLDER)
    if has_signature_content(firm_profile):
        drop.add(SIGNATORY_PLACEHOLDER)
    if not drop:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    skip_blank = False
    for line in lines:
        if line.strip() in drop:
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


def apply_firm_data(text: str, firm_profile: FirmProfile | None) -> str:
    """Chat-Anzeige: Kanzleidaten lokal einsetzen UND die Prueffpunkte dem tatsaechlichen Zustand
    anpassen. Was das Profil liefert, wird nicht zugleich als "offen" gemeldet (das Modell kennt
    das Profil nicht und schreibt sonst "Kanzleiname als Einsetz-Hinweis belassen"); fehlt dagegen
    wirklich etwas, steht ein konkreter Hinweis (statt eines nur beilaeufig erwaehnten)."""
    filled = fill_firm_placeholders(text, firm_profile)
    letter, notes = split_review_notes(filled)
    if not notes and FIRM_PLACEHOLDER not in letter and SIGNATORY_PLACEHOLDER not in letter:
        return filled
    firm_ok = firm_profile is not None and has_letterhead_content(firm_profile)
    signatory_ok = firm_profile is not None and has_signature_content(firm_profile)
    kept: list[str] = []
    for line in notes.splitlines():
        stateful = bool(_STATE_WORDS.search(line))
        if stateful and firm_ok and _FIRM_NOTE.search(line):
            continue
        if stateful and signatory_ok and _SIGNATORY_NOTE.search(line):
            continue
        kept.append(line)
    pending = []
    if FIRM_PLACEHOLDER in letter:
        pending.append("Briefkopf")
    if SIGNATORY_PLACEHOLDER in letter:
        pending.append("Unterzeichner")
    if pending:
        kept.append(
            f"- {' und '.join(pending)} sind noch einzusetzen - im Kanzlei-Profil (Einstellungen) "
            "nicht hinterlegt."
        )
    new_notes = "\n".join(line for line in kept if line.strip())
    if not new_notes:
        return _without_notes(filled)
    return _with_notes(filled, new_notes)


def _without_notes(text: str) -> str:
    return split_review_notes(text)[0].rstrip()


def _with_notes(text: str, notes: str) -> str:
    return f"{split_review_notes(text)[0].rstrip()}\n\n{REVIEW_NOTES_HEADING}\n{notes}"
