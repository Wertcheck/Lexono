"""Chat ohne Akte (skip_general_knowledge_pseudonymization=True): Klartext-Lecks und Fehlalarme im tatsaechlichen Payload (12.10.2026).

Synthetische Faelle: 55 Nachnamen x 8 Kontexte, 14 Fachfragen/Imperative, Fachbegriffe aus diagnose_ner_false_positives.py x 4 Kontexte.
Vergleicht den Produktivcode (presidio_ner.drop_common_noun_persons inkl. get_name_role_tokens) mit der Referenz "Filter aus".
Aufruf:  python scripts/bench_chat_without_file.py
Gemessen am 12.10.2026: Vor der Aenderung 16,4 % Lecks (72/440), danach 7,7 % (34/440), Filter aus 7,7 %; Fachfragen 1/14 in allen Varianten.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_name_detection as B  # noqa: E402
import diagnose_ner_false_positives as D  # noqa: E402

import app.privacy.gateway as gw  # noqa: E402
from app.privacy import presidio_ner as pn  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

SACH = "Akte: (kein spezifischer Fall zugeordnet)"
TEMPLATES = [
    "{N} hat angerufen und bittet um Rückruf.",
    "{N} erklärt, dass die Anlage mangelhaft ist.",
    "Dazu erklärt {N}, die Anlage sei mangelhaft.",
    "Herr {N} hat die Abnahme erklärt.",
    "Name: {N}  Funktion: Zeuge  Datum: 12.03.2026",
    "Die Unterlagen wurden von {N} geprüft und freigegeben.",
    "Die Unterlagen wurden am 12.03.2026 durch\n{N} geprüft und übergeben.",
    "Zeugenaussage {N}\nDer Zeuge schildert den Ablauf.",
]
QUESTIONS = [
    "Gilt das auch für Gewerbemietverträge?", "Was bedeutet Nacherfüllung im Kaufrecht?", "Welche Frist gilt für die Berufungsbegründung?",
    "Wie wirkt sich Verzug auf den Schadensersatz aus?", "Ist die Räumungsfrist angemessen?", "Kündigung wegen Eigenbedarf: Voraussetzungen?",
    "Was ist ein Sicherheitseinbehalt bei Werkverträgen?", "Gewährleistung und Verjährung beim Bauvertrag erläutern.",
    "Erstelle ein Schreiben an die Gegenseite.", "Fasse das Dokument zusammen.", "Überarbeite den Entwurf und kürze ihn.",
    "Berücksichtige § 634 BGB und die VOB/B.", "Wer haftet bei Mängeln der Dämmung?", "Verzug liegt vor, Schadensersatz ist zu leisten.",
]
TERM_TEMPLATES = ["{X} ist erforderlich.", "Die Unterlagen wurden von {X} geprüft.", "Name: {X}  Funktion: Zeuge", "Dazu erklärt {X}, es sei so."]


def call(gateway: ClaudePrivacyGateway, text: str):
    return gateway.prepare_request(
        purpose="chat_response", sachverhalt=SACH, argumentationspunkte=[], quellenverweise=[], stil=None, vorlage=None,
        anwaltliche_anmerkungen=text, known_entities=None, gespraechsverlauf=[], skip_general_knowledge_pseudonymization=True,
    )


def run(label: str) -> None:
    gateway = ClaudePrivacyGateway()
    started = time.perf_counter()
    leaks = total = blocks = 0
    by_context: dict[str, int] = {}
    for name in B.COMMON + B.RARE + B.FOREIGN:
        for template in TEMPLATES:
            total += 1
            result = call(gateway, template.format(N=name))
            if not result.allowed:
                blocks += 1
            elif name in result.payload.anonymisierte_anwaltliche_anmerkungen:
                leaks += 1
                by_context[template[:14]] = by_context.get(template[:14], 0) + 1
    changed = []
    for question in QUESTIONS:
        result = call(gateway, question)
        if not result.allowed or result.payload.anonymisierte_anwaltliche_anmerkungen != question:
            changed.append(question[:30])
    terms = set()
    for word in D.TERMS:
        for template in TERM_TEMPLATES:
            result = call(gateway, template.format(X=word))
            if not result.allowed or word not in result.payload.anonymisierte_anwaltliche_anmerkungen:
                terms.add(word)
    print(
        f"{label}: Lecks {leaks}/{total} ({100 * leaks / total:.1f} %), Blocks {blocks}, Fachfragen veraendert {len(changed)}/{len(QUESTIONS)} "
        f"{changed}, Fachbegriffe betroffen {len(terms)}/{len(D.TERMS)}, {time.perf_counter() - started:.0f} s"
    )
    print("   Lecks je Kontext:", by_context)


if __name__ == "__main__":
    gw.drop_common_noun_persons = pn.drop_common_noun_persons
    run("Produktivcode")
    gw.drop_common_noun_persons = lambda text, spans: spans
    run("Referenz: Filter aus (NICHT produktiv)")
