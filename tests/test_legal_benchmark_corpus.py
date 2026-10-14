"""Realistic Legal Benchmark (benchmarks/legal_e2e_v1): Korpus, Validator, Quellenregister, Renderer.
Nur synthetische Daten. Die Negativtests stellen sicher, dass der Validator erfundene Referenzwerte und ungeklaerte
Nutzungsrechte tatsaechlich zurueckweist."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from app.documents.extraction import extract_text
from scripts.legal_bench import corpus
from scripts.legal_bench.render import render_case


def test_versioned_corpus_is_valid() -> None:
    report = corpus.validate_corpus()
    assert report.ok, report.errors
    cases = corpus.load_cases()
    assert {c["split"] for c in cases} == {"dev", "test"}
    assert all(c["synthetic"] for c in cases)


def test_register_entries_are_not_used_without_checked_rights() -> None:
    register = corpus.load_register()
    for entry in register["sources"]:
        if entry["nutzungsrechte"]["status"] != "nutzbar":
            assert entry["verwendet_in"] == [], entry["id"]


@pytest.fixture()
def corpus_copy(tmp_path: Path) -> Path:
    target = tmp_path / "legal_e2e_v1"
    shutil.copytree(corpus.CORPUS_DIR, target)
    return target


def _edit_case(corpus_dir: Path, case_id: str, mutate) -> None:  # noqa: ANN001
    path = next(corpus_dir.glob(f"cases/*/{case_id}.json"))
    case = json.loads(path.read_text(encoding="utf-8"))
    mutate(case)
    path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")


def _errors(corpus_dir: Path) -> list[str]:
    return corpus.validate_corpus(corpus_dir).errors


def test_invented_reference_value_is_rejected(corpus_copy: Path) -> None:
    _edit_case(corpus_copy, "LE-D001", lambda c: c["reference"]["facts"][0]["values"].append("9.999,99 EUR"))
    assert any("9.999,99 EUR" in e for e in _errors(corpus_copy))


def test_invented_deadline_and_citation_are_rejected(corpus_copy: Path) -> None:
    def mutate(case):  # noqa: ANN001
        case["reference"]["deadlines"].append({"date": "01.01.2030", "kind": "erfunden", "doc": "D3"})
        case["reference"]["citations"].append({"text": "BGH, Urt. v. 01.01.2020 - I ZR 1/20", "kind": "entscheidung", "doc": "D5"})

    _edit_case(corpus_copy, "LE-D001", mutate)
    errors = _errors(corpus_copy)
    assert any("01.01.2030" in e for e in errors)
    assert any("ohne belegte Quelle" in e or "I ZR 1/20" in e for e in errors)


def test_person_missing_from_privacy_requirements_is_rejected(corpus_copy: Path) -> None:
    _edit_case(corpus_copy, "LE-D002", lambda c: c["reference"]["privacy"]["protect"].pop(0))
    assert any("Jonas Wiegelmann" in e and "privacy.protect" in e for e in _errors(corpus_copy))


def test_non_synthetic_case_needs_checked_rights(corpus_copy: Path) -> None:
    def mutate(case):  # noqa: ANN001
        case["synthetic"] = False
        case["source_refs"] = ["Q-OPENJUR"]

    _edit_case(corpus_copy, "LE-T001", mutate)
    assert any("ohne geprueftes Nutzungsrecht" in e for e in _errors(corpus_copy))


def test_split_and_id_prefix_must_match(corpus_copy: Path) -> None:
    _edit_case(corpus_copy, "LE-T001", lambda c: c.update(split="dev"))
    errors = _errors(corpus_copy)
    assert any("Ordner" in e for e in errors) and any("Praefix" in e for e in errors)


def test_register_status_usable_requires_date_and_reason(corpus_copy: Path) -> None:
    path = corpus_copy / "source_register.json"
    register = json.loads(path.read_text(encoding="utf-8"))
    register["sources"][0]["nutzungsrechte"]["status"] = "nutzbar"
    path.write_text(json.dumps(register, ensure_ascii=False), encoding="utf-8")
    assert any("ohne Pruefdatum" in e for e in _errors(corpus_copy))


def test_renderer_produces_all_formats_and_image_only_scans(tmp_path: Path) -> None:
    case = next(c for c in corpus.load_cases() if c["id"] == "LE-D001")
    files = render_case(copy.deepcopy(case), tmp_path)
    assert set(files) == {d["id"] for d in case["documents"]}
    by_format = {d["format"]: files[d["id"]] for d in case["documents"]}
    assert extract_text(by_format["scan"]).needs_ocr  # nur Bild, keine Textebene
    for fmt in ("txt", "docx", "pdf"):
        assert "Osterkamp" in (extract_text(by_format[fmt]).text or "")
