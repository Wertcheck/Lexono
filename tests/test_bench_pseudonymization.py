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


# --- §61 P4 (17.10.2026): Sperrklinke auf den gemessenen Stand -------------------------------------------------------
# `test_no_plaintext_leak_outside_documented_limits` schliesst Faelle MIT bekannter Grenze vollstaendig aus: ein
# zusaetzliches Leck in einem solchen Fall (z. B. ein Personenname in A06), mehr Teil-Lecks, mehr Fehlalarme oder weniger
# erhaltene juristische Werte blieben unbemerkt. Festgeschrieben ist der reproduzierte Stand (identisch zu §57):
# Verbesserungen sind erlaubt (dann hier nachziehen), Verschlechterungen schlagen fehl.
_ALLOWED_LEAKS = {("P10", "PERSON"), ("A06", "ORT"), ("I07", "KENNUNG")}
_ALLOWED_PARTIAL_LEAKS = {("A06", "KENNUNG"), ("K04", "EMAIL")}


def test_leaks_never_exceed_the_documented_known_limits(rows: dict) -> None:
    leaks = {(cid, item["type"]) for cid, row in rows.items() for item in row["protect"] if item["leak"]}
    partial = {(cid, item["type"]) for cid, row in rows.items() for item in row["protect"] if item["partial_leak"]}
    assert leaks <= _ALLOWED_LEAKS, f"neues Klartext-Leck: {sorted(leaks - _ALLOWED_LEAKS)}"
    assert partial <= _ALLOWED_PARTIAL_LEAKS | _ALLOWED_LEAKS, f"neues Teil-Leck: {sorted(partial - _ALLOWED_PARTIAL_LEAKS)}"
    assert all(rows[cid]["known_limit"] for cid, _ in leaks | partial)  # jedes Leck ist als Grenze dokumentiert


def test_false_positives_and_kept_legal_values_do_not_regress(rows: dict) -> None:
    overall = bench.summarise(list(rows.values()), [])["overall"]
    assert overall["false_positives"] <= 6, overall
    kept, total = (int(x) for x in overall["keep_preserved"].split("/"))
    assert total - kept <= 1, overall  # Stand: 67/68 ("Hauptbahnhof" in X05 wird mitmaskiert)
    assert overall["blocked"] == 0 and overall["unnecessary_blocks"] == 0 and overall["errors"] == 0
