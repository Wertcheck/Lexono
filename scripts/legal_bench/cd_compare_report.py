"""P2.2 Phase 3: gemeinsame Auswertung Lexono (vorher/nachher), Claude und OpenAI auf identischen Faellen und mit
identischer Bewertung (`cd_measure.score`). Gibt Markdown-Tabellen aus und schreibt eine JSON-Zusammenfassung.

Aufruf: python -m scripts.legal_bench.cd_compare_report --out benchmarks/classification_deadlines_v1/vergleich.json"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.legal_bench.cd_measure import CORPUS, load, score

SOURCES = {
    "Lexono vorher (e2b3176)": ("lexono", "results_lexono_{split}_e2b3176.json"),
    "Lexono P2.2": ("lexono", "results_lexono_p22.json"),
    "Claude (claude-sonnet-5)": ("cloud", "results_cloud_{split}.json", "claude"),
    "OpenAI (gpt-5.5-2026-04-23)": ("cloud", "results_cloud_{split}.json", "openai"),
}


def _predictions(split: str, spec: tuple) -> dict:
    data = json.loads((CORPUS / spec[1].format(split=split)).read_text(encoding="utf-8"))
    if spec[0] == "lexono":
        return data["splits"][split]["vorhersagen"]
    return data["modelle"][spec[2]]["vorhersagen"]


def _row(scored: dict) -> dict:
    k, f, dur = scored["klassifikation"], scored["fristen"], scored["fristdauern"]
    return {
        "typ_richtig": k["richtig"], "kerntypen": k["kerntypen_richtig"], "unbekannt": k["unbekannt_quote"],
        "frist_gefunden": f"{f['frist']['richtig']}/{f['frist']['erwartet']}",
        "frist_falsch_davon_keine_frist": f"{f['frist']['falsch']} ({f['frist'].get('falsch_keine_frist_als_frist', 0)})",
        "termin": f"{f['termin']['richtig']}/{f['termin']['erwartet']} (falsch {f['termin']['falsch']})",
        "fristbeginn": f"{f['fristbeginn']['richtig']}/{f['fristbeginn']['erwartet']} (falsch {f['fristbeginn']['falsch']})",
        "dauern": f"{dur['erkannt']}/{dur['erwartet']} (+{dur['zusaetzlich']})",
        "kritisch": len(scored["kritisch"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report: dict = {}
    for split in ("dev", "test"):
        data = load(split)
        cloud = json.loads((CORPUS / f"results_cloud_{split}.json").read_text(encoding="utf-8"))
        sent = {doc for doc, reason in cloud["versand"].items() if reason == "ok"}
        subset = {**data, "documents": [d for d in data["documents"] if d["id"] in sent]}
        report[split] = {"n": len(data["documents"]), "n_gesendet": len(sent), "alle": {}, "gesendet": {},
                         "kosten": {}, "je_klasse": {}}
        for label, spec in SOURCES.items():
            predictions = _predictions(split, spec)
            report[split]["alle"][label] = _row(score(data, predictions))
            scored_subset = score(subset, predictions)
            report[split]["gesendet"][label] = _row(scored_subset)
            report[split]["je_klasse"][label] = scored_subset["klassifikation"]["je_klasse"]
            if spec[0] == "cloud":
                m = cloud["modelle"][spec[2]]
                report[split]["kosten"][label] = {"aufrufe": m["aufrufe"], "token_ein": m["token_ein"],
                                                  "token_aus": m["token_aus"], "usd": m["kosten_usd_geschaetzt"],
                                                  "sekunden": m["laufzeit_sekunden"]}
        for part, title in (("alle", f"alle {report[split]['n']} (nicht gesendet = keine Vorhersage)"),
                            ("gesendet", f"{report[split]['n_gesendet']} gesendete Dokumente (identische Teilmenge)")):
            rows = report[split][part]
            cols = list(next(iter(rows.values())).keys())
            print(f"\n### {split}: {title}\n")
            print("| Quelle | " + " | ".join(cols) + " |")
            print("|---|" + "---|" * len(cols))
            for label, row in rows.items():
                print(f"| {label} | " + " | ".join(str(row[c]) for c in cols) + " |")
        print(f"\nKosten {split}:", json.dumps(report[split]["kosten"], ensure_ascii=False))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
