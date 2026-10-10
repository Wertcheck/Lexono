"""Diagnose: erzeugt transliterierter Text (ae/oe/ue/ss statt ä/ö/ü/ß) zusaetzliche Pseudonymisierungstreffer?
(Qualitaetslauf 11.10.2026)

Vergleicht dieselben synthetischen Saetze mit Umlauten und transliteriert. Gezaehlt werden Entitaeten, die NUR in der
transliterierten Fassung erscheinen (Fehlalarme durch unbekannte Woerter) - echte Namen erscheinen in beiden.
Aufruf:  python scripts/diagnose_transliteration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy import presidio_ner as pn  # noqa: E402

SENTENCES = [
    "Die Mängel an der Dachabdichtung wurden im Abnahmeprotokoll vorbehalten.",
    "Der Auftragnehmer schuldet die Beseitigung der Mängel bis zum 03.07.2026.",
    "Die Gewährleistung beträgt fünf Jahre ab Abnahme der Leistung.",
    "Wir fordern Sie zur Zahlung der Vergütung und der Nachbesserung der Flachdachfläche auf.",
    "Die Geschäftsführerin erklärt die Aufrechnung mit der Vertragsstrafe wegen verspäteter Fertigstellung.",
    "Der Sachverständige bestätigt, dass die Dämmung nur 14 cm statt 18 cm stark ist.",
    "Die Kündigung des Mietvertrags erfolgt fristgerecht zum Quartalsende.",
    "Der Käufer rügt Mängel am Fahrzeug und verlangt Minderung des Kaufpreises.",
    "Für die Überprüfung der Unterlagen bitten wir um eine Verlängerung der Frist.",
    "Die Übergabe der Wohnung erfolgte ohne Beanstandung der Zählerstände.",
    "Die Beklagte beantragt die Abweisung der Klage und verweist auf die Verjährung.",
    "Der Schuldner ist zur Erfüllung seiner Verpflichtungen aus dem Darlehensvertrag nicht in der Lage.",
    "Die Rückzahlung der Kaution erfolgt nach Prüfung der Schönheitsreparaturen.",
    "Herr Olaf Thiessen und Frau Henrike Marquardt haben das Protokoll unterschrieben.",
    "Die Lieferung der Baustoffe verzögert sich wegen Lieferschwierigkeiten des Herstellers.",
    "Es besteht Streit über die Höhe der Vergütung für die zusätzlichen Leistungen.",
]


def transliterate(text: str) -> str:
    for a, b in (("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return text


def entities(text: str) -> set[str]:
    return {f"{s.category}:{s.value.strip()}" for s in pn.detect_presidio_entities(text)}


def main() -> None:
    only_ascii: list[str] = []
    for sentence in SENTENCES:
        umlaut = entities(sentence)
        ascii_ = entities(transliterate(sentence))
        extra = sorted(e for e in ascii_ if e.split(":", 1)[1] not in {u.split(":", 1)[1] for u in umlaut})
        only_ascii.extend(extra)
    print(f"{len(only_ascii)} zusätzliche Entitäten nur in der transliterierten Fassung ({len(SENTENCES)} Sätze):")
    for e in only_ascii:
        print("  ", e)


if __name__ == "__main__":
    main()
