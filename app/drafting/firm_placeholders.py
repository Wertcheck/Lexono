"""Kanzleidaten lokal in Schreiben einsetzen (Briefkopf/Unterzeichner).

Die KI schreibt fuer Absender und Unterschrift feste Einsetz-Hinweise
("[Kanzlei einsetzen]", "[Unterzeichner einsetzen]"; siehe
WRITING_SYSTEM_PROMPT) - sie kennt die Kanzleidaten bewusst nicht und darf sie nicht
erfinden. Die echten Angaben stehen zentral im Kanzlei-Profil (`FirmProfile`). Dieses
Einsetzen passiert NACH der Rueckuebersetzung und rein lokal: die Kanzleidaten gehen
nie an die Cloud. Ist im Profil nichts hinterlegt, bleibt der Hinweis stehen (ehrlich
statt erfundener Absender)."""

from __future__ import annotations

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
