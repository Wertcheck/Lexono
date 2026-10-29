"""§75: Hinweis "mehrere mögliche Fristenden" nur bei tatsaechlich KONKURRIERENDEN Fristenden; Beendigungstermin-UI;
Referenzkorrektur LE-D002.

Bis §74 erhielten alle Fristenden desselben Satzes den Hinweis - auch zwei verschiedene Fristen ("Stellungnahme bis
06.03.2026 und Zahlung bis 13.03.2026"). Jetzt nur bei einem Alternativsignal (vorsorglich, spätestens, hilfsweise,
oder, "bei Zugang ... liefe") und ohne anderen Gegenstand zwischen den Daten. Echte Fristen gehen nicht verloren."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.deadlines.extractor import PlaceholderDeadlineExtractor
from scripts.legal_bench import corpus
from tests.test_deadlines_hitl import _login_role, _matter, _suggestion, client, db_session  # noqa: F401

MARK = "mehrere mögliche Fristenden"


def _dated(text: str) -> dict[date, str]:
    return {p.due_date: p.reasoning for p in PlaceholderDeadlineExtractor().extract(text) if p.due_date}


# --- konkurrierende Fristenden (positiv) ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, early, late",
    [
        ("Zugang streitig; vorsorglich Fristende 05.03.2026, spätestens 06.03.2026.", date(2026, 3, 5), date(2026, 3, 6)),
        ("Spätestens 06.03.2026, vorsorglich jedoch Fristende 05.03.2026 beachten.", date(2026, 3, 5), date(2026, 3, 6)),
        ("Vorsorglich ist vom früheren Zugang auszugehen: Fristablauf am 28.04.2026; bei Zugang am 03.03.2026 liefe die "
         "Frist erst am 04.05.2026 ab.", date(2026, 4, 28), date(2026, 5, 4)),
        ("Zugang streitig (15.01.2026 laut Konsulat, 16.01.2026 laut Mandantin); vorsorglich Fristende 05.02.2026, "
         "spätestens 06.02.2026.", date(2026, 2, 5), date(2026, 2, 6)),
        ("Vorsorglich Fristende 05.03.2026 (Zugang laut Arbeitgeberin), spätestens 06.03.2026 (Zugang laut Mandantin).",
         date(2026, 3, 5), date(2026, 3, 6)),
        ("Fristende 05.03.2026 oder Fristende 06.03.2026, je nach Zugang.", date(2026, 3, 5), date(2026, 3, 6)),
    ],
)
def test_competing_deadline_ends_are_marked_with_position(text: str, early: date, late: date) -> None:
    found = _dated(text)
    assert set(found) == {early, late}
    assert MARK in found[early] and "das früheste" in found[early] and late.strftime("%d.%m.%Y") in found[early]
    assert MARK in found[late] and "das späteste" in found[late]
    assert all("NICHT entschieden" in r for r in found.values())


# --- verschiedene Fristen (negativ) -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, dates",
    [
        ("Stellungnahme bis 06.03.2026 und Zahlung bis 13.03.2026.", {date(2026, 3, 6), date(2026, 3, 13)}),
        ("Stellungnahme bis spätestens 06.03.2026, Räumung spätestens am 02.11.2026.", {date(2026, 3, 6), date(2026, 11, 2)}),
        ("Die Frist zur Stellungnahme endet am 06.03.2026, die Zahlungsfrist am 13.03.2026.", {date(2026, 3, 6), date(2026, 3, 13)}),
        ("Widerspruch bis 31.08.2026; Wiedervorlage: 02.03.2026", {date(2026, 8, 31), date(2026, 3, 2)}),
        ("Frist für Stellungnahme: 06.03.2026\nRäumung und Herausgabe erwarten wir spätestens am 02.11.2026.",
         {date(2026, 3, 6), date(2026, 11, 2)}),
        ("Frist zur Stellungnahme an die Gegenseite: 06.03.2026.\nWiedervorlage: 02.03.2026",
         {date(2026, 3, 6), date(2026, 3, 2)}),
    ],
)
def test_two_different_deadlines_are_not_marked_as_competing(text: str, dates: set[date]) -> None:
    found = _dated(text)
    assert set(found) == dates  # keine Frist geht verloren
    assert all(MARK not in r for r in found.values()), found


@pytest.mark.parametrize(
    "text, due",
    [
        # Zustellung/Zugang plus Fristende: der Fristbeginn ist kein konkurrierendes Fristende
        ("Das Urteil wurde am 22.01.2026 zugestellt; Fristende 23.02.2026.", date(2026, 2, 23)),
        ("Zugang der Kündigung am 15.01.2026, Fristende 05.02.2026.", date(2026, 2, 5)),
        # Dokument- und Vermerkdaten neben einem Fristende
        ("Aktenvermerk vom 18.02.2026 - Fristende 06.03.2026.", date(2026, 3, 6)),
        ("Mit Schreiben vom 16.02.2026 wurde Frist bis 06.03.2026 gesetzt.", date(2026, 3, 6)),
        # Beendigungstermin neben einem Fristende
        ("Widerspruch: Fristende 31.08.2026, zwei Monate vor der Beendigung zum 31.10.2026.", date(2026, 8, 31)),
    ],
)
def test_start_document_and_termination_dates_never_make_a_deadline_competing(text: str, due: date) -> None:
    proposals = PlaceholderDeadlineExtractor().extract(text)
    ends = [p for p in proposals if p.due_date and "Art: Frist -" in p.reasoning]
    assert [p.due_date for p in ends] == [due]
    assert all(MARK not in p.reasoning for p in proposals)


# --- Beendigungstermin in der Oberflaeche -----------------------------------------------------------------------------

TERMINATION = ("Platzhalter-Fristerkennung (regelbasiert, kein LLM). Art: Beendigungstermin (kein Fristende) - "
               "Beendigungszeitpunkt laut Text ('zum', Bezug 'kündigen'). Das ist keine Frist.")
DEADLINE = "Platzhalter. Art: Frist - Fristende ausdrücklich im Text genannt ('bis zum')."


def _fields(page: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def test_detail_shows_beendigung_zum_for_terminations_and_faellig_am_for_deadlines(client: TestClient, db_session: Session) -> None:  # noqa: F811
    matter = _matter(db_session)
    termination = _suggestion(db_session, matter, reasoning=TERMINATION, document_id="dok-1",
                              due_date=date.today() + timedelta(days=40),
                              source_text="30.06.2026 :: hiermit kündigen wir das Arbeitsverhältnis ordentlich zum 30.06.2026.")
    deadline = _suggestion(db_session, matter, reasoning=DEADLINE, document_id="dok-1", due_date=date.today() + timedelta(days=20))
    _login_role(db_session, client, "anwalt")
    base = f"/dashboard/tasks?status=&matter_id={matter.id}&selected_type=deadline&selected="

    termination_page = _fields(client.get(base + termination.id).text)
    assert "Beendigung zum " + termination.due_date.strftime("%d.%m.%Y") in termination_page
    assert "Typ Beendigungstermin" in termination_page and "Möglicher Beendigungstermin (kein Fristende)" in termination_page
    assert "ungeprüft" in termination_page  # Unsicherheit/Pruefstatus bleiben sichtbar
    assert "wirksam" not in termination_page.split("Beendigung zum")[1][:200]  # keine Wirksamkeitsaussage am Datum

    deadline_page = _fields(client.get(base + deadline.id).text)
    assert "Fällig am " + deadline.due_date.strftime("%d.%m.%Y") in deadline_page and "Beendigung zum" not in deadline_page


def test_matter_page_names_the_kind_of_each_suggestion(client: TestClient, db_session: Session) -> None:  # noqa: F811
    matter = _matter(db_session)
    _suggestion(db_session, matter, reasoning=TERMINATION, document_id="dok-1")
    _suggestion(db_session, matter, reasoning=DEADLINE, document_id="dok-1")
    _suggestion(db_session, matter, reasoning=None, document_id=None, source_text="Manuell: Rückruf")  # manuell: ohne Art
    _login_role(db_session, client, "anwalt")
    page = _fields(client.get(f"/dashboard/matters/{matter.id}").text)
    assert "Möglicher Beendigungstermin (kein Fristende)" in page and "Mögliche Frist (Fristende genannt)" in page
    assert "Manuell erfasst" not in page


# --- Referenzkorrektur LE-D002 ----------------------------------------------------------------------------------------


def test_le_d002_reference_names_the_termination_with_documented_history() -> None:
    case = next(c for c in corpus.load_cases() if c["id"] == "LE-D002")
    ref = case["reference"]
    assert [d["date"] for d in ref["deadlines"]] == ["13.04.2026"]
    assert [t["date"] for t in ref["terminations"]] == ["30.06.2026"]
    history = case["reference_history"]
    assert len(history) == 1 and history[0]["value"] == "30.06.2026" and history[0]["date"] == "2026-10-18"
    assert "reference.deadlines" in history[0]["old"] and "reference.terminations" in history[0]["new"]
    # unveraendert: keep-Wert und Fakt (das Datum muss weiter im Payload stehen), Fristen-Aufgabe
    assert "30.06.2026" in ref["privacy"]["keep"]
    assert any("30.06.2026" in f["values"] for f in ref["facts"])
    assert next(t for t in ref["tasks"] if t["kind"] == "fristen")["expected_deadlines"] == ["13.04.2026"]
    # die Formulierung wird tatsaechlich als Beendigungstermin erkannt
    kinds = {p.due_date: p.reasoning for p in PlaceholderDeadlineExtractor().extract(
        "hiermit kündigen wir das Arbeitsverhältnis ordentlich zum 30.06.2026.")}
    assert "Art: Beendigungstermin" in kinds[date(2026, 6, 30)]


def test_only_le_d002_carries_a_reference_history() -> None:
    assert [c["id"] for c in corpus.load_cases() if c.get("reference_history")] == ["LE-D002"]
