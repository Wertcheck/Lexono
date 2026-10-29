"""§64 P1: Vorab-Pruefung der Dokumentfreigabe (Modus C) nach dem Commit - Mail-Zuordnung, Kontextaenderungen,
lokaler Hintergrund-Thread. Nur synthetische Daten; Provider-Spione und Netzwerk-Riegel (keine KI-/Cloud-Aufrufe)."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.documents import precheck_queue, release
from app.matching.schema import MatchResult
from app.models import Base, Client, Document, Matter, Message, Party
from app.models.document_release import DocumentPrivacyCheck
from app.privacy.decisions import DECISION_NOT_PERSONAL, DECISION_PERSONAL, record_decision
from tests.auth_test_utils import create_test_user, extract_csrf, seed_roles
from tests.test_document_release import CLEAN, UNCERTAIN, _mode
from tests.test_no_ai_on_open import spies  # noqa: F401  (Fixture)
from tests.test_upload_without_ai import client, db_session, writer  # noqa: F401  (Fixtures)


def _checks(db: Session, document: Document) -> list[DocumentPrivacyCheck]:
    db.expire_all()
    return db.query(DocumentPrivacyCheck).filter_by(document_id=document.id).all()


def _mail_with_attachment(db: Session, text: str = CLEAN) -> tuple[Matter, Message, Document]:
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Mailzuordnung")
    message = Message(matter_id=None, direction="inbound", sender="absender@example-testdomain.invalid",
                      subject="Eingang", body_text="Siehe Anlage.")
    db.add_all([matter, message])
    db.flush()
    # wie beim Mail-Abruf: verarbeitet (Text vorhanden), aber noch OHNE Akte
    document = Document(matter_id=None, message_id=message.id, original_filename="anlage.txt", file_path="x",
                        extracted_text=text, ocr_status="not_needed")
    db.add(document)
    db.commit()
    return matter, message, document


# --- Mail-Anhaenge: Vorab-Pruefung bei der Zuordnung ---------------------------------------------------------------


def test_automatic_mail_assignment_prechecks_the_attachments(db_session, spies) -> None:
    from app.matching.service import MatterAssignmentService

    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, message, document = _mail_with_attachment(db_session)
    assert _checks(db_session, document) == []  # ohne Akte keine Pruefung (kein Pruefkontext)

    class _Matcher:
        def match_message(self, message, db, classification_ok):
            return MatchResult(decision="auto_assigned", matter_id=matter.id, confidence=0.99, reasoning="synthetisch")

    MatterAssignmentService(_Matcher(), classification_low_confidence_threshold=0.0).assign_matter(message, db_session)
    checks = _checks(db_session, document)
    assert [c.outcome for c in checks] == ["passed"]
    assert checks[0].text_version == release.text_version_of(document.extracted_text)
    assert spies["provider"] == [] and spies["network"] == []


def test_accepting_a_matter_suggestion_prechecks_the_attachments(client, db_session, spies) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, message, document = _mail_with_attachment(db_session, UNCERTAIN)
    page = client.get(f"/dashboard/inbox/{message.id}").text
    response = client.post(f"/dashboard/inbox/{message.id}/assign-matter", follow_redirects=False,
                           data={"csrf_token": extract_csrf(page), "matter_id": matter.id})
    assert response.status_code == 303
    assert [c.outcome for c in _checks(db_session, document)] == ["uncertain"]
    # Vorab-Ergebnis "auffaellig" -> Entscheidung erforderlich, keine Freigabe
    assert release.processing_state(db_session, document) == release.STATE_DECISION
    assert spies["provider"] == [] and spies["network"] == []


def test_no_precheck_outside_mode_c(db_session) -> None:
    for mode in (release.MODE_AUTO, release.MODE_MANUAL):
        _mode(db_session, mode)
        matter, message, document = _mail_with_attachment(db_session)
        document.matter_id = matter.id
        db_session.commit()
        assert _checks(db_session, document) == []


# --- Kontextaenderungen: Neuvorpruefung der Akte -------------------------------------------------------------------


def _matter_with_documents(db: Session, count: int = 3) -> tuple[Matter, list[Document]]:
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Kontext")
    db.add(matter)
    db.flush()
    documents = [Document(matter_id=matter.id, file_path="x", original_filename=f"d{i}.txt",
                          extracted_text=f"{CLEAN} Lfd. Nr. {i}.", ocr_status="not_needed") for i in range(count)]
    db.add_all(documents)
    db.commit()
    for document in documents:
        release.check_document(db, document)  # Ausgangslage: gepruefte Dokumente im alten Kontext
    return matter, documents


def test_a_matter_wide_decision_rechecks_the_matter_in_the_background(db_session, spies) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, documents = _matter_with_documents(db_session)
    before = {c.context_key for d in documents for c in _checks(db_session, d)}
    record_decision(db_session, matter_id=matter.id, conversation_id=None, term="Kaltenbach-Ost",
                    decision=DECISION_PERSONAL, user_id=None, actor="qa", manual=True)
    db_session.commit()
    for document in documents:
        keys = {c.context_key for c in _checks(db_session, document)}
        assert len(keys) == 2 and keys - before  # neuer Kontext vorab geprueft
    assert spies["provider"] == [] and spies["network"] == []


def test_a_conversation_decision_does_not_trigger_a_matter_recheck(db_session) -> None:
    from app.models import ChatConversation

    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, documents = _matter_with_documents(db_session, 1)
    user = create_test_user(db_session, seed_roles(db_session)["anwalt"], "anwalt@kanzlei.test")
    conversation = ChatConversation(matter_id=matter.id, user_id=user.id, title="Gespraech")
    db_session.add(conversation)
    db_session.commit()
    record_decision(db_session, matter_id=matter.id, conversation_id=conversation.id, term="Kaution",
                    decision=DECISION_NOT_PERSONAL, user_id=None, actor="qa")
    db_session.commit()
    assert len(_checks(db_session, documents[0])) == 1  # nur gespraechsbezogen: Pruefkontext der Akte unveraendert


def test_a_new_party_rechecks_the_matter(db_session) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, documents = _matter_with_documents(db_session, 2)
    db_session.add(Party(matter_id=matter.id, name="Herr Synthetik Gegner", role="Gegner"))
    db_session.commit()
    assert all(len(_checks(db_session, d)) == 2 for d in documents)


def test_the_matter_recheck_is_bounded(db_session, monkeypatch) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    monkeypatch.setattr(precheck_queue, "MAX_DOCUMENTS_PER_MATTER", 2)
    matter, documents = _matter_with_documents(db_session, 4)
    db_session.add(Party(matter_id=matter.id, name="Frau Synthetik Zeugin", role="Zeugin"))
    db_session.commit()
    assert sorted(len(_checks(db_session, d)) for d in documents) == [1, 1, 2, 2]  # nur die neuesten


# --- Hintergrund-Thread (eigene Verbindung: Datei-Datenbank) -------------------------------------------------------


@pytest.fixture()
def file_db(tmp_path):
    assert precheck_queue.wait_idle(30)  # keine Restauftraege anderer Tests im gemeinsamen Hintergrund-Thread
    engine = create_engine(f"sqlite:///{tmp_path / 'precheck.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        assert precheck_queue.wait_idle(30)
        session.close()
        engine.dispose()


def _gate_worker(monkeypatch):
    """Haelt die Pruefung NUR im Hintergrund-Thread an, bis `release_gate` gesetzt ist."""
    gate = threading.Event()
    threads: list[str] = []
    original = release._run_central_check

    def gated(*args, **kwargs):
        threads.append(threading.current_thread().name)
        if threading.current_thread().name == "lexono-document-precheck":
            assert gate.wait(30)
        return original(*args, **kwargs)

    monkeypatch.setattr(release, "_run_central_check", gated)
    return gate, threads


def test_the_upload_does_not_wait_for_the_precheck(file_db, monkeypatch, spies, tmp_path) -> None:
    from app.documents.service import DocumentProcessingService

    _mode(file_db, release.MODE_AUTO_REVIEW)
    gate, threads = _gate_worker(monkeypatch)
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Hintergrund")
    file_db.add(matter)
    file_db.flush()
    path = tmp_path / "eingang.txt"
    path.write_text(CLEAN, encoding="utf-8")
    document = Document(matter_id=matter.id, file_path=str(path), original_filename="eingang.txt")
    file_db.add(document)
    file_db.commit()

    DocumentProcessingService(ocr_enabled=False).process_document(document, file_db, actor="qa")
    assert document.extracted_text  # Verarbeitung abgeschlossen, waehrend die Vorab-Pruefung noch wartet
    assert _checks(file_db, document) == []
    gate.set()
    assert precheck_queue.wait_idle(30)
    assert [c.outcome for c in _checks(file_db, document)] == ["passed"]
    assert threads == ["lexono-document-precheck"]  # im Hintergrund, nicht im Request
    assert spies["provider"] == [] and spies["network"] == []


def test_a_pending_precheck_never_releases_and_the_send_checks_itself(file_db, monkeypatch) -> None:
    _mode(file_db, release.MODE_AUTO_REVIEW)
    gate, threads = _gate_worker(monkeypatch)
    matter, message, document = _mail_with_attachment(file_db, UNCERTAIN)
    document.matter_id = matter.id
    file_db.commit()  # Vorab-Pruefung eingeplant, haengt im Thread
    # Versand waehrenddessen: eigene, verbindliche Pruefung -> auffaellig -> Entscheidung erforderlich
    assert release.released_document_ids(file_db, [document]) == set()
    gate.set()
    assert precheck_queue.wait_idle(30)
    assert release.processing_state(file_db, document) == release.STATE_DECISION


def test_a_failing_background_precheck_is_not_trusted(file_db, monkeypatch) -> None:
    _mode(file_db, release.MODE_AUTO_REVIEW)

    def boom(*args, **kwargs):
        if threading.current_thread().name == "lexono-document-precheck":
            raise RuntimeError("Abbruch (synthetisch)")
        raise RuntimeError("auch beim Versand nicht pruefbar (synthetisch)")

    monkeypatch.setattr(release, "_run_central_check", boom)
    matter, message, document = _mail_with_attachment(file_db)
    document.matter_id = matter.id
    file_db.commit()
    assert precheck_queue.wait_idle(30)
    assert _checks(file_db, document) == []  # Fehler wird nicht gespeichert
    assert release.processing_state(file_db, document) == release.STATE_BLOCKED  # fail-closed
    monkeypatch.undo()
    assert release.processing_state(file_db, document) == release.STATE_RELEASED_AUTO


def test_a_text_change_after_the_background_precheck_is_checked_again(file_db) -> None:
    _mode(file_db, release.MODE_AUTO_REVIEW)
    matter, message, document = _mail_with_attachment(file_db)
    document.matter_id = matter.id
    file_db.commit()
    assert precheck_queue.wait_idle(30)
    old_version = release.text_version_of(document.extracted_text)
    document.extracted_text = UNCERTAIN  # neuer Textstand (z. B. erneute OCR)
    file_db.commit()
    assert precheck_queue.wait_idle(30)
    versions = {c.text_version: c.outcome for c in _checks(file_db, document)}
    assert versions[old_version] == "passed"
    assert versions[release.text_version_of(UNCERTAIN)] == "uncertain"
    assert release.processing_state(file_db, document) == release.STATE_DECISION  # alter Stand zaehlt nicht


def test_a_changed_client_name_rechecks_the_matters_of_the_client(db_session) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, documents = _matter_with_documents(db_session, 2)
    matter.client.name = "Synthetische Mandantin Neu GmbH"
    db_session.commit()
    assert all(len(_checks(db_session, d)) == 2 for d in documents)  # Pruefkontext (Mandantenname) geaendert


def test_a_failing_hook_never_fails_the_callers_commit(db_session, monkeypatch) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, message, document = _mail_with_attachment(db_session)

    def boom(*args, **kwargs):
        raise RuntimeError("Hook-Fehler (synthetisch)")

    monkeypatch.setattr(precheck_queue, "_collect_changes", boom)
    document.matter_id = matter.id
    db_session.commit()  # kein Fehler beim Aufrufer
    monkeypatch.undo()
    monkeypatch.setattr(precheck_queue, "_dispatch_pending", boom)
    document.extracted_text = UNCERTAIN
    db_session.commit()
    db_session.expire_all()
    assert document.matter_id == matter.id and document.extracted_text == UNCERTAIN  # gespeichert
    assert _checks(db_session, document) == []  # nur die Vorwaermung fehlt ...
    assert release.processing_state(db_session, document) == release.STATE_DECISION  # ... der Versand prueft selbst


def test_a_rolled_back_change_schedules_nothing(db_session) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter, message, document = _mail_with_attachment(db_session)
    document.matter_id = matter.id
    db_session.flush()
    db_session.rollback()
    db_session.commit()
    assert _checks(db_session, document) == []
