"""Erweiterter Negativ-Korpus Versalien (13.10.2026): ganze Versal-Absaetze ohne Personennamen (OCR, Telefax, Formulare,
Gerichtsformeln, Vertragsueberschriften). Goldstandard: KEIN Personen-/Adress-/Orts-Platzhalter darf entstehen und
alle grossgeschriebenen Woerter bleiben lesbar (Gerichts-/Staedtenamen ausgenommen - deren Maskierung ist kein
Fehlalarm im Sinne dieses Korpus und wird separat gezaehlt). Ausschliesslich synthetisch."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

TEXTS = [
    "KAUFVERTRAG\n§ 1 KAUFGEGENSTAND\nDER VERKÄUFER VERKAUFT DEM KÄUFER DAS FAHRZEUG.\n§ 2 KAUFPREIS\nDER KAUFPREIS BETRÄGT 18.400,00 EUR.",
    "ALLGEMEINE GESCHÄFTSBEDINGUNGEN\n1. GELTUNGSBEREICH\nDIESE BEDINGUNGEN GELTEN FÜR ALLE VERTRÄGE.\n2. ZAHLUNG\nZAHLUNGEN SIND SOFORT FÄLLIG.",
    "MAHNBESCHEID\nANTRAGSTELLER UND ANTRAGSGEGNER SIND IM VERFAHREN BEZEICHNET.\nHAUPTFORDERUNG: 3.912,40 EUR\nZINSEN: 5 PROZENTPUNKTE ÜBER DEM BASISZINSSATZ",
    "VOLLSTRECKUNGSBESCHEID\nDIE FORDERUNG IST TITULIERT. DIE KOSTEN DES VERFAHRENS TRÄGT DER SCHULDNER.",
    "PROTOKOLL DER MÜNDLICHEN VERHANDLUNG\nDIE SACH- UND RECHTSLAGE WIRD ERÖRTERT. EIN VERGLEICH KOMMT NICHT ZUSTANDE.",
    "STRAFANZEIGE WEGEN BETRUGS\nDER SACHVERHALT ERGIBT SICH AUS DER ANLAGE. ES WIRD UM AKTENEINSICHT GEBETEN.",
    "KÜNDIGUNG\nHIERMIT KÜNDIGEN WIR DAS MIETVERHÄLTNIS FRISTGERECHT ZUM 31.12.2026.\nBITTE BESTÄTIGEN SIE DEN ERHALT.",
    "ACHTUNG FRIST! WIDERSPRUCH BIS SPÄTESTENS 30.11.2026 EINLEGEN. DANACH TRITT RECHTSKRAFT EIN.",
    "ÜBERGABEPROTOKOLL\nZÄHLERSTAND STROM: 12345\nSCHLÜSSEL: 3 STÜCK\nMÄNGEL: FEUCHTIGKEIT IM KELLER, RISSE IM ESTRICH",
    "RECHNUNG\nPOSITION 1: ESTRICHARBEITEN 1.200,00 EUR\nPOSITION 2: DÄMMUNG 450,00 EUR\nSUMME: 1.650,00 EUR",
    "VERTRAULICH – NICHT WEITERLEITEN\nINTERNE NOTIZ ZUM STAND DES VERFAHRENS: BEWEISAUFNAHME STEHT AUS.",
    "VOLLMACHT\nHIERMIT WIRD VOLLMACHT ERTEILT ZUR VERTRETUNG IN ALLEN RECHTSANGELEGENHEITEN.",
    "BESCHEINIGUNG ÜBER DIE ARBEITSUNFÄHIGKEIT\nERSTBESCHEINIGUNG\nVORAUSSICHTLICH ARBEITSUNFÄHIG BIS 15.04.2026",
    "GUTACHTEN\nZUSAMMENFASSUNG: DIE MÄNGEL SIND AUF EINE FEHLERHAFTE ABDICHTUNG ZURÜCKZUFÜHREN.",
    "TELEFAX\nSEITEN: 3\nBETREFF: ANFORDERUNG DER ERMITTLUNGSAKTE\nMIT DER BITTE UM KENNTNISNAHME",
    "URTEIL\nDIE BEKLAGTE WIRD VERURTEILT. DIE KOSTEN DES RECHTSSTREITS TRÄGT DIE BEKLAGTE. DAS URTEIL IST VORLÄUFIG VOLLSTRECKBAR.",
    "WIDERRUFSBELEHRUNG\nSIE HABEN DAS RECHT, BINNEN VIERZEHN TAGEN OHNE ANGABE VON GRÜNDEN DIESEN VERTRAG ZU WIDERRUFEN.",
    "HAFTUNGSAUSSCHLUSS\nDIE HAFTUNG FÜR LEICHTE FAHRLÄSSIGKEIT IST AUSGESCHLOSSEN, SOWEIT GESETZLICH ZULÄSSIG.",
    "TERMINSLADUNG\nTERMIN ZUR MÜNDLICHEN VERHANDLUNG: DIENSTAG, 10:00 UHR, SAAL 3",
    "BAUSTELLENVERORDNUNG – SICHERHEITS- UND GESUNDHEITSSCHUTZPLAN\nGERÜST, ABSTURZSICHERUNG UND BRANDSCHUTZ SIND ZU PRÜFEN.",
]
PLACEHOLDER = re.compile(r"\[(PERSON|ADRESSE|ORT|ORGANISATION|MANDANT|GEGNER)_\d+\]")

_GATEWAY = ClaudePrivacyGateway()


def run() -> dict:
    hits, blocked, lost_words = [], [], 0
    total_words = 0
    for text in TEXTS:
        result = _GATEWAY.prepare_request(
            purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text),
            anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode="mask",
        )
        words = sorted(set(re.findall(r"[A-ZÄÖÜß]{4,}", text)))
        total_words += len(words)
        if not result.allowed:
            blocked.append(text[:40])
            lost_words += len(words)
            continue
        body = result.payload.anonymisierter_sachverhalt.split("[Schreiben]", 1)[-1]
        missing = [w for w in words if w not in body]
        lost_words += len(missing)
        placeholders = PLACEHOLDER.findall(body)
        if missing or placeholders:
            hits.append(f"{text[:36]!r}: verloren {missing}, Platzhalter {placeholders}")
    return {"texte": len(TEXTS), "versalwoerter": total_words, "verloren": lost_words, "blockiert": blocked, "beispiele": hits}


if __name__ == "__main__":
    result = run()
    print(f"Versal-Negativkorpus: {result['verloren']}/{result['versalwoerter']} Versalwoerter verloren, "
          f"Blockaden {len(result['blockiert'])}, Texte mit Treffer {len(result['beispiele'])}/{result['texte']}")
    for line in result["beispiele"]:
        print("   ", line)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
