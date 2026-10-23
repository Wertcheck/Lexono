"""Synthetische Regressionsfaelle aus Qualitaetsmeldungen gegen die ECHTE lokale Pipeline (ARCHITECTURE.md §75).

Laedt jede Datei tests/regression_cases/*.json (Format des Exports `lexono-regression-cases/1`) und prueft je Fall:
- must_mask: der Wert verlaesst den lokalen Pfad nicht im Klartext (Pseudonymisierung ODER fail-closed Blockade);
- must_keep: die Anfrage ist erlaubt und der Begriff steht unveraendert in der Payload;
- expected_due_dates: die Fristenerkennung findet genau diese Daten (mindestens).
`known_open: true` markiert einen reproduzierten, noch offenen Fehler als strikt erwartet fehlschlagend - wird er
behoben, schlaegt der Test an und der Fall muss bewusst auf `false` gesetzt werden (keine stille Aenderung).
Kein Netz: `prepare_request` sendet nichts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.deadlines.extractor import PlaceholderDeadlineExtractor

CASES_DIR = Path(__file__).parent / "regression_cases"


def _load_cases() -> list:
    params = []
    for path in sorted(CASES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("format") == "lexono-regression-cases/1", path.name
        for case in data["cases"]:
            marks = [pytest.mark.xfail(strict=True, reason=case.get("source", "offen"))] if case.get("known_open") else []
            params.append(pytest.param(case, id=f"{path.stem}:{case['id']}", marks=marks))
    return params


@pytest.fixture(scope="module")
def gateway():
    from app.privacy.gateway import ClaudePrivacyGateway

    return ClaudePrivacyGateway()  # Produktiv-Default inkl. Presidio-NER


def test_case_files_contain_only_well_formed_cases() -> None:
    files = sorted(CASES_DIR.glob("*.json"))
    assert files, "tests/regression_cases ist leer"
    for path in files:
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            expected = case["expected"]
            assert set(expected) == {"must_mask", "must_keep", "expected_due_dates"}, case["id"]
            for term in expected["must_mask"] + expected["must_keep"]:
                assert term in case["text"], case["id"]


@pytest.mark.parametrize("case", _load_cases())
def test_regression_case(case, gateway) -> None:
    expected = case["expected"]
    text = case["text"]
    if expected["must_mask"] or expected["must_keep"]:
        result = gateway.prepare_request(purpose="formulate_draft", sachverhalt=text, review_mode="mask")
        payload = result.payload.anonymisierter_sachverhalt if result.allowed else ""
        for term in expected["must_mask"]:
            assert term not in payload, f"{case['id']}: Wert verlaesst den lokalen Pfad im Klartext"
        for term in expected["must_keep"]:
            assert result.allowed, f"{case['id']}: Anfrage blockiert statt Begriff zu erhalten"
            assert term in payload, f"{case['id']}: Begriff wurde veraendert oder pseudonymisiert"
    if expected["expected_due_dates"]:
        found = {str(c.due_date) for c in PlaceholderDeadlineExtractor().extract(text) if c.due_date}
        assert set(expected["expected_due_dates"]) <= found, f"{case['id']}: Frist nicht erkannt"
