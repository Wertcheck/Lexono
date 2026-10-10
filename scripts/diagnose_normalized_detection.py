"""Diagnose: Umlaut-wiederhergestellte Erkennungsfassung fuer transliterierte Texte (ae/oe/ue/ss) (11.10.2026).

Vergleicht drei Verfahren auf synthetischen Saetzen:
  orig      nur die Originalanalyse (frueherer Stand)
  restored  nur die Analyse der wiederhergestellten Fassung (Treffer auf den Originaltext zurueckgebildet) - NICHT produktiv
  union     Vereinigung beider (Produktivverfahren, `presidio_ner.detect_presidio_entities`)
Gemessen werden uebersehene Namen/Firmen/Anschriften (Datenschutz-Recall, kleiner ist besser) und Fehlalarm-Treffer auf
gewoehnlichen Rechtswoertern (ohne die Ganzwort-Ausnahmen, roher Modelleffekt).

Ergebnis (Referenzlauf): restored-only verpasst MEHR Namen als orig (z. B. "Herr Fuerst" -> "Herr Fürst" nicht mehr
erkannt), union verpasst weniger als orig. Deshalb wird die wiederhergestellte Fassung nur vereinigt.
Aufruf:  python scripts/diagnose_normalized_detection.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy import presidio_ner as pn  # noqa: E402

NAMES = """Mueller Schroeder Haeussler Kuehn Koenig Voelker Hoefer Baeumer Schaefer Boehm Jaeger Wuerth Schuetze Moench Baecker
Loewe Haeuser Daehne Goetz Hoehn Kaestner Boettcher Draeger Fuerst Gruen Hoeppner Kuester Laemmle Noeldeke Oeztuerk Paeschke
Roemer Saenger Toepfer Uebel Wuerfel Zoellner Brueckner Buerger Duerr Eberhaerter Foerster Gaertner Haenel Jaekel Kloeckner
Luedke Maertens Nuernberger Poetsch Quaeck Raeuber Stoeckl Thuermer Thiessen Marquardt Kostka Lindqvist Wiebe""".split()
NAME_TEMPLATES = [
    "Herr {N} hat unterschrieben.", "{N} hat unterschrieben.", "Das Schreiben von {N} liegt vor.",
    "Frau Dr. {N} prüft die Unterlagen.", "Ansprechpartner ist {N}.",
]
COMPANIES = ["Kuestenkontor Verwaltungs GmbH", "Daemmtechnik Mueller KG", "Baeckerei Schulze OHG", "Thiessen Dachbau GmbH & Co. KG"]
ADDRESSES = ["Hauptstrasse 5, 20099 Hamburg", "Muehlenweg 12, 24103 Kiel", "Schoenhauser Allee 3, 10119 Berlin", "Gewerbering 6, 24105 Beispielstadt"]
COMMON = """Maengel Gewaehrleistung Kuendigung Verguetung Flachdachflaeche Geschaeftsfuehrerin Uebergabe Pruefung Aenderung Zusaetze
Schoenheitsreparaturen Maengelruege Rueckzahlung Kaeufer Verkaeufer Moeglichkeit Gebuehr Buergschaft Beschaedigung Fuellmenge
Waermedaemmung Sicherheitseinbehalt Prozessvollmacht Lichtkuppel Quittung Einspruchsfrist Zustaendigkeit Aufwaendungen Schluessel Gruende""".split()
COMMON_TEMPLATES = [
    "Die {X} ist nicht ordnungsgemäß erfolgt.", "Wir verlangen die Beseitigung der {X} bis zum 30.11.2026.",
    "Der Auftragnehmer haftet für die {X}.",
]


def _spans(text: str, mode: str) -> set[tuple[str, int, int]]:
    result: set[tuple[str, int, int]] = set()
    if mode in ("orig", "union"):
        original_restore = pn._restore_for_analysis
        if mode == "orig":
            pn._restore_for_analysis = lambda _t: None  # type: ignore[assignment]
        try:
            result |= {(s.category, s.start, s.end) for s in pn.detect_presidio_entities(text)}
        finally:
            pn._restore_for_analysis = original_restore  # type: ignore[assignment]
    if mode == "restored":
        restored = pn._restore_for_analysis(pn._neutralize_internal_tokens(text))
        analysis, starts, ends = restored if restored else (text, list(range(len(text))), list(range(1, len(text) + 1)))
        for r in pn._get_analyzer_engine().analyze(text=analysis, language="de", entities=list(pn._REQUESTED_ENTITIES), score_threshold=pn._MIN_SCORE):
            cat = pn._ENTITY_TO_CATEGORY.get(r.entity_type)
            if cat:
                result.add((cat, starts[r.start], ends[r.end - 1]))
    return result


def _covers(spans: set[tuple[str, int, int]], text: str, token: str) -> bool:
    i = text.find(token)
    return any(a < i + len(token) and b > i for _c, a, b in spans)


def main() -> None:
    pn._NEVER_ENTITY_WORDS = frozenset()  # roher Modelleffekt
    print(f"{'Verfahren':9s} {'Namen verpasst':>15s} {'Firmen':>8s} {'Anschriften':>12s} {'Fehlalarm-Treffer Rechtswörter':>32s}")
    for mode in ("orig", "restored", "union"):
        missed_names = total_names = 0
        for name in NAMES:
            for template in NAME_TEMPLATES:
                text = template.format(N=name)
                total_names += 1
                missed_names += not _covers(_spans(text, mode), text, name)
        missed_companies = sum(
            not _covers(_spans(f"Auftragnehmerin ist die {c} aus Kiel.", mode), f"Auftragnehmerin ist die {c} aus Kiel.", c.split()[0])
            for c in COMPANIES
        )
        missed_addresses = sum(
            not _covers(_spans(f"Die Lieferung erfolgt an {a} vor Ort.", mode), f"Die Lieferung erfolgt an {a} vor Ort.", a.split()[0])
            for a in ADDRESSES
        )
        false_alarms = 0
        for word in COMMON:
            for template in COMMON_TEMPLATES:
                text = template.format(X=word)
                false_alarms += sum(1 for _c, a, b in _spans(text, mode) if word.lower() in text[a:b].lower())
        print(f"{mode:9s} {missed_names:7d}/{total_names:<7d} {missed_companies:5d}/{len(COMPANIES)} {missed_addresses:8d}/{len(ADDRESSES)} {false_alarms:30d}")


if __name__ == "__main__":
    main()
