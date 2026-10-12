"""Negativ-Benchmark (Folgezyklus 13.10.2026, aus dem unabhaengigen Red-Team-Review): Rollenwort + Objekt/Fachbegriff.

Befund der ersten Fassung von presidio_ner.detect_titled_names: in Nebensaetzen folgt auf ein Rollenwort oft das Objekt
("dass der Mieter Maengel anzeigt") - das wurde als Name maskiert und per Folgeerwaehnung im ganzen Text ersetzt
(Schriftsatz unten: 0 -> 17 Personen-Platzhalter, 8 Fachbegriffe verloren). Goldstandard: der Schriftsatz enthaelt KEINEN
Personennamen; alle Fachbegriffe/Betraege muessen lesbar bleiben. Ausschliesslich synthetisch."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

DOC = """Landgericht Musterstadt
In dem Rechtsstreit
Sehr geehrter Herr Vorsitzender,
namens und in Vollmacht der Klägerin beantragen wir, die Beklagte zur Zahlung zu verurteilen.
Begründung:
Die Klägerin ist Mieterin der Wohnung. Seit März zeigt die Klägerin Mängel regelmäßig an. Die Mängel betreffen die Heizung.
Mit Schreiben vom 12.03.2026 hat die Klägerin Mängel gerügt und Minderung angekündigt. Die Minderung beträgt 15 Prozent.
Es ist anerkannt, dass der Mieter Schadensersatz verlangen kann, wenn der Vermieter Mängel nicht beseitigt. Der Schadensersatz umfasst auch Gutachterkosten.
Im Ortstermin hat der Sachverständige Feuchtigkeit festgestellt. Ob der Gutachter Proben entnommen hat, ist streitig. Die Proben wurden nicht ausgewertet.
Der Beklagte hat Kaution nicht abgerechnet. Hätte der Vermieter Kaution fristgerecht abgerechnet, wäre die Klage entbehrlich gewesen. Die Kaution betrug 2.550,00 EUR.
Zudem hat der Zeuge Angaben zur Mietdauer gemacht. Diese Angaben sind glaubhaft.
Der Richter am Amtsgericht hatte bereits darauf hingewiesen, dass der Kläger Beweis angeboten hat. Der Beweis ist zu erheben.
Mit freundlichen Grüßen
Rechtsanwältin"""
DOC_KEEP = ["Mängel", "Heizung", "Minderung", "Schadensersatz", "Gutachterkosten", "Feuchtigkeit", "Proben", "Kaution",
            "Angaben", "Mietdauer", "Richter", "Beweis", "Vorsitzender", "Klage", "Wohnung", "2.550,00"]
SENTENCES = [
    ("Die Klägerin trägt vor, dass der Mieter Mängel angezeigt hat.", ["Mängel"]),
    ("Es ist streitig, ob der Mieter Schadensersatz verlangen kann.", ["Schadensersatz"]),
    ("Nachdem der Kläger Klage erhoben hatte, zahlte die Beklagte.", ["Klage"]),
    ("Es bleibt offen, ob der Mandant Kosten tragen muss.", ["Kosten"]),
    ("Sobald der Notar Antrag stellt, wird eingetragen.", ["Antrag"]),
    ("Weil die Mieterin Miete gemindert hat, klagt der Vermieter.", ["Miete"]),
    ("Fraglich ist, ob der Zeuge Angaben gemacht hat.", ["Angaben"]),
    ("Solange der Vermieter Kaution einbehält, besteht der Anspruch.", ["Kaution"]),
    ("Mieter  Wohnung  Fläche", ["Wohnung", "Fläche"]),
    ("Herr Vorsitzender, wir beantragen Vertagung.", ["Vorsitzender", "Vertagung"]),
    ("Frau Kollegin, der Streitwert beträgt 12.400,00 EUR.", ["Kollegin", "Streitwert", "12.400,00"]),
    ("Der Staatsanwalt hat Anklage erhoben.", ["Staatsanwalt", "Anklage"]),
]

_GATEWAY = ClaudePrivacyGateway()


def _payload(text: str) -> tuple[bool, str]:
    result = _GATEWAY.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode="mask",
    )
    return result.allowed, result.payload.anonymisierter_sachverhalt.split("[Vertrag]", 1)[-1] if result.payload else ""


def _word(text: str, word: str) -> bool:
    return re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(word) + r"(?![\wÄÖÜäöüß])", text) is not None


def run() -> dict:
    allowed, body = _payload(DOC)
    doc_lost = [w for w in DOC_KEEP if not (allowed and _word(body, w))]
    sentence_lost = []
    for sentence, keep in SENTENCES:
        ok, payload = _payload(sentence)
        lost = [w for w in keep if not (ok and _word(payload, w))]
        if lost:
            sentence_lost.append(f"{sentence[:45]} -> {lost}")
    return {"schriftsatz_personen_platzhalter": len(re.findall(r"\[PERSON_\d+\]", body)), "schriftsatz_verloren": doc_lost,
            "saetze_mit_verlust": len(sentence_lost), "saetze_total": len(SENTENCES), "satz_beispiele": sentence_lost}


if __name__ == "__main__":
    result = run()
    print(f"Schriftsatz ohne Namen: Personen-Platzhalter {result['schriftsatz_personen_platzhalter']}, "
          f"verlorene Fachbegriffe {result['schriftsatz_verloren']}")
    print(f"Saetze Rollenwort+Objekt mit Verlust: {result['saetze_mit_verlust']}/{result['saetze_total']}")
    for line in result["satz_beispiele"]:
        print("   ", line)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
