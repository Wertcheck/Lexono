"""Benchmark: Fachwort nach Artikel als "Person" (NER-Fehlalarm) vs. Nachname nach Artikel (Leck-Gegenprobe).

Ausgangsfall (13.10.2026, installierter Build): "Die Nachbesserungsfrist endet am 30.11.2026." wurde im Dokumentkontext zu
"Die [PERSON_01] endet ..." - spaCys NER (de_core_news_lg) etikettiert das Kompositum kontextabhaengig als PER.

Goldstandard (vorab festgelegt, ausschliesslich synthetisch):
* TERMS: juristische/bautechnische Fachbegriffe mit korrektem Artikel -> muessen im Payload LESBAR bleiben (Fehlalarm =
  maskiert oder blockiert).
* NAMES: Nachnamen (inkl. Komposita-Nachnamen wie "Bergmann") nach einem Artikel, wie in Umgangssprache/Aktennotizen
  ("die Bergmann hat angerufen") -> duerfen NIE im Klartext im Payload stehen (Leck).
Rahmen: identisch fuer beide Gruppen, im Dokumentpfad (Akte + Dokumentkopf, Maskierungsmodus).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

TERMS = [
    ("die", "Nachbesserungsfrist"), ("die", "Kündigungsfrist"), ("die", "Zahlungsfrist"), ("die", "Gewährleistungsfrist"),
    ("die", "Verjährungsfrist"), ("die", "Widerspruchsfrist"), ("die", "Räumungsfrist"), ("die", "Berufungsfrist"),
    ("die", "Nebenkostenabrechnung"), ("die", "Mängelanzeige"), ("die", "Mängelrüge"), ("die", "Abnahmeerklärung"),
    ("die", "Schlussrechnung"), ("die", "Abschlagsrechnung"), ("die", "Vertragsstrafe"), ("die", "Sicherheitsleistung"),
    ("die", "Bürgschaftsurkunde"), ("die", "Mieterhöhung"), ("die", "Modernisierungsankündigung"), ("die", "Betriebskostenabrechnung"),
    ("die", "Unterlassungserklärung"), ("die", "Zustellungsurkunde"), ("die", "Prozessvollmacht"), ("die", "Klageerwiderung"),
    ("die", "Beweisaufnahme"), ("die", "Kostenfestsetzung"), ("die", "Zwangsvollstreckung"), ("die", "Teilabnahme"),
    ("die", "Bauhandwerkersicherung"), ("die", "Schadensersatzforderung"),
    ("der", "Werkvertrag"), ("der", "Mietvertrag"), ("der", "Gewerbemietvertrag"), ("der", "Bauvertrag"),
    ("der", "Kaufvertrag"), ("der", "Nachtrag"), ("der", "Vergleichsvorschlag"), ("der", "Kostenvoranschlag"),
    ("der", "Mangelbeseitigungsanspruch"), ("der", "Schadensersatzanspruch"), ("der", "Vergütungsanspruch"),
    ("der", "Zahlungsanspruch"), ("der", "Streitwert"), ("der", "Mahnbescheid"), ("der", "Vollstreckungsbescheid"),
    ("der", "Kostenfestsetzungsbeschluss"), ("der", "Feuchtigkeitsschaden"), ("der", "Wasserschaden"), ("der", "Baumangel"),
    ("der", "Sachverständigenbeweis"),
    ("das", "Abnahmeprotokoll"), ("das", "Übergabeprotokoll"), ("das", "Gutachten"), ("das", "Sachverständigengutachten"),
    ("das", "Zurückbehaltungsrecht"), ("das", "Leistungsverweigerungsrecht"), ("das", "Kündigungsschreiben"),
    ("das", "Mahnschreiben"), ("das", "Aufmaß"), ("das", "Leistungsverzeichnis"),
]
NAMES = [
    ("die", "Bergmann"), ("die", "Wessolowski"), ("die", "Stuckenschneider"), ("die", "Grotelueschen"), ("die", "Brinkmoeller"),
    ("die", "Zimmermann"), ("die", "Hoffmann"), ("die", "Marquardt"), ("die", "Pfannenstiel"), ("die", "Thiessen"),
    ("der", "Bergmann"), ("der", "Wessolowski"), ("der", "Stuckenschneider"), ("der", "Grotelueschen"), ("der", "Brinkmoeller"),
    ("der", "Zimmermann"), ("der", "Hoffmann"), ("der", "Marquardt"), ("der", "Pfannenstiel"), ("der", "Thiessen"),
    ("die", "Kowalczyk"), ("der", "Kowalczyk"), ("die", "Eberhaerter"), ("der", "Eberhaerter"),
]
FRAMES = [
    "Abnahmeprotokoll zum Werkvertrag. Festgestellt wurden Risse im Estrich und eine undichte Dachrinne. {A} {X} endet am 30.11.2026.",
    "{A} {X} ist bislang nicht eingegangen. Der Betrag von 1.200,00 EUR bleibt offen.",
    "Nach Rücksprache am 12.03.2026 hat {a} {X} die Unterlagen übergeben.",
    "Im Termin am 12.03.2026 wurde {a} {X} ausführlich erörtert.",
]

_GATEWAY = ClaudePrivacyGateway()


def _payload(text: str) -> tuple[bool, str]:
    sachverhalt = "Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text)
    result = _GATEWAY.prepare_request(
        purpose="chat_response", sachverhalt=sachverhalt, anwaltliche_anmerkungen="Fasse das Dokument zusammen.",
        review_mode="mask",
    )
    return result.allowed, result.payload.anonymisierter_sachverhalt if result.payload else ""


def run() -> dict:
    fp, leaks, fp_cases, leak_cases = 0, 0, [], []
    for article, word in TERMS:
        for frame in FRAMES:
            text = frame.format(A=article.capitalize(), a=article, X=word)
            allowed, payload = _payload(text)
            if not allowed or word not in payload:
                fp += 1
                fp_cases.append(f"{article} {word} | {frame[:28]}")
    for article, name in NAMES:
        for frame in FRAMES:
            text = frame.format(A=article.capitalize(), a=article, X=name)
            allowed, payload = _payload(text)
            if allowed and name in payload:
                leaks += 1
                leak_cases.append(f"{article} {name} | {frame[:28]}")
    return {
        "fachwort_faelle": len(TERMS) * len(FRAMES), "fachwort_maskiert": fp, "fachwort_beispiele": fp_cases,
        "namen_faelle": len(NAMES) * len(FRAMES), "namen_lecks": leaks, "leck_beispiele": leak_cases,
    }


if __name__ == "__main__":
    result = run()
    print(f"Fachwort nach Artikel maskiert/blockiert: {result['fachwort_maskiert']}/{result['fachwort_faelle']}")
    for case in result["fachwort_beispiele"]:
        print("   FP:", case)
    print(f"Nachname nach Artikel im Klartext (Leck): {result['namen_lecks']}/{result['namen_faelle']}")
    for case in result["leck_beispiele"]:
        print("   LECK:", case)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
