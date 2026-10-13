"""Adversarialer Pseudonymisierungs-Benchmark (§31): Korpus-Invarianten, Messregeln und die wichtigsten Befunde als
Regressionstest. Nur synthetische Daten (benchmarks/pseudonymization_v1/cases.json)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bench_pseudonymization as bench  # noqa: E402
from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402


@pytest.fixture(scope="module")
def spec() -> dict:
    return bench.load_cases()


@pytest.fixture(scope="module")
def rows(spec: dict) -> dict:
    gateway = ClaudePrivacyGateway()
    return {case["id"]: bench.run_field_case(gateway, case) for case in spec["cases"]}


def test_case_ids_are_unique_and_every_case_is_annotated(spec: dict) -> None:
    ids = [c["id"] for c in spec["cases"]]
    assert len(ids) == len(set(ids)) and spec["version"] == "v1"
    for case in spec["cases"]:
        assert case.get("protect") or case.get("keep") or case.get("neutral"), case["id"]


def test_no_plaintext_leak_outside_documented_limits(spec: dict, rows: dict) -> None:
    leaking = [cid for cid, row in rows.items() if not row["known_limit"] and any(i["leak"] for i in row["protect"])]
    assert leaking == []


def test_style_field_is_protected(rows: dict) -> None:
    """Befund W05 (Stilfeld lief an der Pseudonymisierung vorbei) - behoben."""
    assert not any(item["leak"] for item in rows["W05"]["protect"])


def test_no_unnecessary_blocks_and_no_errors(rows: dict) -> None:
    assert not [cid for cid, r in rows.items() if r["unnecessary_block"] or r["error"]]


@pytest.mark.parametrize("case_id", ["F01", "F02", "F03", "B01", "D01"])
def test_legal_facts_are_preserved(rows: dict, case_id: str) -> None:
    assert rows[case_id]["keep_missing"] == []


def test_matching_rules() -> None:
    assert bench._in_text("Karl Brenner", bench.norm("herr karl brenner zahlt"))
    assert not bench._in_text("Brenner", bench.norm("brennerei"))
    assert bench._overlaps("Karl Brenner", "Brenner") and not bench._overlaps("Karl", "Brenner")
    assert bench.norm("Karl­Brenner") == "karlbrenner"


def test_e2e_paths_protect_and_fail_closed() -> None:
    results = {r["id"]: r for r in bench.run_e2e()}
    assert all(r["as_expected"] for r in results.values())
    assert sum(len(r["leaks"]) for r in results.values()) == 0
    assert results["E05"]["cloud_calls"] == 0 and results["E05b"]["cloud_calls"] == 0  # NER-Ausfall: kein Cloud-Aufruf
    assert results["E04"]["allowed"] is False  # Klarname aus lokaler Vorabanalyse wird nicht gesendet
    assert results["E03"]["quellenverweise"] >= 1 and results["E03"]["keep_missing"] == []  # Wissen gesendet, geschuetzt
