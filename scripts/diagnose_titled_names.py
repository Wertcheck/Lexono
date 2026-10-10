"""Diagnose: gelangt ein Nachname nach "Dr."/"Prof."/"Herr"/"Frau" im Klartext in den Cloud-Payload? (10.10.2026)

Prueft ~100 synthetische Saetze (Titel x Name x Satzbau) ueber das echte Datenschutz-Gateway und zaehlt, wie oft der
Nachname trotz "erlaubt" im ausgehenden Payload steht (Leck) bzw. das Gateway fail-closed blockiert.
VORHER (nur NER): 13 von 96 Saetzen mit Leck, alle nach "Dr."/"Prof. Dr.". NACHHER (detect_titled_person): 0.
Aufruf:  python scripts/diagnose_titled_names.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

NAMES = ["Wiebe", "Kostka", "Thiessen", "Lindqvist", "Brinkmöller", "Hasenclever"]
TEMPLATES = [
    "Herr {N} hat die Abnahme erklärt.",
    "Frau {N} hat die Abnahme erklärt.",
    "Wir bitten Herrn {N} um Rückmeldung.",
    "Das Schreiben von Frau {N} liegt vor.",
    "Ansprechpartner ist Rechtsanwalt {N} aus Hamburg.",
    "Zeuge ist der Nachbar {N}.",
    "Gutachter Dr. {N} prüft die Anlage.",
    "Das Gutachten von Dr. {N} liegt vor.",
    "Der Schiedsgutachter Dr. {N} wird bestellt.",
    "Schiedsgutachter ist Dr. {N}.",
    "Wir haben Dr. {N} beauftragt, die Mängel zu begutachten.",
    "Ansprechpartner ist Prof. Dr. {N} von der Hochschule.",
    "Gerichtsstand Beispielstadt. Schiedsgutachter Dr. {N}. Nachfrist bis zum 30.11.2026.",
    "Die Stellungnahme von Dr. Jonas {N} wurde gestern eingereicht.",
    "Herr Dr. {N} hat die Abnahme erklärt.",
    "Frau Dr. {N} hat die Abnahme erklärt.",
]


def main() -> None:
    leaks: list[str] = []
    blocked = 0
    total = 0
    for template in TEMPLATES:
        for name in NAMES:
            text = template.format(N=name)
            result = ClaudePrivacyGateway().prepare_request(
                purpose="formulate_draft", sachverhalt=text, argumentationspunkte=[], quellenverweise=[], stil=None,
                vorlage=None, anwaltliche_anmerkungen="Erstelle ein Schreiben.", known_entities=None,
                gespraechsverlauf=[], skip_general_knowledge_pseudonymization=False,
            )
            total += 1
            if not result.allowed:
                blocked += 1
            elif name in result.payload.anonymisierter_sachverhalt:
                leaks.append(text)
    print(f"{len(leaks)} von {total} Sätzen: Nachname im Klartext im Cloud-Payload (Gateway erlaubt)")
    print(f"davon vom Gateway blockiert (fail-closed): {blocked}")
    for text in leaks:
        print("  LECK:", text)


if __name__ == "__main__":
    main()
