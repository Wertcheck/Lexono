"""Antwortqualitaets-Benchmark `benchmarks/legal_answers_v1` (§76) - Spezifikationen, Validator, Vorpruefung.

KONZEPT, KEIN LAUF: Es werden keine Modellantworten abgerufen. Dieses Modul
1. prueft jede Spezifikation gegen Akte, Referenz und Quellenregister (jedes Kriterium aus Akte/Quelle ableitbar, nur
   freigegebene Quellen, Fingerabdruck der Akte zur Referenzversionierung, Test-Split eingefroren);
2. bietet eine VORPRUEFUNG fuer spaetere Antworten: je Kriterium "hinweis_gefunden" / "nicht_gefunden" und je
   gefaehrlichem Fehler "verdacht" - ausdruecklich KEINE Note und kein Urteil ueber die juristische Richtigkeit. Die
   Bewertung je Dimension vergibt eine qualifizierte juristische Person (rubric.json).

Aufruf: python -m scripts.legal_bench.answer_specs [--fingerprints]"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.legal_bench import corpus
from scripts.legal_bench.solution_score import _norm

ROOT = Path(__file__).resolve().parents[2] / "benchmarks" / "legal_answers_v1"
EVALUATIONS = {"juristische_pruefung", "teilautomatisch"}


@dataclass
class SpecReport:
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def case_fingerprint(case: dict) -> str:
    """SHA-256 ueber den kanonischen JSON-Inhalt des Falls (unabhaengig von Zeilenenden/Formatierung)."""
    content = {k: v for k, v in case.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_rubric(root: Path = ROOT) -> dict:
    return json.loads((root / "rubric.json").read_text(encoding="utf-8"))


def load_specs(root: Path = ROOT) -> list[dict]:
    specs = []
    for path in sorted((root / "specs").glob("*/*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["_dir_split"] = path.parent.name
        specs.append(spec)
    return specs


def validate_spec(spec: dict, cases: dict[str, dict], register: dict[str, dict], rubric: dict, report: SpecReport) -> None:
    sid = spec.get("id", "?")

    def need(condition: bool, message: str) -> None:
        if not condition:
            report.errors.append(f"{sid}: {message}")

    case = cases.get(spec.get("case"))
    need(case is not None, f"Fall {spec.get('case')!r} unbekannt")
    if case is None:
        return
    need(spec.get("split") == case["split"] == spec.get("_dir_split"), "split passt nicht zu Fall/Ordner")
    need(spec.get("rubric_version") == rubric["version"], "Rubrik-Version passt nicht")
    need(spec.get("case_fingerprint") == case_fingerprint(case),
         "Fingerabdruck der Akte weicht ab - Referenz geaendert, Spezifikation neu pruefen und Version erhoehen")
    if case["split"] == "test":
        need(spec.get("frozen") is True, "Test-Split-Spezifikation muss eingefroren sein (frozen: true)")
    # nur freigegebene Quellen, und nur die des Falls
    need(bool(spec.get("source_refs")) and set(spec["source_refs"]) <= set(case.get("source_refs", [])),
         "source_refs muessen Quellen des Falls sein")
    for ref in spec.get("source_refs", []):
        need(register.get(ref, {}).get("nutzungsrechte", {}).get("status") == "nutzbar", f"Quelle {ref} nicht freigegeben")
    tasks = {t["id"]: t for t in case["reference"]["tasks"]}
    for tid in spec.get("tasks", []):
        need(tid in tasks and tasks[tid].get("analyse"), f"Aufgabe {tid} ist keine Analyse-Aufgabe des Falls")

    solution = case.get("solution") or {}
    facts = {f["id"]: f for f in case["reference"]["facts"]}
    deadlines = {d["date"] for d in case["reference"]["deadlines"]}
    open_points = {p["id"] for p in solution.get("open_points", [])}
    docs_norm = _norm("\n".join(corpus.doc_text(d) for d in case["documents"]))
    dims = {d["id"] for d in rubric["dimensions"]}
    covered = set()
    for crit in spec.get("criteria", []):
        cid = crit.get("id")
        need(crit.get("dimension") in dims, f"{cid}: unbekannte Dimension {crit.get('dimension')!r}")
        need(crit.get("evaluation") in EVALUATIONS, f"{cid}: Bewertungsart fehlt/unbekannt")
        need(bool(crit.get("accept_any")) and all(isinstance(v, list) and v for v in crit["accept_any"]),
             f"{cid}: zulaessige Varianten (accept_any) fehlen")
        covered.add(crit.get("dimension"))
        if crit.get("dimension") == "ergebnis":
            need(crit.get("evaluation") == "juristische_pruefung", f"{cid}: Ergebnis braucht juristische Pruefung")
        if "norm" in crit:
            need(crit["norm"] in solution.get("required_norms", []), f"{cid}: Norm {crit['norm']} nicht in der Referenzloesung")
            need(_norm(crit["norm"]) in docs_norm, f"{cid}: Norm {crit['norm']} steht in keinem Dokument")
        if "fact" in crit:
            need(crit["fact"] in facts and crit["fact"] in solution.get("decisive_facts", []),
                 f"{cid}: Fakt {crit['fact']} ist kein entscheidender Fakt des Falls")
            values = facts.get(crit["fact"], {}).get("values", [])
            need(any(_norm(v) == _norm(" ".join(variant)) for v in values for variant in crit["accept_any"]),
                 f"{cid}: keine Variante entspricht einem Aktenwert von {crit['fact']}")
        if "deadline" in crit:
            need(crit["deadline"] in deadlines and crit["deadline"] in solution.get("deadlines", []),
                 f"{cid}: Frist {crit['deadline']} nicht in Referenz/Loesung")
        if "open_point" in crit:
            need(crit["open_point"] in open_points, f"{cid}: offener Punkt {crit['open_point']} unbekannt")
    for dim in ("ergebnis", "rechtsgrundlagen", "tatsachen", "fristen", "unsicherheiten"):
        need(dim in covered, f"Dimension {dim} ohne Kriterium")
    need(bool(spec.get("dangerous_errors")), "keine gefaehrlichen Fehler definiert")
    for err in spec.get("dangerous_errors", []):
        need(bool(err.get("suspect_any")), f"{err.get('id')}: Verdachtsformulierungen fehlen")
    need(bool(spec.get("known_wrong_decisions")), "typische Fehlentscheidungen fehlen")
    need("gefaehrliche_fehler" in spec.get("requires_qualified_review", []) and
         "ergebnis" in spec.get("requires_qualified_review", []),
         "Ergebnis und gefaehrliche Fehler muessen als juristisch zu pruefen markiert sein")


def validate_all(root: Path = ROOT) -> SpecReport:
    report = SpecReport()
    rubric = load_rubric(root)
    register = corpus.validate_register(corpus.load_register(), corpus.ValidationReport())
    cases = {c["id"]: c for c in corpus.load_cases()}
    specs = load_specs(root)
    if len({s["id"] for s in specs}) != len(specs):
        report.errors.append("doppelte Spezifikations-IDs")
    for spec in specs:
        validate_spec(spec, cases, register, rubric, report)
    return report


def _variant_found(variant: list[str], text_norm: str) -> bool:
    return all(_norm(term) in text_norm for term in variant)


_CITATION_CANDIDATE = re.compile(r"§\s*\d+[a-z]?|\b(?:[IVX]{1,5}|\d{1,3})\s+[A-Z]{1,4}\s+\d+/\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{4}\b")


def precheck(spec: dict, case: dict, answer: str) -> dict:
    """Vorpruefung einer Antwort - NUR Hinweise fuer die juristische Pruefung, keine Note, kein Urteil."""
    text_norm = _norm(answer)
    criteria = {c["id"]: {"dimension": c["dimension"], "evaluation": c["evaluation"],
                          "status": "hinweis_gefunden" if any(_variant_found(v, text_norm) for v in c["accept_any"])
                          else "nicht_gefunden"} for c in spec["criteria"]}
    dangerous = {e["id"]: "verdacht" if any(_variant_found(v, text_norm) for v in e["suspect_any"]) else "kein_hinweis"
                 for e in spec["dangerous_errors"]}
    known = _norm("\n".join(corpus.doc_text(d) for d in case["documents"]) + "\n" + json.dumps(spec, ensure_ascii=False)
                  + "\n" + json.dumps(case.get("solution", {}), ensure_ascii=False))
    unverified = sorted({m.group(0) for m in _CITATION_CANDIDATE.finditer(answer) if _norm(m.group(0)) not in known})
    return {
        "art": "vorpruefung",
        "hinweis": "Keine Bewertung: Note je Dimension durch qualifizierte juristische Person (rubric.json).",
        "kriterien": criteria,
        "gefaehrliche_fehler": dangerous,
        "nicht_in_akte_oder_quelle": unverified,  # zur Pruefung auf unbelegte Behauptungen - Existenz NICHT widerlegt
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fingerprints", action="store_true", help="Fingerabdruecke der Faelle ausgeben")
    args = parser.parse_args()
    if args.fingerprints:
        cases = {c["id"]: c for c in corpus.load_cases()}
        for spec in load_specs():
            print(spec["id"], spec["case"], case_fingerprint(cases[spec["case"]]))
        return 0
    report = validate_all()
    for error in report.errors:
        print("FEHLER:", error)
    print(f"{len(load_specs())} Spezifikationen - {'OK' if report.ok else 'FEHLER'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
