"""§43.11 Phase 2: Rueckfrage- und Maskierungsstrategie fuer die Cloud-Zweitmeinung - Messung auf synthetischen,
vollstaendigen Dokumenten ueber den BESTEHENDEN `ClaudePrivacyGateway` (kein Netz, keine UI, keine Freigabelogik).

Je Dokument und Strategie: Status (gesendet/Rueckfrage/blockiert), Rueckfragen (Runden, Begriffe), Klartext-PII im
tatsaechlich sendefertigen Text, Informationsverlust (`keep`/`keep_gewoehnlich` verloren, automatisch maskierte
Nicht-PII-Begriffe).

- `ask`        Status quo: Rueckfrage ohne Antworten (fail-closed).
- `A_sorgfalt` A: dokumentbezogene Rueckfrage, simulierter SORGFAELTIGER Nutzer: ein Begriff ist "personal", wenn er
               Bestandteil eines Soll-PII-Werts ist, sonst "not_personal"; Runden bis gesendet/blockiert (max. 12).
- `A_fehler`   A mit FEHLBEDIENUNG: der Nutzer gibt jeden Begriff als "not_personal" frei - misst, was die
               uebrigen Schutzschichten (Erkennung, Restscan, Final Payload Gate) dann noch auffangen.
- `H_sorgfalt` / `H_fehler`  Hybrid: kennungsfoermige Begriffe (Rollen kennnummernform/kennzeichenform) werden ohne
               Frage als "personal" maskiert; nur die uebrigen Begriffe gehen an den (sorgfaeltigen bzw.
               fehlbedienenden) Nutzer. Nie eine automatische Freigabe.
- `B_mask`     B: automatische Pseudonymisierung (bestehender `review_mode="mask"`): unsichere Begriffe werden ohne
               Rueckfrage maskiert.

Aufruf: python -m scripts.legal_bench.review_strategy --cases <cases.json> --texts <texte.json> --out <ergebnis.json>"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from scripts.legal_bench.pii_measure import leaks

MAX_ROUNDS = 12
#: Rollen kennungsfoermiger Rueckfrage-Begriffe (app/privacy/review.py) - Variante H maskiert sie ohne Frage
IDENTIFIER_ROLES = {"kennnummernform", "kennzeichenform"}


class _Gold:
    """Ist ein Rueckfrage-Begriff Teil eines Soll-PII-Werts? Ja, wenn er einem Wert gleicht, als Teilstring darin
    steht (Kennungen wie "FH-SW 913") oder einen Wert enthaelt - oder ein Namensbestandteil (>= 3 Zeichen) ist."""

    def __init__(self, document: dict) -> None:
        self.values = [item["value"] for item in document["pii"]]
        self.tokens = {t for v in self.values for t in re.split(r"[\s,./()]+", v) if len(t) >= 3}

    def __contains__(self, term: str) -> bool:
        return term in self.tokens or any(term in v or v in term for v in self.values)


def _gold_tokens(document: dict) -> _Gold:
    return _Gold(document)


def _keep_lost(document: dict, extracted: str, output: str, key: str) -> list[str]:
    def present(value: str, text: str) -> bool:
        return re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(value) + r"(?![\wÄÖÜäöüß])", text) is not None

    return [k for k in document.get(key, []) if present(k, extracted) and not present(k, output)]


def _evaluate(document: dict, text: str, result) -> dict:  # noqa: ANN001
    sent = result.payload.anonymisierter_sachverhalt if result.allowed and result.payload else ""
    out = {"status": "gesendet" if sent else ("rueckfrage" if result.review_required else "blockiert")}
    if sent:
        found = leaks(document, text, sent)
        out["leck_werte"] = [w["value"] for w in found["werte"] if w["verbleibend"]]
        out["teil_leck"] = [w["value"] for w in found["werte"] if w["teil_leck"]]
        out["keep_verloren"] = _keep_lost(document, text, sent, "keep")
        out["gewoehnlich_verloren"] = _keep_lost(document, text, sent, "keep_gewoehnlich")
    return out


def run_document(gateway, document: dict, text: str, known) -> dict:  # noqa: ANN001
    from app.privacy.gateway_schema import SECOND_OPINION_PURPOSE
    from app.privacy.review import PrivacyDecisions

    gold = _gold_tokens(document)
    prepare = lambda mode, decisions=None: gateway.prepare_request(  # noqa: E731
        purpose=SECOND_OPINION_PURPOSE, sachverhalt=text, known_entities=known, review_mode=mode,
        privacy_decisions=decisions)

    ask = prepare("ask")
    entry: dict = {"ask": {**_evaluate(document, text, ask),
                           "kandidaten": [{"begriff": c.term, "pii": c.term in gold, "rollen": list(c.roles)}
                                          for c in ask.review_candidates]}}
    for name, careful, hybrid in (("A_sorgfalt", True, False), ("A_fehler", False, False),
                                  ("H_sorgfalt", True, True), ("H_fehler", False, True)):
        personal, not_personal, asked = set(), set(), []
        result = ask
        rounds = 0
        while result.review_required and rounds < MAX_ROUNDS:
            rounds += 1
            for candidate in result.review_candidates:
                if hybrid and IDENTIFIER_ROLES & set(candidate.roles):
                    personal.add(candidate.term)  # H: kennungsfoermig -> immer maskieren, keine Frage
                    continue
                asked.append(candidate.term)
                (personal if careful and candidate.term in gold else not_personal).add(candidate.term)
            decisions = PrivacyDecisions(personal=frozenset(personal), not_personal=frozenset(not_personal))
            result = prepare("ask", decisions)
        entry[name] = {**_evaluate(document, text, result), "runden": rounds, "fragen": len(asked),
                       "fragen_pii": sum(1 for t in asked if t in gold),
                       "blockgruende": [] if result.allowed or result.review_required else result.reasons[:3]}
    mask = prepare("mask")
    entry["B_mask"] = {**_evaluate(document, text, mask),
                       "auto_maskiert": len(mask.auto_masked_terms),
                       "auto_maskiert_nicht_pii": sorted(t for t in mask.auto_masked_terms if t not in gold)}
    return entry


def summarise(results: dict) -> dict:
    summary: dict = {}
    for strategy in ("ask", "A_sorgfalt", "A_fehler", "H_sorgfalt", "H_fehler", "B_mask"):
        rows = [r[strategy] for r in results.values()]
        summary[strategy] = {
            "status": dict(Counter(r["status"] for r in rows)),
            "leck_werte": sum(len(r.get("leck_werte", [])) for r in rows),
            "dokumente_mit_leck": sum(1 for r in rows if r.get("leck_werte")),
            "teil_leck_werte": sum(len(r.get("teil_leck", [])) for r in rows),
            "keep_verloren": sum(len(r.get("keep_verloren", [])) for r in rows),
            "gewoehnlich_verloren": sum(len(r.get("gewoehnlich_verloren", [])) for r in rows),
        }
        if strategy.startswith(("A_", "H_")):
            summary[strategy]["fragen_gesamt"] = sum(r["fragen"] for r in rows)
            summary[strategy]["fragen_davon_pii"] = sum(r["fragen_pii"] for r in rows)
            summary[strategy]["runden_max"] = max(r["runden"] for r in rows)
        if strategy == "B_mask":
            summary[strategy]["auto_maskiert"] = sum(r["auto_maskiert"] for r in rows)
            summary[strategy]["auto_maskiert_nicht_pii"] = sum(len(r["auto_maskiert_nicht_pii"]) for r in rows)
    candidates = [c for r in results.values() for c in r["ask"]["kandidaten"]]
    summary["kandidaten"] = {"gesamt": len(candidates), "davon_pii": sum(1 for c in candidates if c["pii"]),
                             "rollen": dict(Counter(role for c in candidates for role in c["rollen"]).most_common()),
                             "haeufigste_nicht_pii": Counter(c["begriff"] for c in candidates
                                                             if not c["pii"]).most_common(25)}
    return summary


def main() -> None:
    from app.privacy.gateway import ClaudePrivacyGateway

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--texts", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    texts = json.loads(Path(args.texts).read_text(encoding="utf-8"))
    gateway = ClaudePrivacyGateway()
    results = {d["id"]: run_document(gateway, d, texts[d["id"]], d.get("known_entities") or None)
               for d in data["documents"]}
    report = {"cases": Path(args.cases).name, "texts": Path(args.texts).name, "zusammenfassung": summarise(results),
              "dokumente": results}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["zusammenfassung"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
