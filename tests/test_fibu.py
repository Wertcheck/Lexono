"""Finanzbuchhaltung (MVP, 28.10.2026): Mandantentrennung, Buchungen (Erfassen/Aendern/Buchen/Storno, Audit),
Plausibilitaetspruefungen, Belege (Typ/Groesse/Pfad), Berechnungen (Netto/Steuer, Kennzahlen, USt, SuSa, BWA, DATEV-
Entwurf), Berechtigungen, Navigation und Migration. Nur synthetische Daten, isolierte Datenbanken."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.fibu import service as fs
from app.main import app
from app.models import AuditEvent, Client, FibuBooking
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles
from tests.test_web_chat import client, db_session  # noqa: F401 - Fixtures

PDF = b"%PDF-1.4\n% QA-SYNTH Beleg\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _clients(db: Session) -> tuple[Client, Client]:
    a, b = Client(name="QA Muster GmbH"), Client(name="QA Beispiel KG")
    db.add_all([a, b]); db.commit()
    fs.seed_example_accounts(db, a.id, actor="qa")
    fs.seed_example_accounts(db, b.id, actor="qa")
    return a, b


def _acc(db: Session, client_id: str) -> dict[str, str]:
    return {a.number: a.id for a in fs.list_accounts(db, client_id)}


def _data(db: Session, client_id: str, **over) -> dict:
    acc = _acc(db, client_id)
    data = {"booking_date": "2026-10-27", "receipt_number": "RE-1", "description": "Beratung", "direction": "income",
            "amount": "1.250,00", "tax_rate": "19", "account_id": acc["8400"], "contra_account_id": acc["1000"]}
    data.update(over)
    return data


# --- Berechnungen --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,cents", [("1.250,00", 125000), ("1250,5", 125050), ("1250.00", 125000), ("78,4", 7840),
                                       ("0,01", 1), (" 12 € ", 1200)])
def test_parse_amount_accepts_german_and_technical_notation(raw: str, cents: int) -> None:
    assert fs.parse_amount(raw) == cents


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "1,234", "1.25.00", "999999999999"])
def test_parse_amount_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(fs.FibuError):
        fs.parse_amount(raw)


def test_gross_split_rounds_commercially_and_adds_up() -> None:
    assert fs.split_gross(125000, 19) == (105042, 19958)
    assert fs.split_gross(7840, 19) == (6588, 1252)
    assert fs.split_gross(185000, 7) == (172897, 12103)
    assert fs.split_gross(21500, 0) == (21500, 0)
    for gross in range(1, 3000, 7):
        for rate in (0, 7, 19):
            net, tax = fs.split_gross(gross, rate)
            assert net + tax == gross and tax >= 0


def test_vat_due_date_is_the_tenth_after_the_quarter() -> None:
    assert fs.vat_due_date(2026, 1) == date(2026, 4, 10)
    assert fs.vat_due_date(2026, 4) == date(2027, 1, 10)


def test_kpis_vat_trial_balance_and_bwa_come_from_stored_bookings(db_session: Session) -> None:
    a, _b = _clients(db_session)
    acc = _acc(db_session, a.id)
    fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa", post=True)
    fs.create_booking(db_session, a.id, _data(db_session, a.id, receipt_number="BE-1", direction="expense",
                                              amount="78,40", account_id=acc["4930"]), actor="qa")
    cancelled = fs.create_booking(db_session, a.id, _data(db_session, a.id, receipt_number="BE-2", direction="expense",
                                                          amount="500,00", account_id=acc["4930"]), actor="qa")
    fs.cancel_booking(db_session, a.id, cancelled.id, reason="doppelt", actor="qa")
    fs.create_booking(db_session, a.id, _data(db_session, a.id, booking_date="2025-05-01", amount="1.000,00"),
                      actor="qa", post=True)

    k = fs.compute_kpis(db_session, a.id, 2026, today=date(2026, 10, 28))
    assert (k.revenue_gross, k.cost_gross, k.result_net) == (125000, 7840, 105042 - 6588)
    assert k.revenue_change == 25.0                         # 1.250 vs. 1.000 im Vorjahr
    assert (k.vat_quarter, k.vat_payable, k.vat_due) == (4, 19958 - 1252, date(2027, 1, 10))
    assert (k.booking_count, k.open_count) == (2, 1)        # Storno zaehlt nicht

    rows = fs.trial_balance(db_session, a.id, 2026)
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows) == 125000 + 7840
    bank = next(r for r in rows if r.number == "1000")
    assert (bank.debit, bank.credit) == (125000, 7840)
    bwa = fs.bwa_overview(db_session, a.id, 2026)
    assert (bwa["total_revenue"], bwa["total_expense"], bwa["result"]) == (105042, 6588, 98454)
    q4 = fs.vat_overview(db_session, a.id, 2026)[3]
    assert (q4["ust"], q4["vst"], q4["payable"], q4["count"]) == (19958, 1252, 18706, 2)


def test_datev_draft_contains_only_posted_bookings_and_is_audited(db_session: Session) -> None:
    a, _b = _clients(db_session)
    acc = _acc(db_session, a.id)
    fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa", post=True)
    fs.create_booking(db_session, a.id, _data(db_session, a.id, receipt_number="BE-OFFEN", direction="expense",
                                              amount="10,00", account_id=acc["4930"]), actor="qa")
    csv_text, count = fs.datev_draft_csv(db_session, a.id, 2026, actor="qa")
    lines = csv_text.strip().splitlines()
    assert count == 1 and len(lines) == 2
    assert lines[1] == "1250,00;S;EUR;1000;8400;;2710;RE-1;Beratung;19 %"
    assert "BE-OFFEN" not in csv_text
    assert db_session.query(AuditEvent).filter_by(event_type="fibu_datev_draft_exported").count() == 1
    fs.create_booking(db_session, a.id, _data(db_session, a.id, receipt_number="RE-2", description="=HYPERLINK(1)"),
                      actor="qa", post=True)
    csv_text, _count = fs.datev_draft_csv(db_session, a.id, 2026, actor="qa")
    assert ";'=HYPERLINK(1);" in csv_text                                  # keine Formel-Injection


# --- Buchungen: Validierung, Status, Audit ---------------------------------------------------------------------------


@pytest.mark.parametrize("over,field", [
    ({"receipt_number": ""}, "receipt_number"), ({"description": " "}, "description"),
    ({"description": "x" * 201}, "description"), ({"amount": "0"}, "amount"), ({"tax_rate": "16"}, "tax_rate"),
    ({"booking_date": "28.10.2026"}, "booking_date"), ({"direction": "x"}, "direction"),
    ({"account_id": ""}, "account_id"),
])
def test_booking_plausibility_checks(db_session: Session, over: dict, field: str) -> None:
    a, _b = _clients(db_session)
    with pytest.raises(fs.FibuError) as exc:
        fs.create_booking(db_session, a.id, _data(db_session, a.id, **over), actor="qa")
    assert exc.value.field == field


def test_account_assignment_must_fit_the_direction(db_session: Session) -> None:
    a, _b = _clients(db_session)
    acc = _acc(db_session, a.id)
    with pytest.raises(fs.FibuError, match="Erlöskonto"):
        fs.create_booking(db_session, a.id, _data(db_session, a.id, account_id=acc["4930"]), actor="qa")
    with pytest.raises(fs.FibuError, match="verschieden"):
        fs.create_booking(db_session, a.id, _data(db_session, a.id, contra_account_id=acc["8400"]), actor="qa")
    with pytest.raises(fs.FibuError, match="Gegenkonto passt nicht"):
        fs.create_booking(db_session, a.id, _data(db_session, a.id, direction="expense", account_id=acc["4930"],
                                                  contra_account_id=acc["1200"]), actor="qa")
    fs.set_account_active(db_session, a.id, acc["1000"], active=False, actor="qa")
    with pytest.raises(fs.FibuError, match="Deaktivierte"):
        fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa")


def test_edit_post_cancel_lifecycle_is_traceable(db_session: Session) -> None:
    a, _b = _clients(db_session)
    booking = fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="anwalt@kanzlei.test")
    fs.update_booking(db_session, a.id, booking.id, _data(db_session, a.id, amount="1.300,00", description="Beratung 2"),
                      actor="anwalt@kanzlei.test")
    assert (booking.gross_cents, booking.net_cents, booking.tax_cents) == (130000, 109244, 20756)
    fs.post_booking(db_session, a.id, booking.id, actor="anwalt@kanzlei.test")
    with pytest.raises(fs.FibuError, match="Nur offene"):
        fs.update_booking(db_session, a.id, booking.id, _data(db_session, a.id), actor="x")
    with pytest.raises(fs.FibuError, match="Stornogrund"):
        fs.cancel_booking(db_session, a.id, booking.id, reason=" ", actor="x")
    fs.cancel_booking(db_session, a.id, booking.id, reason="Fehlbuchung", actor="anwalt@kanzlei.test")
    assert booking.status == "cancelled" and booking.cancel_reason == "Fehlbuchung"
    with pytest.raises(fs.FibuError, match="bereits storniert"):
        fs.cancel_booking(db_session, a.id, booking.id, reason="nochmal", actor="x")
    events = [e.event_type for e in fs.booking_history(db_session, booking.id)]
    assert events == ["fibu_booking_created", "fibu_booking_updated", "fibu_booking_posted", "fibu_booking_cancelled"]
    update = fs.booking_history(db_session, booking.id)[1]
    assert "Brutto: 1.250,00 -> 1.300,00" in update.details and "Beschreibung: Beratung -> Beratung 2" in update.details
    assert db_session.get(FibuBooking, booking.id) is not None   # nichts geloescht


def test_archived_client_is_read_only(db_session: Session) -> None:
    a, _b = _clients(db_session)
    a.status = "archived"; db_session.commit()
    with pytest.raises(fs.FibuError, match="archiviert"):
        fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa")


# --- Mandantentrennung ------------------------------------------------------------------------------------------------


def test_client_isolation_in_service(db_session: Session, tmp_path: Path) -> None:
    a, b = _clients(db_session)
    acc_b = _acc(db_session, b.id)
    booking = fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa", post=True)
    receipt = fs.store_receipt(db_session, a.id, filename="beleg.pdf", content=PDF, storage_dir=tmp_path, actor="qa")
    for call in (lambda: fs.get_booking(db_session, b.id, booking.id),
                 lambda: fs.get_receipt(db_session, b.id, receipt.id),
                 lambda: fs.update_booking(db_session, b.id, booking.id, _data(db_session, b.id), actor="x"),
                 lambda: fs.cancel_booking(db_session, b.id, booking.id, reason="x", actor="x"),
                 lambda: fs.account_ledger(db_session, b.id, 2026, _acc(db_session, a.id)["1000"])):
        with pytest.raises(fs.FibuNotFound):
            call()
    with pytest.raises(fs.FibuError, match="dieses Mandanten"):
        fs.create_booking(db_session, a.id, _data(db_session, a.id, account_id=acc_b["8400"]), actor="qa")
    with pytest.raises(fs.FibuError, match="gehört nicht"):
        fs.create_booking(db_session, b.id, _data(db_session, b.id, receipt_id=receipt.id), actor="qa")
    assert fs.list_bookings(db_session, b.id, 2026) == []
    assert fs.compute_kpis(db_session, b.id, 2026).revenue_gross == 0
    assert fs.trial_balance(db_session, b.id, 2026) == [] and fs.list_receipts(db_session, b.id) == []


# --- Belege -----------------------------------------------------------------------------------------------------------


def test_receipts_are_type_checked_and_stored_without_the_original_name_in_the_path(db_session: Session,
                                                                                     tmp_path: Path) -> None:
    a, _b = _clients(db_session)
    receipt = fs.store_receipt(db_session, a.id, filename="..\\..\\boese/../QA-Rechnung.pdf", content=PDF,
                               storage_dir=tmp_path, actor="qa")
    stored = Path(receipt.file_path)
    assert stored.parent == tmp_path / a.id and stored.suffix == ".pdf" and "Rechnung" not in stored.name
    assert stored.read_bytes() == PDF and receipt.original_filename == "QA-Rechnung.pdf"
    assert fs.store_receipt(db_session, a.id, filename="foto.PNG", content=PNG, storage_dir=tmp_path,
                            actor="qa").mime_type == "image/png"
    for name, content, msg in (("x.exe", b"MZ", "nur PDF"), ("x.pdf", b"kein pdf", "keine gültige"),
                               ("x.pdf", b"", "leer"), ("x.png", PDF, "keine gültige")):
        with pytest.raises(fs.FibuError, match=msg):
            fs.store_receipt(db_session, a.id, filename=name, content=content, storage_dir=tmp_path, actor="qa")
    with pytest.raises(fs.FibuError, match="größer"):
        fs.store_receipt(db_session, a.id, filename="gross.pdf", content=b"%PDF-" + b"0" * fs.MAX_RECEIPT_BYTES,
                         storage_dir=tmp_path, actor="qa")


# --- Web: Seite, Rechte, Isolation ------------------------------------------------------------------------------------


@pytest.fixture()
def web(client: TestClient, db_session: Session, tmp_path: Path):  # noqa: F811
    test_settings = get_settings().model_copy(update={"fibu_receipt_storage_dir": str(tmp_path / "fibu")})
    app.dependency_overrides[get_settings] = lambda: test_settings
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    a, b = _clients(db_session)
    login(client, "anwalt@kanzlei.test")
    yield client, a, b
    app.dependency_overrides.pop(get_settings, None)


def _token(client: TestClient) -> str:
    return extract_csrf(client.get("/dashboard/chat").text)


def test_dashboard_renders_real_values_navigation_and_tabs(web, db_session: Session) -> None:
    client_, a, b = web
    fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa", post=True)
    html = client_.get(f"/dashboard/fibu?client={a.id}&year=2026").text
    assert 'sidebar__group-label">Finanzbuchhaltung<' in html and "sidebar__new-badge" in html
    import re
    assert re.search(r'sidebar__group-summary--active"\s+href="/dashboard/fibu"', html)
    for label in ("Übersicht", "Buchen", "Belege", "Konten", "USt-Voranmeldung", "Auswertungen", "Einstellungen"):
        assert f">{label}</a>" in html
    assert 'aria-disabled="true" title="Noch nicht verfügbar' in html            # Jahresabschluss: kein Fake-Link
    assert "1.250,00 €" in html and "RE-1" in html and "Belege hochladen" in html
    other = client_.get(f"/dashboard/fibu?client={b.id}&year=2026").text
    assert "RE-1" not in other and 'class="fibu-kpi__value">0,00 €' in other     # Mandantenwechsel aktualisiert
    for tab in ("buchen", "belege", "konten", "ust", "einstellungen"):
        assert client_.get(f"/dashboard/fibu?client={a.id}&year=2026&tab={tab}").status_code == 200
    for report in ("bwa", "susa", "konto", "op", "datev"):
        assert client_.get(f"/dashboard/fibu?client={a.id}&year=2026&tab=auswertungen&report={report}").status_code == 200


def test_no_demo_numbers_without_bookings(web) -> None:
    client_, a, _b = web
    html = client_.get(f"/dashboard/fibu?client={a.id}&year=2026").text
    assert html.count('class="fibu-kpi__value">0,00 €') == 4 and "125.430,50" not in html
    assert "Für 2026 sind noch keine Buchungen erfasst." in html


def test_create_cancel_and_upload_via_web(web, db_session: Session) -> None:
    client_, a, _b = web
    token = _token(client_)
    resp = client_.post("/dashboard/fibu/bookings", data={"csrf_token": token, "client_id": a.id, "year": "2026",
                                                          "action": "save_post", **_data(db_session, a.id)},
                        follow_redirects=False)
    assert resp.status_code == 303 and "ok=booking_posted" in resp.headers["location"]
    booking = db_session.query(FibuBooking).one()
    assert booking.status == "posted" and booking.created_by == "anwalt@kanzlei.test"
    bad = client_.post("/dashboard/fibu/bookings", data={"csrf_token": token, "client_id": a.id, "year": "2026",
                                                         **_data(db_session, a.id, receipt_number="")})
    assert bad.status_code == 400 and "Bitte eine Belegnummer eingeben." in bad.text and 'value="Beratung"' in bad.text
    resp = client_.post(f"/dashboard/fibu/bookings/{booking.id}/cancel",
                        data={"csrf_token": token, "client_id": a.id, "year": "2026", "reason": "Fehler"},
                        follow_redirects=False)
    assert resp.status_code == 303 and db_session.get(FibuBooking, booking.id).status == "cancelled"
    resp = client_.post("/dashboard/fibu/receipts", data={"csrf_token": token, "client_id": a.id, "year": "2026"},
                        files=[("files", ("QA-1.pdf", PDF, "application/pdf")), ("files", ("QA-2.png", PNG, "image/png"))],
                        follow_redirects=False)
    assert resp.status_code == 303
    receipts = fs.list_receipts(db_session, a.id)
    assert len(receipts) == 2
    file_resp = client_.get(f"/dashboard/fibu/receipts/{receipts[0].id}/file?client={a.id}&download=1")
    assert file_resp.status_code == 200 and "attachment" in file_resp.headers["content-disposition"]
    bad = client_.post("/dashboard/fibu/receipts", data={"csrf_token": token, "client_id": a.id, "year": "2026"},
                       files=[("files", ("QA.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 400 and "nur PDF, JPG und PNG" in bad.text


def test_cross_client_access_via_web_is_404(web, db_session: Session) -> None:
    client_, a, b = web
    booking = fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa")
    token = _token(client_)
    assert client_.get(f"/dashboard/fibu?client={b.id}&tab=buchen&edit={booking.id}").status_code == 404
    for path in (f"/dashboard/fibu/bookings/{booking.id}/post", f"/dashboard/fibu/bookings/{booking.id}/cancel"):
        assert client_.post(path, data={"csrf_token": token, "client_id": b.id, "reason": "x"}).status_code == 404
    assert client_.post(f"/dashboard/fibu/bookings/{booking.id}/update",
                        data={"csrf_token": token, "client_id": b.id, **_data(db_session, b.id)}).status_code == 404
    assert db_session.get(FibuBooking, booking.id).status == "open"
    assert client_.get("/dashboard/fibu?client=00000000-0000-0000-0000-000000000000").status_code == 404


def test_staff_reads_but_cannot_change_and_csrf_is_required(web, db_session: Session) -> None:
    client_, a, _b = web
    fs.create_booking(db_session, a.id, _data(db_session, a.id), actor="qa", post=True)
    token = _token(client_)
    assert client_.post("/dashboard/fibu/tasks", data={"csrf_token": "falsch", "client_id": a.id, "title": "x"}).status_code == 403
    client_.post("/dashboard/logout", data={"csrf_token": token})
    login(client_, "mitarbeiter@kanzlei.test")
    html = client_.get(f"/dashboard/fibu?client={a.id}&year=2026").text
    assert "1.250,00 €" in html and "Belege hochladen</span>" not in html and "fibu-upload-form" not in html
    token = _token(client_)
    for path, data in (("/dashboard/fibu/bookings", _data(db_session, a.id)), ("/dashboard/fibu/accounts/seed", {}),
                       ("/dashboard/fibu/tasks", {"title": "x"}), ("/dashboard/fibu/profile", {"legal_form": "AG"})):
        assert client_.post(path, data={"csrf_token": token, "client_id": a.id, **data}).status_code == 403
    assert client_.get(f"/dashboard/fibu/export/datev-entwurf.csv?client={a.id}&year=2026").status_code == 403
    assert db_session.query(FibuBooking).count() == 1


def test_anonymous_access_redirects_to_login(client: TestClient) -> None:  # noqa: F811
    resp = client.get("/dashboard/fibu", follow_redirects=False)
    assert resp.status_code in (303, 307) and "/dashboard/login" in resp.headers["location"]


# --- Migration --------------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_migration_creates_only_new_tables(tmp_path: Path) -> None:
    from alembic import command

    from tests.test_migrations import _alembic_config_for

    db_file = tmp_path / "fibu.db"
    cfg = _alembic_config_for(db_file)
    command.upgrade(cfg, "schritt3_039")
    con = sqlite3.connect(db_file)
    before = {r[0]: r[1] for r in con.execute("select name, sql from sqlite_master where type='table'")}
    con.close()
    command.upgrade(cfg, "schritt3_040")
    con = sqlite3.connect(db_file)
    after = {r[0]: r[1] for r in con.execute("select name, sql from sqlite_master where type='table'")}
    assert set(after) - set(before) == {"fibu_client_profiles", "fibu_accounts", "fibu_receipts", "fibu_bookings",
                                        "fibu_tasks"}
    assert all(after[name] == sql for name, sql in before.items())          # bestehende Tabellen unveraendert
    assert con.execute("select count(*) from fibu_accounts").fetchone()[0] == 0  # nichts vorbelegt
    con.close()
    command.downgrade(cfg, "schritt3_039")
    con = sqlite3.connect(db_file)
    assert not {r[0] for r in con.execute("select name from sqlite_master where name like 'fibu_%'")}
    con.close()
