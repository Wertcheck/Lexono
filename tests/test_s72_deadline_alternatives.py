"""§72 P1: "Fristende <Datum>" ohne Doppelpunkt und mehrere Fristenden in einem Satz.

Befund (legal_e2e_v1 LE-T004, entwickelt am Dev-Fall LE-D007): "vorsorglich Fristende 05.02.2026, spätestens 06.02.2026"
lieferte nur das spaetere Datum - das fruehere, vorsorgliche Fristende ging verloren. Jetzt: beide Daten als getrennte
Vorschlaege, jeder mit Stellung (frueheste/spaeteste) und Bedeutung laut Text, ausdruecklich ohne Entscheidung."""

from __future__ import annotations

from datetime import date

import pytest

from app.deadlines.extractor import PlaceholderDeadlineExtractor


def _deadlines(text: str) -> dict[date, str]:
    return {d.due_date: d.reasoning for d in PlaceholderDeadlineExtractor().extract(text) if d.due_date}


@pytest.mark.parametrize(
    "sentence",
    [
        "Zugang streitig; vorsorglich Fristende 05.03.2026, spätestens 06.03.2026.",
        "Zugang streitig; vorsorglich Fristende: 05.03.2026, spätestens 06.03.2026.",
        "Zugang streitig; Fristende vorsorglich 05.03.2026, spätestens 06.03.2026.",
        "Zugang streitig; vorsorglich Klagefristende 05.03.2026, spätestens am 06.03.2026.",
    ],
)
def test_both_deadline_ends_are_proposed_separately_with_meaning(sentence: str) -> None:
    found = _deadlines(sentence)
    assert set(found) == {date(2026, 3, 5), date(2026, 3, 6)}, found
    early, late = found[date(2026, 3, 5)], found[date(2026, 3, 6)]
    assert "das früheste" in early and "06.03.2026" in early
    assert "das späteste (laut Text: spätestens)" in late and "05.03.2026" in late
    for reasoning in (early, late):
        # Unsicherheit sichtbar, keine Festlegung, keine Zusammenfuehrung
        assert "NICHT entschieden" in reasoning and "nicht zusammengeführt" in reasoning
        assert "NICHT als verbindlich bestätigt" in reasoning


def test_precautionary_meaning_is_named_when_it_precedes_the_cue() -> None:
    found = _deadlines("Zugang streitig; vorsorglich Fristende 05.03.2026, spätestens 06.03.2026.")
    assert "(laut Text: vorsorglich)" in found[date(2026, 3, 5)]


def test_the_earlier_date_is_not_replaced_when_the_order_is_reversed() -> None:
    found = _deadlines("Spätestens 06.03.2026, vorsorglich jedoch Fristende 05.03.2026 beachten.")
    assert set(found) == {date(2026, 3, 5), date(2026, 3, 6)}
    assert "das früheste" in found[date(2026, 3, 5)] and "das späteste" in found[date(2026, 3, 6)]


@pytest.mark.parametrize(
    "sentence, expected",
    [
        ("Das Fristende 20.03.2026 gilt für die Stellungnahme.", date(2026, 3, 20)),
        ("Für Ihre Stellungnahme gilt: Fristende: 20.03.2026.", date(2026, 3, 20)),
        ("Fristablauf 20.03.2026.", date(2026, 3, 20)),
    ],
)
def test_single_deadline_end_with_and_without_colon(sentence: str, expected: date) -> None:
    found = _deadlines(sentence)
    assert set(found) == {expected}
    assert "mehrere mögliche Fristenden" not in found[expected]  # ein Datum: kein Alternativvermerk


@pytest.mark.parametrize(
    "sentence",
    [
        "Fristende offen. Vermerk vom 17.02.2026.",  # Fristende in anderem Satz
        "Die Klage wurde am 05.03.2026 eingereicht.",  # Ereignis, keine Frist
        "Fristendet 05.03.2026",  # kein Wort "Fristende"
        "Ein Fristende wird nicht genannt, Gutachten 05.03.2026.",  # "Fristende" nicht unmittelbar vor dem Datum
    ],
)
def test_no_deadline_without_an_explicit_deadline_end(sentence: str) -> None:
    assert _deadlines(sentence) == {}


def test_two_dates_in_different_sentences_are_not_marked_as_alternatives() -> None:
    found = _deadlines("Fristende 05.03.2026 für die Klage.\n\nFür die Stellungnahme gilt: Fristende: 20.03.2026.")
    assert set(found) == {date(2026, 3, 5), date(2026, 3, 20)}
    assert all("mehrere mögliche Fristenden" not in r for r in found.values())


def test_start_event_in_the_same_sentence_is_no_alternative_deadline() -> None:
    proposals = PlaceholderDeadlineExtractor().extract(
        "Zugang streitig (12.02.2026 laut Arbeitgeberin, 13.02.2026 laut Mandantin); vorsorglich Fristende 05.03.2026, "
        "spätestens 06.03.2026.")
    start = [p for p in proposals if p.due_date is None]
    assert start and all("mehrere mögliche Fristenden" not in p.reasoning for p in start)
    ends = {p.due_date: p.reasoning for p in proposals if p.due_date}
    assert set(ends) == {date(2026, 3, 5), date(2026, 3, 6)}
    assert "12.02.2026" not in ends[date(2026, 3, 5)].split("mehrere mögliche Fristenden")[1]
