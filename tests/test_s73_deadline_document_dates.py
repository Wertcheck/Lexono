"""§73: Dokument-/Vermerkdaten, Ereignisdaten und offene Angaben sind kein Fristende.

Befunde (legal_e2e_v1 LE-D005 "Aktenvermerk vom 10.03.2026 - Az. ...\\nAusübungsfrist: ...", LE-T004 "Vermerk vom
21.01.2026 ...\\nKlagefrist: ..."), entwickelt am Dev-Fall LE-D008. Ursachen: (1) Umbruch vor einer Feldzeile galt als weich,
die Kopfzeile verschmolz mit "Klagefrist:"; (2) "Fristwort danach" griff auch bei "vom <Datum>"; (3) Zustellung/Zugang
unmittelbar am Datum wurde erst NACH den Fristwort-Regeln geprueft; (4) "das Fristende ist offen" diente als Hinweis.
Jeder Fall hat Gegenproben, damit echte Fristen nicht verloren gehen."""

from __future__ import annotations

from datetime import date

import pytest

from app.deadlines.extractor import PlaceholderDeadlineExtractor


def _proposals(text: str):
    return PlaceholderDeadlineExtractor().extract(text)


def _due(text: str) -> set[date]:
    return {p.due_date for p in _proposals(text) if p.due_date}


# --- Dokument-/Vermerkdaten ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Aktenvermerk vom 10.03.2026 - Az. 26/0733-VK\nAusübungsfrist: Das Vorkaufsrecht kann binnen zwei Monaten ausgeübt werden.",
        "Vermerk vom 21.01.2026 - Az. 26/0158-AR\nKlagefrist: Die Kündigungsschutzklage ist binnen drei Wochen zu erheben.",
        "Aktenvermerk 23.01.2026 - Az. 26/0091-HV\nBerufungsfrist: siehe unten",
        "Von: m@beispiel.example\nDatum: 26.01.2026\nBetreff: Frist Berufung",
        "Mit Schreiben vom 05.03.2026 wurde die Frist verlängert.",
        "Wir verweisen auf unser Schreiben vom 12.12.2025.",
    ],
)
def test_document_and_note_dates_are_no_deadline_end(text: str) -> None:
    assert _due(text) == set(), [p.reasoning for p in _proposals(text)]


@pytest.mark.parametrize(
    "text, expected",
    [
        # Gegenproben: echtes Fristende in der Feldzeile bzw. direkt am Fristwort bleibt erkannt
        ("Aktenvermerk vom 10.03.2026 - Az. 26/0733-VK\nAusübungsfrist: bis 24.04.2026", {date(2026, 4, 24)}),
        ("Vermerk vom 21.01.2026\nKlagefrist: Fristende 05.02.2026", {date(2026, 2, 5)}),
        ("Am 15.05.2026 läuft die Frist ab.", {date(2026, 5, 15)}),
        ("Die Einspruchsfrist läuft am 20.07.2026 ab.", {date(2026, 7, 20)}),
        ("Wiedervorlage: 03.03.2026", {date(2026, 3, 3)}),
        ("Datum: 26.01.2026\nBetreff: Frist bis 09.02.2026", {date(2026, 2, 9)}),
    ],
)
def test_real_deadline_ends_next_to_document_dates_are_kept(text: str, expected: set[date]) -> None:
    assert _due(text) == expected, [p.reasoning for p in _proposals(text)]


def test_soft_pdf_line_wrap_inside_a_sentence_still_joins() -> None:
    """Regression P2.3 (§42): weicher Umbruch mitten im Satz - der Hinweis vor dem Datum darf nicht verloren gehen."""
    assert _due("Die Zahlung erfolgt letztmalig bis zum\n31.07.2026 auf das bekannte Konto.") == {date(2026, 7, 31)}


# --- Ereignis am Datum: Fristbeginn, kein Fristende -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Berufungsfrist: Das Urteil wurde laut Empfangsbekenntnis am 22.01.2026 zugestellt.",
        "Die Gegenseite behauptet eine Zustellung schon am 21.01.2026; das Fristende ist danach offen.",
        "Das Fristende ist offen, die Kündigung ging am 12.02.2026 zu.",
        "Klagefrist: Zugang der Kündigung am 15.01.2026.",
    ],
)
def test_event_dates_become_a_start_without_due_date(text: str) -> None:
    proposals = _proposals(text)
    assert all(p.due_date is None for p in proposals), [p.reasoning for p in proposals]
    starts = [p for p in proposals if "Art: Fristbeginn" in p.reasoning]
    assert starts and all("Das Fristende wurde nicht berechnet" in p.reasoning for p in starts)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Die Frist endet am 15.05.2026 nach Zugang der Mitteilung.", {date(2026, 5, 15)}),
        ("Eingang am 15.05.2026 erforderlich, Fristablauf 15.05.2026.", {date(2026, 5, 15)}),
        ("Zustellung spätestens bis 30.06.2026 veranlassen.", {date(2026, 6, 30)}),
        ("Zugang streitig; vorsorglich Fristende 05.02.2026, spätestens 06.02.2026.", {date(2026, 2, 5), date(2026, 2, 6)}),
    ],
)
def test_explicit_deadline_ends_near_event_words_are_kept(text: str, expected: set[date]) -> None:
    assert _due(text) == expected, [p.reasoning for p in _proposals(text)]


# --- offene Angaben ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Die Frist ist noch unklar, Besprechungsnotiz am 04.03.2026 angelegt.",
        "Das Fristende ist offen, nächste Rücksprache am 11.03.2026 geplant.",
    ],
)
def test_undetermined_deadline_is_not_attached_to_another_date(text: str) -> None:
    assert all("Art: Frist -" not in p.reasoning for p in _proposals(text))


def test_a_determined_deadline_word_with_connector_is_kept() -> None:
    assert _due("Die Frist endet am 11.03.2026.") == {date(2026, 3, 11)}


# --- keine automatische Bestaetigung ----------------------------------------------------------------------------------


def test_every_proposal_stays_unconfirmed_with_source_and_uncertainty() -> None:
    text = ("Aktenvermerk vom 23.01.2026 - Az. 26/0091-HV\nBerufungsfrist: Das Urteil wurde am 22.01.2026 zugestellt; "
            "Fristende 23.02.2026, da 22.02.2026 ein Sonntag ist.")
    proposals = _proposals(text)
    assert _due(text) == {date(2026, 2, 23)}
    for p in proposals:
        assert p.confidence <= 0.5 and p.source_text and p.raw_date_text
        assert "NICHT" in p.reasoning  # "NICHT als verbindlich bestätigt" bzw. Fristende nicht berechnet
