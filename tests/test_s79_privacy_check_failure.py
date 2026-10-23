"""§79 (Befund B1 aus §78): Faellt die Erkennung personenbezogener Daten aus, blockiert der Gateway geordnet
(`GatewayResult(allowed=False)`, fester inhaltsfreier Grund) - Chat UND Schriftsatz zeigen eine verstaendliche Meldung,
kein Provider wird aufgerufen, keine Anfrage erreicht die Leitung.

Vorher: der Schriftsatzpfad (`DraftingService.create_draft`) reichte die Ausnahme der Namenserkennung ungefangen durch
(Web: HTTP 500), der Chat fing sie nur im allgemeinen "unerwarteter technischer Fehler"-Zustand. Nur synthetische
Daten; HTTP-Nachweis ueber den lokalen Aufzeichnungsserver (echter Provider, toter Proxy, gefaelschter Schluessel)."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.privacy.gateway as gateway_module
from app.chat.service import ChatService
from app.models import ApiCallLog, Client, Draft, Matter, Role, User
from app.models.base import Base
from app.privacy.api_logger import categorize_block_reasons, friendly_block_message
from app.privacy.gateway import PRIVACY_CHECK_FAILED_REASON, ClaudePrivacyGateway
from app.privacy.pseudonymizer import Pseudonymizer
from app.privacy.security_check import SecurityCheckService
from tests.test_drafting_service import FakeClaudeWritingProvider, _service
from tests.test_upload_without_ai import _generate
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)

SECRET = "Henrike Wallbaum-Ostertag"  # synthetisch - darf in keinem Log und keiner Meldung auftauchen
SACHVERHALT = f"Unsere Mandantin {SECRET} verlangt die Rückzahlung der Kaution von 4.250,00 EUR."
USER_MESSAGE = "Die Datenschutzprüfung ist fehlgeschlagen"


def _broken_ner(text: str):  # noqa: ANN202
    # bewusst MIT Text in der Fehlermeldung: das Log darf ihn trotzdem nie enthalten
    raise RuntimeError(f"Namenserkennung ausgefallen (QA) bei: {text[:40]}")


@pytest.fixture
def broken_ner(monkeypatch):
    """Simuliert den Ausfall der Presidio-Erkennung fuer jeden danach gebauten Gateway (Standardkonstruktor)."""
    monkeypatch.setattr(gateway_module, "detect_presidio_entities", _broken_ner)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _matter(db: Session) -> Matter:
    matter = Matter(client=Client(name="Ohne Bezug"), title="Kautionsakte")
    db.add(matter)
    db.commit()
    return matter


# --- Gateway --------------------------------------------------------------------------------------------------------


class _BrokenPseudonymizer(Pseudonymizer):
    def pseudonymize(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(f"Absturz in der Pseudonymisierung: {SECRET}")


class _BrokenSecurityCheck(SecurityCheckService):
    def check(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise MemoryError(f"Restrisiko-Scan ohne Speicher: {SECRET}")


@pytest.mark.parametrize("gateway_factory, step, exc_name", [
    (lambda: ClaudePrivacyGateway(), "pseudonymisierung", "RuntimeError"),
    (lambda: ClaudePrivacyGateway(pseudonymizer=_BrokenPseudonymizer()), "pseudonymisierung", "RuntimeError"),
    (lambda: ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(), security_check=_BrokenSecurityCheck()),
     "restrisiko_scan", "MemoryError"),
], ids=["presidio_ausfall", "pseudonymizer_absturz", "restrisiko_scan_absturz"])
def test_gateway_blocks_fail_closed_and_logs_data_sparingly(broken_ner, caplog, gateway_factory, step, exc_name) -> None:
    caplog.set_level(logging.DEBUG)
    result = gateway_factory().prepare_request(purpose="formulate_draft", sachverhalt=SACHVERHALT,
                                               known_entities={"mandant": [SECRET]})
    assert result.allowed is False and result.payload is None and result.mappings == []
    assert result.reasons == [PRIVACY_CHECK_FAILED_REASON]
    assert SECRET not in " ".join(result.reasons)
    log = "\n".join(r.getMessage() for r in caplog.records) + "".join(r.exc_text or "" for r in caplog.records)
    assert f"Schritt {step}, {exc_name}" in log
    assert SECRET not in log and "Wallbaum" not in log and "Kaution" not in log  # weder Text noch Fehlermeldung


def test_block_reason_maps_to_a_clear_content_free_user_message() -> None:
    assert categorize_block_reasons([PRIVACY_CHECK_FAILED_REASON]) == "privacy_check_failed"
    message = friendly_block_message([PRIVACY_CHECK_FAILED_REASON])
    assert message.startswith(USER_MESSAGE) and "nichts an die KI übermittelt" in message


def test_working_gateway_still_allows_and_seals(monkeypatch) -> None:
    """Gegenprobe: ohne Ausfall unveraendert sendefertig, Klartext maskiert."""
    result = ClaudePrivacyGateway().prepare_request(purpose="formulate_draft", sachverhalt=SACHVERHALT,
                                                    known_entities={"mandant": [SECRET]})
    assert result.allowed is True and result.payload is not None
    assert SECRET not in result.payload.model_dump_json()


def test_programming_errors_outside_detection_are_not_swallowed() -> None:
    with pytest.raises(ValueError):
        ClaudePrivacyGateway().prepare_request(purpose="formulate_draft", sachverhalt="x", review_mode="kaputt")


# --- Schriftsatz und Chat (Service) -------------------------------------------------------------------------------------


def test_draft_is_blocked_without_provider_call(broken_ner, db) -> None:
    writer = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider=writer)
    result = service.create_draft(_matter(db).id, "formulate_draft", db, attorney_anmerkungen=SACHVERHALT)
    assert result.success is False and result.blocked_reasons == [PRIVACY_CHECK_FAILED_REASON]
    assert writer.received_payloads == []
    assert db.query(Draft).count() == 0
    blocked = db.query(ApiCallLog).one()  # Audit: blockierter Aufruf mit inhaltsfreier Kategorie
    assert blocked.error_status == "privacy_check_failed" and SECRET not in (blocked.error_status or "")


def test_draft_stream_is_blocked_without_provider_call(broken_ner, db) -> None:
    writer = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider=writer)
    events = list(service.create_draft_stream(_matter(db).id, "formulate_draft", db, attorney_anmerkungen=SACHVERHALT))
    final = events[-1]
    assert final.result is not None and final.result.success is False
    assert final.result.blocked_reasons == [PRIVACY_CHECK_FAILED_REASON]
    assert writer.received_payloads == []


def test_chat_shows_the_privacy_failure_message_without_provider_call(broken_ner, db, tmp_path) -> None:
    role = Role(name="anwalt")
    db.add(role)
    db.commit()
    user = User(email="anwalt@kanzlei.test", password_hash="x", role_id=role.id)
    db.add(user)
    db.commit()
    matter = _matter(db)
    writer = FakeClaudeWritingProvider()
    service, _ = _service(writing_provider=writer)
    chat = ChatService(tmp_path / "chat")
    conversation = chat.create_conversation(db, user=user, matter_id=matter.id, title="Frage", actor=user.email)
    reply = chat.send_message(db, conversation=conversation, content=SACHVERHALT, drafting_service=service,
                              actor=user.email)
    assert reply.blocked is True and reply.content.startswith(USER_MESSAGE)
    assert "unerwarteter technischer Fehler" not in reply.content and SECRET not in reply.content
    assert writer.received_payloads == []


# --- Web (Schriftsatz-Generator und Chat) gegen den lokalen Aufzeichnungsserver ---------------------------------------


def test_web_schriftsatz_shows_message_and_sends_nothing(broken_ner, client, db_session, recorder) -> None:
    matter = Matter(client=Client(name="Synthetischer Mandant"), title="Kautionsakte", status="open")
    db_session.add(matter)
    db_session.commit()
    response = _generate(client, matter_id=matter.id, attorney_anmerkungen=SACHVERHALT)
    # vorher: 500 (ungefangene Ausnahme). Jetzt der bestehende Fehlerweg des Generators (Redirect mit Meldung)
    assert response.status_code == 303, response.status_code
    location = response.headers["location"]
    assert "/edit" not in location and SECRET not in location and "Wallbaum" not in location
    page = client.get(location).text
    assert "Generierung blockiert: " + USER_MESSAGE in page
    assert "nichts an die KI übermittelt" in page and SECRET not in page
    assert recorder.requests == []
    assert db_session.query(Draft).count() == 0


def test_web_chat_shows_message_and_sends_nothing(broken_ner, client, db_session, recorder) -> None:
    from tests.test_upload_without_ai import _ask

    matter = Matter(client=Client(name="Synthetischer Mandant"), title="Kautionsakte", status="open")
    db_session.add(matter)
    db_session.commit()
    events = _ask(client, "", content=SACHVERHALT, matter_id=matter.id)
    done = [e for e in events if e["kind"] == "done"]
    assert done and done[-1]["blocked"] is True
    text = " ".join(str(e) for e in events)
    assert USER_MESSAGE in text and SECRET not in text
    assert recorder.requests == []
