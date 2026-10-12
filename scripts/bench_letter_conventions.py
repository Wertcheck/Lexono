"""Benchmark (Folgezyklus 13.10.2026): Namen in Brief-/Kanzleikonventionen. Getrennt von den anderen Benchmarks.

Goldstandard (vorab festgelegt, ausschliesslich synthetisch): jeder Nachname (und Vorname) darf NIE im Klartext im
Payload stehen. Mitgefuehrt werden Fachbegriffe/Betraege, die lesbar bleiben muessen.
Namen bewusst gemischt: Gattungswort-Nachnamen (Sommer, Winter, Vogel, Fuchs, Koch, Bauer), haeufige (Hartmann, Krause),
seltene/erfundene (Brockhagen, Tellkampf, Ostendarp)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

SURNAMES = ["Sommer", "Winter", "Vogel", "Fuchs", "Koch", "Bauer", "Hartmann", "Krause", "Brockhagen", "Tellkampf", "Ostendarp"]
FIRST = "Klaus"
TEMPLATES = [
    "Sehr geehrte Frau {N},\nwir nehmen Bezug auf die Nachbesserungsfrist bis zum 30.11.2026.",
    "Sehr geehrter Herr {N},\nder Betrag von 1.200,00 EUR ist offen.",
    "z. H. Herrn {N}\nBetreff: Mängelanzeige",
    "Eheleute {N}\nHafenstraße 9\n24105 Kiel",
    "Ehepaar {N} hat die Kaution von 850,00 EUR gezahlt.",
    "Herrn und Frau {N} wurde die Mängelanzeige zugestellt.",
    "Familie {N} hat die Mietminderung angekündigt.",
    "{N}, {F}\nGeschäftszeichen: 12/2026",
    "K. {N} hat die Abnahme verweigert.",
    "Mit freundlichen Grüßen\n{F} {N}\nRechtsanwalt",
    "Unterschrift: {N}",
    "gez. {N}",
    "Ansprechpartner: {N}",
    "In Sachen {N} ./. Baugesellschaft wegen Werklohn",
    "In Sachen Baugesellschaft ./. {N} wegen Werklohn",
    "Mandantin {N} fordert Schadensersatz.",
    "Die Eheleute {N} haben die Miete gemindert.",
    "Ihr Mandant, Herr {N}, hat die Frist versäumt.",
    "Wie mit Ihrer Kollegin, Frau Dr. {N}, besprochen, bleibt der Streitwert bei 12.400,00 EUR.",
    "Absender: {F} {N}, Lindenallee 4, 24103 Kiel",
]
KEEP = ["Nachbesserungsfrist", "1.200,00", "Mängelanzeige", "850,00", "Kaution", "Mietminderung", "Abnahme",
        "Schadensersatz", "Miete", "Frist", "Streitwert", "12.400,00", "Werklohn"]

_GATEWAY = ClaudePrivacyGateway()


def _word(text: str, word: str) -> bool:
    return re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(word) + r"(?![\wÄÖÜäöüß])", text) is not None


def run() -> dict:
    leaks, lost, total = [], [], 0
    for name in SURNAMES:
        for template in TEMPLATES:
            text = template.format(N=name, F=FIRST)
            result = _GATEWAY.prepare_request(
                purpose="chat_response",
                sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
                anwaltliche_anmerkungen="Fasse das Dokument zusammen.",
                review_mode="mask",
            )
            total += 1
            payload = result.payload.anonymisierter_sachverhalt if result.payload else ""
            if result.allowed and (_word(payload, name) or ("{F}" in template and _word(payload, FIRST))):
                leaks.append(f"{name} | {template[:40]!r}")
            for keep in KEEP:
                if _word(text, keep) and not (result.allowed and keep in payload):
                    lost.append(f"{keep} | {template[:40]!r}")
    return {"total": total, "lecks": len(leaks), "leck_beispiele": leaks, "verlorene_fachinhalte": len(lost),
            "verlust_beispiele": sorted(set(lost))}


if __name__ == "__main__":
    result = run()
    print(f"Brief-Konventionen: Name im Klartext {result['lecks']}/{result['total']}, "
          f"verlorene Fachinhalte {result['verlorene_fachinhalte']}")
    for line in result["leck_beispiele"]:
        print("   LECK:", line)
    for line in result["verlust_beispiele"]:
        print("   VERLUST:", line)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
