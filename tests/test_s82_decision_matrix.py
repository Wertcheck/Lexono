"""§82: Nichtinvasive Konsistenzpruefung von Entscheidungsmatrix, Freigabe-Checkliste, Gates und Endpunktsperre.

Prueft nur Dateien und reine Funktionen (keine Ausfuehrung, kein Netzwerk). Schliesst KEINEN Punkt: der Test schlaegt
fehl, sobald ein Punkt als erledigt/freigegeben markiert, eine Person eingetragen oder eine Kennzahl der Matrix nicht
mehr zu den Spezifikationen passt - dann muss die Matrix bewusst aktualisiert werden."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.legal_bench import answer_driver, answer_run, answer_specs, corpus

ROOT = answer_specs.ROOT
MATRIX = json.loads((ROOT / "entscheidungsmatrix.json").read_text(encoding="utf-8"))
MATRIX_MD = (ROOT / "ENTSCHEIDUNGSMATRIX.md").read_text(encoding="utf-8")
CHECKLIST = (ROOT / "FREIGABE_CHECKLISTE.md").read_text(encoding="utf-8")
EXPECTED_IDS = ["J1", "J2", "J3", "J4", "J5", "J6", "F-1", "C-7", "C-11"]


def test_matrix_covers_exactly_the_open_points_without_closing_any() -> None:
    assert [p["id"] for p in MATRIX["punkte"]] == EXPECTED_IDS
    assert MATRIX["c14"] == "GESPERRT"
    for point in MATRIX["punkte"]:
        assert point["status"] in MATRIX["erlaubte_status"], point["id"]
        assert point["person"] == "NICHT BENANNT", point["id"]  # keine erfundenen Zustaendigen
        assert point["zustaendig"] and point["frage"] and point["abschluss"] and point["fehlend"], point["id"]
        text = json.dumps(point, ensure_ascii=False).lower()
        assert not re.search(r"\b(erledigt|erfuellt|freigegeben am)\b", text), point["id"]


def test_all_evidence_paths_exist() -> None:
    for point in MATRIX["punkte"]:
        for path in point["nachweise"]:
            assert (answer_specs.REPO / path).exists(), (point["id"], path)


def test_key_figures_match_the_specifications() -> None:
    cases = {c["id"]: c for c in corpus.load_cases()}
    for spec in answer_specs.load_specs():
        expected = MATRIX["kennzahlen"][spec["id"]]
        case = cases[spec["case"]]
        actual = {"case": spec["case"], "split": spec["split"], "kriterien": len(spec["criteria"]),
                  "antwortvarianten": sum(len(k.get("accept_any") or []) for k in spec["criteria"]),
                  "gefaehrliche_fehler": len(spec["dangerous_errors"]),
                  "fehlentscheidungen": len(spec.get("known_wrong_decisions") or []),
                  "offene_punkte_referenz": len(case["solution"].get("open_points") or []),
                  "alternativergebnisse": len(spec.get("alternative_results") or [])}
        assert actual == expected, spec["id"]


def test_markdown_overview_matches_json_status() -> None:
    for point in MATRIX["punkte"]:
        row = next((line for line in MATRIX_MD.splitlines() if line.startswith(f"| {point['id']} |")), None)
        assert row is not None, point["id"]
        assert row.rstrip().endswith(f"| {point['status']} |"), (point["id"], row)


def test_matrix_status_is_consistent_with_the_repository_state() -> None:
    state = answer_run.legal_state()
    assert set(state["open_specs"]) == {"LA-D005", "LA-D006", "LA-T004"} and state["rubric_open"] is True
    rubric = json.loads((ROOT / "rubric.json").read_text(encoding="utf-8"))
    assert rubric["aggregation"]["weights"] is None and rubric["aggregation"]["thresholds"] is None
    for spec in answer_specs.load_specs():
        assert spec["alternative_results_status"] == "juristische_pruefung_offen"
    assert {p["id"]: p["status"] for p in MATRIX["punkte"]}["J3"] == "OFFEN"


def test_checklist_keeps_every_decision_open() -> None:
    rows = [line for line in CHECKLIST.splitlines() if re.match(r"\| (C-\d+|F-1)", line)]
    ids = [re.match(r"\| ([^|]+?) ", line).group(1) for line in rows]
    assert ids[:17] == [f"C-{i}" for i in range(1, 18)] and "F-1" in ids[-1]
    for line in rows:
        if line.startswith("| C-9 |"):
            assert "fest **true**" in line  # nicht abschwaechbar, keine Entscheidung
            continue
        assert "**OFFEN" in line, line[:60]
    assert "Kein Punkt ist freigegeben" in CHECKLIST
    assert "W2" in CHECKLIST  # J6 ohne Zuordnung bleibt sichtbar


def test_template_and_gate_stay_closed() -> None:
    template = answer_run.load_config(answer_run.TEMPLATE)
    assert template["approved"] is False and all(v is None for v in template["approvals"].values())
    result = answer_run.check(template)
    assert result["runnable"] is False and result["open"]


def test_endpoint_lock_is_unchanged(monkeypatch) -> None:
    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("LEXONO_GATEWAY_URL", raising=False)
    real = AnthropicClaudeWritingProvider(api_key="sk-ant-qa-synthetisch-kein-echter-schluessel-0000", model="claude-x")
    with pytest.raises(answer_driver.RealCloudDisabled):  # offizieller Endpunkt, nur konstruiert - kein Aufruf
        answer_driver.assert_local_only(real)


@pytest.mark.parametrize("doc", ["AUDIT_S82.md", "ENTSCHEIDUNGSMATRIX.md", "README.md"])
def test_documents_reference_the_matrix_and_keep_c14_locked(doc) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert "ENTSCHEIDUNGSMATRIX.md" in text or doc == "ENTSCHEIDUNGSMATRIX.md"
    assert "GESPERRT" in text or doc == "README.md"


def test_paths_in_matrix_markdown_exist() -> None:
    for path in sorted(set(re.findall(r"`((?:benchmarks|scripts|tests)/[^`*…]+?\.(?:json|md|py))`", MATRIX_MD))):
        assert (answer_specs.REPO / path).exists(), path
    assert Path(answer_specs.REPO / "benchmarks/legal_answers_v1/entscheidungsmatrix.json").exists()
