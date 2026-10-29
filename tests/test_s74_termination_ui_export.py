"""§74: Beendigungstermin in "Aufgaben & Fristen" (Liste, Detail) und im Akten-Export als eigene Art - nicht als Frist.

Nutzt die Fixtures/Helfer aus tests/test_deadlines_hitl.py (In-Memory-DB, TestClient, synthetische Daten)."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.export import MatterExportService
from tests.test_deadlines_hitl import _login_role, _matter, _suggestion, client, db_session  # noqa: F401

TERMINATION = ("Platzhalter-Fristerkennung (regelbasiert, kein LLM). Art: Beendigungstermin (kein Fristende) - "
               "Beendigungszeitpunkt laut Text ('zum', Bezug 'gekündigt'). Das ist keine Frist.")
DEADLINE = "Platzhalter. Art: Frist - Fristende ausdrücklich im Text genannt ('bis zum')."


def _type_cells(page: str) -> list[str]:
    # §76: das Label enthaelt eine weiche Trennstelle (U+00AD, unsichtbar) - fuer den Textvergleich entfernt
    return [" ".join(re.sub(r"<[^>]+>", " ", cell).replace("\u00ad", "").split())
            for cell in re.findall(r'<div class="task-type-cell">(.*?)</div>', page, re.S)]


def test_task_list_and_detail_name_the_termination_not_a_deadline(client: TestClient, db_session: Session) -> None:  # noqa: F811
    matter = _matter(db_session)
    termination = _suggestion(db_session, matter, reasoning=TERMINATION, document_id="dok-1",
                              source_text="30.04.2026 :: Das Mietverhältnis wird ordentlich zum 30.04.2026 gekündigt.",
                              due_date=date.today() + timedelta(days=40))
    _suggestion(db_session, matter, reasoning=DEADLINE, document_id="dok-1", due_date=date.today() + timedelta(days=20))
    _login_role(db_session, client, "anwalt")

    page = client.get(f"/dashboard/tasks?status=&matter_id={matter.id}").text
    assert sorted(_type_cells(page)) == ["Beendigungstermin", "Frist"]
    assert "Möglicher Beendigungstermin (kein Fristende)" in page and "Mögliche Frist (Fristende genannt)" in page

    detail = client.get(f"/dashboard/tasks?status=&matter_id={matter.id}&selected={termination.id}&selected_type=deadline").text
    fields = " ".join(re.sub(r"<[^>]+>", " ", detail).split())
    assert "Typ Beendigungstermin" in fields and "Möglicher Beendigungstermin (kein Fristende)" in fields
    assert "ungeprüft" in fields  # bleibt ein ungepruefter Vorschlag


def test_export_names_the_kind_of_each_suggestion(db_session: Session, tmp_path: Path) -> None:  # noqa: F811
    matter = _matter(db_session)
    _suggestion(db_session, matter, reasoning=TERMINATION, document_id="dok-1")
    _suggestion(db_session, matter, reasoning=DEADLINE, document_id="dok-1")

    archive = MatterExportService().export_matter(matter.id, db_session, tmp_path)
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert sorted(d["kind"] for d in manifest["deadlines"]) == ["beendigung", "frist"]
    assert all(d["review_status"] == "unreviewed" for d in manifest["deadlines"])
