"""HITL-Entscheidungen bis zur abschliessenden Antwortpruefung (24.10.2026).

Realer Befund (installierte App, synthetische Finanzamt-Musterdokumente): drei Rueckfragen mit "nicht
personenbezogen" beantwortet, danach blockierte die Antwortpruefung die KI-Antwort ("nicht ausreichend anonymisierter
Wert"). Ursache war NICHT die Entscheidung: der PLZ+Ort-Detektor las die letzten fuenf Ziffern der Steuernummer
"12/345/67890" plus das erste Wort der Folgezeile ("Antrag") als Anschrift; das Alltagswort wurde zum ADRESSE-
Platzhalter, und jede Antwort, die "Antrag" normal verwendete, galt als Leck.

Ausschliesslich synthetische Daten; die Cloud ist ein Test-Double (SIMULATION, keine echte KI-Generierung).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web.chat_router as chat_router_module
from app.models import ChatConversation
from app.models.privacy_decision import PrivacyDecision
from tests.auth_test_utils import extract_upload_token, login_as_admin
from tests.test_web_chat import (  # noqa: F401 - Fixtures
    FakeClaudeWritingProvider,
    _csrf,
    _parse_sse_events,
    _working_drafting_service,
    client,
    db_session,
)

AUFTRAG = "Entwerfe ein Antwort schreiben"  # wortgleich mit dem realen Auftrag
LEAK_MESSAGE = "nicht ausreichend anonymisierten Wert"
DOKUMENT = [
    "Einspruch Umsatzsteuerbescheid 2024 (synthetisches Musterdokument)",
    "Finanzamt Berlin Mitte - Steuernummer 12/345/67890",
    "Antrag",
    "Hiermit legen wir fuer die Muster & Beispiel Handelsservice GmbH Einspruch ein.",
    "Geschaeftsfuehrer: Thomas Berger, Beispielstrasse 17, 10115 Berlin",
    "Wir bestaetigen den Eingang des Schreibens von Richter vom 16.03.2024.",
    "Die Unterlagen wurden am 12.03.2026 durch",
    "Lange geprueft und dem Finanzamt uebergeben.",
]


@pytest.fixture()
def writer(monkeypatch: pytest.MonkeyPatch) -> FakeClaudeWritingProvider:
    fake = FakeClaudeWritingProvider("Sehr geehrte Damen und Herren, [Entwurf].")
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(fake))
    return fake


def _pdf(lines: list[str]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((50, 72 + 16 * i), line)
    data = doc.tobytes()
    doc.close()
    return data


def _upload(client: TestClient) -> str:
    events = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream",
        files={"documents": ("einspruch.pdf", _pdf(DOKUMENT), "application/pdf")},
        data={"upload_token": extract_upload_token(client.get("/dashboard/chat").text), "csrf_token": _csrf(client),
              "conversation_id": "", "content": AUFTRAG},
    ).text)
    assert [e["kind"] for e in events] == ["start", "uploaded"], events
    return events[0]["conversation_id"]


def _order_and_answer_all(client: TestClient, conversation_id: str, decision: str = "not_personal"):
    """Auftrag senden und JEDE Rueckfrage (auch Folgerunden) mit `decision` beantworten. Liefert (Begriffe, done)."""
    events = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream",
        data={"csrf_token": _csrf(client), "conversation_id": conversation_id, "content": AUFTRAG},
    ).text)
    asked: list[str] = []
    for _ in range(5):
        review = next((e for e in events if e["kind"] == "review"), None)
        if review is None:
            break
        terms = [c["term"] for c in review["candidates"]]
        asked += terms
        events = _parse_sse_events(client.post(
            f"/dashboard/chat/{conversation_id}/privacy-review",
            data={"csrf_token": _csrf(client), "message_id": review["message_id"],
                  "decisions": json.dumps([{"term": t, "decision": decision} for t in terms])},
        ).text)
    return asked, next(e for e in events if e["kind"] == "done")


def _sent(writer: FakeClaudeWritingProvider) -> str:
    return writer.received_payloads[-1].model_dump_json()


# --- vollstaendiger Fehlerfall ---------------------------------------------------------------------------------------


def test_full_case_ordinary_word_after_a_tax_number_no_longer_blocks_the_answer(
    client, db_session: Session, writer
) -> None:
    login_as_admin(db_session, client)
    conversation_id = _upload(client)
    writer.response_text = (
        "Sehr geehrte Damen und Herren, zu Ihrem Antrag nehmen wir fuer [ORGANISATION_01] Stellung. "
        "Steuerpflichtiger ist die Gesellschaft. Mit freundlichen Gruessen"
    )
    asked, done = _order_and_answer_all(client, conversation_id)
    assert {"Richter", "Lange"} <= set(asked)  # mehrere Rueckfragen
    assert done.get("blocked") is not True, done["content"]
    assert LEAK_MESSAGE not in done["content"]
    assert len(writer.received_payloads) == 1
    # Schutz unveraendert: die Steuernummer selbst ging NICHT im Klartext hinaus.
    assert "12/345/67890" not in _sent(writer)


STEUER_DOKUMENT = [
    "Gewerbesteuerbescheid 2024 (synthetisches Musterdokument)",
    "Bezirksfinanzamt / Gewerbesteuerstelle Berlin - Musterfall",
    "Gewerbeertrag",
    "71.800,00 EUR",
    "Steuermessbetrag",
    "2.513,00 EUR",
    "Festgesetzte Gewerbesteuer",
    "10.303,30 EUR",
    "Rohergebnis",
    "212.900,00 EUR",
    "Registervermerk",
    "Die Gesellschaft ist seit dem 15.02.2024 eingetragen.",
    "Empfohlene Aktenaktion",
    "Zahlungsstatus pruefen - Einspruchsfristen pruefen.",
    "Geschaeftsfuehrer: Thomas Berger",
]


def test_full_case_ordinary_tax_terms_in_the_answer_no_longer_block(client, db_session: Session, writer) -> None:
    """Zweite Ursache (24.10.2026): Tabellenbeschriftungen/Satzanfaenge wurden von der NER als Person/Organisation/Ort
    maskiert; die KI schrieb diese Alltagswoerter, und die Leck-Pruefung blockierte. SIMULATION (Test-Double)."""
    login_as_admin(db_session, client)
    events = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream",
        files={"documents": ("gewerbesteuer.pdf", _pdf(STEUER_DOKUMENT), "application/pdf")},
        data={"upload_token": extract_upload_token(client.get("/dashboard/chat").text), "csrf_token": _csrf(client),
              "conversation_id": "", "content": AUFTRAG},
    ).text)
    conversation_id = events[0]["conversation_id"]
    writer.response_text = (
        "Die Gesellschaft hat das Rohergebnis korrekt ermittelt; der Steuermessbetrag und die Festgesetzte "
        "Gewerbesteuer sind zu pruefen. Das Bezirksfinanzamt bzw. die Gewerbesteuerstelle wird um Pruefung des "
        "Zahlungsstatus gebeten."
    )
    _, done = _order_and_answer_all(client, conversation_id)
    assert done.get("blocked") is not True, done["content"]
    assert "Thomas Berger" not in _sent(writer)  # der echte Name bleibt geschuetzt


# --- Persistierung und Wiederaufnahme --------------------------------------------------------------------------------


def test_multiple_decisions_are_persisted_for_the_right_conversation_and_term(
    client, db_session: Session, writer
) -> None:
    login_as_admin(db_session, client)
    conversation_id = _upload(client)
    asked, done = _order_and_answer_all(client, conversation_id)
    conversation = db_session.get(ChatConversation, conversation_id)
    db_session.expire_all()
    rows = db_session.query(PrivacyDecision).filter_by(matter_id=conversation.matter_id).all()
    assert {r.term for r in rows} == set(asked) and len(rows) == len(asked) >= 2
    assert all(r.decision == "not_personal" and r.conversation_id == conversation_id for r in rows)  # eng: nur hier
    assert done.get("blocked") is not True and len(writer.received_payloads) == 1  # Wiederaufnahme lief durch


# --- abschliessende Antwortpruefung ----------------------------------------------------------------------------------


def test_terms_decided_not_personal_may_appear_in_the_answer(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    conversation_id = _upload(client)
    writer.response_text = "Laut Zeugenaussage Richter wurden die Unterlagen durch Lange geprueft."
    _, done = _order_and_answer_all(client, conversation_id)
    assert done.get("blocked") is not True, done["content"]
    sent = _sent(writer)
    assert "Richter" in sent and "Lange" in sent  # entschieden "nicht personenbezogen" -> lesbar versendet


def test_a_decision_releases_only_its_own_term(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    conversation_id = _upload(client)
    _order_and_answer_all(client, conversation_id)
    sent = _sent(writer)
    for protected in ("Thomas Berger", "Beispielstrasse 17", "12/345/67890", "Muster & Beispiel Handelsservice GmbH"):
        assert protected not in sent, protected


@pytest.mark.parametrize("leaked", ["Thomas Berger", "12/345/67890"])
def test_a_really_protected_value_in_the_answer_is_still_blocked(
    client, db_session: Session, writer, leaked: str
) -> None:
    login_as_admin(db_session, client)
    conversation_id = _upload(client)
    writer.response_text = f"Sehr geehrte Damen und Herren, betreffend {leaked} nehmen wir Stellung."
    _, done = _order_and_answer_all(client, conversation_id)
    assert done.get("blocked") is True and LEAK_MESSAGE in done["content"]


def test_a_placeholder_from_another_request_is_not_resolved_but_blocked(client, db_session: Session, writer) -> None:
    """Veraltetes Mapping: ein Platzhalter, den DIESE Anfrage nie vergeben hat, wird nicht gegen einen fremden/alten
    Wert aufgeloest, sondern fuehrt zur Blockade."""
    login_as_admin(db_session, client)
    conversation_id = _upload(client)
    writer.response_text = "Sehr geehrte Damen und Herren, wir vertreten [PERSON_99]."
    _, done = _order_and_answer_all(client, conversation_id)
    assert done.get("blocked") is True
    assert "[PERSON_99]" not in done["content"]


def test_a_not_personal_decision_does_not_carry_over_to_another_conversation(
    client, db_session: Session, writer
) -> None:
    login_as_admin(db_session, client)
    first = _upload(client)
    first_asked, _ = _order_and_answer_all(client, first)
    second = _upload(client)
    events = _parse_sse_events(client.post(
        "/dashboard/chat/send-stream", data={"csrf_token": _csrf(client), "conversation_id": second, "content": AUFTRAG},
    ).text)
    review = next(e for e in events if e["kind"] == "review")  # erneut gefragt, nicht still uebernommen
    assert {"Richter", "Lange"} <= {c["term"] for c in review["candidates"]} <= set(first_asked) | {"Richter", "Lange"}
    assert len(writer.received_payloads) == 1  # nur die erste Unterhaltung ging hinaus
