"""Mitarbeiterrechte und Vier-Augen-Prinzip in den Zusatzmodulen (28.10.2026, Owner-Entscheidung).

- Finanzen: Mitarbeiter erfassen/bearbeiten Buchungen und Belege, aber nur fuer zugewiesene Mandanten; buchen,
  stornieren, Konten, Stammdaten, DATEV und Zugriffsverwaltung nur Admin/Anwalt.
- Steuern: Mitarbeiter bereiten vor (zugewiesene Mandanten); freigeben nur Admin/Anwalt UND nie, wer erstellt oder
  bearbeitet hat; uebermitteln nie durch Mitarbeiter (und derzeit ueberhaupt nicht - kein ERiC).
- deny by default, Mandantentrennung, Audit. Nur synthetische Daten.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.permissions import (
    PERM_FIBU_CAPTURE,
    PERM_FIBU_MANAGE,
    PERM_TAX_APPROVE,
    PERM_TAX_PREPARE,
    PERM_TAX_SUBMIT,
    PERMISSION_MATRIX,
    PermissionDeniedError,
)
from app.config.settings import get_settings
from app.fibu import service as fs
from app.main import app
from app.models import AuditEvent, Client, FibuBooking, User
from app.modules.client_access import AccessChangeError, accessible_client_ids, can_access_client, set_staff_access
from app.tax import service as tax
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles
from tests.module_license_utils import LicenseFactory
from tests.test_web_chat import client, db_session  # noqa: F401 - Fixtures


def test_permission_matrix_per_role() -> None:
    staff, lawyer, admin = PERMISSION_MATRIX["mitarbeiter"], PERMISSION_MATRIX["anwalt"], PERMISSION_MATRIX["admin"]
    assert {PERM_FIBU_CAPTURE, PERM_TAX_PREPARE} <= staff
    assert not ({PERM_FIBU_MANAGE, PERM_TAX_APPROVE, PERM_TAX_SUBMIT} & staff)        # nie endgueltig/uebermitteln
    for role in (lawyer, admin):
        assert {PERM_FIBU_CAPTURE, PERM_FIBU_MANAGE, PERM_TAX_PREPARE, PERM_TAX_APPROVE, PERM_TAX_SUBMIT} <= role


@pytest.fixture()
def env(db_session: Session, monkeypatch, tmp_path: Path):  # noqa: F811
    roles = seed_roles(db_session)
    people = {name: create_test_user(db_session, roles[role], f"{name}@kanzlei.test") for name, role in (
        ("admin", "admin"), ("anwalt", "anwalt"), ("anwalt2", "anwalt"), ("ma", "mitarbeiter"), ("ma2", "mitarbeiter"))}
    a, b = Client(name="QA Muster GmbH"), Client(name="QA Andere KG")
    db_session.add_all([a, b]); db_session.commit()
    fs.seed_example_accounts(db_session, a.id, actor="qa"); fs.seed_example_accounts(db_session, b.id, actor="qa")
    LicenseFactory(monkeypatch).activate(db_session, ("finanzen", "steuern"))
    test_settings = get_settings().model_copy(update={"fibu_receipt_storage_dir": str(tmp_path / "fibu")})
    app.dependency_overrides[get_settings] = lambda: test_settings
    yield people, a, b
    app.dependency_overrides.pop(get_settings, None)


# --- Mandantenzuweisung --------------------------------------------------------------------------------------------


def test_deny_by_default_and_assignment_management_with_audit(db_session: Session, env) -> None:
    people, a, b = env
    assert not can_access_client(db_session, people["ma"], a.id) and accessible_client_ids(db_session, people["ma"]) == set()
    assert can_access_client(db_session, people["anwalt"], a.id) and accessible_client_ids(db_session, people["admin"]) is None
    with pytest.raises(AccessChangeError, match="Keine Berechtigung"):
        set_staff_access(db_session, a.id, {people["ma"].id}, actor=people["ma2"])           # Mitarbeiter verwaltet nicht
    with pytest.raises(AccessChangeError, match="Zuweisbar"):
        set_staff_access(db_session, a.id, {people["anwalt2"].id}, actor=people["admin"])   # Anwalt braucht keine
    added, removed = set_staff_access(db_session, a.id, {people["ma"].id}, actor=people["anwalt"])
    assert added == {people["ma"].id} and not removed
    assert can_access_client(db_session, people["ma"], a.id) and not can_access_client(db_session, people["ma"], b.id)
    assert not can_access_client(db_session, people["ma2"], a.id)
    set_staff_access(db_session, a.id, set(), actor=people["admin"])                          # entziehen
    assert not can_access_client(db_session, people["ma"], a.id)
    people["ma"].is_active = False; db_session.commit()
    events = [e.details for e in db_session.query(AuditEvent).filter_by(event_type="client_access_changed")]
    assert any("zugewiesen: ma@kanzlei.test" in d for d in events) and any("entzogen: ma@kanzlei.test" in d for d in events)


# --- Finanzen (Web, serverseitig) -------------------------------------------------------------------------------------


def _booking(db: Session, client_id: str, **over) -> dict:
    acc = {x.number: x.id for x in fs.list_accounts(db, client_id)}
    data = {"booking_date": "2026-10-27", "receipt_number": "BE-MA-1", "description": "Büromaterial", "direction": "expense",
            "amount": "78,40", "tax_rate": "19", "account_id": acc["4930"], "contra_account_id": acc["1000"]}
    data.update(over)
    return data


def test_staff_fibu_rights_end_to_end(client: TestClient, db_session: Session, env) -> None:  # noqa: F811
    people, a, b = env
    set_staff_access(db_session, a.id, {people["ma"].id}, actor=people["admin"])
    login(client, "ma@kanzlei.test")
    page = client.get("/dashboard/fibu?year=2026").text
    assert "QA Muster GmbH" in page and "QA Andere KG" not in page                  # Mandantenauswahl gefiltert
    token = extract_csrf(client.get("/dashboard/chat").text)
    # erfassen (offen) und bearbeiten: erlaubt
    resp = client.post("/dashboard/fibu/bookings", data={"csrf_token": token, "client_id": a.id, "year": "2026",
                                                         "action": "save", **_booking(db_session, a.id)}, follow_redirects=False)
    assert resp.status_code == 303
    booking = db_session.query(FibuBooking).filter_by(client_id=a.id).one()
    assert booking.status == "open" and booking.created_by == "ma@kanzlei.test"
    resp = client.post(f"/dashboard/fibu/bookings/{booking.id}/update",
                       data={"csrf_token": token, "client_id": a.id, "year": "2026",
                             **_booking(db_session, a.id, amount="80,00")}, follow_redirects=False)
    assert resp.status_code == 303 and db_session.get(FibuBooking, booking.id).gross_cents == 8000
    resp = client.post("/dashboard/fibu/receipts", data={"csrf_token": token, "client_id": a.id, "year": "2026"},
                       files=[("files", ("QA-SYNTH.pdf", b"%PDF-1.4 QA", "application/pdf"))], follow_redirects=False)
    assert resp.status_code == 303 and len(fs.list_receipts(db_session, a.id)) == 1
    assert client.post("/dashboard/fibu/tasks", data={"csrf_token": token, "client_id": a.id, "title": "Belege prüfen"},
                       follow_redirects=False).status_code == 303
    # endgueltig/verwaltend: verboten
    for path, data in ((f"/dashboard/fibu/bookings/{booking.id}/post", {}),
                       (f"/dashboard/fibu/bookings/{booking.id}/cancel", {"reason": "x"}),
                       ("/dashboard/fibu/bookings", {**_booking(db_session, a.id, receipt_number="BE-MA-2"), "action": "save_post"}),
                       ("/dashboard/fibu/accounts", {"number": "4999", "name": "x", "kind": "expense"}),
                       ("/dashboard/fibu/profile", {"legal_form": "AG"}),
                       ("/dashboard/fibu/access", {"user_ids": people["ma2"].id})):
        assert client.post(path, data={"csrf_token": token, "client_id": a.id, "year": "2026", **data}).status_code == 403, path
    assert client.get(f"/dashboard/fibu/export/datev-entwurf.csv?client={a.id}").status_code == 403
    assert db_session.get(FibuBooking, booking.id).status == "open"
    assert db_session.query(FibuBooking).filter_by(receipt_number="BE-MA-2").count() == 0
    # fremder Mandant: nicht vorhanden
    assert client.get(f"/dashboard/fibu?client={b.id}").status_code == 404
    assert client.post("/dashboard/fibu/bookings", data={"csrf_token": token, "client_id": b.id, "year": "2026",
                                                         "action": "save", **_booking(db_session, b.id)}).status_code == 404
    receipt = fs.list_receipts(db_session, a.id)[0]
    assert client.get(f"/dashboard/fibu/receipts/{receipt.id}/file?client={b.id}").status_code == 404
    # Audit
    events = {e.event_type: e.actor for e in db_session.query(AuditEvent).filter_by(entity_type="FibuBooking")}
    assert events.get("fibu_booking_created") == "ma@kanzlei.test" and events.get("fibu_booking_updated") == "ma@kanzlei.test"


def test_unassigned_staff_sees_no_client_and_cannot_write(client: TestClient, db_session: Session, env) -> None:  # noqa: F811
    people, a, _b = env
    login(client, "ma2@kanzlei.test")
    page = client.get("/dashboard/fibu").text
    assert "QA Muster GmbH" not in page and "Noch kein aktiver Mandant" in page
    token = extract_csrf(client.get("/dashboard/chat").text)
    assert client.post("/dashboard/fibu/bookings", data={"csrf_token": token, "client_id": a.id, "year": "2026",
                                                         "action": "save", **_booking(db_session, a.id)}).status_code == 404
    assert db_session.query(FibuBooking).count() == 0


def test_lawyer_manages_staff_access_via_settings_tab(client: TestClient, db_session: Session, env) -> None:  # noqa: F811
    people, a, _b = env
    login(client, "anwalt@kanzlei.test")
    page = client.get(f"/dashboard/fibu?client={a.id}&tab=einstellungen").text
    assert "Mitarbeiterzugriff" in page and "ma@kanzlei.test" in page and "anwalt2@kanzlei.test" not in page
    token = extract_csrf(page)
    resp = client.post("/dashboard/fibu/access", data={"csrf_token": token, "client_id": a.id, "year": "2026",
                                                       "user_ids": [people["ma"].id, people["ma2"].id]}, follow_redirects=False)
    assert resp.status_code == 303 and "ok=access_saved" in resp.headers["location"]
    assert can_access_client(db_session, people["ma2"], a.id)
    bad = client.post("/dashboard/fibu/access", data={"csrf_token": token, "client_id": a.id, "year": "2026",
                                                      "user_ids": people["admin"].id})
    assert bad.status_code == 400 and "Zuweisbar" in bad.text


# --- Steuern: Vier-Augen-Prinzip ---------------------------------------------------------------------------------------


def _prepared(db: Session, people: dict, client_id: str, preparer: str = "ma"):
    f = tax.create_filing(db, people[preparer], client_id, tax_type="est", year=2025, period_kind="year")
    part = tax.add_part(db, people[preparer], client_id, f.id, part_code="hauptvordruck", label="Hauptvordruck")
    tax.set_value(db, people[preparer], client_id, part.id, field_key="qa.einkuenfte", value="5000000",
                  value_type="amount_cents")
    tax.transition(db, people[preparer], client_id, f.id, "in_review")
    return f, part


def test_staff_prepares_second_person_approves(db_session: Session, env) -> None:
    people, a, _b = env
    set_staff_access(db_session, a.id, {people["ma"].id}, actor=people["admin"])
    filing, _part = _prepared(db_session, people, a.id)
    with pytest.raises(PermissionDeniedError):                                       # Mitarbeiter gibt nie frei
        tax.transition(db_session, people["ma"], a.id, filing.id, "approved")
    with pytest.raises(PermissionDeniedError):
        tax.submit_filing(db_session, people["ma"], a.id, filing.id)
    tax.transition(db_session, people["anwalt"], a.id, filing.id, "approved")        # zweite, berechtigte Person
    assert filing.status == "approved" and filing.approved_by == "anwalt@kanzlei.test"
    assert filing.created_by == "ma@kanzlei.test" != filing.approved_by
    with pytest.raises(tax.SubmissionNotAvailable):
        tax.submit_filing(db_session, people["anwalt"], a.id, filing.id)


@pytest.mark.parametrize("preparer", ["anwalt", "admin"])
def test_self_approval_is_refused_and_audited(db_session: Session, env, preparer: str) -> None:
    people, a, _b = env
    filing, _part = _prepared(db_session, people, a.id, preparer=preparer)
    with pytest.raises(tax.TaxError, match="Vier-Augen-Prinzip"):
        tax.transition(db_session, people[preparer], a.id, filing.id, "approved")
    assert filing.status == "in_review" and filing.approved_by is None
    refused = db_session.query(AuditEvent).filter_by(entity_type="TaxFiling", entity_id=filing.id,
                                                     event_type="tax_approval_refused").one()
    assert refused.actor == f"{preparer}@kanzlei.test"
    other = "anwalt2" if preparer == "anwalt" else "anwalt"
    tax.transition(db_session, people[other], a.id, filing.id, "approved")
    assert filing.approved_by == f"{other}@kanzlei.test"


def test_whoever_edited_values_may_not_approve(db_session: Session, env) -> None:
    people, a, _b = env
    set_staff_access(db_session, a.id, {people["ma"].id}, actor=people["admin"])
    filing, part = _prepared(db_session, people, a.id)
    tax.transition(db_session, people["anwalt"], a.id, filing.id, "draft", reason="Wert ergänzen")
    tax.set_value(db_session, people["anwalt"], a.id, part.id, field_key="qa.werbungskosten", value="100000",
                  value_type="amount_cents")                                          # Anwalt bearbeitet mit
    tax.transition(db_session, people["ma"], a.id, filing.id, "in_review")
    with pytest.raises(tax.TaxError, match="Vier-Augen-Prinzip"):
        tax.transition(db_session, people["anwalt"], a.id, filing.id, "approved")
    tax.transition(db_session, people["admin"], a.id, filing.id, "approved")
    assert filing.approved_by == "admin@kanzlei.test"
    assert tax.preparers(db_session, filing) == {"ma@kanzlei.test", "anwalt@kanzlei.test"}


def test_tax_client_separation_for_staff(db_session: Session, env) -> None:
    people, a, b = env
    set_staff_access(db_session, a.id, {people["ma"].id}, actor=people["admin"])
    filing_b = tax.create_filing(db_session, people["anwalt"], b.id, tax_type="ust", year=2025, period_kind="year")
    for call in (lambda: tax.create_filing(db_session, people["ma"], b.id, tax_type="est", year=2025, period_kind="year"),
                 lambda: tax.get_filing(db_session, people["ma"], b.id, filing_b.id),
                 lambda: tax.list_filings(db_session, people["ma"], b.id),
                 lambda: tax.get_filing(db_session, people["ma"], a.id, filing_b.id)):
        with pytest.raises(tax.TaxNotFound):
            call()
    assert tax.list_filings(db_session, people["ma"], a.id) == []
