"""§63: Dokumentfreigabe - Standard fuer Neuinstallationen (Modus C), Vorab-Pruefung, Auswahl bei mehr als 30
Dokumenten, fachliche Grenzen und Durchsetzung in allen KI-Pfaden. Nur synthetische Daten, isolierte Datenbanken,
Schreib-/Pruef-Spione bzw. lokaler Aufzeichnungsserver; keine echten Cloud-Aufrufe."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.documents import release
from app.models import ChatConversation, Client, Document, Draft, Matter, Message, PrivacyDecision, User
from app.models.document_release import DocumentPrivacyCheck, DocumentRelease
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles
from tests.test_document_release import CLEAN, UNCERTAIN, _ai_action, _doc, _mode, _release
from tests.test_no_ai_on_open import spies  # noqa: F401  (Fixture)
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)

MARKER = "Geheimvermerk XQ9-NICHT-FREIGEGEBEN"


# --- P1: Modus C fuer Neuinstallationen, bestehende Installationen unveraendert ----------------------------------------


def _alembic(db_path):
    from tests.test_migrations import _alembic_config_for

    return _alembic_config_for(db_path)


def _session(db_path) -> Session:
    return sessionmaker(bind=create_engine(f"sqlite:///{db_path}"))()


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_a_new_installation_starts_in_mode_c_even_after_the_first_admin(tmp_path) -> None:
    from alembic import command

    db_path = tmp_path / "neu.db"
    command.upgrade(_alembic(db_path), "head")  # wie die Ersteinrichtung: Migration vor dem ersten Benutzer
    db = _session(db_path)
    create_test_user(db, seed_roles(db)["admin"], "admin@kanzlei.test")
    assert release.get_mode(db) == release.MODE_AUTO_REVIEW
    command.upgrade(_alembic(db_path), "head")  # jeder weitere Start migriert erneut: nichts aendert sich
    db.expire_all()
    assert release.get_mode(db) == release.MODE_AUTO_REVIEW
    db.close()


@pytest.mark.parametrize("existing", [None, "manual", "auto_review", "auto"])
@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_an_upgrade_never_changes_an_existing_installation(tmp_path, existing) -> None:
    from alembic import command

    db_path = tmp_path / "bestand.db"
    command.upgrade(_alembic(db_path), "schritt3_036")
    db = _session(db_path)
    create_test_user(db, seed_roles(db)["admin"], "admin@kanzlei.test")  # bestehende Installation
    db.close()
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO firm_profiles (id, created_at, updated_at, firm_name, letterhead_name, "
                "default_document_format, timezone, auto_number_new_matters, document_release_mode) "
                "VALUES ('p1', '2026-01-01', '2026-01-01', 'Bestandskanzlei', 'Kanzlei allgemein', 'pdf', "
                "'Europe/Berlin', 0, ?)", (existing,))
    con.commit()
    con.close()
    command.upgrade(_alembic(db_path), "head")
    db = _session(db_path)
    expected = existing or release.MODE_AUTO  # leer -> ausdruecklich B (bisheriges Verhalten), sonst unveraendert
    assert release.get_mode(db) == expected
    assert sqlite3.connect(db_path).execute("SELECT document_release_mode FROM firm_profiles").fetchall() == [
        (expected,)]
    db.close()


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_an_existing_installation_without_profile_keeps_the_previous_behaviour(tmp_path) -> None:
    from alembic import command

    db_path = tmp_path / "ohne_profil.db"
    command.upgrade(_alembic(db_path), "schritt3_036")
    db = _session(db_path)
    create_test_user(db, seed_roles(db)["admin"], "admin@kanzlei.test")
    db.close()
    command.upgrade(_alembic(db_path), "head")
    db = _session(db_path)
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM firm_profiles").fetchone()[0] == 0  # nichts angelegt
    assert release.get_mode(db) == release.MODE_AUTO  # bedarfsweise angelegt, ohne Wert = B
    db.close()


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_existing_data_without_users_is_not_treated_as_a_new_installation(tmp_path) -> None:
    from alembic import command

    db_path = tmp_path / "altdaten.db"
    command.upgrade(_alembic(db_path), "schritt3_036")
    con = sqlite3.connect(db_path)  # Altdaten, aber (noch) kein Benutzer - z. B. abgebrochenes Anlegen des Admins
    con.execute("INSERT INTO clients (id, created_at, updated_at, name) VALUES ('c1', '2026-01-01', '2026-01-01', "
                "'Synthetische Mandantin')")
    con.commit()
    con.close()
    command.upgrade(_alembic(db_path), "head")
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM firm_profiles").fetchone()[0] == 0
    db = _session(db_path)
    assert release.get_mode(db) == release.MODE_AUTO
    db.close()


def test_new_installation_default_is_shown_and_stays_changeable(client, db_session) -> None:
    release.set_mode(db_session, release.NEW_INSTALLATION_MODE, actor="setup")
    page = client.get("/dashboard/privacy/document-release").text
    assert "Automatische Freigabe mit Entscheidung bei Auffälligkeiten" in page
    assert "Standard für Neuinstallationen" in page
    client.post("/dashboard/privacy/document-release/mode", data={"csrf_token": extract_csrf(page), "mode": "auto"})
    assert release.get_mode(db_session) == release.MODE_AUTO


# --- P2: Vorab-Pruefung in Modus C -----------------------------------------------------------------------------------


def _process(db: Session, document: Document) -> None:
    from app.documents.service import DocumentProcessingService

    DocumentProcessingService(ocr_enabled=False).process_document(document, db, actor="qa")


def _text_file(tmp_path, text: str, name: str = "eingang.txt") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _unprocessed(db: Session, tmp_path, text: str = CLEAN) -> Document:
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Vorabpruefung")
    db.add(matter)
    db.flush()
    document = Document(matter_id=matter.id, file_path=_text_file(tmp_path, text), original_filename="eingang.txt")
    db.add(document)
    db.commit()
    return document


def test_precheck_runs_locally_after_processing_in_mode_c(db_session, spies, tmp_path) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    document = _unprocessed(db_session, tmp_path)
    _process(db_session, document)
    checks = db_session.query(DocumentPrivacyCheck).filter_by(document_id=document.id).all()
    assert [c.outcome for c in checks] == ["passed"]
    assert checks[0].text_version == release.text_version_of(document.extracted_text)  # an den Textstand gebunden
    assert spies["provider"] == [] and spies["network"] == []  # lokal: kein Cloud-, kein Netzaufruf


def test_no_precheck_in_mode_b(db_session, tmp_path) -> None:
    from app.models.firm_profile import FirmProfile

    document = _unprocessed(db_session, tmp_path)
    _process(db_session, document)
    assert db_session.query(DocumentPrivacyCheck).count() == 0  # B braucht keine zweite Pruefung
    assert db_session.query(FirmProfile).count() == 0  # die Vorab-Pruefung legt kein Kanzleiprofil an (Review §63)


def test_a_precheck_error_neither_breaks_processing_nor_is_trusted(db_session, tmp_path, monkeypatch) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    document = _unprocessed(db_session, tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("Abbruch der Vorab-Pruefung (synthetisch)")

    monkeypatch.setattr(release, "_run_central_check", _boom)
    _process(db_session, document)
    assert document.extracted_text and db_session.query(DocumentPrivacyCheck).count() == 0
    assert release.processing_state(db_session, document) == release.STATE_BLOCKED  # unklar -> blockiert
    monkeypatch.undo()
    assert release.processing_state(db_session, document) == release.STATE_RELEASED_AUTO  # beim Versand neu geprueft


def test_a_text_change_after_the_precheck_is_checked_again(db_session, tmp_path) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    document = _unprocessed(db_session, tmp_path)
    _process(db_session, document)
    document.extracted_text = UNCERTAIN  # z. B. erneute Texterkennung
    db_session.commit()
    assert release.processing_state(db_session, document) == release.STATE_DECISION  # neuer Stand, neu geprueft
    assert db_session.query(DocumentPrivacyCheck).filter_by(document_id=document.id).count() == 2


def test_upload_in_mode_c_prechecks_without_any_ai_request(client, db_session, recorder) -> None:
    from tests.auth_test_utils import extract_upload_token
    from tests.test_chat_upload_idempotency import _page, _stream, _uploaded

    _mode(db_session, release.MODE_AUTO_REVIEW)
    _uploaded(_stream(client, extract_upload_token(_page(client))))
    document = db_session.query(Document).one()
    assert db_session.query(DocumentPrivacyCheck).filter_by(document_id=document.id).count() == 1
    assert recorder.requests == []


# --- P3: mehr als 30 Dokumente mit gemischtem Freigabestatus ----------------------------------------------------------


def _many(db: Session, *, released: int, unreleased: int, mode: str = release.MODE_MANUAL) -> tuple[Matter, list, list]:
    _mode(db, mode)
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Grosse Akte")
    db.add(matter)
    db.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older, newer = [], []
    for index in range(released):  # aeltere, freigegebene Dokumente
        document = Document(matter_id=matter.id, file_path="x", original_filename=f"frei_{index:02d}.txt",
                            extracted_text=f"Freigegebener Vermerk Nummer {index:02d}: Betrag {100 + index},00 EUR.",
                            ocr_status="not_needed", created_at=base + timedelta(minutes=index))
        db.add(document)
        older.append(document)
    for index in range(unreleased):  # neuere, NICHT freigegebene Dokumente
        document = Document(matter_id=matter.id, file_path="x", original_filename=f"offen_{index:02d}.txt",
                            extracted_text=f"{MARKER} Nummer {index:02d}.", ocr_status="not_needed",
                            created_at=base + timedelta(days=1, minutes=index))
        db.add(document)
        newer.append(document)
    db.commit()
    for document in older:
        db.add(DocumentRelease(document_id=document.id, text_version=release.text_version_of(document.extracted_text),
                               mode_at_release=mode))
    db.commit()
    return matter, older, newer


def _context(db: Session, matter: Matter):
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider

    return RuleBasedLocalAIProvider().prepare_draft_context(matter.id, db)


def test_unreleased_newer_documents_never_displace_older_released_ones(db_session) -> None:
    matter, older, newer = _many(db_session, released=34, unreleased=8)
    preparation = _context(db_session, matter)
    included = set(preparation.included_document_ids)
    assert len(included) == 30 and included <= {d.id for d in older}  # 30 freigegebene - vorher nur 22
    assert MARKER not in preparation.sachverhalt
    notices = " ".join(preparation.notices)
    assert "nicht für die KI-Verarbeitung freigegeben" in notices and "offen_07.txt" in notices
    assert "Höchstzahl von 30 Dokumenten erreicht: 4 weitere" in notices  # unvollstaendige Auswahl: verstaendlich


def test_the_limit_notice_also_appears_in_mode_b(db_session) -> None:
    matter, older, _ = _many(db_session, released=33, unreleased=0, mode=release.MODE_AUTO)
    db_session.add(Document(matter_id=matter.id, file_path="x", original_filename="leer.txt", extracted_text="   ",
                            ocr_status="done"))  # ohne verwertbaren Text: weder verwendet noch mitgezaehlt
    db_session.commit()
    preparation = _context(db_session, matter)
    assert len(preparation.included_document_ids) == 30
    assert any("Höchstzahl von 30 Dokumenten erreicht: 3 weitere" in n for n in preparation.notices)


def test_the_search_is_bounded_and_never_uses_unchecked_documents(db_session) -> None:
    matter, older, newer = _many(db_session, released=3, unreleased=125)
    preparation = _context(db_session, matter)
    assert preparation.included_document_ids == [] and MARKER not in preparation.sachverhalt
    notices = " ".join(preparation.notices)
    assert "und 110 weitere" in notices  # zurueckgehaltene Dokumente benannt (begrenzt)
    # nicht durchsucht -> nicht verwendet, und ausdruecklich als Suchgrenze (nicht als Hoechstzahl) benannt
    assert "Suchgrenze erreicht: 8 weitere(s), ältere(s) Dokument(e)" in notices
    assert "Höchstzahl" not in notices


# --- P4: fachliche Grenzen --------------------------------------------------------------------------------------------


def test_not_personal_in_a_conversation_does_not_release_a_document_in_mode_c(db_session) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    document = _doc(db_session, UNCERTAIN)
    user = User(email="qa@kanzlei.test", password_hash="x", display_name="QA")
    db_session.add(user)
    db_session.flush()
    conversation = ChatConversation(matter_id=document.matter_id, user_id=user.id, title="QA")
    db_session.add(conversation)
    db_session.flush()
    db_session.add(PrivacyDecision(matter_id=document.matter_id, conversation_id=conversation.id, term="Richter",
                                   decision="not_personal", decided_by_user_id=user.id))
    db_session.commit()
    # gilt nur in dieser Unterhaltung - keine aktenweite oder globale Ausnahme, keine Lockerung der Dokumentpruefung
    assert release.release_status(db_session, document).state == release.STATE_DECISION
    assert release.processing_state(db_session, document) == release.STATE_DECISION


def test_employees_can_neither_release_nor_bypass_via_chat_or_privacy_review(db_session, writer) -> None:
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app
    import app.web.chat_router as chat_router_module
    from tests.test_web_chat import _working_drafting_service

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        chat_router_module_get = chat_router_module.get_drafting_service
        chat_router_module.get_drafting_service = lambda: _working_drafting_service(writer)
        roles = seed_roles(db_session)
        create_test_user(db_session, roles["mitarbeiter"], "ma@kanzlei.test")
        employee = TestClient(app)
        login(employee, "ma@kanzlei.test")
        _mode(db_session, release.MODE_AUTO_REVIEW)
        document = _doc(db_session, UNCERTAIN)
        page = employee.get(f"/dashboard/matters/{document.matter_id}/document/{document.id}").text
        csrf = extract_csrf(page)
        assert employee.post(f"/dashboard/matters/{document.matter_id}/document/{document.id}/release",
                             data={"csrf_token": csrf, "text_version": release.text_version_of(
                                 document.extracted_text)}).status_code == 403
        assert employee.post(f"/dashboard/chat/from-document/{document.id}",
                             data={"csrf_token": csrf, "action": "summarize"}).status_code == 403
        assert employee.post("/dashboard/chat/send-stream", data={
            "csrf_token": csrf, "conversation_id": "", "content": "Fasse zusammen."}).status_code == 403
        assert employee.post("/dashboard/chat/x/privacy-review", data={
            "csrf_token": csrf, "message_id": "x", "decisions": "[]"}).status_code == 403
        assert db_session.query(DocumentRelease).count() == 0 and writer.received_payloads == []
    finally:
        chat_router_module.get_drafting_service = chat_router_module_get
        app.dependency_overrides.clear()


# --- P5: Durchsetzung in allen KI-Pfaden (Modus A, ein nicht freigegebenes Dokument mit Markierungstext) ------------


def _service_setup(db: Session):
    _mode(db, release.MODE_MANUAL)
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Pfadakte")
    db.add(matter)
    db.flush()
    released = Document(matter_id=matter.id, file_path="x", original_filename="frei.txt", ocr_status="not_needed",
                        extracted_text="Freigegebener Vermerk: Betrag 4.250,00 EUR.")
    withheld = Document(matter_id=matter.id, file_path="x", original_filename="offen.txt", ocr_status="not_needed",
                        extracted_text=f"{MARKER}: nicht freigegebener Inhalt.")
    db.add_all([released, withheld])
    db.commit()
    db.add(DocumentRelease(document_id=released.id, text_version=release.text_version_of(released.extracted_text),
                           mode_at_release="manual"))
    db.commit()
    return matter, released, withheld


def _payload_text(payloads) -> str:
    return "".join(json.dumps(p.model_dump(), ensure_ascii=False) for p in payloads)


@pytest.mark.parametrize("path", ["create_draft", "create_draft_stream", "apply_instruction", "review_draft"])
def test_every_drafting_path_excludes_unreleased_documents(db_session, path) -> None:
    from app.attorney_instructions.schema import AttorneyInstructionInput
    from app.attorney_instructions.service import AttorneyInstructionService
    from tests.test_review_engine import FakeReviewProvider, _engine
    from tests.test_web_chat import FakeClaudeWritingProvider, _working_drafting_service

    matter, released, withheld = _service_setup(db_session)
    writer = FakeClaudeWritingProvider("Entwurf.")
    service = _working_drafting_service(writer)
    if path == "create_draft":
        service.create_draft(matter.id, "formulate_draft", db_session)
        payloads = writer.received_payloads
    elif path == "create_draft_stream":
        list(service.create_draft_stream(matter.id, "chat_response", db_session))
        payloads = writer.received_payloads
    elif path == "apply_instruction":
        draft = Draft(matter_id=matter.id, content="Bisheriger Entwurf.")
        db_session.add(draft)
        db_session.commit()
        instructions = AttorneyInstructionService(service)
        instruction = instructions.create_instruction(draft, AttorneyInstructionInput(instruction_text="Kürzer."),
                                                      db_session, actor="qa")
        instructions.apply_instruction(instruction, db_session, purpose="improve_draft", actor="qa")
        payloads = writer.received_payloads
    else:
        draft = Draft(matter_id=matter.id, content="Bisheriger Entwurf.")
        db_session.add(draft)
        db_session.commit()
        provider = FakeReviewProvider()
        _engine(review_provider=provider).review_draft(draft.id, db_session, actor="qa")
        payloads = provider.received_payloads
    assert payloads, "der Pfad hat keine Anfrage erzeugt - Nachweis waere leer"
    sent = _payload_text(payloads)
    assert MARKER not in sent and "XQ9" not in sent


def test_chat_and_mail_paths_exclude_unreleased_documents(client, db_session, writer) -> None:
    from tests.test_upload_without_ai import _ask

    matter, released, withheld = _service_setup(db_session)
    admin = db_session.query(User).filter_by(email="admin@kanzlei.test").one()
    conversation = ChatConversation(matter_id=matter.id, user_id=admin.id, title="Pfade")
    message = Message(matter_id=matter.id, direction="inbound", sender="gericht@example.test",
                      subject="Beschluss", body_text="Bitte um Stellungnahme zum Beschluss.")
    db_session.add_all([conversation, message])
    db_session.commit()
    _ask(client, conversation.id, "Welche Dokumente liegen in der Akte?")  # Chat mit Aktenkontext
    page = client.get("/dashboard/chat").text
    client.post(f"/dashboard/chat/from-message/{message.id}", follow_redirects=False,
                data={"csrf_token": extract_csrf(page), "action": "summarize"})  # E-Mail-Aktion (Aktenkontext)
    assert len(writer.received_payloads) >= 2
    sent = _payload_text(writer.received_payloads)
    assert MARKER not in sent and "XQ9" not in sent and "4.250,00 EUR" in sent  # freigegebener Inhalt bleibt
    before = len(writer.received_payloads)
    assert _ai_action(client, withheld) == 303  # ausdruecklich angefragt: lokaler Hinweis, keine Anfrage
    assert len(writer.received_payloads) == before
