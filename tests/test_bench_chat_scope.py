"""Benchmark der Chat-Dokumentauswahl (§30): Invarianten des Offline-Laufs, Bewertungsregeln fuer den Live-Lauf und die
Sperre des Live-Modus ohne Freigabe. Nur synthetische Daten (benchmarks/chat_scope_v1/cases.json)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bench_chat_scope as bench  # noqa: E402


@pytest.fixture(scope="module")
def spec() -> dict:
    return bench.load_cases()


def _case(spec: dict, case_id: str) -> dict:
    return next(c for c in spec["cases"] if c["id"] == case_id)


def test_case_ids_are_stable_and_unique(spec: dict) -> None:
    ids = [c["id"] for c in spec["cases"]]
    assert ids == [f"C{n:02d}" for n in range(1, 13)] and spec["version"] == "v1.1"


@pytest.mark.parametrize("case_id", ["C02", "C07"])
def test_rule_b_omits_only_expected_facts_and_always_shows_the_note(spec: dict, case_id: str) -> None:
    case = _case(spec, case_id)
    a = bench.run_case(spec, case, "A")
    b = bench.run_case(spec, case, "B")
    assert not a["needed_facts_missing"] and not a["error"] and not b["error"]
    assert b["needed_facts_missing"] == case["omission_expected_in_b"] and not b["unexpected_missing"]
    assert b["note_present"] and not b["omission_without_note"]
    assert b["payload_chars"] < a["payload_chars"]
    assert not a["sensitive_plaintext"] and not b["sensitive_plaintext"]


@pytest.mark.parametrize("case_id", ["C08", "C09"])
def test_follow_ups_without_attachment_use_the_whole_matter(spec: dict, case_id: str) -> None:
    b = bench.run_case(spec, _case(spec, case_id), "B")
    assert not b["needed_facts_missing"] and b["note_present"] is False and not b["error"]


def test_duplicate_attachment_is_not_counted_and_deadline_from_other_document_stays(spec: dict) -> None:
    assert bench.run_case(spec, _case(spec, "C04"), "B")["note_count_as_expected"] is True
    assert "30.10.2026" in bench.run_case(spec, _case(spec, "C05"), "B")["needed_facts_present"]


def test_answer_scoring_flags_unsupported_values_and_missing_facts(spec: dict) -> None:
    case = _case(spec, "C07")
    invented = bench.score_answer(spec, case, "Die Kaution betrug 500,00 EUR am 01.01.2019.")
    assert invented["unsupported_values"] == ["2019-01-01", "500.00"] and invented["needed_facts_not_in_answer"] == ["350,00"]
    honest = bench.score_answer(spec, case, "Das ist aus dem angehängten Dokument nicht ersichtlich.")
    assert honest["unsupported_values"] == [] and honest["status"].startswith("VORLAEUFIG")
    correct = bench.score_answer(spec, case, "Die Kaution betrug 350,00 EUR (Schreiben vom 03.05.2019).")
    assert correct["needed_facts_not_in_answer"] == [] and correct["unsupported_values"] == []


@pytest.mark.parametrize("flags", [["--live"], ["--live", "--confirm-synthetic-only"], ["--live", "--confirm-spend-limit"]])
def test_live_mode_refuses_without_both_confirmations(monkeypatch, flags) -> None:
    import app.ai_providers.factory as factory

    def _must_not_be_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("kein Schreib-Provider ohne Freigabe")

    monkeypatch.setattr(factory, "build_writing_provider", _must_not_be_called)
    monkeypatch.setattr(sys, "argv", ["bench_chat_scope.py", *flags])
    assert bench.main() == 2


def test_name_in_question_is_not_sent_in_plaintext(spec: dict) -> None:
    b = bench.run_case(spec, _case(spec, "C11"), "B")
    assert not b["error"] and b["sensitive_plaintext"] == []


@pytest.mark.parametrize(
    "text, expected",
    [("4.250 EUR", {"4250.00"}), ("4250,00", {"4250.00"}), ("15. August 2026", {"2026-08-15"}),
     ("15.8.2026", {"2026-08-15"}), ("14.250,00 EUR", {"14250.00"}), ("Nr. 2026-117, § 558 BGB", set())],
)
def test_value_normalisation_for_answer_scoring(text: str, expected: set) -> None:
    assert bench.canonical_values(text) == expected


def test_fact_matching_respects_number_boundaries_and_note_parsing() -> None:
    assert not bench._fact_in("4.250,00", "14.250,00") and bench._fact_in("4.250,00", "Betrag 4.250,00 EUR")
    assert bench._note_count("der Text eines weiteren Dokuments dieser Akte") == 1
    assert bench._note_count("der Text von 4 weiteren Dokumenten") == 4 and bench._note_count("kein Hinweis") is None


def test_answer_scoring_recognises_other_formats(spec: dict) -> None:
    case = _case(spec, "C01")
    scored = bench.score_answer(spec, case, "Fällig sind 4.250 EUR am 15. August 2026; zuzüglich 14.250,00 EUR.")
    assert scored["needed_facts_not_in_answer"] == [] and scored["unsupported_values"] == ["14250.00"]


def test_knowledge_source_reaches_both_variants(spec: dict) -> None:
    case = _case(spec, "C12")
    for variant in ("A", "B"):
        assert "30 Prozent" in bench.run_case(spec, case, variant)["needed_facts_present"]


@pytest.mark.parametrize("case_id", ["C02", "C07"])
def test_local_preanalysis_fallback_brings_back_no_excluded_document(spec: dict, case_id: str) -> None:
    """Fallback-Pfad: die lokale Vorabanalyse sieht nur den bereits eingeschraenkten Payload."""
    case = _case(spec, case_id)
    plain = bench.run_case(spec, case, "B")
    local = bench.run_case(spec, case, "B_lokal")
    assert local["local_llm_called"] is True and local["local_summary_in_payload"] is True and not local["error"]
    assert local["needed_facts_missing"] == plain["needed_facts_missing"] == case["omission_expected_in_b"]
    assert set(local["unneeded_facts_present"]) == set(plain["unneeded_facts_present"])
    assert not local["sensitive_plaintext"]
