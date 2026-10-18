"""§77: Audit und Laufvorbereitung des Antwortqualitaets-Benchmarks `benchmarks/legal_answers_v1`.

Keine Modellantworten, keine Cloud-Aufrufe. Die Antworttexte in diesen Tests sind HANDGESCHRIEBENE Testeingaben fuer
den Code, keine Modellausgaben und keine Ergebnisse eines Laufs. Geprueft werden: Wortgrenzen der Vorpruefung,
Abdeckungspflicht der Spezifikationen, Lock des Test-Splits, Verweigerung eines nicht freigegebenen Laufs, Aufrufplan,
Pruefbogen mit Referenzbezug und die Zusammenfassung ohne Ausgleich kritischer Fehler."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.legal_bench import answer_run, answer_specs, corpus


def _env():
    rubric = answer_specs.load_rubric()
    register = corpus.validate_register(corpus.load_register(), corpus.ValidationReport())
    cases = {c["id"]: c for c in corpus.load_cases()}
    specs = {s["id"]: s for s in answer_specs.load_specs()}
    return rubric, register, cases, specs


def _errors(spec: dict) -> list[str]:
    rubric, register, cases, _ = _env()
    report = answer_specs.SpecReport()
    answer_specs.validate_spec(spec, cases, register, rubric, report)
    return report.errors


# --- Vorpruefung: ganze Woerter -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term, text, expected",
    [
        ("zu", "Zum Vorkaufsrecht kann ich nichts sagen.", False),
        ("§ 4 KSchG", "Nach § 41 KSchG", False),
        ("§ 4 KSchG", "Klage nach § 4 KSchG", True),
        ("Bescheid", "Der Bescheid fehlt.", True),
        ("Bescheid", "Ein Bescheidungsurteil", False),
        ("614,25", "Differenz 614,25 EUR", True),
    ],
)
def test_terms_are_matched_as_whole_words(term: str, text: str, expected: bool) -> None:
    assert answer_specs._term_found(term, answer_specs._norm(text)) is expected


def test_non_answer_no_longer_produces_a_result_hint() -> None:
    _, _, cases, specs = _env()
    result = answer_specs.precheck(specs["LA-D005"], cases["LE-D005"], "Zum Vorkaufsrecht kann ich ohne Unterlagen nichts sagen.")
    assert result["kriterien"]["K1"]["status"] == "nicht_gefunden"


def test_negated_jurisdiction_raises_suspicion() -> None:
    _, _, cases, specs = _env()
    result = answer_specs.precheck(specs["LA-T004"], cases["LE-T004"], "Die Gerichtsbarkeit ist nicht gegeben.")
    assert result["gefaehrliche_fehler"]["G1"] == "verdacht"


# --- Abdeckung und Struktur der Spezifikationen ---------------------------------------------------------------------------


def test_every_decisive_fact_and_open_point_of_the_reference_is_covered() -> None:
    _, _, cases, specs = _env()
    for spec in specs.values():
        solution = cases[spec["case"]]["solution"]
        facts = {c["fact"] for c in spec["criteria"] if "fact" in c}
        points = [c["open_point"] for c in spec["criteria"] if "open_point" in c]
        assert set(solution["decisive_facts"]) <= facts, spec["id"]
        assert sorted(points) == sorted(p["id"] for p in solution["open_points"]), spec["id"]
        assert spec["alternative_results_status"] == "juristische_pruefung_offen"  # nicht vom Werkzeug erfunden


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s.update(criteria=[c for c in s["criteria"] if c.get("fact") != "F3"]), "F3"),
        (lambda s: s.update(criteria=[c for c in s["criteria"] if c.get("open_point") != "O2"]), "O2"),
        (lambda s: s["criteria"][0]["accept_any"].append(["zu"]), "zu kurz"),
        (lambda s: s["criteria"][6]["accept_any"].append(["Zustellnachweis"]), "Vermischen"),
        (lambda s: s.pop("history"), "Aenderungshistorie"),
        (lambda s: s.update(version=3), "Aenderungshistorie"),
        (lambda s: s.pop("alternative_results_status"), "alternative_results"),
        (lambda s: s.update(alternative_results_status="festgelegt"), "leer"),
        (lambda s: s.update(dangerous_errors=[s["dangerous_errors"][1]]), "Fehlergebnis der Referenz"),
        (lambda s: s.update(not_assessed=[{"ref": "F3"}], criteria=[c for c in s["criteria"] if c.get("fact") != "F3"]),
         "ohne Begruendung"),
    ],
)
def test_validator_rejects_incomplete_or_ambiguous_specs(mutate, expected) -> None:
    _, _, _, specs = _env()
    spec = copy.deepcopy(specs["LA-D005"])
    mutate(spec)
    assert any(expected in e for e in _errors(spec)), _errors(spec)


def test_justified_exclusion_is_accepted() -> None:
    _, _, _, specs = _env()
    spec = copy.deepcopy(specs["LA-D005"])
    spec["criteria"] = [c for c in spec["criteria"] if c.get("fact") != "F3"]
    spec["not_assessed"] = [{"ref": "F3", "reason": "Testzweck: begruendeter Ausschluss"}]
    assert not any("F3" in e for e in _errors(spec))


def test_rubric_forbids_compensation_and_keeps_thresholds_open() -> None:
    rubric = answer_specs.load_rubric()
    agg = rubric["aggregation"]
    assert agg["non_compensation"] is True and agg["critical_rule"] == "FAIL_KRITISCH"
    assert agg["thresholds"] is None and agg["weights"] is None and "OFFEN" in agg["thresholds_status"]


# --- Lock des Test-Splits -------------------------------------------------------------------------------------------------


def test_lock_matches_and_covers_the_test_split() -> None:
    assert answer_specs.check_lock() == []
    lock = json.loads(answer_specs.LOCK_PATH.read_text(encoding="utf-8"))
    paths = set(lock["entries"])
    assert "benchmarks/legal_answers_v1/specs/test/LA-T004.json" in paths
    assert "benchmarks/legal_answers_v1/rubric.json" in paths
    assert {f"benchmarks/legal_e2e_v1/cases/test/LE-T00{i}.json" for i in range(1, 5)} <= paths
    assert not any("/dev/" in p for p in paths)


def test_lock_detects_a_changed_test_reference(tmp_path: Path) -> None:
    root = tmp_path / "legal_answers_v1"
    shutil.copytree(answer_specs.ROOT, root)
    spec_path = root / "specs" / "test" / "LA-T004.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["criteria"][6]["accept_any"].append(["06.02.2026"])  # "Anpassung an ein Ergebnis"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    errors = answer_specs.check_lock(root, root / "LOCK.json")
    assert any("LA-T004.json wurde geaendert" in e for e in errors)


def test_relock_requires_a_reason(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        answer_specs.write_lock("", lock_path=tmp_path / "LOCK.json")


# --- Laufvorbereitung: keine Ausfuehrung ohne Freigabe -------------------------------------------------------------------


def _template() -> dict:
    return answer_run.load_config(answer_run.TEMPLATE)


def test_template_is_not_runnable_and_lists_open_decisions() -> None:
    result = answer_run.check(_template())
    assert result["runnable"] is False and result["violations"] == []
    for item in ("model.model_id", "model.model_version", "scope.repetitions", "limits.max_calls", "limits.max_cost_eur",
                 "approvals.juristische_freigabe_spezifikationen", "storage.results_dir"):
        assert item in result["open"], item
    with pytest.raises(answer_run.NotApproved):
        answer_run.ensure_runnable(_template())


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda c: c["privacy"].update(leak_check_required=False), "privacy.leak_check_required"),
        (lambda c: c["privacy"].update(real_client_data=True), "privacy.real_client_data"),
        (lambda c: c["limits"].update(abort_on_first_privacy_finding=False), "abort_on_first_privacy_finding"),
        (lambda c: c["pipeline"].update(path="anthropic_direkt"), "pipeline.path"),
    ],
)
def test_weakened_privacy_settings_are_violations(mutate, expected) -> None:
    config = _template()
    mutate(config)
    assert any(expected in v for v in answer_run.check(config)["violations"])


def test_fully_filled_config_still_needs_valid_specs_and_explicit_approval() -> None:
    config = _template()
    for path, _value in list(answer_run._walk("", config)):
        node = config
        *parents, leaf = path.split(".")
        for p in parents:
            node = node[p]
        if node[leaf] is None:
            node[leaf] = "TESTWERT"
    assert "approved (ausdrueckliche Freigabe fehlt)" in answer_run.check(config)["open"]
    config["approved"] = True
    # §78: Platzhalterwerte sind KEINE Freigabe mehr (vorher wurde "TESTWERT" ueberall angenommen) - die Positivkontrolle
    # mit gueltigen Werten steht in tests/test_s78_benchmark_gate.py
    result = answer_run.check(config)
    assert result["runnable"] is False
    assert any(v.startswith("approvals.") for v in result["violations"])
    assert any(v.startswith("model.temperature") for v in result["violations"])
    assert not hasattr(answer_run, "execute") and not hasattr(answer_run, "run")


def test_plan_lists_calls_with_fingerprints_without_executing() -> None:
    plan = answer_run.plan(_template())
    assert plan["art"].endswith("KEINE Ausfuehrung") and plan["lock_errors"] == []
    assert {(c["spec_id"], c["task_id"]) for c in plan["calls"]} == {
        ("LA-D005", "Q1"), ("LA-D005", "Q2"), ("LA-D006", "Q1"), ("LA-D006", "Q2"), ("LA-T004", "Q1"), ("LA-T004", "Q2")}
    assert plan["anzahl_aufrufe"] == 6 and plan["kosten_schaetzung_eur"] == "OFFEN"
    assert all(len(c["case_fingerprint"]) == 64 and len(c["spec_fingerprint"]) == 64 for c in plan["calls"])


def test_dev_only_scope_excludes_the_test_split() -> None:
    config = _template()
    config["scope"]["splits"] = ["dev"]
    assert {c["split"] for c in answer_run.plan(config)["calls"]} == {"dev"}


# --- Pruefbogen und Zusammenfassung (handgeschriebene Testeingabe, KEINE Modellantwort) ----------------------------------

TEST_INPUT = "Handgeschriebene Testeingabe: Dem Mieter steht das Vorkaufsrecht zu (§ 577 BGB); vorsorglich bis 28.04.2026."


def _record(text: str = TEST_INPUT) -> dict:
    return {"answer_id": "TEST-0001", "spec_id": "LA-D005", "task_id": "Q1", "repetition": 1, "model_id": "TEST",
            "model_version": "TEST", "prompt_version": "TEST", "lexono_commit": "TEST", "payload_sha256": "0" * 64,
            "answer_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "created_at": "2026-10-18T00:00:00Z",
            "answer_text": text}


def _sheet() -> dict:
    _, _, cases, specs = _env()
    return answer_run.review_sheet(specs["LA-D005"], cases["LE-D005"], _record())


def test_answer_record_integrity_is_checked() -> None:
    record = _record()
    assert answer_run.validate_answer_record(record) == []
    record["answer_text"] += " (nachtraeglich geaendert)"
    assert any("answer_sha256" in e for e in answer_run.validate_answer_record(record))
    with pytest.raises(ValueError):
        _env_spec = _env()
        answer_run.review_sheet(_env_spec[3]["LA-D005"], _env_spec[2]["LE-D005"], record)


def test_review_sheet_is_blind_and_links_every_criterion_to_its_reference() -> None:
    sheet = _sheet()
    text = json.dumps(sheet, ensure_ascii=False)
    assert "model_id" not in text and "TEST-0001" in text  # blind: keine Modellangaben
    rows = {r["id"]: r for r in sheet["kriterien"]}
    assert rows["K8"]["referenz"]["fakt"]["id"] == "F3" and rows["K8"]["referenz"]["fakt"]["dokumente"]
    assert rows["K6"]["referenz"]["frist"]["datum"] == "28.04.2026" and rows["K6"]["referenz"]["frist"]["dokument"]
    assert rows["K1"]["referenz"]["quelle"]["aktenzeichen"] == "VIII ZR 18/24"
    assert rows["K10"]["referenz"]["offener_punkt"]["id"] == "O2"
    assert all(r["bewertung"]["note"] is None for r in sheet["kriterien"])  # nichts vorbewertet


def _fill(sheet: dict, note=2, confirm=False) -> dict:
    for r in sheet["kriterien"]:
        r["bewertung"].update(note=note, begruendung="Testbegruendung", fundstelle_in_antwort="Satz 1")
    for e in sheet["gefaehrliche_fehler"]:
        e.update(bestaetigt=confirm, begruendung="Testbegruendung")
    sheet["unbelegte_behauptungen"].update(note=note, begruendung="Testbegruendung")
    return sheet


def test_confirmed_critical_error_cannot_be_compensated() -> None:
    rubric = answer_specs.load_rubric()
    sheet = _fill(_sheet(), note=2)
    sheet["gefaehrliche_fehler"][0]["bestaetigt"] = True
    result = answer_run.aggregate(sheet, rubric)
    assert result["status"] == "FAIL_KRITISCH" and result["kritische_fehler"] == ["G1"]


def test_without_thresholds_there_is_never_a_pass() -> None:
    rubric = answer_specs.load_rubric()
    result = answer_run.aggregate(_fill(_sheet(), note=2), rubric)
    assert result["status"] == "OFFEN" and "kein PASS" in result["hinweis"]


def test_incomplete_and_unjustified_na_are_reported() -> None:
    rubric = answer_specs.load_rubric()
    assert answer_run.aggregate(_sheet(), rubric)["status"] == "UNVOLLSTAENDIG"
    sheet = _fill(_sheet(), note=2)
    sheet["kriterien"][0]["bewertung"].update(note="n/a", begruendung=None)
    assert answer_run.aggregate(sheet, rubric)["status"] == "NICHT_BEWERTBAR"


def test_thresholds_once_set_give_pass_or_fail_with_an_error_report() -> None:
    rubric = copy.deepcopy(answer_specs.load_rubric())
    rubric["aggregation"]["thresholds"] = {"ergebnis": 2, "fristen": 2}  # Testwerte, KEINE Festlegung
    sheet = _fill(_sheet(), note=2)
    sheet["kriterien"][5]["bewertung"]["note"] = 1  # K6 Frist nur teilweise
    result = answer_run.aggregate(sheet, rubric)
    assert result["status"] == "FAIL" and result["unter_schwelle"] == ["fristen"]
    assert result["fehlerbericht"][0]["kriterium"] == "K6" and result["fehlerbericht"][0]["referenz"]["frist"]["datum"]
