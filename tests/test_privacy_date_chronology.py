"""Tests fuer app/privacy/date_chronology.py (nur synthetische Daten)."""

from datetime import date

import pytest

from app.privacy.date_chronology import (
    CHRONOLOGY_MARKER,
    build_chronology_note,
    parse_german_date,
)
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.pseudonymizer import PseudonymMapping


def _m(placeholder: str, value: str) -> PseudonymMapping:
    return PseudonymMapping(placeholder=placeholder, category="datum", original_value=value)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("02.10.2026", date(2026, 10, 2)),
        ("2.10.26", date(2026, 10, 2)),
        ("2. Oktober 2026", date(2026, 10, 2)),
        ("31. Maerz 2026", date(2026, 3, 31)),
        ("31.02.2026", None),
        ("kein Datum", None),
    ],
)
def test_parse_german_date(value: str, expected: date | None) -> None:
    assert parse_german_date(value) == expected


def test_chronology_orders_dates_and_reports_gaps_without_absolute_dates() -> None:
    mappings = [
        _m("[DATUM_01]", "01.09.2023"),
        _m("[DATUM_02]", "11.08.2026"),
        _m("[DATUM_03]", "31.08.2026"),
        _m("[DATUM_04]", "24.08.2026"),
    ]

    note = build_chronology_note(mappings, today=date(2026, 9, 1))

    assert note is not None and note.startswith(CHRONOLOGY_MARKER)
    assert "[DATUM_01] → [DATUM_02] → [DATUM_04] → [DATUM_03]" in note
    assert "[DATUM_02] bis [DATUM_04]: 13 Tage" in note
    assert "[DATUM_04] bis [DATUM_03]: 7 Tage" in note
    assert "in der Vergangenheit: [DATUM_01], [DATUM_02], [DATUM_04], [DATUM_03]" in note
    # Kein absolutes Datum und keine Jahreszahl der Originalwerte in der Zeile
    for original in ("2023", "2026", "11.08", "31.08", "24.08"):
        assert original not in note


def test_chronology_marks_future_and_same_day_and_equal_dates() -> None:
    mappings = [
        _m("[DATUM_01]", "01.10.2026"),
        _m("[DATUM_02]", "1.10.2026"),
        _m("[DATUM_03]", "15.10.2026"),
    ]

    note = build_chronology_note(mappings, today=date(2026, 10, 1))

    assert "[DATUM_01] = [DATUM_02] → [DATUM_03]" in note
    assert "heute: [DATUM_01], [DATUM_02]" in note
    assert "in der Zukunft: [DATUM_03]" in note


def test_chronology_is_none_without_parsable_dates_or_with_too_many() -> None:
    assert build_chronology_note([]) is None
    assert build_chronology_note([_m("[DATUM_01]", "irgendwann")]) is None
    many = [_m(f"[DATUM_{i:02d}]", f"{(i % 28) + 1:02d}.{(i % 12) + 1:02d}.20{i % 90 + 10}") for i in range(30)]
    assert build_chronology_note(many) is None


def test_gateway_payload_contains_chronology_but_no_original_date() -> None:
    """ROOT CAUSE (Real-E2E 08.10.): Claude sah nur [DATUM_NN] und konnte weder
    Reihenfolge noch Abstaende ableiten."""
    gw = ClaudePrivacyGateway()

    result = gw.prepare_request(
        purpose="chat_response",
        sachverhalt=(
            "Maengelanzeige vom 11.08.2026 mit Frist bis zum 31.08.2026. "
            "Antwort vom 24.08.2026, Stellungnahme bis 15.09.2026."
        ),
        anwaltliche_anmerkungen="Welche Frist laeuft zuletzt ab?",
    )

    assert result.allowed is True
    notes = [a for a in result.payload.anonymisierte_argumentationspunkte if a.startswith(CHRONOLOGY_MARKER)]
    assert len(notes) == 1
    payload_text = result.payload.model_dump_json()
    for original in ("11.08.2026", "31.08.2026", "24.08.2026", "15.09.2026"):
        assert original not in payload_text
