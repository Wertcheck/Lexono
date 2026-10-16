"""§54: "Namen schützen" in Schriftsatz-, Aenderungsauftrags-, Editor- und E-Mail-Formularen - dieselbe Pruefung
(`decisions.parse_manual_terms`) und dieselbe aktenweite Entscheidung wie im Chat, keine zweite Logik.

Echte Endpunkte, echter Gateway (Presidio), Schreib-Provider als Attrappe, die die Payload aufzeichnet; HTTP-Nachweis
ueber den lokalen Aufzeichnungsserver (§47). Nur synthetische Daten."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.chat_router as chat_router_module
import app.web.drafts_router as drafts_router_module
import app.web.schriftsatz_router as schriftsatz_router_module
from app.attorney_instructions.service import AttorneyInstructionService
from app.db.session import get_db
from app.drafting.editor_service import EditorService
from app.main import app
from app.models import ChatConversation, Client, Draft, Matter, Message
from app.models.attorney_instruction import AttorneyInstruction
from app.models.base import Base
from app.models.privacy_decision import PrivacyDecision
from app.web.service_factory import get_editor_service_for_ai_edit
from tests.auth_test_utils import extract_csrf, login_as_admin
from tests.test_web_chat import FakeClaudeWritingProvider, _working_drafting_service

P10 = "Der Richter Zweigelt hat den Termin auf den 22.07.2026 verlegt; Streitwert 4.250,00 EUR nach § 3 ZPO."
LEGAL = ["22.07.2026", "4.250,00 EUR", "§ 3 ZPO"]


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


class EchoWriter(FakeClaudeWritingProvider):
    """Zeichnet die Payload auf und antwortet - wie eine echte Antwort - mit den Platzhaltern der Anfrage (die
    Vollstaendigkeitspruefung der Entwurfspfade verlangt sie; ein fester Text ohne Platzhalter wird zu Recht verworfen)."""

    def write(self, payload):
        self.received_payloads.append(payload)
        from app.ai_providers.claude_writing_provider import ClaudeWritingResult

        placeholders = sorted(set(re.findall(r"\[[A-Z][A-Z_]*_\d{2}\]", json.dumps(payload.model_dump()))))
        echo = payload.anonymisierte_anwaltliche_anmerkungen or ""
        return ClaudeWritingResult(text=f"{echo}\n\nEntwurf (synthetisch): " + " ".join(placeholders), token_count=10)


@pytest.fixture()
def writer() -> EchoWriter:
    return EchoWriter()


@pytest.fixture()
def client(db_session: Session, writer, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    from app.config.settings import Settings

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    service = lambda: _working_drafting_service(writer)  # noqa: E731
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_editor_service_for_ai_edit] = lambda: EditorService(AttorneyInstructionService(service()))
    monkeypatch.setattr(schriftsatz_router_module, "get_drafting_service", service)
    monkeypatch.setattr(schriftsatz_router_module, "get_settings",
                        lambda: Settings(_env_file=None, schriftsatz_upload_storage_dir=str(tmp_path / "uploads")))
    monkeypatch.setattr(drafts_router_module, "get_attorney_instruction_service",
                        lambda: AttorneyInstructionService(service()))
    monkeypatch.setattr(chat_router_module, "get_drafting_service", service)
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _sent(writer, index: int = -1) -> str:
    return json.dumps(writer.received_payloads[index].model_dump(), ensure_ascii=False)


def _matter(db: Session, title: str = "Haselhorst ./. Wendeburg - Mietsache") -> Matter:
    matter = Matter(client=Client(name="Corinna Haselhorst"), title=title)
    db.add(matter)
    db.commit()
    return matter


def _draft(db: Session, content: str) -> Draft:
    matter = _matter(db)
    draft = Draft(matter_id=matter.id, content=content)
    db.add(draft)
    db.commit()
    return draft


# --- Schriftsatz-Generator ----------------------------------------------------------------------------------------


def _generate(client, matter_id: str = "", terms=None, **fields):
    page = client.get("/dashboard/tools/schriftsatz")
    data = {"csrf_token": extract_csrf(page.text), "matter_id": matter_id, **fields}
    if terms is not None:
        data["protected_terms"] = terms if isinstance(terms, str) else json.dumps(terms)
    return client.post("/dashboard/tools/schriftsatz/generate", data=data, follow_redirects=False)


def test_schriftsatz_without_marking_sends_the_unrecognised_judge(client, db_session, writer) -> None:
    """Gegenprobe: die automatische Erkennung findet "Zweigelt" nicht (§50) - deshalb die Markierung."""
    _generate(client, _matter(db_session).id, attorney_anmerkungen=P10)
    assert "Zweigelt" in _sent(writer)


def test_schriftsatz_marking_protects_the_name_and_keeps_legal_content(client, db_session, writer) -> None:
    matter = _matter(db_session)
    response = _generate(client, matter.id, ["Zweigelt"], attorney_anmerkungen=P10)
    assert response.status_code == 303 and "/edit" in response.headers["location"]
    sent = _sent(writer)
    assert "Zweigelt" not in sent and all(value in sent for value in LEGAL)
    decision = db_session.query(PrivacyDecision).one()
    assert (decision.matter_id, decision.conversation_id, decision.decision) == (matter.id, None, "personal")


def test_schriftsatz_names_in_any_field_with_hyphen_apostrophe_and_particles(client, db_session, writer) -> None:
    _generate(client, _matter(db_session).id,
              ["Hans-Peter Müller", "O'Brien", "Anna von Berg", "Bernd von Berg"],
              stil="sachlich, an Dr. Hans-Peter Müller gerichtet",
              vorlage="Muster: Sehr geehrter Herr O’Brien, ...",
              attorney_anmerkungen="Anna von Berg und Bernd von Berg streiten um 2.300,00 EUR; Herr Müller vermittelt.")
    sent = _sent(writer)
    for secret in ("Hans-Peter", "Müller", "Brien", "Anna", "Bernd", "Berg"):
        assert secret not in sent
    assert "2.300,00 EUR" in sent and "sachlich" in sent
    berg = re.findall(r"(\[PERSON_\d+\]) und (\[PERSON_\d+\]) streiten", sent)
    assert berg and berg[0][0] != berg[0][1]  # zwei Personen gleichen Familiennamens bleiben getrennt


@pytest.mark.parametrize("terms,fields", [
    (["Zweigelt"], {"attorney_anmerkungen": "Der Richter hat den Termin verlegt."}),  # Text geaendert
    (["Richter Zweigelt"], {"attorney_anmerkungen": P10}),  # Rolle im Namen
    (["von"], {"attorney_anmerkungen": P10}),
    ("kein json", {"attorney_anmerkungen": P10}),
    ([["Zweigelt"]], {"attorney_anmerkungen": P10}),
])
def test_schriftsatz_invalid_or_stale_marking_creates_and_sends_nothing(client, db_session, writer, terms, fields) -> None:
    matters_before = db_session.query(Matter).count()
    response = _generate(client, "", terms, new_matter_title="Neue Akte", **fields)
    assert response.status_code == 303 and "error=" in response.headers["location"]
    assert "Zweigelt" not in response.headers["location"]
    assert writer.received_payloads == []
    assert db_session.query(PrivacyDecision).count() == 0
    assert db_session.query(Matter).count() == matters_before  # nicht einmal eine Schnellakte
    assert db_session.query(Draft).count() == 0


def test_schriftsatz_failed_storage_sends_nothing(client, db_session, writer, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("simulierter Speicherfehler")

    monkeypatch.setattr(schriftsatz_router_module, "record_manual_terms", broken)
    response = _generate(client, _matter(db_session).id, ["Zweigelt"], attorney_anmerkungen=P10)
    assert "error=" in response.headers["location"] and writer.received_payloads == []


# --- Aenderungsauftrag am Entwurf ---------------------------------------------------------------------------------


def _instruct(client, draft_id: str, text: str, terms=None, apply: bool = True):
    page = client.get(f"/dashboard/drafts/{draft_id}")
    data = {"csrf_token": extract_csrf(page.text), "instruction_text": text}
    if terms is not None:
        data["protected_terms"] = json.dumps(terms)
    url = f"/dashboard/drafts/{draft_id}/instructions" + ("/apply" if apply else "")
    return client.post(url, data=data, follow_redirects=False)


def test_instruction_marking_protects_the_name_and_keeps_legal_content(client, db_session, writer) -> None:
    draft = _draft(db_session, "Ursprünglicher Entwurfstext.")
    response = _instruct(client, draft.id, "Ergänze: " + P10, ["Zweigelt"])
    assert response.status_code == 303 and "error=" not in response.headers["location"]
    sent = _sent(writer)
    assert "Zweigelt" not in sent and all(value in sent for value in LEGAL)


def test_instruction_without_marking_sends_the_unrecognised_name(client, db_session, writer) -> None:
    draft = _draft(db_session, "Ursprünglicher Entwurfstext.")
    _instruct(client, draft.id, "Ergänze: " + P10)
    assert "Zweigelt" in _sent(writer)


def test_name_shown_only_in_the_draft_can_be_protected_for_the_matter(client, db_session, writer) -> None:
    """Gemessen: der Aenderungsauftrag sendet den bisherigen Entwurf nicht mit - ein Name daraus ist trotzdem
    markierbar und gilt dann aktenweit (z. B. fuer einen spaeteren Auftrag, der ihn nennt)."""
    draft = _draft(db_session, P10)
    _instruct(client, draft.id, "Bitte den Ton sachlicher fassen.", ["Zweigelt"])
    assert db_session.query(PrivacyDecision).filter_by(matter_id=draft.matter_id, term="Zweigelt").count() == 1
    _instruct(client, draft.id, "Ergänze den Hinweis von Richter Zweigelt zum Termin.")
    assert "Zweigelt" not in _sent(writer)


def test_instruction_with_stale_marking_stores_and_sends_nothing(client, db_session, writer) -> None:
    draft = _draft(db_session, "Ursprünglicher Entwurfstext ohne Namen.")
    response = _instruct(client, draft.id, "Bitte kürzen.", ["Zweigelt"])
    assert "error=" in response.headers["location"] and writer.received_payloads == []
    assert db_session.query(AttorneyInstruction).count() == 0 and db_session.query(PrivacyDecision).count() == 0


def test_saved_instruction_records_the_marking_for_the_matter(client, db_session, writer) -> None:
    draft = _draft(db_session, "Ursprünglicher Entwurfstext.")
    _instruct(client, draft.id, "Richter Zweigelt bittet um Kürzung.", ["Zweigelt"], apply=False)
    assert db_session.query(PrivacyDecision).filter_by(matter_id=draft.matter_id, term="Zweigelt").count() == 1
    assert writer.received_payloads == []  # "Anmerkung speichern" ruft nichts auf


# --- Editor: KI-Bearbeitung -----------------------------------------------------------------------------------------


def _ai_edit(client, draft_id: str, instruction: str, selected: str = "", terms=None):
    page = client.get(f"/dashboard/drafts/{draft_id}/edit")
    data = {"csrf_token": extract_csrf(page.text), "instruction_text": instruction, "purpose": "improve_draft",
            "selected_text": selected}
    if terms is not None:
        data["protected_terms"] = json.dumps(terms)
    return client.post(f"/dashboard/drafts/{draft_id}/ai-edit", data=data)


def test_editor_marking_protects_names_from_selection_and_draft(client, db_session, writer) -> None:
    draft = _draft(db_session, P10 + " Zeugin Dell'Anna Rossi bestätigte das.")
    response = _ai_edit(client, draft.id, "Formuliere den markierten Satz neu.", "Der Richter Zweigelt hat den Termin",
                        ["Zweigelt", "Dell'Anna Rossi"])
    assert response.status_code == 200 and response.json()["success"] is True
    sent = _sent(writer)
    assert "Zweigelt" not in sent and "Dell'Anna" not in sent and "Rossi" not in sent
    # gesendet werden Anweisung und Auswahl (nicht der ganze Entwurf); deren Inhalt bleibt erhalten
    assert "Der Richter [PERSON_" in sent and "hat den Termin" in sent and "Formuliere den markierten Satz neu." in sent
    assert db_session.query(PrivacyDecision).filter_by(matter_id=draft.matter_id).count() == 2


def test_editor_invalid_marking_is_rejected_without_a_request(client, db_session, writer) -> None:
    draft = _draft(db_session, P10)
    response = _ai_edit(client, draft.id, "Kürzen.", terms=["Weber"])  # kommt nirgends vor
    assert response.status_code == 422 and response.json()["success"] is False
    assert "Weber" not in response.json()["error"] and writer.received_payloads == []


def test_editor_failed_storage_sends_nothing(client, db_session, writer, monkeypatch) -> None:
    import app.web.name_protection as name_protection_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("simulierter Speicherfehler")

    monkeypatch.setattr(name_protection_module, "record_manual_terms", broken)
    draft = _draft(db_session, P10)
    response = _ai_edit(client, draft.id, "Kürzen.", terms=["Zweigelt"])
    assert response.status_code == 422 and writer.received_payloads == []


# --- E-Mail: Zusammenfassen / Antworten -----------------------------------------------------------------------------


def _message(db: Session, body: str) -> Message:
    matter = _matter(db)
    message = Message(matter_id=matter.id, direction="inbound", sender="kanzlei@beispiel-gericht.invalid",
                      subject="Terminsverlegung 3 C 117/26", body_text=body)
    db.add(message)
    db.commit()
    return message


def _from_message(client, message_id: str, action: str, terms=None):
    page = client.get(f"/dashboard/inbox/{message_id}")
    data = {"csrf_token": extract_csrf(page.text), "action": action}
    if terms is not None:
        data["protected_terms"] = json.dumps(terms)
    return client.post(f"/dashboard/chat/from-message/{message_id}", data=data, follow_redirects=False)


@pytest.mark.parametrize("action", ["summarize", "draft_reply"])
def test_email_actions_protect_a_marked_name(client, db_session, writer, action) -> None:
    message = _message(db_session, P10)
    response = _from_message(client, message.id, action, ["Zweigelt"])
    assert response.status_code == 303 and response.headers["location"].startswith("/dashboard/chat/")
    sent = _sent(writer)
    assert "Zweigelt" not in sent and all(value in sent for value in LEGAL)
    assert db_session.query(PrivacyDecision).filter_by(matter_id=message.matter_id).count() == 1


def test_email_action_with_manipulated_marking_starts_nothing(client, db_session, writer) -> None:
    message = _message(db_session, P10)
    response = _from_message(client, message.id, "draft_reply", ["Zweigelt'); DROP TABLE x;--"])
    assert response.status_code == 400 and "Zweigelt" not in response.text
    assert writer.received_payloads == [] and db_session.query(ChatConversation).count() == 0


def test_email_page_offers_the_marking_next_to_both_actions(client, db_session) -> None:
    message = _message(db_session, P10)
    page = client.get(f"/dashboard/inbox/{message.id}").text
    assert 'id="message-protect-btn"' in page and page.count('class="message-protected-terms"') == 2
    assert page.count('class="js-ai-form"') >= 2  # bestehende Ladeanzeige unveraendert


# --- Aktenweite Geltung ueber alle Wege ------------------------------------------------------------------------------


def test_marking_from_the_email_applies_to_the_schriftsatz_and_chat_of_the_same_matter(client, db_session, writer) -> None:
    message = _message(db_session, P10)
    _from_message(client, message.id, "summarize", ["Zweigelt"])
    _generate(client, message.matter_id, attorney_anmerkungen=P10)
    assert "Zweigelt" not in _sent(writer)
    sent_before = len(writer.received_payloads)
    events = client.post("/dashboard/chat/send-stream", data={
        "csrf_token": extract_csrf(client.get("/dashboard/chat").text), "conversation_id": "",
        "matter_id": message.matter_id, "content": P10})
    assert events.status_code == 200 and len(writer.received_payloads) == sent_before + 1
    assert "Zweigelt" not in _sent(writer)


# --- HTTP-Nachweis -------------------------------------------------------------------------------------------------


def test_schriftsatz_marking_never_reaches_the_http_request(client, db_session, monkeypatch) -> None:
    from tests.test_privacy_wire_payload import _provider, _Recorder

    recorder = _Recorder()
    for key, value in (("ANTHROPIC_BASE_URL", recorder.url), ("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1"),
                       ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                       ("NO_PROXY", "127.0.0.1,localhost")):
        monkeypatch.setenv(key, value)
    try:
        monkeypatch.setattr(schriftsatz_router_module, "get_drafting_service",
                            lambda: _working_drafting_service(_provider()))
        response = _generate(client, _matter(db_session).id, ["Zweigelt", "O'Brien"],
                             attorney_anmerkungen=P10 + " Zeuge Seán O’Brien.")
        assert response.status_code == 303 and "error=" not in response.headers["location"]
        assert len(recorder.requests) == 1
        decoded = json.dumps(json.loads(recorder.requests[0]["raw"].decode("utf-8")), ensure_ascii=False)
        assert "Zweigelt" not in decoded and "brien" not in decoded.lower()
        assert all(value in decoded for value in LEGAL)
    finally:
        recorder.server.shutdown()
        recorder.server.server_close()


# --- Rueckfuehrung im gespeicherten Entwurf ---------------------------------------------------------------------------


def test_schriftsatz_draft_restores_compound_names_without_duplicates(client, db_session, writer) -> None:
    text = ("Anna von Berg und Bernd von Berg stritten am 03.11.2026. Frau von Berg zahlte 2.300,00 EUR. "
            "Dr. Hans-Peter Müller vertrat sie; Herr Müller berief sich auf § 535 BGB. Zeuge Seán O'Brien.")
    response = _generate(client, _matter(db_session).id, ["Anna von Berg", "Bernd von Berg", "Hans-Peter Müller", "O'Brien"],
                         attorney_anmerkungen=text)
    assert response.status_code == 303 and "error=" not in response.headers["location"]
    sent = _sent(writer)
    for secret in ("Anna", "Bernd", "Berg", "Hans-Peter", "Müller", "Brien"):
        assert secret not in sent
    content = db_session.query(Draft).order_by(Draft.created_at.desc()).first().content
    assert text in content  # exakt zurueckgefuehrt
    assert "von Anna von Berg" not in content and "Herr Dr." not in content and "Dr. Dr." not in content
