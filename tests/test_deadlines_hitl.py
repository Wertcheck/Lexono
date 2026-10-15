"""ARCHITECTURE.md §73: Human-in-the-Loop fuer erkannte Fristen - ausschliesslich synthetische Daten, kein Netz.

Abgedeckt: automatische Speicherung als ungepruefter Vorschlag, Einzelentscheidungen (bestaetigen, verwerfen,
zurueckstellen) mit Audit, Berechtigungen (nur Anwalt/Admin bestaetigt), Korrektur mit alten und neuen Werten,
Duplikat ohne Pruefumgehung, Pruefliste aktenuebergreifend und je Akte, naechster Vorschlag, Hinweise ohne
Versaeumnis-Behauptung, erneute Analyse (Schutz bestehender Fristen, Konflikt, keine Duplikate, Aktenisolation)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.deadlines.extractor import PlaceholderDeadlineExtractor
from app.deadlines.review import (
    CONFLICT_MARKER,
    ReviewDecisionError,
    decide,
    deadline_kind,
    deferred_notes,
    next_unreviewed,
    review_hint,
)
from app.deadlines.service import DeadlineAnalysisService
from app.main import app
from app.models import AuditEvent, Client, Deadline, Document, Matter
from app.models.base import Base
from app.tasks.service import (
    DeadlinePermissionError,
    count_task_items,
    delete_deadline,
    duplicate_deadline,
    list_task_items,
    set_deadline_status,
    update_deadline,
)
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles

FRIST_TEXT = "Bitte antworten Sie bis zum 15.03.2027."


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _matter(db: Session, title: str = "Testakte HITL") -> Matter:
    client_row = Client(name=f"Testmandant {title}")
    matter = Matter(client=client_row, title=title)
    db.add_all([client_row, matter])
    db.commit()
    return matter


def _document(db: Session, matter: Matter, text: str = FRIST_TEXT) -> Document:
    document = Document(file_path="/tmp/synthetisch.pdf", matter_id=matter.id, extracted_text=text)
    db.add(document)
    db.commit()
    return document


def _service() -> DeadlineAnalysisService:
    return DeadlineAnalysisService(PlaceholderDeadlineExtractor())


def _suggestion(db: Session, matter: Matter, **kwargs) -> Deadline:
    kwargs.setdefault("source_text", "15.03.2027 :: Bitte antworten Sie bis zum 15.03.2027.")
    kwargs.setdefault("due_date", date.today() + timedelta(days=30))
    kwargs.setdefault("reasoning", "Platzhalter. Art: Frist - Fristende ausdrücklich im Text genannt ('bis zum').")
    deadline = Deadline(matter_id=matter.id, **kwargs)
    db.add(deadline)
    db.commit()
    return deadline


def _events(db: Session, entity_id: str, event_type: str | None = None) -> list[AuditEvent]:
    query = db.query(AuditEvent).filter(AuditEvent.entity_id == entity_id)
    if event_type:
        query = query.filter(AuditEvent.event_type == event_type)
    return query.order_by(AuditEvent.created_at.asc()).all()


def _login_role(db: Session, client: TestClient, role: str) -> str:
    roles = seed_roles(db)
    email = f"{role}@kanzlei.test"
    create_test_user(db, roles[role], email)
    login(client, email)
    return extract_csrf(client.get("/dashboard/tasks").text)


# --- Automatische Speicherung ---------------------------------------------------------------------------------------


def test_analysis_stores_every_finding_as_unreviewed_suggestion_with_source(db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(
        db_session, matter, FRIST_TEXT + " Termin zur muendlichen Verhandlung am 20.04.2027."
    )
    created = _service().analyze_document(document, db_session)

    assert {deadline_kind(d) for d in created} == {"frist", "termin"}
    for deadline in created:
        assert deadline.review_status == "unreviewed"
        assert deadline.matter_id == matter.id and deadline.document_id == document.id
        assert " :: " in deadline.source_text  # Originalangabe :: Fundstelle
        assert deadline.reasoning and deadline.confidence is not None


def test_period_and_trigger_are_stored_without_a_computed_date(db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(
        db_session, matter, "Der Bescheid wurde am 02.02.2027 zugestellt. Einspruch binnen eines Monats."
    )
    created = _service().analyze_document(document, db_session)

    kinds = {deadline_kind(d): d for d in created}
    assert set(kinds) == {"fristbeginn", "fristdauer"}
    assert all(d.due_date is None for d in created)  # keine eigenmaechtige Fristberechnung


# --- Entscheidungen -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("decision,expected", [("confirm", "confirmed"), ("reject", "rejected")])
def test_decide_changes_status_once_with_audit_and_note(db_session: Session, decision, expected) -> None:
    deadline = _suggestion(db_session, _matter(db_session))
    decide(db_session, deadline, decision=decision, actor="anwalt@kanzlei.test", note="geprüft am Original")

    assert deadline.review_status == expected
    (event,) = _events(db_session, deadline.id, "deadline_review_status_changed")
    assert event.actor == "anwalt@kanzlei.test"
    assert f"unreviewed -> {expected}" in event.details and "geprüft am Original" in event.details


def test_defer_keeps_suggestion_unreviewed_and_findable(db_session: Session) -> None:
    matter = _matter(db_session)
    deadline = _suggestion(db_session, matter)
    decide(db_session, deadline, decision="defer", actor="ma@kanzlei.test", note="Original anfordern")

    assert deadline.review_status == "unreviewed"
    assert deferred_notes(db_session, [deadline.id]) == {deadline.id: "Original anfordern"}
    assert [i.id for i in list_task_items(db_session, review_only=True)] == [deadline.id]


def test_defer_is_refused_for_decided_deadlines(db_session: Session) -> None:
    deadline = _suggestion(db_session, _matter(db_session), review_status="confirmed")
    with pytest.raises(ReviewDecisionError):
        decide(db_session, deadline, decision="defer", actor="x@kanzlei.test")
    with pytest.raises(ReviewDecisionError):
        decide(db_session, deadline, decision="unreviewed", actor="x@kanzlei.test")


def test_deferral_ends_with_a_decision(db_session: Session) -> None:
    deadline = _suggestion(db_session, _matter(db_session))
    decide(db_session, deadline, decision="defer", actor="ma@kanzlei.test")
    decide(db_session, deadline, decision="confirm", actor="anwalt@kanzlei.test")
    assert deferred_notes(db_session, [deadline.id]) == {}


# --- Pruefliste und Reihenfolge -------------------------------------------------------------------------------------


def test_review_list_spans_matters_includes_done_and_excludes_decided(db_session: Session) -> None:
    first, second = _matter(db_session, "Akte A"), _matter(db_session, "Akte B")
    open_a = _suggestion(db_session, first)
    done_b = _suggestion(db_session, second, status="done")  # versehentlich erledigt: bleibt in der Pruefung
    _suggestion(db_session, first, review_status="confirmed")
    _suggestion(db_session, second, review_status="rejected")

    assert {i.id for i in list_task_items(db_session, review_only=True)} == {open_a.id, done_b.id}
    assert count_task_items(db_session, review_only=True, matter_id=first.id) == 1
    assert [i.id for i in list_task_items(db_session, review_only=True, matter_id=second.id)] == [done_b.id]


def test_review_order_puts_deferred_last_and_next_skips_them(db_session: Session) -> None:
    matter = _matter(db_session)
    soon = _suggestion(db_session, matter, due_date=date.today() + timedelta(days=2))
    later = _suggestion(db_session, matter, due_date=date.today() + timedelta(days=40))
    undated = _suggestion(db_session, matter, due_date=None)
    decide(db_session, soon, decision="defer", actor="x@kanzlei.test")

    assert [i.id for i in list_task_items(db_session, review_only=True)] == [later.id, undated.id, soon.id]
    assert next_unreviewed(db_session).id == later.id
    assert next_unreviewed(db_session, exclude_id=later.id).id == undated.id
    assert next_unreviewed(db_session, exclude_id=undated.id, matter_id=matter.id).id == later.id


def test_review_hint_never_claims_a_missed_deadline() -> None:
    today = date(2027, 3, 10)
    past = Deadline(review_status="unreviewed", due_date=date(2027, 3, 1))
    soon = Deadline(review_status="unreviewed", due_date=date(2027, 3, 15))
    far = Deadline(review_status="unreviewed", due_date=date(2027, 6, 1))
    confirmed = Deadline(review_status="confirmed", due_date=date(2027, 3, 1))

    assert review_hint(past, today) == "Datum verstrichen – bitte umgehend prüfen"
    assert review_hint(soon, today) == "Dringend prüfen – Datum in Kürze"
    assert review_hint(far, today) is None and review_hint(confirmed, today) is None
    for hint in (review_hint(past, today), review_hint(soon, today)):
        assert "versäum" not in hint.lower() and "überfällig" not in hint


# --- Korrektur, Duplikat, Loeschen ----------------------------------------------------------------------------------


def test_correction_keeps_status_and_audits_old_and_new_values(db_session: Session) -> None:
    deadline = _suggestion(db_session, _matter(db_session), due_date=date(2027, 3, 15))
    update_deadline(
        db_session, deadline, source_text="Stellungnahme Gegenseite", due_date=date(2027, 3, 22), priority="Hoch",
        actor="ma@kanzlei.test", may_change_confirmed=False,
    )

    assert deadline.review_status == "unreviewed"  # Korrektur ist keine Bestaetigung
    (event,) = _events(db_session, deadline.id, "deadline_updated")
    assert "'2027-03-15' -> '2027-03-22'" in event.details
    assert "'15.03.2027 :: Bitte antworten Sie bis zum 15.03.2027.' -> 'Stellungnahme Gegenseite'" in event.details
    assert "Pruefstatus unveraendert: unreviewed" in event.details


def test_confirmed_deadline_can_only_be_changed_or_deleted_with_permission(db_session: Session) -> None:
    deadline = _suggestion(db_session, _matter(db_session), review_status="confirmed", due_date=date(2027, 3, 15))
    with pytest.raises(DeadlinePermissionError):
        update_deadline(db_session, deadline, source_text="x", due_date=None, priority=None, actor="ma",
                        may_change_confirmed=False)
    with pytest.raises(DeadlinePermissionError):
        delete_deadline(db_session, deadline, actor="ma", may_delete=False)
    db_session.refresh(deadline)
    assert deadline.due_date == date(2027, 3, 15) and deadline.review_status == "confirmed"


@pytest.mark.parametrize("source_status,may_confirm,expected", [
    ("unreviewed", True, "unreviewed"),   # vorher: "confirmed" - Umgehung der Pruefung
    ("confirmed", False, "unreviewed"),
    ("confirmed", True, "confirmed"),
])
def test_duplicate_never_bypasses_review(db_session: Session, source_status, may_confirm, expected) -> None:
    deadline = _suggestion(db_session, _matter(db_session), review_status=source_status)
    copy = duplicate_deadline(db_session, deadline, actor="x@kanzlei.test", may_confirm=may_confirm)
    assert copy.review_status == expected and copy.matter_id == deadline.matter_id


def test_marking_done_does_not_review(db_session: Session) -> None:
    deadline = _suggestion(db_session, _matter(db_session))
    set_deadline_status(db_session, deadline, status="done", actor="x@kanzlei.test")
    assert deadline.review_status == "unreviewed"
    assert count_task_items(db_session, review_only=True) == 1


# --- Erneute Analyse ------------------------------------------------------------------------------------------------


def test_reanalysis_never_changes_existing_deadlines_and_creates_no_duplicates(db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (first,) = _service().analyze_document(document, db_session)
    decide(db_session, first, decision="confirm", actor="anwalt@kanzlei.test")
    snapshot = (first.source_text, first.due_date, first.review_status, first.reasoning)

    result = _service().reanalyze_document(document, db_session, actor="anwalt@kanzlei.test")

    assert result.created == [] and result.already_known == 1 and result.conflicts == 0
    db_session.refresh(first)
    assert (first.source_text, first.due_date, first.review_status, first.reasoning) == snapshot
    assert db_session.query(Deadline).count() == 1


def test_reanalysis_does_not_resurrect_rejected_suggestions(db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (first,) = _service().analyze_document(document, db_session)
    decide(db_session, first, decision="reject", actor="anwalt@kanzlei.test")

    result = _service().reanalyze_document(document, db_session, actor="anwalt@kanzlei.test")
    assert result.created == [] and first.review_status == "rejected"


def test_changed_text_yields_new_unreviewed_conflict_suggestion(db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (first,) = _service().analyze_document(document, db_session)
    decide(db_session, first, decision="confirm", actor="anwalt@kanzlei.test")

    document.extracted_text = "Bitte antworten Sie bis zum 22.03.2027."  # z. B. neue Texterkennung
    db_session.commit()
    result = _service().reanalyze_document(document, db_session, actor="anwalt@kanzlei.test")

    (new,) = result.created
    assert new.review_status == "unreviewed" and new.due_date == date(2027, 3, 22)
    assert new.reasoning.startswith(CONFLICT_MARKER) and "15.03.2027" in new.reasoning
    assert result.conflicts == 1 and result.confirmed_not_found == 1
    db_session.refresh(first)
    assert first.review_status == "confirmed" and first.due_date == date(2027, 3, 15)
    (event,) = _events(db_session, document.id, "deadline_reanalysis_completed")
    assert "1 Konflikt(e)" in event.details and "nicht veraendert" in event.details

    again = _service().reanalyze_document(document, db_session, actor="anwalt@kanzlei.test")
    assert again.created == []  # zweiter Lauf: keine Duplikate


def test_reanalysis_keeps_matter_assignment_and_skips_without_matter(db_session: Session) -> None:
    matter, other = _matter(db_session, "Akte A"), _matter(db_session, "Akte B")
    _suggestion(db_session, other, due_date=date(2027, 3, 15), review_status="confirmed")  # fremde Akte
    document = _document(db_session, matter)

    result = _service().reanalyze_document(document, db_session, actor="x@kanzlei.test")
    (new,) = result.created
    assert new.matter_id == matter.id and result.conflicts == 0  # keine Vermischung mit Akte B

    loose = Document(file_path="/tmp/ohne_akte.pdf", matter_id=None, extracted_text=FRIST_TEXT)
    db_session.add(loose)
    db_session.commit()
    skipped = _service().reanalyze_document(loose, db_session, actor="x@kanzlei.test")
    assert skipped.skipped_reason and skipped.created == []


def test_legacy_deadlines_without_reasoning_stay_untouched(db_session: Session) -> None:
    """Altdaten ohne Art-Angabe (vor P2.1) werden weder umgedeutet noch veraendert - keine Migration noetig."""
    matter = _matter(db_session)
    document = _document(db_session, matter, "Kein Datum in diesem Text.")
    legacy = Deadline(matter_id=matter.id, document_id=document.id, source_text="Altvorschlag", due_date=None,
                      reasoning=None)
    db_session.add(legacy)
    db_session.commit()

    _service().reanalyze_document(document, db_session, actor="x@kanzlei.test")
    db_session.refresh(legacy)
    assert (legacy.source_text, legacy.reasoning, legacy.review_status) == ("Altvorschlag", None, "unreviewed")
    assert deadline_kind(legacy) == "frist"  # aus einem Dokument: als moegliche Frist angezeigt, nicht umgedeutet


# --- Web: Berechtigungen und Ablauf ---------------------------------------------------------------------------------


def test_staff_cannot_confirm_or_reject_but_can_defer(client: TestClient, db_session: Session) -> None:
    deadline = _suggestion(db_session, _matter(db_session))
    csrf = _login_role(db_session, client, "mitarbeiter")

    page = client.get(f"/dashboard/tasks?review=1&selected={deadline.id}&selected_type=deadline").text
    assert "Als Frist bestätigen" not in page and "Zurückstellen" in page

    for status in ("confirmed", "rejected"):
        response = client.post(f"/dashboard/tasks/{deadline.id}/review",
                               data={"csrf_token": csrf, "status": status}, follow_redirects=False)
        assert response.status_code == 403
    db_session.refresh(deadline)
    assert deadline.review_status == "unreviewed"

    response = client.post(f"/dashboard/tasks/{deadline.id}/defer", data={"csrf_token": csrf, "note": "Rückfrage"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert _events(db_session, deadline.id, "deadline_review_deferred")[0].actor == "mitarbeiter@kanzlei.test"


def test_staff_manual_entry_and_copy_stay_unreviewed(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    confirmed = _suggestion(db_session, matter, review_status="confirmed")
    csrf = _login_role(db_session, client, "mitarbeiter")

    client.post(f"/dashboard/matters/{matter.id}/deadlines",
                data={"csrf_token": csrf, "source_text": "Manuell erfasst", "due_date": "2027-05-01"},
                follow_redirects=False)
    manual = db_session.query(Deadline).filter_by(source_text="Manuell erfasst").one()
    assert manual.review_status == "unreviewed"

    client.post(f"/dashboard/tasks/deadline/{confirmed.id}/duplicate", data={"csrf_token": csrf},
                follow_redirects=False)
    copy = db_session.query(Deadline).filter(Deadline.source_text.like("%(Kopie)")).one()
    assert copy.review_status == "unreviewed"

    for path, data in ((f"/dashboard/tasks/deadline/{confirmed.id}/update",
                        {"source_text": "geändert", "due_date": "2027-01-01"}),
                       (f"/dashboard/tasks/deadline/{confirmed.id}/delete", {})):
        response = client.post(path, data={"csrf_token": csrf, **data}, follow_redirects=False)
        assert response.status_code == 403
    db_session.refresh(confirmed)
    assert confirmed.review_status == "confirmed" and confirmed.source_text != "geändert"


def test_lawyer_manual_entry_is_confirmed(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    csrf = _login_role(db_session, client, "anwalt")
    client.post(f"/dashboard/matters/{matter.id}/deadlines",
                data={"csrf_token": csrf, "source_text": "Berufungsfrist", "due_date": "2027-05-01"},
                follow_redirects=False)
    assert db_session.query(Deadline).filter_by(source_text="Berufungsfrist").one().review_status == "confirmed"


def test_review_tab_decides_one_item_and_moves_to_the_next(client: TestClient, db_session: Session) -> None:
    matter, other = _matter(db_session, "Akte A"), _matter(db_session, "Akte B")
    first = _suggestion(db_session, matter, due_date=date.today() + timedelta(days=3))
    second = _suggestion(db_session, matter, due_date=date.today() + timedelta(days=20))
    foreign = _suggestion(db_session, other, due_date=date.today() + timedelta(days=1))
    csrf = _login_role(db_session, client, "anwalt")

    page = client.get(f"/dashboard/tasks?review=1&matter_id={matter.id}&selected={first.id}&selected_type=deadline")
    assert "Zu prüfen (2)" in page.text and "Dringend prüfen" in page.text
    assert "Fundstelle" in page.text and "Mögliche Frist (Fristende genannt)" in page.text

    response = client.post(
        f"/dashboard/tasks/{first.id}/review",
        data={"csrf_token": csrf, "status": "confirmed", "note": "ok", "return_to": "review",
              "review_matter_id": matter.id},
        follow_redirects=False,
    )
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["selected"] == [second.id] and query["matter_id"] == [matter.id] and query["review"] == ["1"]
    db_session.refresh(second)
    db_session.refresh(foreign)
    assert second.review_status == foreign.review_status == "unreviewed"  # nur genau eine Entscheidung

    client.post(f"/dashboard/tasks/{second.id}/defer",
                data={"csrf_token": csrf, "return_to": "review", "review_matter_id": matter.id},
                follow_redirects=False)
    response = client.post(
        f"/dashboard/tasks/{second.id}/review",
        data={"csrf_token": csrf, "status": "rejected", "return_to": "review", "review_matter_id": matter.id},
        follow_redirects=False,
    )
    assert "selected" not in parse_qs(urlparse(response.headers["location"]).query)  # Akte A abgearbeitet


def test_reanalysis_route_respects_matter_isolation_and_reports(client: TestClient, db_session: Session) -> None:
    matter, other = _matter(db_session, "Akte A"), _matter(db_session, "Akte B")
    document = _document(db_session, matter)
    csrf = _login_role(db_session, client, "mitarbeiter")

    wrong = client.post(f"/dashboard/matters/{other.id}/document/{document.id}/reanalyze-deadlines",
                        data={"csrf_token": csrf}, follow_redirects=False)
    assert wrong.status_code == 404 and db_session.query(Deadline).count() == 0

    response = client.post(f"/dashboard/matters/{matter.id}/document/{document.id}/reanalyze-deadlines",
                           data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("?reanalysis=1-0-0-0")
    assert db_session.query(Deadline).one().review_status == "unreviewed"

    missing_csrf = client.post(f"/dashboard/matters/{matter.id}/document/{document.id}/reanalyze-deadlines",
                               data={"csrf_token": "falsch"}, follow_redirects=False)
    assert missing_csrf.status_code in (400, 403)
    assert db_session.query(Deadline).count() == 1


def test_document_page_shows_kind_review_link_button_and_summary(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (suggestion,) = _service().analyze_document(document, db_session)
    _login_role(db_session, client, "anwalt")

    page = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}?reanalysis=2-1-1-0").text
    assert "Fristen erneut analysieren" in page
    assert "Mögliche Frist (Fristende genannt)" in page
    assert f"selected={suggestion.id}" in page
    assert "2 neue Vorschläge (ungeprüft), 1 bereits bekannt, 1 mit Konflikt" in page

    injected = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}?reanalysis=<script>").text
    assert "Erneute Fristenanalyse:" not in injected and "<script>" not in injected.split("Erkannte Fristen")[1][:400]


def test_creator_of_a_suggestion_is_the_system_not_the_reviewer(client: TestClient, db_session: Session) -> None:
    """Vorher galt das erste Audit-Ereignis der Frist als Anlage - nach einem Zurueckstellen stand dort der Pruefer."""
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (suggestion,) = _service().analyze_document(document, db_session)
    decide(db_session, suggestion, decision="defer", actor="pruefer@kanzlei.test")
    _login_role(db_session, client, "anwalt")

    page = client.get(f"/dashboard/tasks?selected={suggestion.id}&selected_type=deadline").text
    created_by = page.split("Erstellt von</span>")[1][:200]
    assert "System (automatische Erkennung)" in created_by and "pruefer@" not in created_by


# --- §73.1 QA: stabile Fund-Zuordnung, Rechte, Audit ------------------------------------------------------------------


def test_corrected_finding_is_recognised_on_reanalysis_and_original_is_kept(db_session: Session) -> None:
    """Reproduziert (QA §73.1): Korrektur 15.03. -> 22.03. (z. B. OCR-Fehler) und Bestaetigung; die erneute Analyse
    legte den urspruenglichen Fund vorher erneut als Konflikt-Vorschlag an."""
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (finding,) = _service().analyze_document(document, db_session)
    original = finding.source_text
    update_deadline(db_session, finding, source_text="Antwort an Gegenseite", due_date=date(2027, 3, 22),
                    priority=None, actor="anwalt@kanzlei.test")
    decide(db_session, finding, decision="confirm", actor="anwalt@kanzlei.test")

    result = _service().reanalyze_document(document, db_session, actor="anwalt@kanzlei.test")

    assert result.created == [] and result.already_known == 1 and result.conflicts == 0
    assert result.confirmed_not_found == 0
    db_session.refresh(finding)
    assert (finding.source_text, finding.due_date, finding.review_status) == (
        "Antwort an Gegenseite", date(2027, 3, 22), "confirmed")
    assert finding.original_source_text == original and finding.finding_key == "frist|15.03.2027|1"
    (event,) = _events(db_session, finding.id, "deadline_updated")
    assert "'2027-03-15' -> '2027-03-22'" in event.details


def test_two_findings_with_the_same_date_stay_two_on_reanalysis(db_session: Session) -> None:
    """Reproduziert (QA §73.1): der Abgleich ueber Art + Datum verschluckte eine zweite Frist mit gleichem Datum."""
    matter = _matter(db_session)
    document = _document(db_session, matter)
    _service().analyze_document(document, db_session)
    document.extracted_text += " Die Kaution ist bis zum 15.03.2027 zu zahlen."
    db_session.commit()

    result = _service().reanalyze_document(document, db_session, actor="x@kanzlei.test")

    assert len(result.created) == 1 and result.already_known == 1
    assert result.created[0].finding_key == "frist|15.03.2027|2"
    assert db_session.query(Deadline).filter_by(document_id=document.id).count() == 2
    assert _service().reanalyze_document(document, db_session, actor="x@kanzlei.test").created == []


def test_reanalysis_matches_legacy_rows_without_finding_key_without_writing(db_session: Session) -> None:
    """Altdaten (vor schritt3_030) ohne Schluessel: Abgleich zur Laufzeit aus `source_text`, kein Zurueckschreiben."""
    matter = _matter(db_session)
    document = _document(db_session, matter)
    legacy = _suggestion(db_session, matter, document_id=document.id, due_date=date(2027, 3, 15))
    assert legacy.finding_key is None

    result = _service().reanalyze_document(document, db_session, actor="x@kanzlei.test")

    assert result.created == [] and result.already_known == 1
    db_session.refresh(legacy)
    assert legacy.finding_key is None and legacy.original_source_text is None


def test_corrected_finding_is_shown_with_its_original(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (finding,) = _service().analyze_document(document, db_session)
    update_deadline(db_session, finding, source_text="Antwort an Gegenseite", due_date=date(2027, 3, 22),
                    priority=None, actor="ma@kanzlei.test")
    _login_role(db_session, client, "anwalt")

    page = client.get(f"/dashboard/tasks?selected={finding.id}&selected_type=deadline").text
    assert "Ursprünglicher Fund" in page and "15.03.2027 :: Bitte antworten Sie" in page


def test_role_change_takes_effect_on_the_next_request(client: TestClient, db_session: Session) -> None:
    from app.models import User

    deadline = _suggestion(db_session, _matter(db_session))
    csrf = _login_role(db_session, client, "anwalt")
    user = db_session.query(User).filter_by(email="anwalt@kanzlei.test").one()
    user.role_id = seed_roles(db_session)["mitarbeiter"].id  # Herabstufung waehrend der laufenden Sitzung
    db_session.commit()

    response = client.post(f"/dashboard/tasks/{deadline.id}/review",
                           data={"csrf_token": csrf, "status": "confirmed"}, follow_redirects=False)
    assert response.status_code == 403
    db_session.refresh(deadline)
    assert deadline.review_status == "unreviewed"


@pytest.mark.parametrize("path,data", [
    ("/dashboard/tasks/{id}/review", {"status": "confirmed"}),
    ("/dashboard/tasks/{id}/review", {"status": "rejected"}),
    ("/dashboard/tasks/{id}/defer", {}),
    ("/dashboard/tasks/deadline/{id}/update", {"source_text": "x", "due_date": "2027-01-01"}),
    ("/dashboard/tasks/deadline/{id}/status", {"status": "done"}),
    ("/dashboard/tasks/deadline/{id}/duplicate", {}),
    ("/dashboard/tasks/deadline/{id}/delete", {}),
])
def test_every_deadline_write_path_requires_csrf(client: TestClient, db_session: Session, path, data) -> None:
    deadline = _suggestion(db_session, _matter(db_session))
    _login_role(db_session, client, "anwalt")
    before = db_session.query(AuditEvent).count()

    for token in (None, "gefaelscht"):
        payload = dict(data) if token is None else {**data, "csrf_token": token}
        response = client.post(path.format(id=deadline.id), data=payload, follow_redirects=False)
        assert response.status_code in (400, 403, 422)

    db_session.refresh(deadline)
    assert (deadline.review_status, deadline.status) == ("unreviewed", "open")
    assert db_session.query(Deadline).count() == 1 and db_session.query(AuditEvent).count() == before


def test_manual_create_requires_csrf_and_login(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    anonymous = client.post(f"/dashboard/matters/{matter.id}/deadlines",
                            data={"csrf_token": "x", "source_text": "X", "due_date": "2027-05-01"},
                            follow_redirects=False)
    assert anonymous.status_code in (303, 401, 403) and db_session.query(Deadline).count() == 0
    _login_role(db_session, client, "anwalt")
    forged = client.post(f"/dashboard/matters/{matter.id}/deadlines",
                         data={"csrf_token": "gefaelscht", "source_text": "X", "due_date": "2027-05-01"},
                         follow_redirects=False)
    assert forged.status_code in (400, 403) and db_session.query(Deadline).count() == 0


def test_extra_form_fields_cannot_set_review_status(client: TestClient, db_session: Session) -> None:
    """Direkter Aufruf mit untergeschobenem Feld: Korrektur, Anlage und Kopie eines Mitarbeiters bleiben ungeprueft."""
    matter = _matter(db_session)
    deadline = _suggestion(db_session, matter)
    csrf = _login_role(db_session, client, "mitarbeiter")

    client.post(f"/dashboard/tasks/deadline/{deadline.id}/update",
                data={"csrf_token": csrf, "source_text": "korrigiert", "due_date": "2027-04-01",
                      "review_status": "confirmed"}, follow_redirects=False)
    client.post(f"/dashboard/matters/{matter.id}/deadlines",
                data={"csrf_token": csrf, "source_text": "neu", "due_date": "2027-04-02",
                      "review_status": "confirmed"}, follow_redirects=False)
    client.post(f"/dashboard/tasks/deadline/{deadline.id}/duplicate",
                data={"csrf_token": csrf, "review_status": "confirmed"}, follow_redirects=False)
    client.post(f"/dashboard/tasks/deadline/{deadline.id}/status",
                data={"csrf_token": csrf, "status": "confirmed"}, follow_redirects=False)

    assert {d.review_status for d in db_session.query(Deadline).all()} == {"unreviewed"}
    assert db_session.query(Deadline).count() == 3


def test_done_suggestion_is_still_unreviewed_everywhere(client: TestClient, db_session: Session) -> None:
    """"erledigt" ist nie "bestaetigt": nach dem Erledigt-Markieren weiter in der Pruefliste und im
    Erledigt-Reiter als ungeprueft gekennzeichnet."""
    matter = _matter(db_session)
    deadline = _suggestion(db_session, matter)
    csrf = _login_role(db_session, client, "mitarbeiter")
    client.post(f"/dashboard/tasks/deadline/{deadline.id}/status", data={"csrf_token": csrf, "status": "done"},
                follow_redirects=False)

    db_session.refresh(deadline)
    assert (deadline.status, deadline.review_status) == ("done", "unreviewed")
    review = client.get("/dashboard/tasks?review=1").text
    assert "Zu prüfen (1)" in review and deadline.id in review
    done_tab = client.get("/dashboard/tasks?status=done").text
    assert deadline.id in done_tab and "ungeprüft" in done_tab



def test_date_only_correction_shows_original_finding(client: TestClient, db_session: Session) -> None:
    """Gefunden im echten Klickablauf (QA §73.1): nach einer reinen Datumskorrektur fehlte der urspruengliche Fund."""
    matter = _matter(db_session)
    document = _document(db_session, matter)
    (finding,) = _service().analyze_document(document, db_session)
    update_deadline(db_session, finding, source_text=finding.source_text, due_date=date(2027, 3, 22),
                    priority=None, actor="ma@kanzlei.test")
    _login_role(db_session, client, "anwalt")

    page = client.get(f"/dashboard/tasks?selected={finding.id}&selected_type=deadline").text
    assert "Ursprünglicher Fund" in page and "(seither korrigiert, siehe Verlauf)" in page


def test_staff_sees_no_dead_delete_or_edit_buttons(client: TestClient, db_session: Session) -> None:
    """Gefunden im echten Klickablauf (QA §73.1): Mitarbeiter sahen "Löschen" (und bei bestaetigten Fristen
    "Bearbeiten"), der Klick endete in 403. Korrigieren eines ungeprueften Vorschlags bleibt sichtbar."""
    matter = _matter(db_session)
    suggestion = _suggestion(db_session, matter)
    confirmed = _suggestion(db_session, matter, review_status="confirmed")
    _login_role(db_session, client, "mitarbeiter")

    for deadline, editable in ((suggestion, True), (confirmed, False)):
        page = client.get(f"/dashboard/tasks?selected={deadline.id}&selected_type=deadline").text
        assert f"/dashboard/tasks/deadline/{deadline.id}/delete" not in page
        assert ("edit-deadline-modal').showModal()" in page) is editable
