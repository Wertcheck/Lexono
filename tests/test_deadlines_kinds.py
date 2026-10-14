"""P2.1 (§40): Fristvorschlaege unterscheiden blosse Datumsangaben/Ereignisdaten, Termine und Fristen.

Vorher wurde JEDES Datum ein Fristvorschlag (Konfidenz 0,15 ohne Schluesselwort) - im Legal-E2E-Korpus waren 27 von 41
(dev) bzw. 17 von 25 (test) Vorschlaegen blosse Ereignisdaten. Synthetische Entwicklungsfaelle, getrennt vom Korpus."""

from datetime import date

import pytest

from app.deadlines.extractor import PlaceholderDeadlineExtractor

extractor = PlaceholderDeadlineExtractor()


def _dated(text: str) -> dict[date, object]:
    return {r.due_date: r for r in extractor.extract(text) if r.due_date}


@pytest.mark.parametrize("text", [
    "Musterstadt, den 10.03.2026\nMietrückstand Februar und März",
    "Von: mandant@beispielpost.example\nDatum: 03.02.2026\nBetreff: Schimmel",
    "Die Abnahme war am 14.11.2025, und zwar nur unter Vorbehalt.",
    "Er sei früher gegangen (Arzttermin).  Die Abmahnung vom 12.02.2026 habe er nie erhalten.",
    "Wir beziehen uns auf unser Schreiben vom 15.03.2027.",
    "Beginn des Arbeitsverhältnisses: 15.01.2019. Tätigkeit: Disposition.",
    "Wertermittlungsstichtag: 01.03.2026",
    "Der Bescheid betrifft den Zeitraum vom 01.01.2025 bis 31.12.2025.",
])
def test_plain_dates_and_event_dates_are_never_deadline_suggestions(text: str) -> None:
    """Ein Datum allein begruendet keine Frist (keine Frist aus einem blossen Datum ableiten)."""
    assert _dated(text) == {}


@pytest.mark.parametrize("text, expected", [
    ("Wir erwarten Ihre Stellungnahme bis zum 15.04.2026.", date(2026, 4, 15)),
    ("Ich fordere Sie auf, den Rückstand bis zum 31.03.2026 auszugleichen.", date(2026, 3, 31)),
    ("Der Betrag ist zahlbar bis 31.03.2026.", date(2026, 3, 31)),
    ("Die Restzahlung ist fällig am 14.04.2026.", date(2026, 4, 14)),
    ("Spätestens am 20.08.2026 muss der Einspruch eingehen.", date(2026, 8, 20)),
    ("Am 15.05.2026 läuft die Frist ab.", date(2026, 5, 15)),
    ("hiermit kündigen wir das Arbeitsverhältnis ordentlich zum 30.06.2026.", date(2026, 6, 30)),
])
def test_explicit_deadlines_are_recognised_as_explicit(text: str, expected: date) -> None:
    found = _dated(text)
    assert set(found) == {expected}
    candidate = found[expected]
    assert candidate.confidence >= 0.4
    assert "Art: Frist" in candidate.reasoning and "ausdrücklich" in candidate.reasoning


def test_deadline_with_start_event_keeps_event_and_duration_and_only_end_is_a_deadline() -> None:
    """Fristbeginn/Ereignis und Fristende getrennt: das Zugangsdatum ist das Ereignis, nicht die Frist."""
    text = ("Die Klagefrist nach § 4 KSchG endet drei Wochen nach Zugang der Kündigung am 23.03.2026, "
            "also am 13.04.2026. Die Kündigung ist unwirksam.")
    found = _dated(text)
    assert set(found) == {date(2026, 4, 13)}
    reasoning = found[date(2026, 4, 13)].reasoning
    assert "23.03.2026" in reasoning and "Zugang der Kündigung" in reasoning
    assert "drei Wochen" in reasoning
    assert "nicht nachgerechnet" in reasoning


@pytest.mark.parametrize("text, expected", [
    ("Termin zur mündlichen Verhandlung am 12.05.2026, 10:00 Uhr, Saal 3.", date(2026, 5, 12)),
    ("Ortstermin am 02.12.2025 im Dachgeschoss.", date(2025, 12, 2)),
])
def test_appointments_are_appointments_not_deadlines(text: str, expected: date) -> None:
    found = _dated(text)
    assert set(found) == {expected}
    reasoning = found[expected].reasoning
    assert "Art: Termin" in reasoning and "Art: Frist" not in reasoning


def test_relative_period_without_start_is_marked_incomplete() -> None:
    results = extractor.extract("Bitte nehmen Sie binnen zwei Wochen Stellung.")
    assert len(results) == 1 and results[0].due_date is None
    assert "Art: Fristdauer" in results[0].reasoning
    assert "Fristbeginn fehlt" in results[0].reasoning


def test_relative_period_with_named_start_event_is_not_computed() -> None:
    """Dauer + Ereignis mit Datum, aber kein Fristende im Text: keine eigene Berechnung (keine Fristberechnungslogik fuer
    Zustellfiktion, Wochenende/Feiertage) - Ereignis und Dauer werden genannt, das Ende bleibt offen."""
    text = "Der Bescheid wurde am 06.08.2026 zugestellt. Einspruch ist innerhalb von zwei Wochen nach Zustellung möglich."
    results = extractor.extract(text)
    assert all(r.due_date is None for r in results)
    relative = [r for r in results if "Art: Fristdauer" in r.reasoning]
    assert len(relative) == 1
    assert "nicht berechnet" in relative[0].reasoning


def test_birthdate_next_to_a_deadline_stays_excluded() -> None:
    assert set(_dated("Herr Beispiel, geb. 12.03.1980, Frist bis 30.11.2026.")) == {date(2026, 11, 30)}


def test_source_passage_is_kept_for_every_suggestion() -> None:
    text = "Rechtsanwalt Beispiel\nMusterstadt, den 01.04.2026\nWir erwarten Ihre Stellungnahme bis zum 15.04.2026."
    (candidate,) = extractor.extract(text)
    assert candidate.raw_date_text == "15.04.2026"
    assert "Stellungnahme bis zum 15.04.2026" in candidate.source_text


def test_appointment_word_after_the_date_is_an_appointment() -> None:
    found = _dated("06.03.2024: Termin beim Nachlassgericht")
    assert set(found) == {date(2024, 3, 6)} and "Art: Termin" in found[date(2024, 3, 6)].reasoning


@pytest.mark.parametrize("text, raw", [
    ("Am 05.03.2024 erfuhr die Tochter, dass der Vater verstorben ist.", "05.03.2024"),
    ("Der Bescheid wurde am 06.08.2026 zugestellt.", "06.08.2026"),
    ("Kenntnis vom Testament am 10.02.2026.", "10.02.2026"),
])
def test_triggering_event_is_a_possible_deadline_start_without_a_deadline_date(text: str, raw: str) -> None:
    """Kenntnis/Zugang/Zustellung loesen Fristen aus (z. B. Ausschlagung ab Kenntnis) - nie stillschweigend verwerfen,
    aber auch nie als Fristende ausgeben: ohne due_date, Datum in der Textstelle, Fristende ausdruecklich offen."""
    results = extractor.extract(text)
    assert len(results) == 1
    candidate = results[0]
    assert candidate.due_date is None and candidate.raw_date_text == raw and raw in candidate.source_text
    assert "Art: Fristbeginn" in candidate.reasoning and "nicht berechnet" in candidate.reasoning
