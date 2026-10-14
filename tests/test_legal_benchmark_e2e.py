"""Realistic Legal Benchmark: E2E-Runner (scripts/legal_bench/e2e.py) als Regressionstest.

Zwei Faelle (Dev LE-D002 mit gescannter Abmahnung und Geburtsdatum im Arbeitsvertrag, Test LE-T001 mit gescannter
Unfallmitteilung) laufen in EINER Datenbank durch Import, Verarbeitung/OCR, Suche, Chat, Schriftsatz, Bearbeitung und
Export. Der Schreib-Provider erfasst den Payload vor der Cloud-Grenze; es wird nichts gesendet. Test-Embedding statt
Produktionsmodell (Retrieval-Qualitaet misst nur der Benchmark-Lauf selbst). Nur synthetische Daten."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from scripts.legal_bench import e2e
from scripts.legal_bench.corpus import load_cases

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_embedding_provider import FakeEmbeddingProvider  # noqa: E402


@pytest.fixture(scope="module")
def outcome(tmp_path_factory: pytest.TempPathFactory) -> dict:
    if e2e._tesseract() is None:
        pytest.skip("Tesseract nicht verfuegbar - Scans waeren nicht auswertbar")
    cases = [c for c in load_cases() if c["id"] in ("LE-D002", "LE-T001")]
    result = e2e.run(cases, tmp_path_factory.mktemp("legal_e2e"), embedding=(FakeEmbeddingProvider(), "fake"))
    result["summary"] = e2e.summarise(result["results"])
    return result


def test_no_critical_privacy_findings(outcome: dict) -> None:
    for split in ("dev", "test"):
        summary = outcome["summary"][split]
        assert summary["kritisch"] == [], summary["kritisch"]
        assert summary["pii_leaks"] == 0 and summary["fremde_akte_im_payload"] == 0


def test_birthdate_from_unattached_contract_does_not_reach_payload(outcome: dict) -> None:
    """Regression §34: Chat mit nur der Abmahnung als Anhang - das Geburtsdatum aus dem Arbeitsvertrag erschien vorher
    ueber einen Fristausschnitt im Klartext im Payload."""
    case = next(r for r in outcome["results"] if r["case"] == "LE-D002")
    q2 = next(t for t in case["tasks"] if t["id"] == "Q2")
    assert q2["status"] == "payload" and q2["pii_leaks"] == []


def test_scans_are_ocred_and_all_paths_complete(outcome: dict) -> None:
    for split in ("dev", "test"):
        summary = outcome["summary"][split]
        found, total = map(int, summary["ocr_werte"].split("/"))
        assert summary["ocr_dokumente"] >= 1 and found == total > 0
        assert summary["dokumentfehler"] == 0 and summary["aufgabenfehler"] == []
        assert summary["aufgaben_ohne_payload"] == []
        assert summary["schriftsatz_ok"].split("/")[0] == summary["schriftsatz_ok"].split("/")[1]
        assert summary["export_mit_bearbeitung"] == summary["schriftsatz_ok"]
        assert summary["fristen_datum_recall"].split("/")[0] == summary["fristen_datum_recall"].split("/")[1]


def test_critical_findings_are_reported_separately_not_averaged() -> None:
    rows = [{"case": "X", "split": "dev", "critical": [{"kind": "klartext_im_payload", "task": "Q1", "values": ["v"]}],
             "documents": {}, "deadlines": {"recall": "0/0", "not_in_reference": [], "known_dates_as_deadline": [],
                                            "unexplained": []},
             "tasks": [{"id": "Q1", "kind": "chat", "payload_chars": 1, "pii_leaks": ["v"], "cross_matter_values": [],
                        "needed_present": [], "needed_missing": []}]}]
    summary = e2e.summarise(rows)["dev"]
    assert summary["kritisch"] == [{"kind": "klartext_im_payload", "task": "Q1", "values": ["v"], "case": "X"}]
    assert summary["pii_leaks"] == 1



def test_leak_check_covers_surname_and_date_variants() -> None:
    """Review §34: "Herr Wiegelmann" oder "14. Juli 1988" im Payload muessen als Leck zaehlen."""
    assert "Wiegelmann" in e2e.leak_variants({"value": "Jonas Wiegelmann", "type": "PERSON"})
    variants = e2e.leak_variants({"value": "14.07.1988", "type": "PERS_DATUM"})
    assert {"14.07.88", "14.7.1988", "14. Juli 1988"} <= set(variants)
    assert e2e._leaked("Wiegelmann", "Sehr geehrter Herr Wiegelmann,")
    assert e2e._leaked("Wiegelmann", "Herrn Wiegelmanns Kuendigung")  # Genitiv zaehlt mit
    assert not e2e._leaked("Wiegelmann", "Wiegelmannstrasse")
    assert e2e._leaked("14. Juli 1988", "am 14.  Juli 1988")


def test_preservation_and_unreferenced_masks_are_measured(outcome: dict) -> None:
    """Fakten-/Chronologie-/Normenerhalt, Widersprueche und Fehlalarm-Kandidaten der Maskierung werden je Split
    ausgewiesen (Werte selbst sind Messergebnis, keine Schwelle - ausser: Normen und Chronologie gehen nicht verloren)."""
    for split in ("dev", "test"):
        summary = outcome["summary"][split]
        for key in ("erhalt_keep", "erhalt_chronologie", "erhalt_normen", "widersprueche_beide_seiten"):
            assert re.fullmatch(r"\d+/\d+", summary[key]), key
        assert isinstance(summary["maskiert_ohne_referenz"], list)
        present, total = map(int, summary["erhalt_chronologie"].split("/"))
        assert present == total


def test_unreferenced_masks_exclude_protected_values() -> None:
    from types import SimpleNamespace

    case = {"parties": [{"name": "Jonas Wiegelmann"}],
            "reference": {"privacy": {"protect": [{"value": "Jonas Wiegelmann", "type": "PERSON"},
                                                  {"value": "14.07.1988", "type": "PERS_DATUM"}]}}}
    mappings = [SimpleNamespace(original_value="Herrn Wiegelmann", category="person", placeholder="[MANDANT_01]"),
                SimpleNamespace(original_value="14.07.1988", category="geburtsdatum", placeholder="[GEBURTSDATUM_01]"),
                SimpleNamespace(original_value="Schichtleiters", category="person", placeholder="[PERSON_01]")]
    assert [m["original"] for m in e2e._unreferenced_masks(case, mappings)] == ["Schichtleiters"]


def test_source_integrity_probe_is_measured(outcome: dict) -> None:
    """Quellenintegritaet (§34.8): erfundene Fundstellen einer simulierten Modellantwort werden gezaehlt - derzeit
    markiert Lexono sie nicht (Befund, Owner-Entscheidung Q1-Q3). Der Test sichert die Messung, nicht ein Soll-Ergebnis."""
    for split in ("dev", "test"):
        assert re.fullmatch(r"\d+/\d+", outcome["summary"][split]["erfundene_fundstellen_markiert"])
    probes = [t["quellenintegritaet"] for r in outcome["results"] for t in r["tasks"] if "quellenintegritaet" in t]
    assert probes and all(p["status"] in ("ok", "blockiert") for p in probes)
