"""Vorbereitung eines SPAETEREN Laufs des Antwortqualitaets-Benchmarks `benchmarks/legal_answers_v1` (§77/§78).

Dieses Modul fuehrt NICHTS aus: es enthaelt keinen Netzwerk-, Cloud- oder Modellaufruf (statisch geprueft,
tests/test_s78_benchmark_gate.py). Es
1. prueft eine Laufkonfiguration (`check`) - seit §78 STRENG: jeder Schluessel der Vorlage muss vorhanden sein, keine
   unbekannten Schluessel, Typen und Wertebereiche, strukturierte Freigaben (Person, Rolle, Datum, Entscheidung),
   aktueller Commit, bekannte Spezifikationen, Aufrufzahl und Kosten innerhalb der Grenzen, unveraenderte Datenschutz-
   Pflichtwerte, gueltige Spezifikationen und Lock. Ohne vollstaendige Freigabe wird jede Ausfuehrung verweigert
   (`ensure_runnable`); es gibt keinen Standardwert und keine Freigabe durch Weglassen;
2. erstellt einen versionierten Aufrufplan (`plan`) mit Fingerabdruecken von Akte, Spezifikation, Rubrik und Lock und
   einem reproduzierbaren Plan-Fingerabdruck; ein Plan ohne gueltige Freigabe ist ausdruecklich NICHT ausfuehrbar;
3. definiert das Format eines spaeteren Antwortdatensatzes (`validate_answer_record`) - echte Antworten muessen ueber
   den regulaeren, pseudonymisierten Lexono-Pfad entstehen (Gateway-Siegel, Payload-Erfassung, Leak-Check, Fail-Closed);
4. erzeugt fuer eine vorhandene Antwort einen BLINDEN Pruefbogen mit Referenzbezug und Fundstellen (`review_sheet`) und
   fasst einen ausgefuellten Bogen nach den Rubrikregeln zusammen (`aggregate`): bestaetigter kritischer Fehler ->
   FAIL_KRITISCH ohne Ausgleich; ohne festgelegte Schwellen nie PASS.

Aufruf: python -m scripts.legal_bench.answer_run {check,plan} --config PFAD"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from datetime import date
from pathlib import Path

from scripts.legal_bench import answer_specs, corpus

TEMPLATE = answer_specs.ROOT / "run_config.template.json"
#: Version des Konfigurationsschemas - eine Konfiguration mit anderer Version gilt als veraltet
SCHEMA_VERSION = 2  # §80: Tokenpreise fuer die tatsaechliche Kostenerfassung (limits.price_eur_per_million_*)
#: Datenschutz-Schalter, die fuer einen Lauf exakt diesen Wert haben MUESSEN (keine Abschwaechung)
REQUIRED_PRIVACY = {"payload_capture": True, "leak_check_required": True, "fail_closed_required": True,
                    "real_client_data": False}
REQUIRED_PIPELINE = "lexono_chat_pseudonymisiert"
#: einzig unterstuetzter Cloud-KI-Anbieter in Lexono (app/web/settings_router.py::_SUPPORTED_CLOUD_AI_PROVIDERS)
SUPPORTED_PROVIDERS = {"claude"}
#: Obergrenze wie Settings.claude_max_tokens (Relay lehnt groessere Anfragen ab, §70)
MAX_TOKENS_LIMIT = 4000
ANSWER_FIELDS = ("answer_id", "spec_id", "task_id", "repetition", "model_id", "model_version", "prompt_version",
                 "lexono_commit", "payload_sha256", "answer_sha256", "created_at", "answer_text")
SCORES = {0, 1, 2, "n/a"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class NotApproved(RuntimeError):
    """Lauf nicht freigegeben - offene Entscheidungen oder unzulaessige Konfiguration. `reasons`: konkrete Gruende."""

    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = list(reasons or [])


def load_config(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _walk(prefix: str, value):
    if isinstance(value, dict):
        for key, inner in value.items():
            if not key.startswith("_"):
                yield from _walk(f"{prefix}.{key}" if prefix else key, inner)
    else:
        yield prefix, value


def _get(config: dict, path: str):
    node = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


_MISSING = object()


def _is_int(value, minimum: int = 1, maximum: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool) and value >= minimum
            and (maximum is None or value <= maximum))


def _is_number(value, minimum: float = 0.0, exclusive: bool = True) -> bool:
    # §81: nur endliche Zahlen - ein unendliches Kostenlimit/Preis waere sonst "gueltig" (kein Budget)
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and (value > minimum if exclusive else value >= minimum))


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


#: §81: erlaubte Felder einer Freigabe (Zusatzfelder wie "signatur" taeuschen Sicherheit vor, die es nicht gibt)
APPROVAL_FIELDS = {"person", "rolle", "datum", "entscheidung"}
#: die Owner-Freigabe von Lauf und Kosten gilt fuer GENAU einen Plan (Fingerabdruck aus `plan`)
OWNER_APPROVAL = "owner_freigabe_lauf_und_kosten"


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or not _DATE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _approval_ok(value, fields: set[str] = APPROVAL_FIELDS) -> bool:
    return (isinstance(value, dict) and set(value) == fields and _is_text(value.get("person"))
            and _is_text(value.get("rolle")) and _parse_date(value.get("datum")) is not None
            and value.get("entscheidung") == "freigegeben")


def _owner_approval_ok(value) -> bool:
    return _approval_ok(value, APPROVAL_FIELDS | {"plan_fingerprint"}) and bool(
        re.fullmatch(r"[0-9a-f]{64}", str(value.get("plan_fingerprint"))))


def legal_state() -> dict:
    """§81: juristischer Stand der Bezugsobjekte - eine juristische Freigabe ist ein WIDERSPRUCH, solange die
    Spezifikationen als Entwurf/offen markiert sind bzw. die Rubrik keine Schwellen und Gewichte hat."""
    open_specs = {s["id"]: s.get("status") for s in answer_specs.load_specs()
                  if "entwurf" in str(s.get("status")) or "offen" in str(s.get("status"))}
    aggregation = json.loads((answer_specs.ROOT / "rubric.json").read_text(encoding="utf-8")).get("aggregation", {})
    return {"open_specs": open_specs,
            "rubric_open": aggregation.get("thresholds") is None or aggregation.get("weights") is None}


def _lock_date() -> date | None:
    try:
        history = json.loads(answer_specs.LOCK_PATH.read_text(encoding="utf-8")).get("history") or []
        return _parse_date(history[-1].get("date")) if history else None
    except (OSError, ValueError, AttributeError):
        return None


def _approval_findings(config: dict, plan_fingerprint: str) -> list[str]:
    """§81: Widersprueche und veraltete Freigaben (nur fuer formal gueltige Eintraege). Grenze: ohne Signatur und
    Vertrauensanker bleibt offen, WER den Eintrag geschrieben hat - siehe AUDIT_S81.md."""
    findings = []
    approvals = config.get("approvals") if isinstance(config.get("approvals"), dict) else {}
    today, locked = date.today(), _lock_date()
    for name, value in sorted(approvals.items()):
        if not isinstance(value, dict) or _parse_date(value.get("datum")) is None:
            continue
        when = _parse_date(value["datum"])
        if when > today:
            findings.append(f"approvals.{name}: in der Zukunft datiert ({when}) - unzulaessig")
        if locked and when < locked:
            findings.append(f"approvals.{name}: veraltet - datiert {when}, vor der letzten Neusperre der "
                            f"Referenzen ({locked}); die Freigabe bezieht sich nicht auf den gesperrten Stand")
    owner = approvals.get(OWNER_APPROVAL)
    if isinstance(owner, dict) and owner.get("plan_fingerprint") not in (None, plan_fingerprint):
        findings.append(f"approvals.{OWNER_APPROVAL}: gilt fuer einen anderen Plan "
                        f"({str(owner.get('plan_fingerprint'))[:12]} != {plan_fingerprint[:12]}) - Plan, Commit oder "
                        "Sperre haben sich seit der Freigabe geaendert")
    state = legal_state()
    scope = set(_get(config, "scope.specs") or []) if isinstance(_get(config, "scope.specs"), list) else set()
    if _approval_ok(approvals.get("juristische_freigabe_spezifikationen")):
        for sid in sorted(scope & set(state["open_specs"])):
            findings.append(f"approvals.juristische_freigabe_spezifikationen: Widerspruch - {sid} hat Status "
                            f"{state['open_specs'][sid]!r}")
    if _approval_ok(approvals.get("juristische_freigabe_schwellen_und_gewichte")) and state["rubric_open"]:
        findings.append("approvals.juristische_freigabe_schwellen_und_gewichte: Widerspruch - rubric.json hat keine "
                        "Schwellen/Gewichte (null)")
    return findings


def _field_rules() -> dict:
    """Pflichtregel je Konfigurationsfeld (Pfad -> (Pruefung, Fehlermeldung))."""
    return {
        "schema_version": (lambda v: v == SCHEMA_VERSION, f"muss {SCHEMA_VERSION} sein (veraltete/unbekannte Konfiguration)"),
        "run_id": (lambda v: isinstance(v, str) and bool(re.fullmatch(r"[A-Za-z0-9._-]{3,64}", v)), "3-64 Zeichen [A-Za-z0-9._-]"),
        # false = Freigabe OFFEN (siehe unten), alles ausser true/false ("true", 1, "ja") ist ein Verstoss
        "approved": (lambda v: isinstance(v, bool), "nur true/false - Gesamtfreigabe ausschliesslich als true"),
        "approvals.owner_freigabe_lauf_und_kosten": (_owner_approval_ok, "Freigabe {person, rolle, datum YYYY-MM-DD, entscheidung: freigegeben, plan_fingerprint} - genau diese Felder"),
        "approvals.juristische_freigabe_spezifikationen": (_approval_ok, "Freigabe {person, rolle, datum, entscheidung: freigegeben}"),
        "approvals.juristische_freigabe_schwellen_und_gewichte": (_approval_ok, "Freigabe {person, rolle, datum, entscheidung: freigegeben}"),
        "approvals.datenschutz_freigabe": (_approval_ok, "Freigabe {person, rolle, datum, entscheidung: freigegeben}"),
        "model.provider": (lambda v: v in SUPPORTED_PROVIDERS, f"unterstuetzt nur {sorted(SUPPORTED_PROVIDERS)}"),
        "model.model_id": (lambda v: isinstance(v, str) and bool(re.fullmatch(r"claude-[a-z0-9][a-z0-9.-]*", v)), "Modell-ID 'claude-...'"),
        "model.model_version": (_is_text, "genaue Modellversion angeben"),
        "model.temperature": (lambda v: _is_number(v, 0.0, exclusive=False) and v <= 1, "Zahl 0..1"),
        "model.max_tokens": (lambda v: _is_int(v, 1, MAX_TOKENS_LIMIT), f"ganze Zahl 1..{MAX_TOKENS_LIMIT}"),
        "pipeline.path": (lambda v: v == REQUIRED_PIPELINE, f"muss {REQUIRED_PIPELINE} sein (kein Direktaufruf am Datenschutz vorbei)"),
        "pipeline.lexono_commit": (lambda v: isinstance(v, str) and bool(re.fullmatch(r"[0-9a-f]{40}", v)), "vollstaendiger Commit-Hash"),
        "pipeline.prompt_version": (_is_text, "Prompt-Version angeben"),
        "scope.splits": (lambda v: isinstance(v, list) and bool(v) and set(v) <= {"dev", "test"} and len(v) == len(set(v)), "Teilmenge von dev/test"),
        "scope.specs": (lambda v: isinstance(v, list) and bool(v) and all(_is_text(s) for s in v) and len(v) == len(set(v)), "nicht leere Liste ohne Doppelungen"),
        "scope.repetitions": (lambda v: _is_int(v, 1), "ganze Zahl >= 1"),
        "limits.max_calls": (lambda v: _is_int(v, 1), "ganze Zahl >= 1"),
        "limits.max_cost_eur": (lambda v: _is_number(v, 0.0), "Zahl > 0"),
        "limits.price_per_call_eur_estimate": (lambda v: _is_number(v, 0.0), "Zahl > 0"),
        # §80: tatsaechliche Kosten = Usage-Tokens der Provider-Antwort x freigegebener Tokenpreis (Owner-Entscheidung)
        "limits.price_eur_per_million_input_tokens": (lambda v: _is_number(v, 0.0), "Zahl > 0 (EUR je 1 Mio. Eingabe-Tokens)"),
        "limits.price_eur_per_million_output_tokens": (lambda v: _is_number(v, 0.0), "Zahl > 0 (EUR je 1 Mio. Ausgabe-Tokens)"),
        "limits.abort_on_first_privacy_finding": (lambda v: v is True, "muss true sein"),
        "limits.abort_after_failed_calls": (lambda v: _is_int(v, 1), "ganze Zahl >= 1"),
        **{f"privacy.{k}": ((lambda expected: lambda v: v is expected)(v), f"muss {str(v).lower()} sein")
           for k, v in REQUIRED_PRIVACY.items()},
        "storage.results_dir": (_is_text, "Ablageort angeben"),
        "storage.retention": (_is_text, "Aufbewahrung angeben"),
        "evaluation.blind_review": (lambda v: v is True, "muss true sein"),
        "evaluation.reviewers": (lambda v: isinstance(v, list) and bool(v) and all(_is_text(r) for r in v), "nicht leere Liste"),
        "evaluation.second_reviewer_for_critical": (lambda v: isinstance(v, bool), "true/false festlegen"),
    }


def _template_paths() -> set[str]:
    template = load_config(TEMPLATE)
    paths = set()

    def collect(prefix: str, node) -> None:
        for key, value in node.items():
            if key.startswith("_"):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                collect(path, value)
            else:
                paths.add(path)

    collect("", template)
    return paths


def _config_paths(config: dict) -> set[str]:
    paths = set()

    def collect(prefix: str, node) -> None:
        for key, value in node.items():
            if key.startswith("_"):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and not path.startswith("approvals."):
                collect(path, value)
            else:
                paths.add(path)

    if isinstance(config, dict):
        collect("", config)
    return paths


def check(config: dict) -> dict:
    """Strenge Freigabepruefung: offene (null/fehlende) Entscheidungen und Verstoesse mit konkretem Grund - nichts wird
    stillschweigend angenommen, Weglassen eines Feldes ist keine Freigabe."""
    if not isinstance(config, dict):
        return {"open": [], "violations": ["Konfiguration ist kein JSON-Objekt"], "runnable": False}
    rules = _field_rules()
    expected = _template_paths()
    open_items, violations = [], []
    for path in sorted(expected | set(rules)):
        value = _get(config, path)
        if value is _MISSING:
            violations.append(f"{path}: fehlt (Pflichtfeld der Vorlage - Weglassen ist keine Freigabe)")
        elif value is None:
            open_items.append(path)
        elif path in rules and not rules[path][0](value):
            violations.append(f"{path}: ungueltig ({rules[path][1]})")
    unknown = sorted(p for p in _config_paths(config) - expected if not any(p.startswith(e + ".") for e in expected))
    violations += [f"{p}: unbekannter Schluessel (nicht in der Vorlage - keine Zusatzschalter)" for p in unknown]
    if config.get("approved") is not True and "approved" not in open_items:
        open_items.append("approved (ausdrueckliche Freigabe fehlt)")

    commit = _get(config, "pipeline.lexono_commit")
    current = _git_commit()
    if isinstance(commit, str) and commit and current and commit != current:
        violations.append(f"pipeline.lexono_commit: veraltet ({commit[:12]} != aktueller Stand {current[:12]})")
    specs = {s["id"]: s for s in answer_specs.load_specs()}
    scope_specs = _get(config, "scope.specs")
    scope_splits = _get(config, "scope.splits")
    if isinstance(scope_specs, list):
        for sid in scope_specs:
            if sid not in specs:
                violations.append(f"scope.specs: unbekannte Spezifikation {sid!r}")
            elif isinstance(scope_splits, list) and specs[sid]["split"] not in scope_splits:
                violations.append(f"scope.specs: {sid} gehoert zu Split {specs[sid]['split']!r}, der nicht in scope.splits steht")
    planned = _planned_calls(config)
    violations += _approval_findings(config, _plan_fingerprint(planned))
    limits = config.get("limits") if isinstance(config.get("limits"), dict) else {}
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    if (_is_number(limits.get("price_per_call_eur_estimate")) and _is_number(limits.get("price_eur_per_million_output_tokens"))
            and _is_int(model.get("max_tokens"), 1)):
        worst_output = model["max_tokens"] * limits["price_eur_per_million_output_tokens"] / 1_000_000
        if limits["price_per_call_eur_estimate"] < worst_output:
            violations.append(f"limits.price_per_call_eur_estimate: Widerspruch - {limits['price_per_call_eur_estimate']} "
                              f"EUR liegt unter den Kosten der maximalen Ausgabe ({worst_output:.4f} EUR = max_tokens x "
                              "Ausgabepreis); die Schaetzung waere zu niedrig")
    if not open_items and not violations:
        calls = len(planned)
        if calls == 0:
            violations.append("Aufrufplan leer")
        if calls > config["limits"]["max_calls"]:
            violations.append(f"limits.max_calls: {calls} geplante Aufrufe > {config['limits']['max_calls']}")
        cost = calls * config["limits"]["price_per_call_eur_estimate"]
        if cost > config["limits"]["max_cost_eur"]:
            violations.append(f"limits.max_cost_eur: geschaetzt {cost:.2f} EUR > {config['limits']['max_cost_eur']} EUR")
    spec_report = answer_specs.validate_all()
    violations += [f"Spezifikationen/Lock: {e}" for e in spec_report.errors]
    return {"open": open_items, "violations": violations, "runnable": not open_items and not violations}


def ensure_runnable(config: dict) -> None:
    result = check(config)
    if not result["runnable"]:
        reasons = [f"offen: {item}" for item in result["open"]] + result["violations"]
        # §79: konkreter Grund statt nur Anzahlen (die ersten drei in der Meldung, alle in `reasons`)
        raise NotApproved(f"Lauf nicht freigegeben: {len(result['open'])} offene Entscheidungen, "
                          f"{len(result['violations'])} Verstoesse - " + "; ".join(reasons[:3]), reasons)


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=answer_specs.REPO,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _planned_calls(config: dict) -> list[dict]:
    cases = {c["id"]: c for c in corpus.load_cases()}
    specs = {s["id"]: s for s in answer_specs.load_specs()}
    scope = config.get("scope") if isinstance(config.get("scope"), dict) else {}
    repetitions = scope.get("repetitions")
    repetitions = repetitions if _is_int(repetitions, 1) else 1
    splits = scope.get("splits") if isinstance(scope.get("splits"), list) else []
    calls = []
    for sid in scope.get("specs") if isinstance(scope.get("specs"), list) else []:
        spec = specs.get(sid)
        if spec is None or spec["split"] not in splits:
            continue
        case = cases[spec["case"]]
        tasks = {t["id"]: t for t in case["reference"]["tasks"]}
        for tid in spec["tasks"]:
            for rep in range(1, repetitions + 1):
                calls.append({"spec_id": sid, "case": spec["case"], "split": spec["split"], "task_id": tid,
                              "kind": tasks[tid]["kind"], "question": tasks[tid]["question"], "repetition": rep,
                              "case_fingerprint": answer_specs.case_fingerprint(case),
                              "spec_fingerprint": answer_specs.spec_fingerprint(spec), "spec_version": spec["version"]})
    return calls


def _plan_fingerprint(calls: list[dict]) -> str:
    lock_path = answer_specs.LOCK_PATH
    basis = json.dumps({"calls": calls, "commit": _git_commit(),
                        "rubric": answer_specs.file_fingerprint(answer_specs.ROOT / "rubric.json"),
                        "lock": hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.exists() else None},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def plan(config: dict) -> dict:
    """Aufrufplan mit allen Eingabe-Fingerabdruecken - KEINE Ausfuehrung. Ohne gueltige Freigabe ausdruecklich nicht
    ausfuehrbar (`ausfuehrbar: false` mit Gruenden)."""
    calls = _planned_calls(config)
    rubric_fp = answer_specs.file_fingerprint(answer_specs.ROOT / "rubric.json")
    lock_path = answer_specs.LOCK_PATH
    lock_fp = hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.exists() else None
    commit = _git_commit()
    limits = config.get("limits") if isinstance(config.get("limits"), dict) else {}
    price = limits.get("price_per_call_eur_estimate")
    max_calls = limits.get("max_calls")
    scope = config.get("scope") if isinstance(config.get("scope"), dict) else {}
    repetitions = scope.get("repetitions")
    gate = check(config)
    return {
        "art": "aufrufplan - KEINE Ausfuehrung",
        "ausfuehrbar": gate["runnable"],
        "nicht_ausfuehrbar_weil": [] if gate["runnable"] else gate["open"] + gate["violations"],
        "plan_fingerprint": _plan_fingerprint(calls),
        "git_commit": commit,
        "rubric_fingerprint": rubric_fp,
        "lock_sha256": lock_fp,
        "lock_errors": answer_specs.check_lock(),
        "repetitions": repetitions if _is_int(repetitions, 1) else "OFFEN (Plan mit 1 Wiederholung)",
        "calls": calls,
        "anzahl_aufrufe": len(calls),
        "kosten_schaetzung_eur": round(len(calls) * price, 2) if _is_number(price) else "OFFEN",
        "innerhalb_max_calls": (len(calls) <= max_calls) if _is_int(max_calls, 1) else "OFFEN",
        "innerhalb_max_kosten": ((len(calls) * price) <= limits["max_cost_eur"])
        if _is_number(price) and _is_number(limits.get("max_cost_eur")) else "OFFEN",
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
    try:
        config = load_config(Path(args.config))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Konfiguration nicht lesbar ({type(exc).__name__}) - LAUF NICHT FREIGEGEBEN")
        return 2
    if args.command == "check":
        result = check(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("LAUF FREIGEGEBEN" if result["runnable"] else "LAUF NICHT FREIGEGEBEN")
        return 0 if result["runnable"] else 2
    result = plan(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ausfuehrbar"] else 2  # §78: ein nicht ausfuehrbarer Plan ist kein Erfolg


if __name__ == "__main__":
    raise SystemExit(main())
