"""Benchmark (26.10.2026): Absender nach "<Kommunikationsnomen> von X" - Lecks und Fehlmaskierungen ueber das ECHTE
Gateway. Ausschliesslich synthetisch.

Positiv: Nachnamen, die zugleich Gattungs-/Titelwoerter sind, als Absender in 6 Kommunikationswendungen.
  - Leck (Modus "mask", nicht-interaktive Pfade): Name steht im Klartext im erlaubten Payload.
  - Ungefragt (Modus "ask", Chat): weder maskiert noch als Rueckfrage gestellt.
Negativ: Redewendungen/Abstrakta/Zeitangaben/Artikel/andere Praepositionen - jede neue Rueckfrage oder ein Platzhalter
  ueber einem Nicht-Namen ist ein Fehlalarm; zusaetzlich wird geprueft, ob Kommunikationsnomen und Datum lesbar bleiben.

Aufruf:  python scripts/bench_sender_after_communication_noun.py [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

SACH = "Akte: Beispielakte\n[Vertrag] "
NAMES = """König Kaiser Graf Bauer Sommer Winter Fuchs Wolf Hahn Vogel Engel Berg Bach Stein Busch Koch Fischer Lange Krause
Brandt Strauss Adler Haas Notar Kommissar Pfarrer Direktor Meister Richter Kaufmann""".split()
TEMPLATES = [
    "Wir bestaetigen den Eingang des Schreibens von {N} vom 16.03.2024.",
    "Der Brief von {N} ist am 12.03.2026 eingegangen.",
    "Die E-Mail von {N} wurde an die Mandantin weitergeleitet.",
    "Nach dem Anruf von {N} haben wir die Frist notiert.",
    "Die Stellungnahme von {N} liegt der Akte bei.",
    "Das Gutachten von {N} bestaetigt die Maengel.",
]
NEGATIVES = [
    ("Das Schreiben von Amts wegen ging am 12.03.2026 ein.", "Amts"),
    ("Das Schreiben von Bedeutung liegt der Akte bei.", "Bedeutung"),
    ("Der Brief von Montag liegt der Akte bei.", "Montag"),
    ("Die Mitteilung von Interesse wurde weitergeleitet.", "Interesse"),
    ("Das Gutachten von Seiten der Gegenseite liegt vor.", "Seiten"),
    ("Der Brief vom Finanzamt ist eingegangen.", "Finanzamt"),
    ("Wir bestaetigen den Eingang des Schreibens vom König vom 16.03.2024.", "König"),
    ("Der Bericht von Anfang an war unvollstaendig.", "Anfang"),
    ("Das Schreiben von Hand wurde eingescannt.", "Hand"),
    ("Die Erklaerung von Gesetzes wegen ist entbehrlich.", "Gesetzes"),
    ("Der Antrag von Juni wurde zurueckgenommen.", "Juni"),
    ("Ludwig II. war König von Bayern.", "Bayern"),
    ("Die Abnahme von Bauleistungen erfolgte am 12.03.2026.", "Bauleistungen"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    gateway = ClaudePrivacyGateway()
    started = time.perf_counter()
    leaks, unasked, positives = [], [], 0
    readable_ok = 0
    for template in TEMPLATES:
        for name in NAMES:
            text = template.format(N=name)
            positives += 1
            mask = gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + text,
                                           anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode="mask")
            payload = mask.payload.anonymisierter_sachverhalt if mask.allowed and mask.payload else ""
            if mask.allowed and name in payload:
                leaks.append(text)
            noun = template.split(" von ")[0].split()[-1]
            if mask.allowed and noun in payload and ("16.03.2024" not in text or "16.03.2024" in payload):
                readable_ok += 1
            ask = gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + text,
                                          anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode="ask")
            asked = ask.review_required and name in [c.term for c in ask.review_candidates]
            masked = bool(ask.mappings) and any(name in m.original_value for m in ask.mappings)
            if not asked and not masked:
                unasked.append(text)
    false_alarms = []
    for sentence, word in NEGATIVES:
        ask = gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + sentence,
                                      anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode="ask")
        if word in [c.term for c in ask.review_candidates] or any(m.original_value == word for m in ask.mappings):
            false_alarms.append(sentence)
    result = {
        "positiv_faelle": positives, "lecks_mask": len(leaks), "ungefragt_ask": len(unasked),
        "kommunikationsnomen_und_datum_lesbar": readable_ok,
        "negativ_faelle": len(NEGATIVES), "fehlalarme": len(false_alarms),
        "lecks_beispiele": leaks[:12], "ungefragt_beispiele": unasked[:12], "fehlalarm_saetze": false_alarms,
        "laufzeit_s": round(time.perf_counter() - started, 1),
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
