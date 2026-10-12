"""Benchmark (Folgezyklus 13.10.2026): Nachnamen in Folgeerwaehnungen und nach Artikel - NEUE Namen, vom bisherigen
Artikel-Benchmark (scripts/bench_article_noun_persons.py) getrennt, damit Regeln nicht an dessen 12 Namen angepasst werden.

Goldstandard (vorab festgelegt, ausschliesslich synthetisch; Nachnamen aus oeffentlichen Haeufigkeitslisten bzw.
erfundene seltene Namen - keine realen Personen/Mandanten):

A  Einfuehrung + Folgeerwaehnung: "Am ... erschien {INTRO} {N} ..." gefolgt von einer Kurzerwaehnung ohne Anrede.
   Leck = der Nachname steht IRGENDWO im Payload im Klartext.
B  Nachname nach Artikel ohne Einfuehrung (feminin "die"/"der" Dativ und maskulin "der").
N  Negativfaelle (muessen lesbar bleiben): Berufs-/Rollenwoerter mit Artikel, feminine Nomen auf -er, Fachbegriffe,
   Geldbetraege.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

# Haeufige deutsche Nachnamen (inkl. Berufsnamen = zugleich Gattungswoerter) + erfundene seltene Namen.
NAMES = [
    "Hartmann", "Lehmann", "Kaufmann", "Neumann", "Baumann", "Herrmann", "Schuhmann", "Seemann",
    "Schneider", "Fischer", "Weber", "Becker", "Schreiber", "Schuster", "Jäger", "Ziegler",
    "Krüger", "Köhler", "Wagner", "Bauer", "Koch", "Wolf", "König", "Kaiser", "Fuchs", "Vogel", "Hahn", "Winter",
    "Sommer", "Engel", "Busch", "Horn", "Graf", "Stein", "Schmitz", "Kowalski", "Nowak", "Yilmaz",
    "Brockhagen", "Tellkampf", "Quernheim", "Ostendarp", "Schwienhorst", "Vosskuhle", "Fliedermeier", "Wendehals",
]
INTROS = ["Herr", "Frau", "Herr Dr.", "Zeugin", "Rechtsanwältin", "Thomas", "Petra"]
FOLLOWUPS = [
    "{N} hat die Unterlagen am Folgetag übergeben.",
    "Nach Rücksprache hat die {N} die Unterlagen übergeben.",
    "Die Mail von {N} ist eingegangen.",
    "Das Schreiben ging an {N} zurück.",
]
ARTICLE_FRAMES = [
    "Nach Rücksprache hat die {N} die Unterlagen übergeben.",
    "Im Termin wurde mit der {N} die Zahlung von 1.200,00 EUR erörtert.",
    "Der {N} hat am 12.03.2026 angerufen.",
]
NEGATIVES = [
    ("Der Richter hat den Termin auf den 30.11.2026 verlegt.", ["Richter", "Termin"]),
    ("Der Kaufmann im Sinne des HGB haftet für die Verbindlichkeiten.", ["Kaufmann", "HGB"]),
    ("Der Bauer hat die Pacht von 1.200,00 EUR gezahlt.", ["Bauer", "Pacht", "1.200,00"]),
    ("Der Fachmann hat den Schaden begutachtet.", ["Fachmann", "Schaden"]),
    ("Der Hauptmann der Feuerwehr war vor Ort.", ["Hauptmann", "Feuerwehr"]),
    ("Der Zimmermann hat den Dachstuhl repariert.", ["Zimmermann", "Dachstuhl"]),
    ("Der Schneider hat die Hose gekürzt.", ["Schneider", "Hose"]),
    ("Die Steuer wurde mit 1.200,00 EUR festgesetzt.", ["Steuer", "1.200,00"]),
    ("Die Kammer hat die Berufung zurückgewiesen.", ["Kammer", "Berufung"]),
    ("Die Dauer des Verfahrens ist unangemessen.", ["Dauer", "Verfahrens"]),
    ("Die Mauer war bereits beschädigt.", ["Mauer"]),
    ("Die Nummer des Vertrags lautet 4711.", ["Nummer", "Vertrags"]),
    ("Die Leiter ist umgefallen.", ["Leiter"]),
    ("Die Mutter des Klägers war anwesend.", ["Mutter", "Klägers"]),
    ("Die Fischer haben die Netze eingeholt.", ["Fischer", "Netze"]),
    ("Die Schneider haben ihre Werkstätten geschlossen.", ["Schneider", "Werkstätten"]),
    ("Die Nachbesserungsfrist endet am 30.11.2026.", ["Nachbesserungsfrist"]),
    ("Der Sachverständige hat die Feuchtigkeit gemessen.", ["Sachverständige", "Feuchtigkeit"]),
    ("Die Richterin hat den Vergleich protokolliert.", ["Richterin", "Vergleich"]),
    ("Mit der Kammer wurde der Streitwert von 12.400,00 EUR erörtert.", ["Kammer", "Streitwert", "12.400,00"]),
    ("Der Mieter hat die Miete von 850,00 EUR gemindert.", ["Mieter", "Miete", "850,00"]),
    ("Die Vermieterin hat die Kaution einbehalten.", ["Vermieterin", "Kaution"]),
]

_GATEWAY = ClaudePrivacyGateway()


def _payload(text: str) -> tuple[bool, str]:
    result = _GATEWAY.prepare_request(
        purpose="chat_response",
        sachverhalt="Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.",
        review_mode="mask",
    )
    return result.allowed, result.payload.anonymisierter_sachverhalt if result.payload else ""


def _contains_word(text: str, word: str) -> bool:
    import re

    return re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(word) + r"(?![\wÄÖÜäöüß])", text) is not None


def run() -> dict:
    a_leaks, a_cases, a_total = [], 0, 0
    for name in NAMES:
        for intro in INTROS:
            for follow in FOLLOWUPS:
                text = f"Am 12.03.2026 erschien {intro} {name} in der Kanzlei. " + follow.format(N=name)
                allowed, payload = _payload(text)
                a_total += 1
                if allowed and _contains_word(payload, name):
                    a_leaks.append(f"{intro} {name} | {follow[:30]}")
    b_leaks, b_total = [], 0
    for name in NAMES:
        for frame in ARTICLE_FRAMES:
            allowed, payload = _payload(frame.format(N=name))
            b_total += 1
            if allowed and _contains_word(payload, name):
                b_leaks.append(f"{name} | {frame[:30]}")
    n_fp = []
    for sentence, keep in NEGATIVES:
        allowed, payload = _payload(sentence)
        lost = [k for k in keep if not (allowed and k in payload)]
        if lost:
            n_fp.append(f"{sentence[:40]} -> verloren {lost}")
    return {
        "A_folgeerwaehnung_lecks": len(a_leaks), "A_total": a_total, "A_beispiele": a_leaks,
        "B_artikel_lecks": len(b_leaks), "B_total": b_total, "B_beispiele": b_leaks,
        "N_fehlalarme": len(n_fp), "N_total": len(NEGATIVES), "N_beispiele": n_fp,
    }


if __name__ == "__main__":
    result = run()
    print(f"A Folgeerwaehnung nach Einfuehrung, Name im Klartext: {result['A_folgeerwaehnung_lecks']}/{result['A_total']}")
    print(f"B Name nach Artikel ohne Einfuehrung, Klartext:      {result['B_artikel_lecks']}/{result['B_total']}")
    print(f"N Negativfaelle mit verlorenem Fachinhalt:          {result['N_fehlalarme']}/{result['N_total']}")
    for key in ("A_beispiele", "B_beispiele", "N_beispiele"):
        for line in result[key]:
            print(f"   {key[0]}: {line}")
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
