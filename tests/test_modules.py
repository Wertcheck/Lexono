"""Modularisierung (28.10.2026): signierte Modul-Lizenzen (fail-closed), serverseitige Modulsperre, Navigation fuer
allgemeine Kanzleien, Lizenz-Import, Lexono-Steuern-Datenmodell (Rechte, Statusmodell, Mandantentrennung, keine
Uebermittlung) und Migration. Nur synthetische Daten, Wegwerf-Schluessel zur Laufzeit."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.permissions import PermissionDeniedError
from app.models import AuditEvent, Client, Matter, TaxFieldValue, TaxSubmission
from app.modules import licensing
from app.modules.catalog import BASE_MODULES, CATALOG
from app.tax import service as tax
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles
from tests.module_license_utils import LicenseFactory
from tests.test_web_chat import client, db_session  # noqa: F401 - Fixtures


# --- Lizenzpruefung --------------------------------------------------------------------------------------------------


def test_valid_license_unlocks_exactly_the_listed_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    f = LicenseFactory(monkeypatch)
    status = licensing.verify_license_text(f.text(("finanzen",)))
    assert status.valid and status.modules == {"finanzen"} and status.licensee == "QA Kanzlei (synthetisch)"
    both = licensing.verify_license_text(f.text(("finanzen", "steuern")))
    assert both.valid and both.modules == {"finanzen", "steuern"}


def test_no_trusted_key_means_nothing_unlocks(monkeypatch: pytest.MonkeyPatch) -> None:
    f = LicenseFactory(monkeypatch, trusted=False)
    assert licensing.TRUSTED_PUBLIC_KEYS == {}            # Auslieferungszustand: kein Herstellerschluessel
    status = licensing.verify_license_text(f.text())
    assert not status.valid and "Herstellerschlüssel" in status.reason


def _tamper(text: str, **changes) -> str:
    env = json.loads(text)
    payload = json.loads(base64.urlsafe_b64decode(env["payload"] + "=" * (-len(env["payload"]) % 4)))
    payload.update(changes)
    env["payload"] = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return json.dumps(env)


@pytest.mark.parametrize("mutate,reason", [
    (lambda t, f: _tamper(t, modules=["finanzen", "steuern"]), "Signatur"),          # Module nachtraeglich ergaenzt
    (lambda t, f: _tamper(t, valid_until="2099-12-31"), "Signatur"),                 # Laufzeit verlaengert
    (lambda t, f: json.dumps({**json.loads(t), "signature": "AAAA"}), "Signatur"),
    (lambda t, f: json.dumps({**json.loads(t), "key_id": "0000000000000000"}), "Herstellerschlüssel"),
    (lambda t, f: json.dumps({**json.loads(t), "format": 2}), "Lizenzformat"),
    (lambda t, f: "kein json", "beschädigt"),
    (lambda t, f: "", "leer"),
    (lambda t, f: "x" * (licensing.MAX_LICENSE_BYTES + 1), "zu groß"),
])
def test_tampered_or_broken_licenses_are_rejected(monkeypatch: pytest.MonkeyPatch, mutate, reason: str) -> None:
    f = LicenseFactory(monkeypatch)
    status = licensing.verify_license_text(mutate(f.text(("finanzen",)), f))
    assert not status.valid and reason in status.reason and not status.modules


def test_foreign_signing_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    trusted = LicenseFactory(monkeypatch)
    rogue = LicenseFactory(monkeypatch, trusted=False)
    forged = json.loads(rogue.text(("finanzen", "steuern")))
    forged["key_id"] = trusted.key_id                    # fremder Schluessel gibt sich als vertrauenswuerdig aus
    assert "Signatur" in licensing.verify_license_text(json.dumps(forged)).reason


def test_validity_period_and_unknown_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    f = LicenseFactory(monkeypatch)
    expired = f.text(valid_from=date.today() - timedelta(days=30), valid_until=date.today() - timedelta(days=1))
    assert "abgelaufen" in licensing.verify_license_text(expired).reason
    future = f.text(valid_from=date.today() + timedelta(days=3))
    assert "erst ab" in licensing.verify_license_text(future).reason
    assert licensing.verify_license_text(f.text(days=None)).valid               # unbefristet
    from scripts.lexono_license_tool import build_license
    with pytest.raises(SystemExit):
        build_license(f.key, licensee="x", modules=["kanzlei"], valid_from=date.today(), valid_until=None)


def test_import_replace_remove_and_audit(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    f = LicenseFactory(monkeypatch)
    assert licensing.enabled_modules(db_session) == BASE_MODULES
    with pytest.raises(licensing.LicenseImportError):
        licensing.import_license(db_session, "kaputt", actor="admin@kanzlei.test")
    f.activate(db_session, ("finanzen",))
    assert licensing.enabled_modules(db_session) == BASE_MODULES | {"finanzen"}
    f.activate(db_session, ("finanzen", "steuern"))
    assert licensing.enabled_modules(db_session) == BASE_MODULES | {"finanzen", "steuern"}
    assert licensing.remove_license(db_session, actor="admin@kanzlei.test")
    assert licensing.enabled_modules(db_session) == BASE_MODULES
    rows = db_session.query(licensing.ModuleLicense).all()
    assert len(rows) == 2 and not any(r.is_active for r in rows)               # nichts geloescht
    events = [e.event_type for e in db_session.query(AuditEvent).filter_by(entity_type="ModuleLicense")]
    assert events.count("module_license_imported") == 2 and "module_license_removed" in events


def test_license_stored_in_db_is_reverified_on_every_check(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    f = LicenseFactory(monkeypatch)
    f.activate(db_session, ("finanzen",), valid_until=date.today() + timedelta(days=1))
    assert licensing.is_enabled(db_session, "finanzen")
    assert "finanzen" not in licensing.enabled_modules(db_session, today=date.today() + timedelta(days=2))
    row = licensing.active_license(db_session)
    row.license_text = _tamper(row.license_text, modules=["finanzen", "steuern"])  # Manipulation direkt in der DB
    db_session.commit()
    assert licensing.enabled_modules(db_session) == BASE_MODULES


def test_license_tool_refuses_to_write_private_key_into_repository(tmp_path: Path, monkeypatch) -> None:
    import sys
    from scripts import lexono_license_tool as tool
    repo_dir = Path(tool.__file__).resolve().parents[1] / "qa-should-not-exist"
    monkeypatch.setattr(sys, "argv", ["tool", "keygen", "--out-dir", str(repo_dir)])
    with pytest.raises(SystemExit, match="nicht im Repository"):
        tool.main()
    assert not repo_dir.exists()
    monkeypatch.setattr(sys, "argv", ["tool", "keygen", "--out-dir", str(tmp_path / "keys")])
    tool.main()
    assert (tmp_path / "keys" / "lexono_license_private.pem").exists()


# --- Web: Basispaket ohne Zusatzmodule, Sperre, Navigation, Lizenz-Import ------------------------------------------------


@pytest.fixture()
def users(client: TestClient, db_session: Session):  # noqa: F811
    roles = seed_roles(db_session)
    for role in ("admin", "anwalt", "mitarbeiter"):
        create_test_user(db_session, roles[role], f"{role}@kanzlei.test")
    c = Client(name="QA Muster GmbH")
    db_session.add(c); db_session.commit()
    return client, c


def _nav_has_fibu(html: str) -> bool:
    return 'href="/dashboard/fibu"' in html


@pytest.mark.parametrize("role", ["admin", "anwalt", "mitarbeiter"])
def test_base_package_works_and_fibu_is_locked_without_license(users, role: str) -> None:
    client_, c = users
    login(client_, f"{role}@kanzlei.test")
    for path in ("/dashboard/chat", "/dashboard/clients", "/dashboard/matters", "/dashboard/tasks"):
        resp = client_.get(path)
        assert resp.status_code == 200 and not _nav_has_fibu(resp.text), path
    locked = client_.get(f"/dashboard/fibu?client={c.id}")
    assert locked.status_code == 403 and "Lexono Finanzen ist nicht freigeschaltet" in locked.text
    assert ("Lizenz in den Einstellungen importieren" in locked.text) == (role == "admin")
    token = extract_csrf(client_.get("/dashboard/chat").text)
    for path in ("/dashboard/fibu/accounts/seed", "/dashboard/fibu/tasks"):
        assert client_.post(path, data={"csrf_token": token, "client_id": c.id, "title": "x"}).status_code == 403
    assert client_.get(f"/dashboard/fibu/export/datev-entwurf.csv?client={c.id}").status_code == 403


def test_locked_page_renders_with_per_request_sessions_like_production(users, db_session: Session) -> None:
    """Fund aus der QA am laufenden Server (28.10.2026): dort ist die DB-Sitzung beim Fehlerhandler schon geschlossen,
    die Hinweisseite lieferte 500 (DetachedInstanceError). Hier: echte, nach jeder Anfrage geschlossene Sitzungen."""
    from sqlalchemy.orm import sessionmaker

    from app.db.session import get_db
    from app.main import app as fastapi_app

    client_, c = users
    factory = sessionmaker(bind=db_session.get_bind())

    def per_request():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    fastapi_app.dependency_overrides[get_db] = per_request
    login(client_, "admin@kanzlei.test")
    resp = client_.get(f"/dashboard/fibu?client={c.id}")
    assert resp.status_code == 403 and "Lexono Finanzen ist nicht freigeschaltet" in resp.text
    assert "Lizenz in den Einstellungen importieren" in resp.text


def test_locked_module_still_requires_login(client: TestClient) -> None:  # noqa: F811
    resp = client.get("/dashboard/fibu", follow_redirects=False)
    assert resp.status_code in (303, 307) and "/dashboard/login" in resp.headers["location"]


def test_license_unlocks_navigation_and_routes(users, db_session: Session, monkeypatch) -> None:
    client_, c = users
    LicenseFactory(monkeypatch).activate(db_session, ("finanzen",))
    login(client_, "anwalt@kanzlei.test")
    page = client_.get("/dashboard/chat").text
    assert _nav_has_fibu(page) and "sidebar__new-badge" in page
    assert client_.get(f"/dashboard/fibu?client={c.id}").status_code == 200


def test_admin_imports_and_removes_license_in_settings(users, db_session: Session, monkeypatch, tmp_path) -> None:
    client_, c = users
    f = LicenseFactory(monkeypatch)
    login(client_, "admin@kanzlei.test")
    settings_page = client_.get("/dashboard/settings?tab=lizenz").text
    assert 'data-module="finanzen"' in settings_page and "Nicht freigeschaltet" in settings_page
    token = extract_csrf(settings_page)
    bad = client_.post("/dashboard/settings/license/import", data={"csrf_token": token},
                       files={"license_file": ("x.lexono-license", b"{}", "text/plain")}, follow_redirects=False)
    assert bad.status_code == 303 and "error=" in bad.headers["location"]
    assert not licensing.is_enabled(db_session, "finanzen")
    ok = client_.post("/dashboard/settings/license/import", data={"csrf_token": token},
                      files={"license_file": ("k.lexono-license", f.text(("finanzen", "steuern")).encode(), "text/plain")},
                      follow_redirects=False)
    assert ok.status_code == 303 and "success=" in ok.headers["location"]
    assert licensing.enabled_modules(db_session) >= {"finanzen", "steuern"}
    page = client_.get("/dashboard/settings?tab=lizenz").text
    assert re.search(r'data-module="steuern">.*?Freigeschaltet', page, re.S) and "Lizenz deaktivieren" in page
    client_.post("/dashboard/settings/license/remove", data={"csrf_token": token})
    assert not licensing.is_enabled(db_session, "finanzen")


@pytest.mark.parametrize("role", ["anwalt", "mitarbeiter"])
def test_only_admin_may_import_licenses(users, db_session: Session, monkeypatch, role: str) -> None:
    client_, _c = users
    f = LicenseFactory(monkeypatch)
    login(client_, f"{role}@kanzlei.test")
    token = extract_csrf(client_.get("/dashboard/chat").text)
    resp = client_.post("/dashboard/settings/license/import", data={"csrf_token": token, "license_text": f.text()})
    assert resp.status_code == 403 and not licensing.is_enabled(db_session, "finanzen")


# --- Lexono Steuern: Datenmodell und Fachlogik -----------------------------------------------------------------------


@pytest.fixture()
def tax_env(db_session: Session, monkeypatch):  # noqa: F811
    roles = seed_roles(db_session)
    people = {r: create_test_user(db_session, roles[r], f"{r}@kanzlei.test") for r in ("admin", "anwalt", "mitarbeiter")}
    a, b = Client(name="QA Steuer GmbH"), Client(name="QA Andere KG")
    db_session.add_all([a, b]); db_session.commit()
    factory = LicenseFactory(monkeypatch)
    return people, a, b, factory


def test_tax_module_is_locked_without_license(db_session: Session, tax_env) -> None:
    people, a, _b, factory = tax_env
    with pytest.raises(licensing.ModuleLockedError):
        tax.create_filing(db_session, people["anwalt"], a.id, tax_type="est", year=2025, period_kind="year")
    factory.activate(db_session, ("finanzen",))                     # Finanzen allein schaltet Steuern nicht frei
    with pytest.raises(licensing.ModuleLockedError):
        tax.list_filings(db_session, people["anwalt"], a.id)


def test_staff_has_no_tax_rights_by_default(db_session: Session, tax_env) -> None:
    people, a, _b, factory = tax_env
    factory.activate(db_session, ("steuern",))
    with pytest.raises(PermissionDeniedError):
        tax.create_filing(db_session, people["mitarbeiter"], a.id, tax_type="est", year=2025, period_kind="year")
    filing = tax.create_filing(db_session, people["anwalt"], a.id, tax_type="est", year=2025, period_kind="year")
    for call in (lambda: tax.transition(db_session, people["mitarbeiter"], a.id, filing.id, "in_review"),
                 lambda: tax.add_part(db_session, people["mitarbeiter"], a.id, filing.id, part_code="x", label="x"),
                 lambda: tax.submit_filing(db_session, people["mitarbeiter"], a.id, filing.id)):
        with pytest.raises(PermissionDeniedError):
            call()


@pytest.mark.parametrize("kw,msg", [
    ({"tax_type": "xyz", "period_kind": "year"}, "Unbekannte Steuerart"),
    ({"tax_type": "est", "period_kind": "quarter", "period_no": 1}, "nicht vorgesehen"),
    ({"tax_type": "ust_va", "period_kind": "month", "period_no": 13}, "gültigen Zeitraum"),
    ({"tax_type": "ust_va", "period_kind": "quarter"}, "gültigen Zeitraum"),
    ({"tax_type": "est", "period_kind": "year", "year": 1999}, "Bereichs"),
])
def test_filing_validation(db_session: Session, tax_env, kw: dict, msg: str) -> None:
    people, a, _b, factory = tax_env
    factory.activate(db_session, ("steuern",))
    with pytest.raises(tax.TaxError, match=msg):
        tax.create_filing(db_session, people["anwalt"], a.id, **{"year": 2025, **kw})


def test_supports_many_tax_types_not_only_vat_prepayments(db_session: Session, tax_env) -> None:
    people, a, _b, factory = tax_env
    factory.activate(db_session, ("steuern",))
    created = [tax.create_filing(db_session, people["anwalt"], a.id, tax_type=t, year=2025, period_kind=p, period_no=n)
               for t, p, n in (("est", "year", None), ("ust_va", "quarter", 4), ("einspruch", "none", None),
                               ("gewst", "year", None), ("kst", "year", None), ("ebilanz", "year", None))]
    assert {f.filing_kind for f in created} == {"erklaerung", "voranmeldung", "nachricht", "bilanz"}
    est = created[0]
    tax.add_part(db_session, people["anwalt"], a.id, est.id, part_code="hauptvordruck", label="Hauptvordruck")
    one = tax.add_part(db_session, people["anwalt"], a.id, est.id, part_code="anlage_v", label="Anlage Objekt 1")
    two = tax.add_part(db_session, people["anwalt"], a.id, est.id, part_code="anlage_v", label="Anlage Objekt 2")
    assert (one.instance_no, two.instance_no) == (1, 2)


def test_values_workflow_approval_and_audit(db_session: Session, tax_env) -> None:
    people, a, _b, factory = tax_env
    factory.activate(db_session, ("steuern",))
    anwalt = people["anwalt"]
    filing = tax.create_filing(db_session, anwalt, a.id, tax_type="ust", year=2025, period_kind="year")
    part = tax.add_part(db_session, anwalt, a.id, filing.id, part_code="hauptvordruck", label="Hauptvordruck")
    tax.set_value(db_session, anwalt, a.id, part.id, field_key="qa.umsatz_19", value="125043", value_type="amount_cents")
    tax.set_value(db_session, anwalt, a.id, part.id, field_key="qa.umsatz_19", value="125050", value_type="amount_cents",
                  source="fibu")
    with pytest.raises(tax.TaxError, match="ganze Zahl"):
        tax.set_value(db_session, anwalt, a.id, part.id, field_key="qa.x", value="12,50", value_type="amount_cents")
    with pytest.raises(tax.TaxError, match="nicht zulässig"):
        tax.transition(db_session, anwalt, a.id, filing.id, "approved")             # Pruefung uebersprungen
    tax.transition(db_session, anwalt, a.id, filing.id, "in_review")
    with pytest.raises(tax.TaxError, match="nur im Entwurf"):
        tax.set_value(db_session, anwalt, a.id, part.id, field_key="qa.umsatz_19", value="1", value_type="amount_cents")
    tax.transition(db_session, people["admin"], a.id, filing.id, "approved")
    assert filing.status == "approved" and filing.approved_by == "admin@kanzlei.test" and filing.approved_at
    with pytest.raises(tax.TaxError, match="Grund"):
        tax.transition(db_session, anwalt, a.id, filing.id, "draft")
    tax.transition(db_session, anwalt, a.id, filing.id, "draft", reason="Korrektur Umsatz")
    assert filing.approved_by is None and filing.approved_at is None
    details = [e.details for e in db_session.query(AuditEvent).filter_by(entity_type="TaxFiling", entity_id=filing.id)
               .order_by(AuditEvent.created_at)]
    assert any("qa.umsatz_19: 125043 -> 125050 (fibu)" in d for d in details)
    assert any("Freigegeben -> Entwurf: Korrektur Umsatz" in d for d in details)


def test_submission_is_never_performed(db_session: Session, tax_env) -> None:
    people, a, _b, factory = tax_env
    factory.activate(db_session, ("steuern",))
    filing = tax.create_filing(db_session, people["anwalt"], a.id, tax_type="ust_va", year=2026, period_kind="month",
                               period_no=9)
    with pytest.raises(tax.TaxError, match="Nur freigegebene"):
        tax.submit_filing(db_session, people["anwalt"], a.id, filing.id)
    tax.transition(db_session, people["anwalt"], a.id, filing.id, "in_review")
    tax.transition(db_session, people["anwalt"], a.id, filing.id, "approved")
    for env in ("test", "production"):
        with pytest.raises(tax.SubmissionNotAvailable, match="nichts übermittelt"):
            tax.submit_filing(db_session, people["anwalt"], a.id, filing.id, environment=env)
    assert filing.status == "approved" and db_session.query(TaxSubmission).count() == 0


def test_tax_client_isolation(db_session: Session, tax_env) -> None:
    people, a, b, factory = tax_env
    factory.activate(db_session, ("steuern",))
    anwalt = people["anwalt"]
    filing = tax.create_filing(db_session, anwalt, a.id, tax_type="est", year=2025, period_kind="year")
    part = tax.add_part(db_session, anwalt, a.id, filing.id, part_code="hauptvordruck", label="HV")
    for call in (lambda: tax.get_filing(db_session, anwalt, b.id, filing.id),
                 lambda: tax.add_part(db_session, anwalt, b.id, filing.id, part_code="x", label="x"),
                 lambda: tax.set_value(db_session, anwalt, b.id, part.id, field_key="k", value="v"),
                 lambda: tax.transition(db_session, anwalt, b.id, filing.id, "in_review")):
        with pytest.raises(tax.TaxNotFound):
            call()
    matter_b = Matter(client_id=b.id, title="QA Akte B")
    db_session.add(matter_b); db_session.commit()
    with pytest.raises(tax.TaxError, match="Akte gehört nicht"):
        tax.create_filing(db_session, anwalt, a.id, tax_type="est", year=2025, period_kind="year", matter_id=matter_b.id)
    with pytest.raises(tax.TaxError, match="berichtigte Vorgang"):
        tax.create_filing(db_session, anwalt, b.id, tax_type="est", year=2025, period_kind="year",
                          correction_of_id=filing.id)
    assert tax.list_filings(db_session, anwalt, b.id) == [] and db_session.query(TaxFieldValue).count() == 0


def test_catalog_keeps_base_package_independent() -> None:
    assert BASE_MODULES == {"kanzlei"} and not CATALOG["kanzlei"].licensed
    assert CATALOG["finanzen"].licensed and CATALOG["steuern"].licensed


# --- Migration ----------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_migration_041_adds_only_new_tables_and_keeps_fibu_data(tmp_path: Path) -> None:
    from alembic import command

    from tests.test_migrations import _alembic_config_for

    db_file = tmp_path / "m.db"
    cfg = _alembic_config_for(db_file)
    command.upgrade(cfg, "schritt3_040")
    con = sqlite3.connect(db_file)
    con.execute("insert into clients (id, name, status, created_at, updated_at) values ('c1', 'QA', 'active', "
                "'2026-01-01', '2026-01-01')")
    con.execute("insert into fibu_accounts (id, client_id, number, name, kind, is_active, created_at, updated_at) "
                "values ('a1', 'c1', '1000', 'Bank', 'bank', 1, '2026-01-01', '2026-01-01')")
    con.commit()
    before = {r[0]: r[1] for r in con.execute("select name, sql from sqlite_master where type='table'")}
    con.close()
    command.upgrade(cfg, "schritt3_041")
    con = sqlite3.connect(db_file)
    after = {r[0]: r[1] for r in con.execute("select name, sql from sqlite_master where type='table'")}
    assert set(after) - set(before) == {"module_licenses", "tax_filings", "tax_filing_parts", "tax_field_values",
                                        "tax_submissions"}
    assert all(after[n] == s for n, s in before.items())
    assert con.execute("select count(*) from module_licenses").fetchone()[0] == 0     # nichts freigeschaltet
    assert con.execute("select name from fibu_accounts").fetchall() == [("Bank",)]     # FiBu-Daten erhalten
    con.close()
    command.downgrade(cfg, "schritt3_040")
    con = sqlite3.connect(db_file)
    assert not {r[0] for r in con.execute("select name from sqlite_master where name like 'tax_%' or "
                                          "name = 'module_licenses'")}
    assert con.execute("select count(*) from fibu_accounts").fetchone()[0] == 1
    con.close()
