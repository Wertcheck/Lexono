"""§56: "Namen schützen" in hochgeladenen Dokumenten - Akten- und Chat-Dokumentansicht, KI-Aktionen auf Dokumenten.

Dieselbe Pruefung und Speicherung wie Chat/Formulare (`parse_manual_terms`/`record_manual_terms`), geprueft gegen den
extrahierten Dokumenttext und gebunden an dessen angezeigten Textstand (`document_text_version`). Echte Endpunkte,
echter Gateway, echte PDF-Extraktion beim Chat-Upload; HTTP-Nachweis ueber den lokalen Aufzeichnungsserver.
Nur synthetische Daten."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.chat_router as chat_router_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.db.session import get_db
from app.main import app
from app.models import ChatConversation, ChatMessage, Client, Document, Matter
from app.models.base import Base
from app.models.privacy_decision import PrivacyDecision
from app.web.name_protection import document_text_version
from tests.auth_test_utils import extract_upload_token, create_test_user, extract_csrf, login, login_as_admin, seed_roles
from tests.test_web_chat import FakeClaudeWritingProvider, _parse_sse_events, _working_drafting_service

DOC_TEXT = (
    "Beschluss des Amtsgerichts Musterstadt vom 03.11.2026, Az. 3 C 117/26.\n\n"
    "Der Richter Zweigelt hat den Termin auf den 22.07.2026 verlegt; Streitwert 4.250,00 EUR nach § 3 ZPO.\n\n"
    "Zeugen: Anna von Berg und Bernd von Berg. Frau von Berg bestätigte die Zahlung. "
    "Zeuge Seán O'Brien widersprach; Herr O’Brien blieb dabei. Dr. Hans-Peter Müller vertrat die Klägerin."
)
LEGAL = ["03.11.2026", "22.07.2026", "4.250,00 EUR", "§ 3 ZPO"]


class SachverhaltEcho(FakeClaudeWritingProvider):
    """Zeichnet die Payload auf und antwortet mit dem pseudonymisierten Sachverhalt (wie eine Antwort, die den
    Dokumentinhalt wiedergibt) - damit ist die Rueckfuehrung pruefbar."""

    def write(self, payload):
        self.received_payloads.append(payload)
        return ClaudeWritingResult(text=payload.anonymisierter_sachverhalt, token_count=10)


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def writer() -> SachverhaltEcho:
    return SachverhaltEcho()


@pytest.fixture()
def client(db_session: Session, writer, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    from app.config.settings import Settings

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(writer))
    monkeypatch.setattr(chat_router_module, "get_settings",
                        lambda: Settings(_env_file=None, chat_upload_storage_dir=str(tmp_path / "chat_uploads")))
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _sent(writer, index: int = -1) -> str:
    return json.dumps(writer.received_payloads[index].model_dump(), ensure_ascii=False)


def _document(db: Session, text: str = DOC_TEXT) -> Document:
    matter = Matter(client=Client(name="Corinna Haselhorst"), title="Mietsache Kaution 2026")
    db.add(matter)
    db.flush()
    document = Document(matter_id=matter.id, file_path="/tmp/beschluss.pdf", original_filename="Beschluss.pdf",
                        extracted_text=text)
    db.add(document)
    db.commit()
    return document


def _view(client, document: Document) -> str:
    return client.get(f"/dashboard/matters/{document.matter_id}/document/{document.id}").text


def _protect(client, document: Document, terms, *, version: str | None = None, return_to: str = ""):
    page = _view(client, document)
    data = {"csrf_token": extract_csrf(page), "protected_terms": json.dumps(terms) if not isinstance(terms, str) else terms,
            "text_version": version if version is not None else document_text_version(document), "return_to": return_to}
    return client.post(f"/dashboard/matters/{document.matter_id}/document/{document.id}/protect-names", data=data,
                       follow_redirects=False)


def _action(client, document: Document, action: str = "summarize", terms=None, version: str | None = None):
    page = _view(client, document)
    data = {"csrf_token": extract_csrf(page), "action": action}
    if terms is not None:
        data["protected_terms"] = json.dumps(terms)
        data["text_version"] = version if version is not None else document_text_version(document)
    return client.post(f"/dashboard/chat/from-document/{document.id}", data=data, follow_redirects=False)


# --- Gegenprobe und Grundfall --------------------------------------------------------------------------------------


def test_without_marking_the_unrecognised_judge_in_the_document_reaches_the_payload(client, db_session, writer) -> None:
    document = _document(db_session)
    _action(client, document)
    assert "Zweigelt" in _sent(writer)  # automatische Erkennung uebersieht ihn (§50) - deshalb die Markierung


def test_marking_in_the_document_view_protects_the_name_in_every_later_ai_action(client, db_session, writer) -> None:
    document = _document(db_session)
    response = _protect(client, document, ["Zweigelt"])
    assert response.status_code == 303 and "name_protection=saved" in response.headers["location"]
    decision = db_session.query(PrivacyDecision).one()
    assert (decision.matter_id, decision.conversation_id, decision.term, decision.decision) == (
        document.matter_id, None, "Zweigelt", "personal")
    page = client.get(response.headers["location"]).text
    assert "Markierung gespeichert" in page
    assert 'pii-highlight--protected" title="Geschützt (Als Person behandeln)">Zweigelt</mark>' in page
    assert 'pii-highlight--protected' in _view(client, document)  # bleibt bei jedem Oeffnen sichtbar
    _action(client, document)
    sent = _sent(writer)
    assert "Zweigelt" not in sent and all(value in sent for value in LEGAL)


def test_matter_document_view_offers_the_marking_bound_to_the_text_version(client, db_session) -> None:
    document = _document(db_session)
    page = _view(client, document)
    assert 'id="document-protect-btn"' in page and 'id="document-text"' in page and 'id="document-protect-form"' in page
    assert f'name="text_version" value="{document_text_version(document)}"' in page
    assert page.count('class="document-protected-terms"') >= 5  # Speichern + vier KI-Aktionen tragen die Markierung
    assert "/dashboard/static/js/app_name_protection.js" in page


def test_marking_travels_with_the_ai_action_and_is_saved_before_the_request(client, db_session, writer) -> None:
    document = _document(db_session)
    response = _action(client, document, "analyze", ["Zweigelt", "Hans-Peter Müller"])
    assert response.status_code == 303
    sent = _sent(writer)
    assert "Zweigelt" not in sent and "Hans-Peter" not in sent and "Müller" not in sent
    assert db_session.query(PrivacyDecision).filter_by(matter_id=document.matter_id).count() == 2


def test_compound_names_two_people_and_repetitions_in_the_document(client, db_session, writer) -> None:
    document = _document(db_session)
    _protect(client, document, ["Anna von Berg", "Bernd von Berg", "O'Brien", "Hans-Peter Müller", "Zweigelt"])
    _action(client, document)
    sent = _sent(writer)
    for secret in ("Anna", "Bernd", "Berg", "Brien", "BRIEN", "Hans-Peter", "Müller", "Zweigelt"):
        assert secret not in sent
    pair = re.search(r"Zeugen: (\[PERSON_\d+\]) und (\[PERSON_\d+\])", sent)
    assert pair and pair.group(1) != pair.group(2)  # zwei Personen gleichen Familiennamens bleiben getrennt
    obrien = re.findall(r"(?:Zeuge|Herr) (\[PERSON_\d+\])", sent)
    assert len(set(obrien)) == 1  # O'Brien und O’Brien: ein Platzhalter
    assert all(value in sent for value in LEGAL) and "[AKTENZEICHEN_" in sent


def test_reconstruction_restores_the_document_content_exactly(client, db_session, writer) -> None:
    document = _document(db_session)
    _protect(client, document, ["Anna von Berg", "Bernd von Berg", "O'Brien", "Hans-Peter Müller", "Zweigelt"])
    response = _action(client, document)
    conversation_id = response.headers["location"].rsplit("/", 1)[-1]
    answer = (db_session.query(ChatMessage).filter_by(conversation_id=conversation_id, role="assistant").one().content)
    for sentence in ("Der Richter Zweigelt hat den Termin auf den 22.07.2026 verlegt; Streitwert 4.250,00 EUR nach § 3 ZPO.",
                     "Frau von Berg bestätigte die Zahlung.", "Herr O’Brien blieb dabei.",
                     "Dr. Hans-Peter Müller vertrat die Klägerin."):
        assert sentence in answer
    assert "von Anna von Berg" not in answer and "Dr. Dr." not in answer


# --- Bindung an den Textstand, ungueltige und manipulierte Markierungen ----------------------------------------------


def test_changed_document_text_rejects_the_marking_and_starts_nothing(client, db_session, writer) -> None:
    document = _document(db_session)
    old_version = document_text_version(document)
    document.extracted_text = DOC_TEXT.replace("Zweigelt", "Weber")  # z. B. erneute Texterkennung nach dem Oeffnen
    db_session.commit()
    response = _protect(client, document, ["Zweigelt"], version=old_version)
    assert "name_protection_error=" in response.headers["location"] and "Zweigelt" not in response.headers["location"]
    response = _action(client, document, terms=["Weber"], version=old_version)
    assert response.status_code == 400 and "geändert" in response.text and "Weber" not in response.text
    assert writer.received_payloads == [] and db_session.query(PrivacyDecision).count() == 0
    assert db_session.query(ChatConversation).count() == 0


@pytest.mark.parametrize("terms", [
    ["Weber"],  # kommt im Dokument nicht vor
    ["Zweig"],  # nur ein Wortteil
    ["Richter Zweigelt"],  # Rolle im Namen
    ["von"],
    ["Zweigelt'); DROP TABLE x;--"],
    "kein json",
    [],
])
def test_invalid_markings_are_rejected_without_storage_or_request(client, db_session, writer, terms) -> None:
    document = _document(db_session)
    response = _protect(client, document, terms)
    location = response.headers["location"]
    assert response.status_code == 303 and "name_protection_error=" in location
    assert "Zweig" not in location and "Weber" not in location
    assert db_session.query(PrivacyDecision).count() == 0 and writer.received_payloads == []


def test_failed_storage_in_the_ai_action_sends_nothing(client, db_session, writer, monkeypatch) -> None:
    import app.web.name_protection as name_protection_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("simulierter Speicherfehler")

    monkeypatch.setattr(name_protection_module, "record_manual_terms", broken)
    document = _document(db_session)
    response = _action(client, document, terms=["Zweigelt"])
    assert response.status_code == 400 and writer.received_payloads == []
    assert db_session.query(ChatConversation).count() == 0


def test_document_of_another_matter_cannot_be_marked_via_this_matter(client, db_session, writer) -> None:
    document = _document(db_session)
    other = _document(db_session)
    page = _view(client, document)
    response = client.post(f"/dashboard/matters/{other.matter_id}/document/{document.id}/protect-names",
                           data={"csrf_token": extract_csrf(page), "protected_terms": '["Zweigelt"]',
                                 "text_version": document_text_version(document)}, follow_redirects=False)
    assert response.status_code == 404 and db_session.query(PrivacyDecision).count() == 0


def test_return_target_is_limited_to_own_views(client, db_session, writer) -> None:
    document = _document(db_session)
    response = _protect(client, document, ["Zweigelt"], return_to="https://example.invalid/phish")
    assert response.headers["location"].startswith(f"/dashboard/matters/{document.matter_id}/document/{document.id}")
    response = _protect(client, document, ["Zweigelt"], return_to="chat:fremde-unterhaltung")
    assert response.headers["location"].startswith(f"/dashboard/matters/{document.matter_id}/document/")


def test_staff_can_add_protection_but_not_revoke_it(client, db_session, writer) -> None:
    document = _document(db_session)
    staff = create_test_user(db_session, seed_roles(db_session)["mitarbeiter"], "mitarbeiter@kanzlei.test")
    client.cookies.clear()
    login(client, staff.email)
    response = _protect(client, document, ["Zweigelt"])
    assert "name_protection=saved" in response.headers["location"]
    decision = db_session.query(PrivacyDecision).one()
    page = client.get(f"/dashboard/matters/{document.matter_id}").text
    response = client.post(f"/dashboard/matters/{document.matter_id}/privacy-decisions/{decision.id}/revoke",
                           data={"csrf_token": extract_csrf(page)}, follow_redirects=False)
    assert db_session.query(PrivacyDecision).count() == 1  # Aufheben bleibt Admin/Anwalt vorbehalten


# --- Upload im Chat: Extraktion -> Anzeige -> Markierung -> Folgeanfrage ---------------------------------------------


def test_chat_upload_then_mark_in_the_chat_document_view_then_ask(client, db_session, writer) -> None:
    from tests.test_web_privacy_review import _pdf_bytes

    lines = ["Beschluss vom 03.11.2026, Az. 3 C 117/26.", "Der Richter Zweigelt hat den Termin auf den",
             "22.07.2026 verlegt; Streitwert 4.250,00 EUR."]
    response = client.post("/dashboard/chat/send-stream", data={"upload_token": extract_upload_token(client.get("/dashboard/chat").text),
        "csrf_token": extract_csrf(client.get("/dashboard/chat").text), "conversation_id": "",
        "content": "Bitte nur ablegen, noch nichts zusammenfassen."},
        files={"documents": ("beschluss.pdf", _pdf_bytes(lines), "application/pdf")})
    events = _parse_sse_events(response.text)
    conversation_id = events[0]["conversation_id"]
    document = db_session.query(Document).one()
    assert "Zweigelt" in (document.extracted_text or "")  # Extraktion beim Upload (synchron)
    # §57 (schliesst die Grenze §56.4): der Upload sendet NICHTS - auch nicht den mitgetippten Text
    assert writer.received_payloads == []
    assert [e["kind"] for e in events] == ["start", "uploaded"]
    assert events[1]["target"] == f"/dashboard/chat/{conversation_id}/document/{document.id}?uploaded=1&text_not_sent=1#name-protection"
    page = client.get(events[1]["target"].split("#")[0]).text
    assert 'id="document-protect-btn"' in page and 'id="document-text"' in page
    assert 'id="upload-notice"' in page and "noch keine KI-Verarbeitung" in page
    response = _protect(client, document, ["Zweigelt"], return_to=f"chat:{conversation_id}")
    assert response.headers["location"].startswith(f"/dashboard/chat/{conversation_id}/document/{document.id}")
    assert "name_protection=saved" in response.headers["location"]
    client.post("/dashboard/chat/send-stream", data={
        "csrf_token": extract_csrf(client.get("/dashboard/chat").text), "conversation_id": conversation_id,
        "content": "Fasse das angehängte Dokument jetzt zusammen."})
    # die bewusste Folgeanfrage ist die ERSTE Cloud-Anfrage - mit dem Dokument, ohne den markierten Namen
    assert len(writer.received_payloads) == 1 and "Zweigelt" not in _sent(writer)
    assert "Streitwert" in _sent(writer) and "4.250,00" in _sent(writer)


def test_chat_composer_refuses_to_send_while_document_marks_are_unsaved(client, db_session) -> None:
    """Unabhaengiges Review §56 (mittel): eine in der Chat-Dokumentansicht gesetzte, nicht gespeicherte Markierung
    darf beim Senden aus dem Eingabefeld nicht still verloren gehen (der Anhang geht mit). Browser-QA belegt das
    Verhalten; hier: die Sperre und der globale Baustein sind auf der Seite verdrahtet."""
    page = client.get("/dashboard/chat").text
    assert "window.LexonoDocumentNameProtection" in page
    assert "Im Dokument markierte Namen sind noch nicht gespeichert" in page


def test_document_error_message_speaks_of_the_document_text(client, db_session, writer) -> None:
    document = _document(db_session)
    response = _protect(client, document, ["Weber"])
    page = client.get(response.headers["location"]).text
    assert "kommt im Dokumenttext nicht mehr vor" in page and "Weber" not in page


# --- HTTP-Nachweis und Protokolle ----------------------------------------------------------------------------------


def test_document_marking_never_reaches_the_http_request_and_no_log_contains_it(client, db_session, monkeypatch,
                                                                                caplog) -> None:
    from tests.test_privacy_wire_payload import _provider, _Recorder

    recorder = _Recorder()
    for key, value in (("ANTHROPIC_BASE_URL", recorder.url), ("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1"),
                       ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                       ("NO_PROXY", "127.0.0.1,localhost")):
        monkeypatch.setenv(key, value)
    try:
        monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(_provider()))
        document = _document(db_session)
        with caplog.at_level(logging.DEBUG):
            _action(client, document, "summarize", ["Zweigelt", "O'Brien", "Anna von Berg", "Bernd von Berg"])
            _action(client, document, "summarize", ["Zweigelt"], version="veraltet")  # abgewiesen
        assert len(recorder.requests) == 1
        decoded = json.dumps(json.loads(recorder.requests[0]["raw"].decode("utf-8")), ensure_ascii=False)
        for secret in ("Zweigelt", "Brien", "Anna von Berg", "Bernd von Berg"):
            assert secret not in decoded
        assert all(value in decoded for value in LEGAL)
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "Zweigelt" not in logged and "Brien" not in logged
    finally:
        recorder.server.shutdown()
        recorder.server.server_close()
