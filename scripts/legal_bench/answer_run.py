"""Vorbereitung eines SPAETEREN Laufs des Antwortqualitaets-Benchmarks `benchmarks/legal_answers_v1` (§77).

Dieses Modul fuehrt NICHTS aus: es enthaelt keinen Netzwerk-, Cloud- oder Modellaufruf. Es
1. prueft eine Laufkonfiguration auf offene Entscheidungen (`check`) - ohne vollstaendige Freigabe wird jede
   Ausfuehrung verweigert (`ensure_runnable`);
2. erstellt einen versionierten Aufrufplan (`plan`) mit Fingerabdruecken von Akte, Spezifikation, Rubrik und Lock;
3. definiert das Format eines spaeteren Antwortdatensatzes (`validate_answer_record`) - echte Antworten muessen ueber
   den regulaeren, pseudonymisierten Lexono-Pfad entstehen (Payload-Erfassung, Leak-Check, Fail-Closed);
4. erzeugt fuer eine vorhandene Antwort einen BLINDEN Pruefbogen mit Referenzbezug und Fundstellen (`review_sheet`) und
   fasst einen ausgefuellten Bogen nach den Rubrikregeln zusammen (`aggregate`): bestaetigter kritischer Fehler ->
   FAIL_KRITISCH ohne Ausgleich; ohne festgelegte Schwellen nie PASS.

Aufruf: python -m scripts.legal_bench.answer_run {check,plan} --config PFAD"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from scripts.legal_bench import answer_specs, corpus

TEMPLATE = answer_specs.ROOT / "run_config.template.json"
#: Datenschutz-Schalter, die fuer einen Lauf exakt diesen Wert haben MUESSEN (keine Abschwaechung)
REQUIRED_PRIVACY = {"payload_capture": True, "leak_check_required": True, "fail_closed_required": True,
                    "real_client_data": False}
ANSWER_FIELDS = ("answer_id", "spec_id", "task_id", "repetition", "model_id", "model_version", "prompt_version",
                 "lexono_commit", "payload_sha256", "answer_sha256", "created_at", "answer_text")
SCORES = {0, 1, 2, "n/a"}


class NotApproved(RuntimeError):
    """Lauf nicht freigegeben - offene Entscheidungen oder unzulaessige Konfiguration."""


def load_config(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _walk(prefix: str, value):
    if isinstance(value, dict):
        for key, inner in value.items():
            if not key.startswith("_"):
                yield from _walk(f"{prefix}.{key}" if prefix else key, inner)
    else:
        yield prefix, value


def check(config: dict) -> dict:
    """Offene Entscheidungen (null) und Verstoesse gegen Pflichtwerte - nichts wird stillschweigend angenommen."""
    open_items = [path for path, value in _walk("", config) if value is None]
    if config.get("approved") is not True:
        open_items.append("approved (ausdrueckliche Freigabe fehlt)")
    violations = [f"privacy.{k} muss {v} sein" for k, v in REQUIRED_PRIVACY.items()
                  if (config.get("privacy") or {}).get(k) is not v]
    if (config.get("limits") or {}).get("abort_on_first_privacy_finding") is not True:
        violations.append("limits.abort_on_first_privacy_finding muss true sein")
    if (config.get("pipeline") or {}).get("path") != "lexono_chat_pseudonymisiert":
        violations.append("pipeline.path muss lexono_chat_pseudonymisiert sein (kein Direktaufruf am Datenschutz vorbei)")
    spec_report = answer_specs.validate_all()
    violations += [f"Spezifikationen: {e}" for e in spec_report.errors]
    return {"open": open_items, "violations": violations, "runnable": not open_items and not violations}


def ensure_runnable(config: dict) -> None:
    result = check(config)
    if not result["runnable"]:
        raise NotApproved(f"Lauf nicht freigegeben: {len(result['open'])} offene Entscheidungen, "
                          f"{len(result['violations'])} Verstoesse")


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=answer_specs.REPO,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def plan(config: dict) -> dict:
    """Aufrufplan mit allen Eingabe-Fingerabdruecken - ohne Ausfuehrung, auch ohne Freigabe erstellbar."""
    cases = {c["id"]: c for c in corpus.load_cases()}
    specs = {s["id"]: s for s in answer_specs.load_specs()}
    rubric_fp = answer_specs.file_fingerprint(answer_specs.ROOT / "rubric.json")
    scope = config.get("scope") or {}
    repetitions = scope.get("repetitions")
    calls = []
    for sid in scope.get("specs") or []:
        spec = specs.get(sid)
        if spec is None or spec["split"] not in (scope.get("splits") or []):
            continue
        case = cases[spec["case"]]
        tasks = {t["id"]: t for t in case["reference"]["tasks"]}
        for tid in spec["tasks"]:
            for rep in range(1, (repetitions or 1) + 1):
                calls.append({"spec_id": sid, "case": spec["case"], "split": spec["split"], "task_id": tid,
                              "kind": tasks[tid]["kind"], "question": tasks[tid]["question"], "repetition": rep,
                              "case_fingerprint": answer_specs.case_fingerprint(case),
                              "spec_fingerprint": answer_specs.spec_fingerprint(spec), "spec_version": spec["version"]})
    limits = config.get("limits") or {}
    price = limits.get("price_per_call_eur_estimate")
    max_calls = limits.get("max_calls")
    return {
        "art": "aufrufplan - KEINE Ausfuehrung",
        "git_commit": _git_commit(),
        "rubric_fingerprint": rubric_fp,
        "lock_errors": answer_specs.check_lock(),
        "repetitions": repetitions if repetitions is not None else "OFFEN (Plan mit 1 Wiederholung)",
        "calls": calls,
        "anzahl_aufrufe": len(calls),
        "kosten_schaetzung_eur": round(len(calls) * price, 2) if price is not None else "OFFEN",
        "innerhalb_max_calls": (len(calls) <= max_calls) if max_calls is not None else "OFFEN",
        "innerhalb_max_kosten": ((len(calls) * price) <= limits["max_cost_eur"])
        if price is not None and limits.get("max_cost_eur") is not None else "OFFEN",
    }


# --- Antwortdatensatz, Pruefbogen, Zusammenfassung -------------------------------------------------------------------


def validate_answer_record(record: dict) -> list[str]:
    errors = [f"Feld {f} fehlt" for f in ANSWER_FIELDS if record.get(f) in (None, "")]
    text = record.get("answer_text") or ""
    if record.get("answer_sha256") and hashlib.sha256(text.encode("utf-8")).hexdigest() != record["answer_sha256"]:
        errors.append("answer_sha256 passt nicht zum Antworttext (veraendert?)")
    return errors


def _reference(crit: dict, case: dict) -> dict:
    ref: dict = {}
    facts = {f["id"]: f for f in case["reference"]["facts"]}
    if "fact" in crit:
        fact = facts[crit["fact"]]
        ref["fakt"] = {"id": fact["id"], "aussage": fact["statement"], "werte": fact["values"], "dokumente": fact.get("docs", [])}
    if "norm" in crit:
        ref["norm"] = crit["norm"]
    if "deadline" in crit:
        ref["frist"] = next(({"datum": d["date"], "dokument": d["doc"], "art": d["kind"]}
                             for d in case["reference"]["deadlines"] if d["date"] == crit["deadline"]), None)
    if "open_point" in crit:
        point = next(p for p in case["solution"]["open_points"] if p["id"] == crit["open_point"])
        ref["offener_punkt"] = {"id": point["id"], "thema": point["topic"]}
    if crit["dimension"] == "ergebnis":
        src = case["solution"]["source"]
        ref["quelle"] = {"gericht": src["court"], "aktenzeichen": src["aktenzeichen"], "ecli": src["ecli"], "verwendet": src["used"]}
    return ref


def review_sheet(spec: dict, case: dict, record: dict) -> dict:
    """Blinder Pruefbogen (ohne Modellangaben) fuer eine VORHANDENE Antwort - Felder fuer die juristische Bewertung leer."""
    errors = validate_answer_record(record)
    if errors:
        raise ValueError("Antwortdatensatz ungueltig: " + "; ".join(errors))
    pre = answer_specs.precheck(spec, case, record["answer_text"])
    return {
        "art": "pruefbogen",
        "hinweis": "Bewertung durch qualifizierte juristische Person; Vorpruefung nur als Hinweis.",
        "answer_id": record["answer_id"], "spec_id": spec["id"], "spec_version": spec["version"],
        "spec_fingerprint": answer_specs.spec_fingerprint(spec), "case_fingerprint": answer_specs.case_fingerprint(case),
        "rubric_version": spec["rubric_version"], "task_id": record["task_id"],
        "antwort": record["answer_text"],
        "alternative_results": spec["alternative_results"], "alternative_results_status": spec["alternative_results_status"],
        "kriterien": [{"id": c["id"], "dimension": c["dimension"], "aussage": c["statement"], "bewertungsart": c["evaluation"],
                       "referenz": _reference(c, case), "vorpruefung": pre["kriterien"][c["id"]]["status"],
                       "bewertung": {"note": None, "begruendung": None, "fundstelle_in_antwort": None}}
                      for c in spec["criteria"]],
        "gefaehrliche_fehler": [{"id": e["id"], "aussage": e["statement"], "vorpruefung": pre["gefaehrliche_fehler"][e["id"]],
                                 "bestaetigt": None, "begruendung": None, "fundstelle_in_antwort": None}
                                for e in spec["dangerous_errors"]],
        "unbelegte_behauptungen": {"kandidaten": pre["nicht_in_akte_oder_quelle"], "note": None, "begruendung": None},
    }


def aggregate(sheet: dict, rubric: dict) -> dict:
    """Zusammenfassung eines AUSGEFUELLTEN Pruefbogens nach rubric.aggregation - kein Ausgleich kritischer Fehler."""
    agg = rubric["aggregation"]
    confirmed = [e["id"] for e in sheet["gefaehrliche_fehler"] if e["bestaetigt"] is True]
    if confirmed:
        return {"status": agg["critical_rule"], "kritische_fehler": confirmed,
                "hinweis": "Bestaetigter gefaehrlicher Fehler - durch andere Dimensionen nicht ausgleichbar."}
    rows = sheet["kriterien"] + [{"id": "unbelegt", "dimension": "unbelegte_behauptungen",
                                  "bewertung": {"note": sheet["unbelegte_behauptungen"]["note"],
                                                "begruendung": sheet["unbelegte_behauptungen"]["begruendung"]}}]
    missing = [r["id"] for r in rows if r["bewertung"]["note"] is None]
    missing += [e["id"] for e in sheet["gefaehrliche_fehler"] if e["bestaetigt"] is None]
    if missing:
        return {"status": "UNVOLLSTAENDIG", "fehlend": missing}
    invalid = [r["id"] for r in rows if r["bewertung"]["note"] not in SCORES]
    unjustified = [r["id"] for r in rows if r["bewertung"]["note"] == "n/a" and not r["bewertung"].get("begruendung")]
    if invalid or unjustified:
        return {"status": "NICHT_BEWERTBAR", "ungueltige_note": invalid, "n/a_ohne_begruendung": unjustified}
    per_dim: dict[str, list] = {}
    for r in rows:
        if r["bewertung"]["note"] != "n/a":
            per_dim.setdefault(r["dimension"], []).append(r["bewertung"]["note"])
    dims = {d: round(sum(v) / len(v), 2) for d, v in sorted(per_dim.items())}
    errors = [{"kriterium": r["id"], "dimension": r["dimension"], "note": r["bewertung"]["note"],
               "begruendung": r["bewertung"]["begruendung"], "fundstelle_in_antwort": r["bewertung"].get("fundstelle_in_antwort"),
               "referenz": r.get("referenz")} for r in rows if r["bewertung"]["note"] in (0, 1)]
    thresholds = agg.get("thresholds")
    if not thresholds:
        return {"status": "OFFEN", "dimensionen": dims, "fehlerbericht": errors,
                "hinweis": "Schwellen nicht festgelegt (rubric.aggregation.thresholds) - kein PASS moeglich."}
    failed = sorted(d for d, minimum in thresholds.items() if dims.get(d, 0) < minimum)
    return {"status": "FAIL" if failed else "PASS", "dimensionen": dims, "unter_schwelle": failed, "fehlerbericht": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "plan"])
    parser.add_argument("--config", default=str(TEMPLATE))
    args = parser.parse_args()
    config = load_config(Path(args.config))
    if args.command == "check":
        result = check(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("LAUF FREIGEGEBEN" if result["runnable"] else "LAUF NICHT FREIGEGEBEN")
        return 0 if result["runnable"] else 2
    print(json.dumps(plan(config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
