"""Reproduzierbarer Benchmark der Personen-/Namenserkennung ueber das ECHTE Gateway (Qualitaetslauf 12.10.2026).

Misst an den tatsaechlich AUSGEHENDEN Payloads (nicht an internen Trefferlisten):
  - Recall: Anteil der Testnamen, die NICHT im Klartext im Cloud-Payload stehen (Gateway erlaubt oder blockiert fail-closed)
  - Klartext-Lecks: Name im Payload, obwohl das Gateway die Anfrage erlaubt hat
  - Blocks: Gateway lehnt (fail-closed) ab
  - Fehlalarme: gewoehnliche Rechts-/Fachwoerter, die als Platzhalter im Payload landen
  - Laufzeit je Fall
Alles SYNTHETISCH. Der Korpus ist deterministisch; Varianten werden ueber `--variant` zugeschaltet, damit "vorher/nachher" mit
IDENTISCHEN Testfaellen verglichen wird.

Aufruf:  python scripts/bench_name_detection.py --variant baseline [--groups ...] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

COMMON = """Mueller Schmidt Schneider Fischer Weber Wagner Becker Hoffmann Schulz Koch Richter Klein Wolf Neumann Schwarz Krueger Zimmermann
Braun Hartmann Lange Werner Schmitt Krause Meier Lehmann Koenig Schaefer Jaeger Fuerst Baecker""".split()
RARE = """Hasenclever Quakernack Schwabedissen Vollmerhaus Wessolowski Brinkmoeller Grotelueschen Pfannenstiel Stuckenschneider Eberhaerter
Thiessen Marquardt Kostka Lindqvist Wiebe""".split()
FOREIGN = "Kowalczyk Yilmaz Nguyen Papadopoulos Oeztuerk Rossi Dubois Johansson Petrovic Haddad".split()
FIRST = ["Henrike", "Olaf", "Jonas", "Svenja", "Dirk", "Karin", "Rainer", "Lena"]

# Kontexte: (Gruppe, Vorlage mit {N} fuer den Nachnamen [und {F} Vorname, {M} zweiter Name], Feld)
CONTEXTS: list[tuple[str, str, str]] = [
    ("satzanfang", "{N} hat unterschrieben.", "sachverhalt"),
    ("satzanfang", "Die Anlage ist defekt. {N} hat das bestätigt.", "sachverhalt"),
    ("satzmitte", "Die Unterlagen wurden von {N} geprüft und freigegeben.", "sachverhalt"),
    ("satzmitte", "Dazu erklärt {N}, die Anlage sei mangelhaft.", "sachverhalt"),
    ("anrede", "Herr {N} hat die Abnahme erklärt.", "sachverhalt"),
    ("anrede", "Wir bitten Frau {N} um Rückmeldung.", "sachverhalt"),
    ("titel", "Gutachter Dr. {N} prüft die Anlage.", "sachverhalt"),
    ("titel", "Ansprechpartnerin ist Prof. Dr. {N} von der Hochschule.", "sachverhalt"),
    ("tabelle", "Name: {N}  Funktion: Zeuge  Datum: 12.03.2026", "sachverhalt"),
    ("tabelle", "Zeuge  |  {N}  |  Hamburg  |  geladen", "sachverhalt"),
    ("ueberschrift", "Zeugenaussage {N}\nDer Zeuge schildert den Ablauf.", "sachverhalt"),
    ("ueberschrift", "Protokoll\n{N}, Zeuge\nDie Anlage wurde besichtigt.", "sachverhalt"),
    ("pdf_umbruch", "Die Unterlagen wurden am 12.03.2026 durch\n{N} geprüft und dem Auftraggeber übergeben.", "sachverhalt"),
    ("pdf_umbruch", "Der Gutachter Dr.\n{N} bestätigt den Mangel an der Dämmung der Dachfläche.", "sachverhalt"),
    ("zwei_namen", "{N} und {M} haben das Protokoll unterschrieben.", "sachverhalt"),
    ("zwei_namen", "Zeugen sind {M}, {N} sowie Herr {M}.", "sachverhalt"),
    ("vollname", "{F} {N} hat unterschrieben.", "sachverhalt"),
    ("vollname", "Die Zeugin {F} {N} wohnt in Kiel.", "sachverhalt"),
    ("feldanfang", "{N} hat angerufen und bittet um Rückruf.", "anmerkungen"),
    ("feldanfang", "{N} erklärt, dass die Anlage mangelhaft ist.", "anmerkungen"),
]
PREFIX = "Akte: Beispielakte\n[Vertrag] "

# Negativ: gewoehnliche Rechts-/Fachwoerter und Institutionen (duerfen NICHT zu Platzhaltern werden; Institutionen/Orte sind
# bewusst enthalten, damit sichtbar bleibt, was heute pseudonymisiert wird)
NEGATIVE_WORDS = """Mängel Gewährleistung Kündigung Vergütung Übergabe Prüfung Änderung Zusätze Bürgschaft Möglichkeit Gebühr Beschädigung
Dämmung Abdichtung Sicherheitseinbehalt Vertragsstrafe Abnahme Nachfrist Verzug Schadensersatz Haftung Minderung Rücktritt Frist Anlage
Auftragnehmer Auftraggeber Käufer Verkäufer Mieter Vermieter Kläger Beklagter Zeuge Gutachter Sachverständiger Partei Gericht Behörde
Amtsgericht Landgericht Finanzamt Gemeinde Stadt Bund Kreis Zahlung Rechnung Mahnung Klage Berufung Beschluss Urteil Bescheid
Vollmacht Kaution Quittung Protokoll Lieferung Leistung Bestellung Garantie Eigentümer Verwalter Insolvenzverwalter Notar""".split()
NEGATIVE_TEMPLATES = ["Die {X} ist nicht ordnungsgemäß erfolgt.", "Wir verlangen die Beseitigung der {X} bis zum 30.11.2026."]


# Anweisungen des Anwalts und Chat-Fragen ohne Personendaten: duerfen UNVERAENDERT durchs Gateway (Zweitsignal darf
# Imperative/Fachwoerter am Satz-/Feldanfang nicht maskieren oder blockieren).
INSTRUCTIONS = [
    "Erstelle ein Schreiben an die Gegenseite.", "Fasse das Dokument zusammen.", "Prüfe die Fristen und nenne Risiken.",
    "Überarbeite den Entwurf und kürze ihn.", "Formuliere eine Mahnung mit Fristsetzung.", "Ergänze einen Absatz zur Gewährleistung.",
    "Antworte sachlich und höflich.", "Schreibe eine kurze Stellungnahme.", "Übersetze den Text ins Englische.",
    "Korrigiere Rechtschreibung und Zeichensetzung.", "Berücksichtige § 634 BGB und die VOB/B.", "Beachte die Verjährung.",
    "Gilt das auch für Gewerbemietverträge?", "Welche Frist gilt für die Berufungsbegründung?", "Was bedeutet Nacherfüllung im Kaufrecht?",
    "Gewährleistung und Verjährung: bitte erläutern.", "Kündigung wegen Eigenbedarf: Voraussetzungen nennen.",
]


def instruction_false_alarms(gateway: ClaudePrivacyGateway) -> tuple[int, int, list[str]]:
    flagged, words = 0, []
    for text in INSTRUCTIONS:
        result = gateway.prepare_request(
            purpose="formulate_draft", sachverhalt="Akte: Beispielakte", argumentationspunkte=[], quellenverweise=[], stil=None,
            vorlage=None, anwaltliche_anmerkungen=text, known_entities=None, gespraechsverlauf=[],
            skip_general_knowledge_pseudonymization=False, review_mode=REVIEW_MODE,
        )
        if not result.allowed or result.payload.anonymisierte_anwaltliche_anmerkungen != text:
            flagged += 1
            words.append(text if not result.allowed else result.payload.anonymisierte_anwaltliche_anmerkungen)
    return flagged, len(INSTRUCTIONS), words


REVIEW_MODE = "mask"


def build_cases(limit_names: int | None = None) -> list[dict]:
    rng = random.Random(7)
    names = COMMON + RARE + FOREIGN
    if limit_names:
        names = names[:limit_names]
    cases: list[dict] = []
    for index, name in enumerate(names):
        for ctx_index, (group, template, field) in enumerate(CONTEXTS):
            other = names[(index + 7) % len(names)]
            first = FIRST[(index + ctx_index) % len(FIRST)]
            secrets = [name]
            if "{M}" in template:
                secrets.append(other)
            if "{F}" in template:
                secrets.append(first)
            cases.append({"kind": "name", "group": group, "field": field, "text": template.format(N=name, M=other, F=first), "secrets": secrets,
                          "category": "ASCII" if name.isascii() and any(x in name for x in ("ae", "oe", "ue")) else "normal"})
    rng.shuffle(cases)
    return cases


def run_case(gateway: ClaudePrivacyGateway, case: dict) -> dict:
    sach, anm = ("Akte: Beispielakte", "Erstelle ein Schreiben.")
    if case["field"] == "sachverhalt":
        sach = PREFIX + case["text"]
    else:
        anm = case["text"]
    t = time.perf_counter()
    result = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt=sach, argumentationspunkte=[], quellenverweise=[], stil=None, vorlage=None,
        anwaltliche_anmerkungen=anm, known_entities=None, gespraechsverlauf=[], skip_general_knowledge_pseudonymization=False,
        review_mode=REVIEW_MODE,
    )
    seconds = time.perf_counter() - t
    if not result.allowed:
        return {"allowed": False, "leaked": [], "seconds": seconds, "review": result.review_required}
    payload = result.payload
    haystack = payload.anonymisierter_sachverhalt + "\n" + (payload.anonymisierte_anwaltliche_anmerkungen or "")
    return {"allowed": True, "leaked": [s for s in case["secrets"] if s in haystack], "seconds": seconds, "payload": haystack}


def false_alarms(gateway: ClaudePrivacyGateway) -> tuple[int, int, list[str]]:
    flagged, total = 0, 0
    words: set[str] = set()
    for word in NEGATIVE_WORDS:
        for template in NEGATIVE_TEMPLATES:
            total += 1
            text = PREFIX + template.format(X=word)
            result = gateway.prepare_request(
                purpose="formulate_draft", sachverhalt=text, argumentationspunkte=[], quellenverweise=[], stil=None, vorlage=None,
                anwaltliche_anmerkungen="Erstelle ein Schreiben.", known_entities=None, gespraechsverlauf=[],
                skip_general_knowledge_pseudonymization=False, review_mode=REVIEW_MODE,
            )
            if not result.allowed:
                flagged += 1
                words.add(word + "(Block)")
            elif word not in result.payload.anonymisierter_sachverhalt:
                flagged += 1
                words.add(word)
    return flagged, total, sorted(words)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--limit-names", type=int, default=None)
    ap.add_argument("--no-negatives", action="store_true")
    ap.add_argument("--json", default=None, help="Ergebnisdatei")
    ap.add_argument("--review-mode", default="mask", choices=("off", "mask", "ask"),
                    help="Gateway-Modus fuer unsichere Begriffe (off = Verhalten vor 12.10.2026)")
    args = ap.parse_args()
    global REVIEW_MODE
    REVIEW_MODE = args.review_mode

    import bench_variants  # noqa: PLC0415 - lokale Experimentvarianten

    bench_variants.apply(args.variant)
    gateway = ClaudePrivacyGateway()
    cases = build_cases(args.limit_names)
    by_group: dict[str, list[dict]] = defaultdict(list)
    t0 = time.perf_counter()
    for case in cases:
        by_group[case["group"]].append(run_case(gateway, case))
    elapsed = time.perf_counter() - t0
    print(f"Variante {args.variant}: {len(cases)} Namensfaelle in {elapsed:.0f} s")
    print(f"{'Gruppe':14s} {'n':>4s} {'Leck %':>7s} {'Block %':>8s} {'sicher %':>9s} {'s/Fall':>7s}")
    total = leaks = blocks = 0
    summary = {}
    for group, rows in sorted(by_group.items()):
        n = len(rows)
        leak = sum(1 for r in rows if r["allowed"] and r["leaked"])
        block = sum(1 for r in rows if not r["allowed"])
        total += n; leaks += leak; blocks += block
        summary[group] = {"n": n, "leaks": leak, "blocks": block, "seconds": sum(r["seconds"] for r in rows) / n}
        print(f"{group:14s} {n:4d} {100 * leak / n:7.1f} {100 * block / n:8.1f} {100 * (n - leak) / n:9.1f} {summary[group]['seconds']:7.2f}")
    print(f"{'GESAMT':14s} {total:4d} {100 * leaks / total:7.1f} {100 * blocks / total:8.1f} {100 * (total - leaks) / total:9.1f}")
    reviews = sum(1 for rows in by_group.values() for r in rows if r.get("review"))
    print(f"Modus {REVIEW_MODE}: davon Rueckfragen (kein Cloud-Aufruf bis zur Entscheidung) {reviews}/{total}")
    result = {"variant": args.variant, "total": total, "leaks": leaks, "blocks": blocks, "groups": summary}
    if not args.no_negatives:
        flagged, ntotal, words = false_alarms(gateway)
        print(f"Fehlalarme (gewoehnliche Woerter als Platzhalter/Block): {flagged}/{ntotal}: {words}")
        result["false_alarms"] = {"flagged": flagged, "total": ntotal, "words": words}
        iflag, itotal, iwords = instruction_false_alarms(gateway)
        print(f"Anweisungen/Chat-Fragen veraendert oder blockiert: {iflag}/{itotal}: {iwords}")
        result["instruction_false_alarms"] = {"flagged": iflag, "total": itotal, "texts": iwords}
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
