"""Datenschutz-Benchmark mit Quellenbezug (§65): benchmarks/privacy_sources_v1/cases.json.

Juristische Problemstellungen aus amtlich veroeffentlichten Entscheidungen (Rechtsprechung im Internet) und amtliche
Normtexte (Gesetze im Internet) in VOLLSTAENDIG SYNTHETISCHEN Sachverhalten (Quellen und Pruefung:
benchmarks/legal_e2e_v1/source_register.json). Gemessen mit DENSELBEN Pruef- und Bewertungsfunktionen wie
pseudonymization_v1 (scripts/bench_pseudonymization.py: `_call`, `evaluate`) - ergaenzt um:
  - Validierung des Korpus (jeder Wert steht im Text, Quellen nur mit Status "nutzbar"),
  - Chronologie (Daten im Payload in derselben Reihenfolge wie im Dokument),
  - Fail-Closed bei NER-Ausfall (Pseudonymisierung und Restrisiko-Scan): keine Anfrage.
Kein externer Aufruf - gemessen wird der Payload unmittelbar vor der Cloud-Grenze (`prepare_request`).

Aufruf:  python scripts/bench_privacy_sources.py [--out PFAD]   (Standard: benchmarks/privacy_sources_v1/results.json)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import bench_pseudonymization as base  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

BENCH_DIR = ROOT / "benchmarks" / "privacy_sources_v1"
REGISTER = ROOT / "benchmarks" / "legal_e2e_v1" / "source_register.json"


def load_corpus() -> dict:
    return json.loads((BENCH_DIR / "cases.json").read_text(encoding="utf-8"))


def validate(corpus: dict) -> list[str]:
    """Fehlerliste (leer = gueltig): Referenzwerte stehen woertlich im Text, Quellen sind freigegeben."""
    errors = []
    register = {s["id"]: s for s in json.loads(REGISTER.read_text(encoding="utf-8"))["sources"]}
    problems = {p["id"]: p for p in corpus["problems"]}
    for problem in problems.values():
        for ref in problem["source_refs"]:
            entry = register.get(ref)
            if entry is None or entry["nutzungsrechte"]["status"] != "nutzbar":
                errors.append(f"{problem['id']}: Quelle {ref} nicht freigegeben")
        if not problem.get("expected_result") or not problem.get("decision", {}).get("ecli"):
            errors.append(f"{problem['id']}: erwartetes Ergebnis oder ECLI fehlt")
    seen = set()
    for case in corpus["cases"]:
        cid = case["id"]
        if cid in seen:
            errors.append(f"{cid}: doppelte ID")
        seen.add(cid)
        if case.get("problem") not in problems:
            errors.append(f"{cid}: unbekannte Problemstellung {case.get('problem')!r}")
        for kind in ("protect", "keep", "chronology", "neutral"):
            for entry in case.get(kind, []):
                value = entry["value"] if isinstance(entry, dict) else entry
                if value not in case["text"]:
                    errors.append(f"{cid}: {kind}-Wert steht nicht im Text: {value[:30]!r}")
        positions = [case["text"].find(v) for v in case.get("chronology", [])]
        if positions != sorted(positions):
            errors.append(f"{cid}: Chronologie entspricht nicht der Reihenfolge im Text")
    return errors


def chronology_ok(case: dict, allowed: bool, payload: str) -> bool | None:
    dates = case.get("chronology", [])
    if not dates or not allowed:
        return None
    positions = [payload.find(d) for d in dates]
    return all(p >= 0 for p in positions) and positions == sorted(positions)


def run_case(gateway: ClaudePrivacyGateway, case: dict) -> dict:
    try:
        result = base._call(gateway, case)
    except Exception as exc:  # noqa: BLE001 - gemessen (fail-closed: kein Payload)
        row = base.evaluate(case, False, "", [], error=type(exc).__name__)
        row["chronology_ok"] = None
        return row
    allowed = bool(result.allowed and result.payload is not None)
    payload = base.payload_text(result.payload.model_dump()) if result.payload is not None else ""
    row = base.evaluate(case, allowed, payload, list(result.mappings or []))
    row["problem"] = case["problem"]
    row["chronology_ok"] = chronology_ok(case, allowed, payload)
    return row


def failing_gateways() -> list[tuple[str, ClaudePrivacyGateway]]:
    from app.privacy.presidio_ner import get_entity_types, get_pos_tags
    from app.privacy.pseudonymizer import Pseudonymizer
    from app.privacy.security_check import SecurityCheckService

    def failing(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("NER nicht verfuegbar (simuliert)")

    return [
        ("ner_pseudonymisierung", ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(ner_detector=failing))),
        ("ner_restrisiko", ClaudePrivacyGateway(security_check=SecurityCheckService(
            ner_detector=failing, pos_tagger=get_pos_tags, entity_type_tagger=get_entity_types))),
    ]


def run_fail_closed(cases: list[dict]) -> list[dict]:
    rows = []
    for label, gateway in failing_gateways():
        for case in cases:
            try:
                result = base._call(gateway, case)
                sent = bool(result.allowed and result.payload is not None)
            except Exception:  # noqa: BLE001 - Ausnahme = nichts gesendet
                sent = False
            rows.append({"id": case["id"], "failure": label, "payload_created": sent})
    return rows


def summarise(rows: list[dict], fail_closed: list[dict]) -> dict:
    protect = [item for row in rows for item in row["protect"]]
    fps = [p for row in rows for p in row["predicted"] if p["verdict"] == "FP"]
    keep_total = sum(row["keep_total"] for row in rows)
    keep_missing = [f"{row['id']}:{k}" for row in rows for k in row["keep_missing"]]
    chrono = [row["chronology_ok"] for row in rows if row["chronology_ok"] is not None]
    by_type: dict[str, dict] = {}
    for item in protect:
        entry = by_type.setdefault(item["type"], {"gold": 0, "leaks": 0, "partial_leaks": 0})
        entry["gold"] += 1
        entry["leaks"] += int(item["leak"])
        entry["partial_leaks"] += int(item["partial_leak"])
    return {
        "cases": len(rows), "gold": len(protect), "protected": sum(1 for i in protect if not i["leak"]),
        "leaks": sorted(f"{row['id']}:{i['type']}:{i['value']}" for row in rows for i in row["protect"] if i["leak"]),
        "partial_leaks": sorted(f"{row['id']}:{i['type']}:{i['value']}" for row in rows for i in row["protect"]
                                if i["partial_leak"]),
        "false_positives": sorted(f"{row['id']}:{p['type']}:{p['value']}" for row in rows for p in row["predicted"]
                                  if p["verdict"] == "FP"),
        "false_positive_count": len(fps),
        "keep_preserved": f"{keep_total - len(keep_missing)}/{keep_total}", "keep_missing": sorted(keep_missing),
        "chronology_preserved": f"{sum(chrono)}/{len(chrono)}",
        "blocked": sorted(row["id"] for row in rows if not row["allowed"]),
        "errors": sorted(f"{row['id']}:{row['error']}" for row in rows if row["error"]),
        "fail_closed": f"{sum(1 for r in fail_closed if not r['payload_created'])}/{len(fail_closed)}",
        "per_type": by_type,
    }


def run() -> dict:
    corpus = load_corpus()
    errors = validate(corpus)
    if errors:
        raise ValueError("Korpus ungueltig: " + "; ".join(errors))
    gateway = ClaudePrivacyGateway()
    rows = [run_case(gateway, case) for case in corpus["cases"]]
    fail_closed = run_fail_closed(corpus["cases"])
    return {"summary": summarise(rows, fail_closed), "rows": rows, "fail_closed": fail_closed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    outcome = run()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    output = {"benchmark": "privacy_sources_v1", "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "git_commit": commit,
              "cases_sha256": hashlib.sha256((BENCH_DIR / "cases.json").read_bytes()).hexdigest()[:16],
              "nicht_verifiziert": ["Antwortqualitaet eines echten Modells", "echte Transportstrecke"], **outcome}
    out = Path(args.out) if args.out else BENCH_DIR / "results.json"
    out.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
