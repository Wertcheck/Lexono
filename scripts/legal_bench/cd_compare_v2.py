"""P2.3/P2.4: Auswertung auf dem blinden Split `test_v2` - Lexono (vorher/nachher) gegen Claude und OpenAI ueber mehrere
Laeufe (Mittelwert, Minimum, Maximum je Kennzahl), identische Bewertung (`cd_measure.score`).

Aufruf: python -m scripts.legal_bench.cd_compare_v2 --out benchmarks/classification_deadlines_v1/vergleich_v2.json"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from scripts.legal_bench.cd_measure import CORPUS, load, score

KEYS = ("typ_richtig", "kerntypen_richtig", "unbekannt", "frist_richtig", "frist_uebersehen", "frist_keine_frist_als_frist",
        "termin_richtig", "termin_falsch", "fristbeginn_richtig", "fristbeginn_falsch", "dauern_erkannt",
        "dauern_zusaetzlich", "kritisch")


def _numbers(scored: dict) -> dict:
    k, f, dur = scored["klassifikation"], scored["fristen"], scored["fristdauern"]
    first = lambda fraction: int(fraction.split("/")[0])  # noqa: E731
    return {
        "typ_richtig": first(k["richtig"]), "kerntypen_richtig": first(k["kerntypen_richtig"]),
        "unbekannt": first(k["unbekannt_quote"]),
        "frist_richtig": f["frist"]["richtig"], "frist_uebersehen": f["frist"]["uebersehen"],
        "frist_keine_frist_als_frist": f["frist"].get("falsch_keine_frist_als_frist", 0),
        "termin_richtig": f["termin"]["richtig"], "termin_falsch": f["termin"]["falsch"],
        "fristbeginn_richtig": f["fristbeginn"]["richtig"], "fristbeginn_falsch": f["fristbeginn"]["falsch"],
        "dauern_erkannt": dur["erkannt"], "dauern_zusaetzlich": dur["zusaetzlich"], "kritisch": len(scored["kritisch"]),
    }


def _spread(values: list[int]) -> str:
    if len(values) == 1:
        return str(values[0])
    return f"{statistics.mean(values):.1f} [{min(values)}–{max(values)}]"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    data = load("test_v2")
    cloud = json.loads((CORPUS / "results_cloud_test_v2.json").read_text(encoding="utf-8"))
    sent = {doc for doc, reason in cloud["versand"].items() if reason == "ok"}
    subsets = {"alle": data, "gesendet": {**data, "documents": [d for d in data["documents"] if d["id"] in sent]}}
    sources: dict[str, list[dict]] = {
        "Lexono vorher (07c919d)": [json.loads((CORPUS / "results_lexono_test_v2_07c919d.json").read_text(
            encoding="utf-8"))["splits"]["test_v2"]["vorhersagen"]],
        "Lexono P2.3": [json.loads((CORPUS / "results_lexono_test_v2_p23.json").read_text(
            encoding="utf-8"))["splits"]["test_v2"]["vorhersagen"]],
    }
    for name, label in (("claude", "Claude (claude-sonnet-5)"), ("openai", "OpenAI (gpt-5.5-2026-04-23)")):
        sources[label] = [run["modelle"][name]["vorhersagen"] for run in cloud["laeufe"] if name in run["modelle"]]
    report: dict = {"n": len(data["documents"]), "n_gesendet": len(sent), "laeufe": len(cloud["laeufe"]),
                    "mit_dateiname": cloud.get("mit_dateiname"), "kosten": cloud.get("gesamt"), "teilmengen": {},
                    "je_klasse_gesendet": {}}
    for subset_name, subset in subsets.items():
        rows = {}
        for label, runs in sources.items():
            per_run = [_numbers(score(subset, predictions)) for predictions in runs]
            rows[label] = {key: _spread([r[key] for r in per_run]) for key in KEYS}
            rows[label]["laeufe"] = len(per_run)
            if subset_name == "gesendet":
                report["je_klasse_gesendet"][label] = score(subset, runs[0])["klassifikation"]["je_klasse"]
        report["teilmengen"][subset_name] = rows
        n = len(subset["documents"])
        print(f"\n### test_v2: {subset_name} (n={n}; Cloud-Werte: Mittelwert [Min–Max] über {report['laeufe']} Läufe)\n")
        print("| Quelle | " + " | ".join(KEYS) + " |")
        print("|---|" + "---|" * len(KEYS))
        for label, row in rows.items():
            print(f"| {label} | " + " | ".join(row[k] for k in KEYS) + " |")
    expected = {"frist": 0, "termin": 0, "fristbeginn": 0, "dauern": 0}
    for d in subsets["gesendet"]["documents"]:
        for kind in ("frist", "termin", "fristbeginn"):
            expected[kind] += len(set(d["fristen"][kind]) - set(d["fristen"].get("nicht_bewertet", [])))
        expected["dauern"] += d["fristen"]["fristdauern"]
    report["erwartet_gesendet"] = expected
    print("\nErwartet (gesendete Teilmenge):", expected, "| Kosten:", json.dumps(cloud.get("gesamt")))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
