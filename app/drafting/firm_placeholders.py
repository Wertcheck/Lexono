"""Kanzleidaten lokal in Schreiben einsetzen (Briefkopf/Unterzeichner).

Die KI schreibt fuer Absender und Unterschrift feste Einsetz-Hinweise
("[Kanzlei einsetzen]", "[Unterzeichner einsetzen]"; siehe
WRITING_SYSTEM_PROMPT) - sie kennt die Kanzleidaten bewusst nicht und darf sie nicht
erfinden. Die echten Angaben stehen zentral im Kanzlei-Profil (`FirmProfile`). Dieses
Einsetzen passiert NACH der Rueckuebersetzung und rein lokal: die Kanzleidaten gehen
nie an die Cloud. Ist im Profil nichts hinterlegt, bleibt der Hinweis stehen (ehrlich
statt erfundener Absender)."""

from __future__ import annotations

from app.export.letterhead import address_and_contact_lines
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
