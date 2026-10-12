"""Benchmark (Folgezyklus 13.10.2026): Gesetzesabkuerzungen in Normzitaten muessen lesbar bleiben.

Befund: "Der Halter haftet nach § 7 StVG." -> "§ 7 [PERSON_01]" (vorbestehend). Goldstandard: in allen Saetzen bleiben
die Abkuerzung und das Zitat lesbar; zur Gegenprobe enthalten einige Saetze zusaetzlich einen Personennamen, der
geschuetzt bleiben muss. Ausschliesslich synthetisch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

CODES = (
    "BGB HGB StGB StVG StVO ZPO GG VwGO BauGB WEG AGG UWG InsO GmbHG AktG BetrVG KSchG TzBfG ArbZG BUrlG EStG UStG AO "
    "SGB HOAI BImSchG StPO OWiG BDSG DSGVO TKG GewO GVG RVG GKG FamFG ZVG BeurkG ProdHaftG PflVG VVG FeV MuSchG "
    "BauNVO ArbSchG"
).split()
FRAMES = [
    "Der Halter haftet nach § 7 {C}.",
    "Der Anspruch folgt aus § 280 Abs. 1 {C} in Verbindung mit § 241 BGB.",
    "Gemäß Art. 3 {C} ist das zu prüfen.",
    "Herr Wessolowski beruft sich auf §§ 1, 2 {C}.",
]
PROTECTED = "Wessolowski"

_GATEWAY = ClaudePrivacyGateway()


def run() -> dict:
    lost, leaks, blocked = [], [], []
    for code in CODES:
        for frame in FRAMES:
            text = frame.format(C=code)
            result = _GATEWAY.prepare_request(
                purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text),
                anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode="mask",
            )
            if not result.allowed:
                blocked.append(text)
                continue
            payload = result.payload.anonymisierter_sachverhalt
            if code not in payload:
                lost.append(f"{code} | {frame[:30]}")
            if PROTECTED in text and PROTECTED in payload:
                leaks.append(text)
    return {"faelle": len(CODES) * len(FRAMES), "abkuerzung_verloren": len(lost), "beispiele": lost,
            "name_lecks": len(leaks), "blockiert": len(blocked)}


if __name__ == "__main__":
    result = run()
    print(f"Normzitate: Abkuerzung verloren {result['abkuerzung_verloren']}/{result['faelle']}, "
          f"Namen-Lecks {result['name_lecks']}, Blockaden {result['blockiert']}")
    for line in result["beispiele"]:
        print("   VERLOREN:", line)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
