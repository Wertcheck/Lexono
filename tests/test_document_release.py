"""§62: konfigurierbare Dokumentfreigabe vor KI-Verarbeitung - serverseitige Durchsetzung, nicht nur Oberflaeche.

Modi (kanzleiweit): A manuell, B automatisch (Standard = bisheriges Verhalten), C automatisch mit Entscheidung bei
Auffaelligkeiten. In allen Modi bleibt die zentrale Pruefung verpflichtend; eine fehlgeschlagene Pruefung blockiert
immer (auch gegen eine manuelle Freigabe). Freigaben sind an den Textstand gebunden. Upload/Import/Oeffnen loesen keine
KI aus. Die informative Meldung "automatisch freigegeben" ist pro Benutzer ausblendbar - Warnungen nie.
Echte Endpunkte, echter Gateway (lokal), Schreib-Spion bzw. lokaler Aufzeichnungsserver. Nur synthetische Daten."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.documents import release
from app.models import AuditEvent, Client, Deadline, Document, Matter, User
from app.models.document_release import DocumentPrivacyCheck, DocumentRelease
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)

CLEAN = "Beschluss vom 03.11.2026: Der Streitwert wird auf 4.250,00 EUR festgesetzt; Termin am 22.07.2026."
UNCERTAIN = "Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024. Streitwert 4.250,00 EUR."


def _doc(db: Session, text: str = CLEAN, *, title: str = "Kautionssache 2026") -> Document:
    matter = Matter(client=Client(name="Synthetische Mandantin"), title=title)
    db.add(matter)
    db.flush()
    document = Document(matter_id=matter.id, file_path="/tmp/synthetisch.pdf", original_filename="beschluss.pdf",
                        extracted_text=text, ocr_status="not_needed")
    db.add(document)
    db.commit()
    return document


def _mode(db: Session, mode: str) -> None:
    release.set_mode(db, mode, actor="qa@kanzlei.test")


def _view(client: TestClient, document: Document) -> str:
    return client.get(f"/dashboard/matters/{document.matter_id}/document/{document.id}").text


def _ai_action(client: TestClient, document: Document) -> int:
    page = _view(client, document)
    return client.post(f"/dashboard/chat/from-document/{document.id}", follow_redirects=False,
                       data={"csrf_token": extract_csrf(page), "action": "summarize"}).status_code


def _release(client: TestClient, document: Document, version: str | None = None):
    page = _view(client, document)
    return client.post(f"/dashboard/matters/{document.matter_id}/document/{document.id}/release",
                       follow_redirects=False,
                       data={"csrf_token": extract_csrf(page),
                             "text_version": version if version is not None else release.text_version_of(
                                 document.extracted_text)})


def _sent(writer) -> str:
    return "".join(json.dumps(p.model_dump(), ensure_ascii=False) for p in writer.received_payloads)


# --- Modus B (Standard): unveraendertes Verhalten, sichtbarer Status --------------------------------------------------


def test_default_mode_is_auto_and_keeps_the_existing_behaviour(client, db_session, writer) -> None:
    assert release.get_mode(db_session) == release.MODE_AUTO  # kein gespeicherter Wert = bisheriges Verhalten
    document = _doc(db_session)
    page = _view(client, document)
    assert "Automatisch freigegeben" in page and 'id="document-release-auto-notice"' in page
    assert "Pseudonymisierung überprüfen" in page and "Freigabeeinstellungen öffnen" in page
    assert "Diese Meldung nicht mehr anzeigen" in page
    _ai_action(client, document)
    assert len(writer.received_payloads) == 1 and "4.250,00 EUR" in _sent(writer)


# --- Modus A: manuelle Freigabe, an den Textstand gebunden ------------------------------------------------------------


def test_manual_mode_blocks_until_released_and_binds_the_release_to_the_text(client, db_session, writer) -> None:
    _mode(db_session, release.MODE_MANUAL)
    document = _doc(db_session)
    page = _view(client, document)
    assert "Freigabe erforderlich" in page and "Für KI-Verarbeitung freigeben" in page
    _ai_action(client, document)
    assert writer.received_payloads == []  # serverseitig blockiert: kein Cloud-Aufruf

    response = _release(client, document)
    assert response.status_code == 303 and "released=1" in response.headers["location"]
    assert "Manuell freigegeben" in _view(client, document)
    assert db_session.query(AuditEvent).filter_by(event_type="document_released_manually").count() == 1
    _ai_action(client, document)
    assert len(writer.received_payloads) == 1 and "4.250,00 EUR" in _sent(writer)

    document.extracted_text = CLEAN + " Nachtrag: neue Seite."  # Dokumentstand aendert sich (z. B. erneute OCR)
    db_session.commit()
    assert release.release_status(db_session, document).state == release.STATE_REQUIRED  # Freigabe veraltet
    _ai_action(client, document)
    assert len(writer.received_payloads) == 1  # kein zweiter Aufruf mit dem neuen, nicht freigegebenen Stand


def test_a_stale_release_form_is_rejected(client, db_session, writer) -> None:
    _mode(db_session, release.MODE_MANUAL)
    document = _doc(db_session)
    response = _release(client, document, version="veraltet")
    assert "release_error" in response.headers["location"]
    assert db_session.query(DocumentRelease).count() == 0


def test_unreleased_documents_never_enter_the_matter_context_or_deadlines(client, db_session, writer) -> None:
    """Schriftsatz-Generierung (ganze Akte): nicht freigegebener Text und daraus erkannte Fristausschnitte fehlen."""
    from tests.auth_test_utils import extract_operation_token

    _mode(db_session, release.MODE_MANUAL)
    document = _doc(db_session, CLEAN + " Frist bis 30.11.2026 zur Stellungnahme (Geheimvermerk XQ7).")
    db_session.add(Deadline(matter_id=document.matter_id, document_id=document.id, due_date=date(2026, 11, 30),
                            source_text="Frist bis 30.11.2026 zur Stellungnahme (Geheimvermerk XQ7)."))
    db_session.commit()
    page = client.get("/dashboard/tools/schriftsatz").text
    client.post("/dashboard/tools/schriftsatz/generate", follow_redirects=False, data={
        "csrf_token": extract_csrf(page), "operation_token": extract_operation_token(page),
        "matter_id": document.matter_id}, files={"unbenutzt": ("", b"", "application/octet-stream")})
    sent = _sent(writer)
    assert "4.250,00" not in sent and "XQ7" not in sent  # weder Dokumenttext noch Fristausschnitt


# --- Modus C: automatisch, Entscheidung bei Auffaelligkeiten ----------------------------------------------------------


def test_auto_review_mode_releases_clean_and_holds_uncertain_documents(client, db_session, writer) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    clean, uncertain = _doc(db_session, CLEAN), _doc(db_session, UNCERTAIN, title="Zweite Akte")
    assert release.release_status(db_session, clean).state == release.STATE_RELEASED_AUTO
    status = release.release_status(db_session, uncertain)
    assert status.state == release.STATE_DECISION and status.candidate_count >= 1
    page = _view(client, uncertain)
    assert "Entscheidung erforderlich" in page and "Für KI-Verarbeitung freigeben" in page
    _ai_action(client, uncertain)
    assert writer.received_payloads == []
    _release(client, uncertain)  # die Entscheidung des Anwalts
    _ai_action(client, uncertain)
    assert len(writer.received_payloads) == 1


def test_auto_review_decision_can_also_be_a_name_marking(client, db_session, writer) -> None:
    """Die bestehende Markierung (§56) loest die Auffaelligkeit - neue Pruefung, weil sich der Kontext aendert."""
    from tests.test_web_document_name_protection import _protect

    _mode(db_session, release.MODE_AUTO_REVIEW)
    # "Richter" ist ein Rollenwort und bewusst nicht markierbar (§51) - hier ein unsicherer Nachname
    document = _doc(db_session, "Die Unterlagen wurden am 12.03.2026 durch\nLange geprueft und dem Auftraggeber "
                                "uebergeben. Streitwert 4.250,00 EUR.")
    assert release.release_status(db_session, document).state == release.STATE_DECISION
    response = _protect(client, document, ["Lange"])
    assert "name_protection=saved" in response.headers["location"]
    assert release.release_status(db_session, document).state == release.STATE_RELEASED_AUTO
    assert db_session.query(DocumentPrivacyCheck).filter_by(document_id=document.id).count() == 2  # neu geprueft


# --- Fehlgeschlagene Pruefung: blockiert in jedem Modus, nicht uebersteuerbar ------------------------------------------


@pytest.mark.parametrize("mode", release.MODES)
def test_failed_check_blocks_in_every_mode_and_cannot_be_released(client, db_session, writer, monkeypatch,
                                                                  mode) -> None:
    monkeypatch.setattr(release, "_run_central_check", lambda *a, **k: (release.CHECK_FAILED, 0))
    _mode(db_session, mode)
    document = _doc(db_session)
    status = release.release_status(db_session, document)
    assert status.state == release.STATE_BLOCKED and not status.can_release_manually
    page = _view(client, document)
    assert "Verarbeitung blockiert – Datenschutzprüfung fehlgeschlagen" in page
    assert "Für KI-Verarbeitung freigeben" not in page
    response = _release(client, document)
    assert "release_error" in response.headers["location"] and db_session.query(DocumentRelease).count() == 0


def test_failed_check_cannot_be_overridden_by_an_existing_manual_release(client, db_session, writer,
                                                                         monkeypatch) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    document = _doc(db_session)
    _release(client, document)  # unauffaellig: bereits automatisch freigegeben, keine manuelle noetig
    db_session.add(DocumentRelease(document_id=document.id, text_version=release.text_version_of(
        document.extracted_text), mode_at_release="auto_review"))
    db_session.commit()
    db_session.query(DocumentPrivacyCheck).delete()
    db_session.commit()
    monkeypatch.setattr(release, "_run_central_check", lambda *a, **k: (release.CHECK_FAILED, 0))
    assert release.release_status(db_session, document).state == release.STATE_BLOCKED
    _ai_action(client, document)
    assert writer.received_payloads == []


def test_an_error_in_the_check_is_fail_closed_and_not_cached(db_session, monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("Pruefung nicht verfuegbar (synthetisch)")

    monkeypatch.setattr(release, "_run_central_check", _boom)
    document = _doc(db_session)
    assert release.release_status(db_session, document).state == release.STATE_BLOCKED
    assert db_session.query(DocumentPrivacyCheck).count() == 0


# --- Upload, Import, Oeffnen: keine KI in keinem Modus ---------------------------------------------------------------


@pytest.mark.parametrize("mode", release.MODES)
def test_upload_and_opening_trigger_no_ai_in_any_mode(client, db_session, recorder, mode) -> None:
    from tests.test_chat_upload_idempotency import PDF, _page, _stream, _uploaded
    from tests.auth_test_utils import extract_upload_token

    _mode(db_session, mode)
    event = _uploaded(_stream(client, extract_upload_token(_page(client))))
    document = db_session.query(Document).one()
    client.get(event["target"].split("#")[0])
    _view(client, document)
    client.get(f"/dashboard/matters/{document.matter_id}/document/{document.id}/pseudonymisierung")
    assert recorder.requests == []
    assert PDF  # synthetisches Dokument


# --- Anzeigepraeferenz: nur die informative Meldung ------------------------------------------------------------------


def test_hiding_the_auto_notice_is_persistent_and_changes_nothing_else(client, db_session, writer) -> None:
    document = _doc(db_session)
    page = _view(client, document)
    client.post("/dashboard/privacy/document-release/notice", follow_redirects=False,
                data={"csrf_token": extract_csrf(page), "show": "0",
                      "return_to": f"/dashboard/matters/{document.matter_id}/document/{document.id}"})
    user = db_session.query(User).filter_by(email="admin@kanzlei.test").one()
    db_session.refresh(user)
    assert user.show_auto_release_notice is False  # dauerhaft gespeichert
    page = _view(client, document)
    assert 'id="document-release-auto-notice"' not in page and "Automatisch freigegeben" in page  # Status bleibt
    assert release.get_mode(db_session) == release.MODE_AUTO
    _ai_action(client, document)
    assert len(writer.received_payloads) == 1  # Verarbeitung unveraendert
    settings_page = client.get("/dashboard/privacy/document-release").text
    assert 'name="show" value="1"' in settings_page and "checked" not in settings_page.split('name="show" value="1"')[1][:20]
    client.post("/dashboard/privacy/document-release/notice", data={"csrf_token": extract_csrf(settings_page),
                                                                    "show": "1"})
    db_session.refresh(user)
    assert user.show_auto_release_notice is True  # in den Einstellungen wieder einschaltbar


@pytest.mark.parametrize("mode,forced", [(release.MODE_MANUAL, False), (release.MODE_AUTO_REVIEW, False),
                                         (release.MODE_AUTO, True)])
def test_warnings_and_required_releases_are_never_hidden(client, db_session, writer, monkeypatch, mode,
                                                         forced) -> None:
    user = db_session.query(User).filter_by(email="admin@kanzlei.test").one()
    user.show_auto_release_notice = False
    db_session.commit()
    _mode(db_session, mode)
    if forced:
        monkeypatch.setattr(release, "_run_central_check", lambda *a, **k: (release.CHECK_FAILED, 0))
    document = _doc(db_session, UNCERTAIN if mode == release.MODE_AUTO_REVIEW else CLEAN)
    page = _view(client, document)
    banner = {release.MODE_MANUAL: 'id="document-release-required"', release.MODE_AUTO_REVIEW:
              'id="document-release-required"', release.MODE_AUTO: 'id="document-release-blocked"'}[mode]
    assert banner in page


def test_return_target_of_the_notice_form_is_internal_only(client, db_session) -> None:
    page = client.get("/dashboard/privacy/document-release").text
    response = client.post("/dashboard/privacy/document-release/notice", follow_redirects=False,
                           data={"csrf_token": extract_csrf(page), "show": "0", "return_to": "https://evil.example"})
    assert response.headers["location"].startswith("/dashboard/privacy/document-release")


# --- Einstellungen und Berechtigungen ---------------------------------------------------------------------------------


def test_mode_change_via_settings_is_audited_and_visible(client, db_session) -> None:
    page = client.get("/dashboard/privacy/document-release").text
    assert "Aktiver Freigabemodus" in page and "Automatische Freigabe" in page and "Standard" in page
    response = client.post("/dashboard/privacy/document-release/mode", follow_redirects=False,
                           data={"csrf_token": extract_csrf(page), "mode": "manual"})
    assert response.status_code == 303 and release.get_mode(db_session) == release.MODE_MANUAL
    assert db_session.query(AuditEvent).filter_by(event_type="document_release_mode_changed").count() == 1
    assert "Manuelle Freigabe" in client.get("/dashboard/privacy/document-release").text
    assert "Manuelle Freigabe" in client.get("/dashboard/settings").text  # dauerhaft im Bereich KI & Datenschutz
    bad = client.post("/dashboard/privacy/document-release/mode", follow_redirects=False,
                      data={"csrf_token": extract_csrf(page), "mode": "alles_erlaubt"})
    assert "error=" in bad.headers["location"] and release.get_mode(db_session) == release.MODE_MANUAL


def test_employees_can_view_but_neither_change_the_mode_nor_release(db_session) -> None:
    from app.db.session import get_db
    from app.main import app

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        roles = seed_roles(db_session)
        create_test_user(db_session, roles["mitarbeiter"], "ma@kanzlei.test")
        employee = TestClient(app)
        login(employee, "ma@kanzlei.test")
        _mode(db_session, release.MODE_MANUAL)
        document = _doc(db_session)
        page = employee.get("/dashboard/privacy/document-release").text
        assert "Manuelle Freigabe" in page and 'name="mode"' not in page
        assert employee.post("/dashboard/privacy/document-release/mode",
                             data={"csrf_token": extract_csrf(page), "mode": "auto"}).status_code == 403
        doc_page = employee.get(f"/dashboard/matters/{document.matter_id}/document/{document.id}").text
        assert "Freigabe erforderlich" in doc_page and "Für KI-Verarbeitung freigeben" not in doc_page
        response = employee.post(f"/dashboard/matters/{document.matter_id}/document/{document.id}/release",
                                 data={"csrf_token": extract_csrf(doc_page),
                                       "text_version": release.text_version_of(document.extracted_text)})
        assert response.status_code == 403 and db_session.query(DocumentRelease).count() == 0
        assert release.get_mode(db_session) == release.MODE_MANUAL
    finally:
        app.dependency_overrides.clear()


def test_release_respects_matter_isolation(client, db_session) -> None:
    _mode(db_session, release.MODE_MANUAL)
    document, other = _doc(db_session), _doc(db_session, title="Fremde Akte")
    page = _view(client, document)
    response = client.post(f"/dashboard/matters/{other.matter_id}/document/{document.id}/release",
                           data={"csrf_token": extract_csrf(page),
                                 "text_version": release.text_version_of(document.extracted_text)})
    assert response.status_code == 404 and db_session.query(DocumentRelease).count() == 0


# --- Human in the Loop: Pseudonymisierung pruefen, Feedback -----------------------------------------------------------


def test_pseudonymization_review_shows_replacements_locally_with_feedback_paths(client, db_session) -> None:
    document = _doc(db_session, "Die Mandantin Henrike Wallbaum-Ostertag zahlte 4.250,00 EUR an Konrad Pfefferkorn.")
    page = client.get(f"/dashboard/matters/{document.matter_id}/document/{document.id}/pseudonymisierung").text
    assert "Pseudonymisierung überprüfen" in page and "nichts davon wurde an die Cloud gesendet" in page
    assert "Wallbaum-Ostertag" in page and "[PERSON_" in page  # Original (lokal) und Platzhalter
    assert 'id="pseudonymized-text"' in page and "4.250,00 EUR" in page
    assert "#name-protection" in page and "#quality-report" in page and "#privacy-decisions" in page
    assert 'id="quality-report"' in _view(client, document)  # bestehende Qualitaetsmeldung als Feedback-Weg


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_document_release_migration_is_additive_and_reversible(tmp_path) -> None:
    import sqlite3

    from alembic import command

    from tests.test_migrations import _alembic_config_for

    db_path = tmp_path / "release.db"
    cfg = _alembic_config_for(db_path)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    firm = {r[1] for r in con.execute("pragma table_info(firm_profiles)")}
    users = {r[1]: r[4] for r in con.execute("pragma table_info(users)")}
    con.close()
    assert {"document_privacy_checks", "document_releases"} <= tables and "document_release_mode" in firm
    assert users["show_auto_release_notice"] is not None  # server_default: bestehende Benutzer sehen die Meldung
    command.downgrade(cfg, "schritt3_035")
    con = sqlite3.connect(str(db_path))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    con.close()
    assert "document_releases" not in tables and "document_privacy_checks" not in tables


def test_chat_question_about_an_unreleased_upload_is_answered_locally(client, db_session, recorder) -> None:
    """Pflicht-Dokument im Chat (zuvor hochgeladen, §57): in Modus A lokaler Hinweis statt Cloud-Anfrage; nach der
    Freigabe genau EINE Anfrage (verifizierter HTTP-Payload enthaelt den Dokumentinhalt)."""
    from tests.auth_test_utils import extract_upload_token
    from tests.test_chat_upload_idempotency import _page, _stream, _uploaded
    from tests.test_upload_without_ai import _ask, _wire

    _mode(db_session, release.MODE_MANUAL)
    conversation_id = _uploaded(_stream(client, extract_upload_token(_page(client))))["conversation_id"]
    events = _ask(client, conversation_id, "Fasse das angehängte Dokument zusammen.")
    done = [e for e in events if e["kind"] == "done"][0]
    assert done["blocked"] is True and "noch nicht für die KI-Verarbeitung freigegeben" in done["content"]
    assert recorder.requests == []
    document = db_session.query(Document).one()
    _release(client, document)
    _ask(client, conversation_id, "Fasse das angehängte Dokument jetzt zusammen.")
    assert len(recorder.requests) == 1 and "4.250,00 EUR" in _wire(recorder.requests[0])


# --- Nachtraege aus dem unabhaengigen Review (§62) -------------------------------------------------------------------


def test_a_transient_check_failure_is_retried_instead_of_blocking_forever(db_session, monkeypatch) -> None:
    calls = []

    def _flaky(*args, **kwargs):
        calls.append(1)
        return (release.CHECK_FAILED, 0) if len(calls) == 1 else (release.CHECK_PASSED, 0)

    monkeypatch.setattr(release, "_run_central_check", _flaky)
    _mode(db_session, release.MODE_AUTO_REVIEW)
    document = _doc(db_session)
    assert release.release_status(db_session, document).state == release.STATE_BLOCKED  # fail-closed
    assert db_session.query(DocumentPrivacyCheck).count() == 0  # "fehlgeschlagen" wird nicht gespeichert
    assert release.release_status(db_session, document).state == release.STATE_RELEASED_AUTO
    assert [c.outcome for c in db_session.query(DocumentPrivacyCheck)] == ["passed"]


def test_manual_release_with_a_later_failed_check_blocks_processing_too(client, db_session, writer,
                                                                        monkeypatch) -> None:
    _mode(db_session, release.MODE_MANUAL)
    document = _doc(db_session)
    _release(client, document)
    monkeypatch.setattr(release, "_run_central_check", lambda *a, **k: (release.CHECK_FAILED, 0))
    db_session.query(DocumentPrivacyCheck).delete()
    db_session.commit()
    assert release.processing_state(db_session, document) == release.STATE_BLOCKED  # wie die Anzeige
    _ai_action(client, document)
    assert writer.received_payloads == []


def test_unchanged_mode_writes_no_audit_entry(db_session) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    _mode(db_session, release.MODE_AUTO_REVIEW)
    assert db_session.query(AuditEvent).filter_by(event_type="document_release_mode_changed").count() == 1


def test_a_changed_privacy_code_invalidates_cached_results(db_session, monkeypatch) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    document = _doc(db_session)
    release.release_status(db_session, document)
    monkeypatch.setattr(release, "_check_code_version", lambda: "neue-erkennungsregeln")
    release.release_status(db_session, document)
    assert db_session.query(DocumentPrivacyCheck).filter_by(document_id=document.id).count() == 2


def test_release_manually_swallows_a_concurrent_duplicate_without_crashing(db_session, monkeypatch) -> None:
    """Regression (23.10.2026): release_manually fing `IntegrityError`, ohne es zu importieren - bei einem echten
    Doppel-Insert (Unique-Constraint uq_document_release_version, z. B. Doppelklick/paralleler Request) waere ein
    NameError statt des beabsichtigten, sauberen Rollbacks entstanden. Simuliert das Rennen zwischen Pruefung und
    Commit: die bereits (committed) vorhandene Freigabe wird von der Pruefung NICHT gesehen, der zweite Insert
    verletzt den Unique-Constraint. Erwartet: kein Absturz, Rollback, weiterhin genau EINE Freigabe."""
    from tests.auth_test_utils import create_test_user, seed_roles

    user = create_test_user(db_session, seed_roles(db_session)["admin"], "race@kanzlei.test")
    _mode(db_session, release.MODE_MANUAL)  # Modus A -> STATE_REQUIRED, der Insert-Pfad wird erreicht
    document = _doc(db_session, CLEAN)
    version = release.text_version_of(document.extracted_text)
    # Freigabe, die der "andere" Request soeben committet hat (reale, bestehende Zeile).
    db_session.add(DocumentRelease(document_id=document.id, text_version=version,
                                   released_by_user_id=user.id, mode_at_release=release.MODE_MANUAL))
    db_session.commit()
    # Rennen: die Pruefung in release_manually/release_status sieht diese Freigabe NICHT -> zweiter Insert.
    monkeypatch.setattr(release, "_valid_manual_release", lambda db, doc, v: None)

    status = release.release_manually(db_session, document, text_version=version, user=user)  # darf NICHT abbrechen

    assert status.state in (release.STATE_REQUIRED, release.STATE_DECISION) or status.released
    assert db_session.query(DocumentRelease).filter_by(
        document_id=document.id, text_version=version).count() == 1  # doppelter Insert zurueckgerollt
