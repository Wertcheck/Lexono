"""§76: weitere Alternativformeln mit Kontextpruefung, vollstaendiges Typ-Label in der Liste, nachvollziehbarer Export.

Alternativen: "alternativ", "im Fall(e) ...", "je nach ...", "falls/sofern ...", "<Fristende> oder/alternativ <Datum>" -
ein Signalwort genuegt nie allein; zwischen den Daten darf kein anderer Gegenstand stehen. Echte Fristen gehen nicht
verloren, unabhaengige Fristen erhalten keinen Hinweis."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.deadlines.extractor import PlaceholderDeadlineExtractor
from app.export import MatterExportService
from tests.test_deadlines_hitl import _login_role, _matter, _suggestion, client, db_session  # noqa: F401

MARK = "mehrere mögliche Fristenden"


def _dated(text: str) -> dict[date, str]:
    return {p.due_date: p.reasoning for p in PlaceholderDeadlineExtractor().extract(text) if p.due_date}


# --- Alternativen (positiv): beide Fristenden erkannt UND als konkurrierend markiert ------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Fristende 05.03.2026, alternativ (bei Zugang am 13.02.2026) 06.03.2026.",
        "Klagefrist bis 05.03.2026; alternativ, falls der spätere Zugang zutrifft, bis 06.03.2026.",
        "Fristende 05.03.2026, im Falle eines späteren Zugangs Fristende 06.03.2026.",
        "Fristende je nach Zugang 05.03.2026 oder 06.03.2026.",
        "Fristende 05.03.2026 bzw. 06.03.2026, je nach Zugang.",
        "Fristende 05.03.2026, hilfsweise 06.03.2026.",
    ],
)
def test_alternative_wordings_mark_both_deadline_ends(text: str) -> None:
    found = _dated(text)
    assert set(found) == {date(2026, 3, 5), date(2026, 3, 6)}, found
    assert all(MARK in r and "NICHT entschieden" in r for r in found.values())


# --- unabhaengige Fristen und andere Daten (negativ) --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, dates",
    [
        ("Zahlung bis 13.03.2026, alternativ Ratenzahlung bis 31.03.2026.", {date(2026, 3, 13), date(2026, 3, 31)}),
        ("Stellungnahme bis 06.03.2026; falls Zahlung erfolgt, Frist zur Rücknahme bis 13.03.2026.",
         {date(2026, 3, 6), date(2026, 3, 13)}),
        ("Alternativ kann die Stellungnahme bis 06.03.2026 erfolgen, die Zahlung ist bis 13.03.2026 fällig.",
         {date(2026, 3, 6), date(2026, 3, 13)}),
        ("Stellungnahme bis 06.03.2026 und Zahlung bis 13.03.2026.", {date(2026, 3, 6), date(2026, 3, 13)}),
        ("Frist für Stellungnahme: 06.03.2026\nAlternativ Zahlung bis 13.03.2026.", {date(2026, 3, 6), date(2026, 3, 13)}),
        ("Im Fall der Zahlung bis 13.03.2026 entfällt die Stellungnahme bis 06.03.2026.", {date(2026, 3, 6), date(2026, 3, 13)}),
    ],
)
def test_independent_deadlines_get_no_alternative_note(text: str, dates: set[date]) -> None:
    found = _dated(text)
    assert set(found) == dates, found  # keine Frist geht verloren
    assert all(MARK not in r for r in found.values()), found


@pytest.mark.parametrize(
    "text, ends",
    [
        ("Das Urteil wurde am 22.01.2026 zugestellt; alternativ Fristende 23.02.2026.", {date(2026, 2, 23)}),
        ("Aktenvermerk vom 18.02.2026, alternativ Fristende 06.03.2026.", {date(2026, 3, 6)}),
        ("Zugang am 12.02.2026 oder 13.02.2026 (streitig).", set()),  # Fristbeginn-Alternativen sind kein Fristende
        ("Schreiben vom 05.03.2026 oder 06.03.2026 liegt vor.", set()),  # Dokumentdaten
        ("Termin am 05.03.2026 oder 06.03.2026.", set()),  # Termin, kein Fristende
    ],
)
def test_start_document_and_appointment_dates_never_become_alternative_ends(text: str, ends: set[date]) -> None:
    proposals = PlaceholderDeadlineExtractor().extract(text)
    assert {p.due_date for p in proposals if p.due_date and "Art: Frist -" in p.reasoning} == ends
    assert all(MARK not in p.reasoning for p in proposals)


def test_existing_alternatives_and_single_deadlines_unchanged() -> None:
    both = _dated("Zugang streitig; vorsorglich Fristende 05.02.2026, spätestens 06.02.2026.")
    assert set(both) == {date(2026, 2, 5), date(2026, 2, 6)} and all(MARK in r for r in both.values())
    single = _dated("Wir erwarten Ihre Stellungnahme bis zum 15.04.2026.")
    assert set(single) == {date(2026, 4, 15)} and MARK not in single[date(2026, 4, 15)]


# --- Liste: vollstaendiges Typ-Label --------------------------------------------------------------------------------------

TERMINATION = ("Platzhalter-Fristerkennung (regelbasiert, kein LLM). Art: Beendigungstermin (kein Fristende) - "
               "Beendigungszeitpunkt laut Text ('zum', Bezug 'kündig'). Das ist keine Frist: ob die Beendigung wirksam "
               "ist ..., wurde NICHT geprüft. Konfidenz bewusst niedrig - diese Frist gilt NICHT als verbindlich bestätigt.")
DEADLINE = "Platzhalter. Art: Frist - Fristende ausdrücklich im Text genannt ('bis zum')."


def test_list_type_label_is_complete_with_soft_hyphen_and_tooltip(client: TestClient, db_session: Session) -> None:  # noqa: F811
    matter = _matter(db_session)
    _suggestion(db_session, matter, reasoning=TERMINATION, document_id="dok-1", due_date=date.today() + timedelta(days=9))
    _suggestion(db_session, matter, reasoning=DEADLINE, document_id="dok-1", due_date=date.today() + timedelta(days=5))
    _login_role(db_session, client, "anwalt")
    page = client.get(f"/dashboard/tasks?status=&matter_id={matter.id}").text
    labels = re.findall(r'<span class="task-type-cell__label" title="([^"]+)" data-type-label="([^"]+)">([^<]+)</span>', page)
    assert sorted((title, data) for title, data, _ in labels) == [("Beendigungstermin", "Beendigungstermin"), ("Frist", "Frist")]
    shown = {data: text for _, data, text in labels}
    # vollstaendig, nur weiche Silbentrennstellen (Be-en-di-gungs-ter-min), kein Umbruch mitten in der Silbe
    assert shown["Beendigungstermin"] == "Be\u00aden\u00addi\u00adgungs\u00adter\u00admin"
    assert shown["Beendigungstermin"].replace("\u00ad", "") == "Beendigungstermin"
    assert shown["Frist"] == "Frist"


def test_label_css_allows_wrapping_instead_of_clipping() -> None:
    css = Path("app/web/static/css/app.css").read_text(encoding="utf-8")
    block = css.split(".task-type-cell__label {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: normal" in block and "hyphens: manual" in block and "min-width: 0" in block
    # unter 1366 px wird das Detailpanel unter die Liste gestapelt (vorher erst unter 900 px)
    assert "@media (max-width: 1365.98px) {\n  .tasks-layout {\n    flex-direction: column;" in css.replace("\r\n", "\n")


# --- Export: Art nachvollziehbar ------------------------------------------------------------------------------------------


def test_export_names_kind_label_reasoning_and_confidence(db_session: Session, tmp_path: Path) -> None:  # noqa: F811
    matter = _matter(db_session)
    _suggestion(db_session, matter, reasoning=TERMINATION, document_id="dok-1", confidence=0.5)
    _suggestion(db_session, matter, reasoning=DEADLINE, document_id="dok-1", confidence=0.5)
    archive = MatterExportService().export_matter(matter.id, db_session, tmp_path)
    with zipfile.ZipFile(archive) as zf:
        rows = {d["kind"]: d for d in json.loads(zf.read("manifest.json"))["deadlines"]}
    assert rows["beendigung"]["kind_label"] == "Möglicher Beendigungstermin (kein Fristende)"
    assert rows["frist"]["kind_label"] == "Mögliche Frist (Fristende genannt)"
    assert "kein Fristende" in rows["beendigung"]["reasoning"] and "NICHT geprüft" in rows["beendigung"]["reasoning"]
    assert rows["beendigung"]["confidence"] == 0.5 and rows["beendigung"]["review_status"] == "unreviewed"
