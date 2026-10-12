"""Benchmark Versalien (13.10.2026): Namen/Anschriften in Grossbuchstaben vs. gewoehnliche Versal-Texte.

Goldstandard (vorab festgelegt, ausschliesslich synthetisch; Namen erfunden bzw. aus oeffentlichen Haeufigkeitslisten):
* POSITIVE: jeder aufgefuehrte Wert (Name, Strasse, Ort) darf NIE im Klartext im Payload stehen (Leck).
* NEGATIVE: Versal-Ueberschriften, Gerichtsformeln, Gesetzesabkuerzungen, Betraege - die aufgefuehrten Woerter muessen
  lesbar bleiben (Fehlalarm = maskiert oder Anfrage blockiert).
Gemessen im Dokumentpfad (Akte + Dokumentkopf + `_normalize_document_text`), Modus "mask", sowie Blockaden.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

POSITIVE = [
    ("HERRN\nGERNOT HAHN\nBIRKENALLEE 17\n12345 MUSTERSTADT\n\nBetreff: Mahnung", ["GERNOT", "HAHN", "BIRKENALLEE", "MUSTERSTADT"]),
    ("FRAU\nODETTE PASCHKE\nROSENWEG 2\n24103 KIEL", ["ODETTE", "PASCHKE", "ROSENWEG", "KIEL"]),
    ("Herrn\nKLAUS-DIETER BROCKHAGEN\nHAFENSTRASSE 9\n20457 HAMBURG", ["KLAUS-DIETER", "BROCKHAGEN", "HAFENSTRASSE"]),
    ("EHELEUTE\nANKE UND JÖRG TELLKAMPF\nAM MÜHLENTEICH 4\n31134 HILDESHEIM", ["ANKE", "JÖRG", "TELLKAMPF", "HILDESHEIM"]),
    ("Der Beklagte WINKELMANN hat die Forderung bestritten.", ["WINKELMANN"]),
    ("Die Klägerin HABERLAND verlangt Zahlung von 3.912,40 EUR.", ["HABERLAND"]),
    ("In Sachen HABERLAND ./. WINKELMANN wegen Werklohn", ["HABERLAND", "WINKELMANN"]),
    ("IN DEM RECHTSSTREIT\nDES HERRN GERNOT HAHN, BIRKENALLEE 17, 12345 MUSTERSTADT,\n- KLÄGER -", ["GERNOT", "HAHN", "BIRKENALLEE", "MUSTERSTADT"]),
    ("Herr WINKELMANN hat gezahlt. WINKELMANN bestätigte das am 12.03.2026.", ["WINKELMANN"]),
    ("Frau Dr. SOMMER-BRANDT hat geschrieben.", ["SOMMER-BRANDT"]),
    ("ZEUGE | OSTENDARP | HAMBURG | geladen", ["OSTENDARP"]),
    ("Mit freundlichen Grüßen\nGEZ. FENJA RÜBSAM\nRechtsanwältin", ["FENJA", "RÜBSAM"]),
    ("Absender: MAREIKE THORMÄHLEN, LINDENALLEE 4, 24103 KIEL", ["MAREIKE", "THORMÄHLEN", "LINDENALLEE"]),
    ("Mandant: KOWALCZYK, PIOTR", ["KOWALCZYK", "PIOTR"]),
    ("Die Zeugin SCHWIENHORST hat ausgesagt.", ["SCHWIENHORST"]),
    ("Herrn Rechtsanwalt Dr. QUERNHEIM\nKANZLEISTRASSE 1\n10115 BERLIN", ["QUERNHEIM", "KANZLEISTRASSE"]),
]
NEGATIVE = [
    ("BETREFF: MAHNUNG ODER KLAGE", ["BETREFF", "MAHNUNG", "ODER", "KLAGE"]),
    ("FORDERUNG AUS WERKVERTRAG – LETZTE MAHNUNG", ["FORDERUNG", "WERKVERTRAG", "LETZTE", "MAHNUNG"]),
    ("IM NAMEN DES VOLKES\nURTEIL\nIn dem Rechtsstreit", ["NAMEN", "VOLKES", "URTEIL"]),
    ("TATBESTAND\nDie Parteien streiten über Mängel.\nENTSCHEIDUNGSGRÜNDE\nDie Klage ist begründet.", ["TATBESTAND", "ENTSCHEIDUNGSGRÜNDE"]),
    ("BESCHLUSS\nDer Streitwert wird auf 12.400,00 EUR festgesetzt.", ["BESCHLUSS", "12.400,00"]),
    ("ANTRÄGE\n1. Die Beklagte wird verurteilt, an den Kläger 3.912,40 EUR zu zahlen.", ["ANTRÄGE", "3.912,40"]),
    ("WICHTIG: BITTE FRISTEN BEACHTEN UND ZAHLUNG LEISTEN", ["WICHTIG", "BITTE", "FRISTEN", "BEACHTEN", "ZAHLUNG", "LEISTEN"]),
    ("VERTRAULICH – NUR FÜR DEN EMPFÄNGER", ["VERTRAULICH", "EMPFÄNGER"]),
    ("ANLAGE K1: KAUFVERTRAG VOM 12.03.2026", ["ANLAGE", "KAUFVERTRAG"]),
    ("BEGLAUBIGTE ABSCHRIFT", ["BEGLAUBIGTE", "ABSCHRIFT"]),
    ("Der Halter haftet nach § 7 STVG und § 823 BGB; die AGB gelten.", ["STVG", "BGB", "AGB"]),
    ("Die GMBH & CO. KG hat nach § 377 HGB untersucht; ZPO und GVG sind anwendbar.", ["HGB", "ZPO", "GVG"]),
    ("RECHNUNG NR. 4711 ÜBER EUR 1.200,00 FÜR ESTRICHARBEITEN", ["RECHNUNG", "ESTRICHARBEITEN", "1.200,00"]),
    ("KÜNDIGUNG DES MIETVERHÄLTNISSES ZUM 31.12.2026", ["KÜNDIGUNG", "MIETVERHÄLTNISSES"]),
    ("SCHADENSERSATZ UND SCHMERZENSGELD NACH § 253 BGB", ["SCHADENSERSATZ", "SCHMERZENSGELD", "BGB"]),
    ("ACHTUNG: FRISTABLAUF AM 30.11.2026", ["ACHTUNG", "FRISTABLAUF"]),
    ("Die Bescheinigung (ORIGINAL) liegt bei. Kopie an MANDANTSCHAFT.", ["ORIGINAL", "MANDANTSCHAFT"]),
    ("LANDGERICHT\nZIVILKAMMER\nGESCHÄFTSSTELLE", ["LANDGERICHT", "ZIVILKAMMER", "GESCHÄFTSSTELLE"]),
    ("EILT! SOFORT VORLEGEN", ["EILT", "SOFORT", "VORLEGEN"]),
    ("MIETVERTRAG ÜBER WOHNRAUM\n§ 1 MIETSACHE\n§ 2 MIETZEIT\n§ 3 MIETE UND NEBENKOSTEN", ["MIETVERTRAG", "WOHNRAUM", "MIETSACHE", "MIETZEIT", "MIETE", "NEBENKOSTEN"]),
]

_GATEWAY = ClaudePrivacyGateway()


def _word(text: str, word: str) -> bool:
    return re.search(r"(?<![\wÄÖÜäöüß-])" + re.escape(word) + r"(?![\wÄÖÜäöüß])", text) is not None


def _run(text: str):
    return _GATEWAY.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode="mask",
    )


def run() -> dict:
    leaks, lost, blocked = [], [], []
    leak_values = total_values = 0
    for text, values in POSITIVE:
        result = _run(text)
        if not result.allowed:
            blocked.append(text[:40])
            continue
        payload = result.payload.anonymisierter_sachverhalt
        for value in values:
            total_values += 1
            if _word(payload, value):
                leak_values += 1
                leaks.append(f"{value} | {text[:40]!r}")
    lost_values = total_keep = 0
    for text, keep in NEGATIVE:
        result = _run(text)
        if not result.allowed:
            blocked.append(text[:40])
            total_keep += len(keep)
            lost_values += len(keep)
            continue
        payload = result.payload.anonymisierter_sachverhalt
        for word in keep:
            total_keep += 1
            if not _word(payload, word):
                lost_values += 1
                lost.append(f"{word} | {text[:40]!r}")
    return {
        "leck_werte": leak_values, "positiv_werte": total_values, "leck_beispiele": leaks,
        "verlorene_werte": lost_values, "negativ_werte": total_keep, "verlust_beispiele": lost,
        "blockiert": blocked,
    }


if __name__ == "__main__":
    result = run()
    print(f"Versalien: Namen/Anschriften im Klartext {result['leck_werte']}/{result['positiv_werte']}, "
          f"Versal-Fachinhalt verloren {result['verlorene_werte']}/{result['negativ_werte']}, "
          f"Blockaden {len(result['blockiert'])}")
    for line in result["leck_beispiele"]:
        print("   LECK:", line)
    for line in result["verlust_beispiele"]:
        print("   VERLUST:", line)
    for line in result["blockiert"]:
        print("   BLOCK:", line)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
