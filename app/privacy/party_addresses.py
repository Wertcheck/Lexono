"""Zuordnung Beteiligte -> Anschrift, lokal aus der Dokumentstruktur.

Root Cause (Real-E2E 09.10.): In "Verkaeufer: Dirk Neumann, Lindenallee 3, 30000 Beispielstadt"
sieht das Modell nach der Pseudonymisierung nur "[PERSON_01], [ADRESSE_01]" und schrieb trotzdem
"[Anschrift einsetzen]" - eine rein positionelle Zuordnung im Fliesstext war ihm zu unsicher
(auch nach dem Zusammenfassen von Strasse und PLZ/Ort zu EINEM Platzhalter und einer
Prompt-Regel). Wie bei der Chronologie (app/privacy/date_chronology.py) ist die robustere Loesung
strukturierte lokale Verarbeitung: Person/Organisation und Anschrift, die im Dokument DIREKT
nebeneinander stehen, werden als verlaessliche Zuordnung mitgegeben - ausschliesslich als
Platzhalter, nie mit Klartext. Es werden nur unmittelbar benachbarte Angaben zugeordnet; nichts
wird erfunden oder einem anderen Beteiligten zugeschrieben."""

from __future__ import annotations

import re

PARTY_ADDRESS_MARKER = "Zuordnung der Beteiligten zu Anschriften laut Dokument"

# "[PERSON_01], [ADRESSE_01]" / "[ORGANISATION_02]  [ADRESSE_03]" (Komma/Semikolon und/oder
# Zeilenumbruch = zwei Leerzeichen dazwischen, sonst nichts).
_PARTY_ADDRESS = re.compile(
    r"(\[(?:PERSON|ORGANISATION)_\d{2,}\])[ \t]*[,;]?[ \t]*(?:\n|[ \t]{2})?[ \t]*(\[ADRESSE_\d{2,}\])"
)


def build_party_address_note(pseudonymized_text: str) -> str | None:
    pairs: list[tuple[str, str]] = []
    for match in _PARTY_ADDRESS.finditer(pseudonymized_text):
        pair = (match.group(1), match.group(2))
        if pair not in pairs:
            pairs.append(pair)
    if not pairs:
        return None
    listed = "; ".join(f"{party} wohnt/sitzt unter {address}" for party, address in pairs)
    return (
        f"{PARTY_ADDRESS_MARKER} (lokal aus der Dokumentstruktur ermittelt, nur direkt benachbarte "
        f"Angaben): {listed}. Verwende für den Empfänger- oder Absenderblock einer genannten Person "
        "genau diesen Adress-Platzhalter."
    )
