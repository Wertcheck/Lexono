"""Tests fuer app/deadlines/extractor.py (Prompt 10).

Nutzt ausschliesslich synthetische Testtexte."""

from datetime import date

from app.deadlines.extractor import PlaceholderDeadlineExtractor

extractor = PlaceholderDeadlineExtractor()


def test_extracts_numeric_date_with_deadline_keyword() -> None:
    results = extractor.extract("Bitte antworten Sie bis zum 15.03.2027.")
    assert len(results) == 1
    assert results[0].due_date == date(2027, 3, 15)
    assert results[0].confidence >= 0.4


def test_bare_date_without_keyword_gets_low_confidence() -> None:
    results = extractor.extract("Wir beziehen uns auf unser Schreiben vom 15.03.2027.")
    assert len(results) == 1
    assert results[0].due_date == date(2027, 3, 15)
    assert results[0].confidence <= 0.2


def test_extracts_date_with_german_month_name() -> None:
    results = extractor.extract("Frist bis spätestens 15. März 2027.")
    assert len(results) == 1
    assert results[0].due_date == date(2027, 3, 15)


def test_extracts_relative_period_without_due_date() -> None:
    results = extractor.extract("Bitte antworten Sie binnen zwei Wochen.")
    assert len(results) == 1
    assert results[0].due_date is None
    assert "zwei" in results[0].raw_date_text.lower()


def test_no_dates_returns_empty_list() -> None:
    results = extractor.extract("Ein Text ganz ohne Datumsangaben.")
    assert results == []


def test_multiple_dates_are_all_extracted() -> None:
    text = "Erste Frist: bis zum 01.01.2027. Zweite Frist: bis zum 15.06.2027."
    results = extractor.extract(text)
    assert len(results) == 2
    due_dates = {r.due_date for r in results}
    assert due_dates == {date(2027, 1, 1), date(2027, 6, 15)}


def test_confidence_never_signals_high_certainty() -> None:
    """Auch mit Schluesselwort bleibt die Konfidenz eines Platzhalters
    deutlich unter "sicher"."""
    results = extractor.extract("Frist: spätestens bis zum 01.01.2027.")
    assert all(r.confidence <= 0.6 for r in results)


def test_invalid_date_is_not_extracted() -> None:
    """z. B. 32.13.2027 ist kein gueltiges Datum - darf nicht als Frist
    durchrutschen."""
    results = extractor.extract("Ungültiges Datum: 32.13.2027.")
    assert results == []


def test_reasoning_mentions_placeholder_nature() -> None:
    results = extractor.extract("Bis zum 15.03.2027 antworten.")
    assert "Platzhalter" in results[0].reasoning
    assert "kein LLM" in results[0].reasoning


def test_context_window_snaps_outward_to_word_boundaries_not_mid_word() -> None:
    """ZWEITER ECHTER FUND (selbe Abnahme-Runde, 13.09.): das feste
    Zeichen-Fenster kann mitten in ein Wort schneiden (z. B.
    "eschäftigungsmonat" statt "Beschäftigungsmonat"), wenn der Fund
    zufaellig so weit vom Wortanfang entfernt liegt, dass genau die
    Fenstergrenze mitten hineinfaellt. Presidios NER erkannte dieses
    abgeschnittene Fragment faelschlich als eigene Entitaet, die dann -
    weil sie zufaellig Teilstring des andernorts korrekt geschriebenen
    Worts ist - vom Final Payload Gate faelschlich als "geleakter
    Originalwert" gemeldet wurde und JEDE Chat-Nachricht blockierte."""
    from app.deadlines.extractor import _CONTEXT_WINDOW_CHARS

    prefix_len = _CONTEXT_WINDOW_CHARS
    word = "Beschäftigungsmonat"
    gap = "y" * 49 + " "
    text = ("x" * prefix_len) + word + gap + "15.03.2027 danach."

    results = extractor.extract(text)

    assert len(results) == 1
    assert word in results[0].source_text


def test_source_text_preserves_paragraph_boundary_around_heading() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): eine Ueberschrift direkt
    gefolgt vom naechsten Absatz (z. B. Word-Absaetze "2. Schriftverkehr"
    + "Mit Schreiben vom 12.08.2026 ...") darf im `source_text` NICHT zu
    "Schriftverkehr Mit" mit nur EINEM Leerzeichen verschmelzen - genau das
    wertete app/privacy/security_check.py::_find_possible_unrecognized_names
    faelschlich als moeglichen unerkannten Namen und blockierte dauerhaft
    jede Chat-Nachricht der betroffenen Unterhaltung, real reproduziert."""
    text = "1. Vorbemerkung\n2. Schriftverkehr\nMit Schreiben vom 12.08.2026 forderte die Mieterin den Vermieter auf."
    results = extractor.extract(text)
    assert len(results) == 1
    assert "Schriftverkehr Mit" not in results[0].source_text
    assert "Schriftverkehr" in results[0].source_text
    assert "Mit Schreiben" in results[0].source_text


def test_source_text_paragraph_boundary_does_not_trigger_unrecognized_name_heuristic() -> None:
    """Ende-zu-Ende-Nachweis: dieselbe Ueberschrift+Folgesatz-Grenze, die
    frueher `source_text` verschmelzen liess, fuehrt jetzt beim
    Sicherheitscheck-Heuristik-Punkt 6 nicht mehr zu einem falschen
    Namens-Kandidaten."""
    from app.privacy.security_check import _find_possible_unrecognized_names

    text = "1. Vorbemerkung\n2. Schriftverkehr\nMit Schreiben vom 12.08.2026 forderte die Mieterin den Vermieter auf."
    results = extractor.extract(text)
    candidates = _find_possible_unrecognized_names(results[0].source_text)
    assert "Schriftverkehr Mit" not in candidates
