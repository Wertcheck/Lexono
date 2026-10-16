"""§52: mehrteilige Namen bei "Namen schützen", Verwaltung gespeicherter Namensentscheidungen, Kleinschreibungs-Grenze.

Echte Endpunkte und echter Gateway (Presidio); HTTP-Nachweis ueber den lokalen Aufzeichnungsserver (§47). Nur
synthetische Namen."""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy.orm import Session

import app.web.chat_router as chat_router_module
from app.models import AuditEvent, ChatConversation
from app.models.privacy_decision import PrivacyDecision
from app.privacy.decisions import InvalidPrivacyDecisionError, record_decision, validate_manual_terms
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.review import MANUAL_NAME_PREFIX_WORDS, NAME_PARTICLES, PrivacyDecisions
from tests.auth_test_utils import create_test_user, login, login_as_admin, seed_roles
from tests.test_web_chat import (  # noqa: F401 - Fixtures
    FakeClaudeWritingProvider,
    _csrf,
    _parse_sse_events,
    _working_drafting_service,
    client,
    db_session,
)

TEXT = ("Dr. Hans-Peter Müller und Anna von Berg erschienen am 03.11.2026 vor dem AG Musterstadt (Az. 4 C 112/26). "
        "Herr Müller verlangte 2.300,00 EUR nach § 535 BGB; Frau von Berg widersprach. Hans-Peter Müllers Antrag "
        "wies die Richterin zurück, Anna von Bergs Zeugin blieb aus.")
LEGAL = ["03.11.2026", "2.300,00 EUR", "§ 535 BGB", "Antrag", "Zeugin", "Richterin", "widersprach"]


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _masked(gateway, text: str, terms: set[str]) -> str:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: x", anwaltliche_anmerkungen=text,
        privacy_decisions=PrivacyDecisions(personal=frozenset(terms), not_personal=frozenset()), review_mode="mask",
    )
    assert result.allowed, result.reasons
    return result.payload.model_dump()["anonymisierte_anwaltliche_anmerkungen"]


# --- Namensform ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("term", ["Hans-Peter Müller", "Anna von Berg", "Jean-Luc de la Fontaine", "van der Meer",
                                  "Zweigelt", "Hans-Peter"])
def test_name_shaped_terms_are_accepted_for_manual_marking(term) -> None:
    assert validate_manual_terms([term], f"Text mit {term} darin.") == [term]


def test_whitespace_inside_a_marked_name_is_normalised() -> None:
    assert validate_manual_terms(["Anna  von\tBerg"], "Anna von Berg kam.") == ["Anna von Berg"]


@pytest.mark.parametrize("term,expected", [
    ("von", "Bitte nur Namen markieren"),
    ("von der", "Ungültiger Begriff|Bitte nur Namen markieren"),
    ("Anna von", "Ungültiger Begriff|Bitte nur Namen markieren"),  # Name endet nicht auf einem Zusatz
    ("Dr. Müller", "Ungültiger Begriff|Bitte nur Namen markieren"),
    ("Richter Zweigelt", "ohne Anrede, Titel oder Rolle"),
    ("Herr Müller", "ohne Anrede, Titel oder Rolle"),
    ("Hans--Peter Müller", "Ungültiger Begriff|Bitte nur Namen markieren"),
    ("A1 Berg", "Ungültiger Begriff"),
    ("Eins Zwei Drei Vier Fünf Sechs Sieben Acht Neun", "Ungültiger Begriff|Bitte nur Namen markieren"),  # §53: max. 8 Teile
])
def test_not_name_shaped_terms_are_rejected(term, expected) -> None:
    with pytest.raises(InvalidPrivacyDecisionError, match=expected):
        validate_manual_terms([term], f"Text: {term}.")


def test_review_answers_stay_single_words(db_session: Session) -> None:
    """Audit 12.10.2026 bleibt: ueber die Rueckfrage kein eingeschleuster Mehrwort-Begriff."""
    with pytest.raises(InvalidPrivacyDecisionError):
        record_decision(db_session, matter_id="m", conversation_id="c", term="Hans-Peter Müller", decision="personal",
                        user_id=None, actor="qa")


# --- Konsistenz ----------------------------------------------------------------------------------------------------


def test_multiword_and_hyphen_names_keep_one_placeholder_each(gateway) -> None:
    sent = _masked(gateway, TEXT, {"Hans-Peter Müller", "Anna von Berg"})
    for secret in ("Hans", "Peter", "Müller", "Anna", "Berg"):
        assert secret not in sent
    first, second = re.match(r"(?:Dr\. )?(\[PERSON_\d+\]) und (\[PERSON_\d+\])", sent).groups()
    assert first != second
    assert f"Herr {first} verlangte" in sent and f"{first}s Antrag" in sent  # Nachname und Genitiv: dieselbe Person
    assert f"{second}s Zeugin" in sent and "Frau" in sent and f"{second} widersprach" in sent  # "Frau von Berg"
    for value in LEGAL:
        assert value in sent
    assert "[AKTENZEICHEN_" in sent  # Aktenzeichen bleibt geschuetzt (eigene Kategorie, unveraendert)


def test_different_people_sharing_a_surname_are_not_merged(gateway) -> None:
    text = "Anna von Berg und Bernd von Berg stritten. Frau Anna von Berg zahlte, Bernd von Berg nicht."
    sent = _masked(gateway, text, {"Anna von Berg", "Bernd von Berg"})
    placeholders = re.findall(r"\[PERSON_\d+\]", sent)
    assert len(set(placeholders)) == 2 and "Berg" not in sent


def test_marked_surname_next_to_marked_full_name_shares_the_placeholder(gateway) -> None:
    sent = _masked(gateway, "Hans-Peter Müller rief an. Müller bestätigte den Termin am 03.11.2026.",
                   {"Hans-Peter Müller", "Müller"})
    placeholders = re.findall(r"\[PERSON_\d+\]", sent)
    assert len(set(placeholders)) == 1 and "Müller" not in sent and "03.11.2026" in sent


def test_name_across_a_line_break_and_in_capitals_is_masked(gateway) -> None:
    sent = _masked(gateway, "Beklagter:\nHANS-PETER MÜLLER\nvertreten durch Hans-Peter\nMüller selbst.",
                   {"Hans-Peter Müller"})
    assert "MÜLLER" not in sent and "Müller" not in sent and "Beklagter" in sent and "vertreten durch" in sent


def test_role_before_a_marked_name_stays_readable(gateway) -> None:
    sent = _masked(gateway, "Die Klage wies Richter Brummelbeck am 03.11.2026 ab.", {"Brummelbeck"})
    assert "Brummelbeck" not in sent and "Richter [PERSON_" in sent and "03.11.2026" in sent


# --- Web: Markierung, veraenderter Text, HTTP ------------------------------------------------------------------------


@pytest.fixture()
def writer(monkeypatch: pytest.MonkeyPatch) -> FakeClaudeWritingProvider:
    fake = FakeClaudeWritingProvider("Antwort erstellt.")
    monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(fake))
    return fake


def _send(client, content: str, terms, conversation_id: str = "") -> list[dict]:
    data = {"csrf_token": _csrf(client), "conversation_id": conversation_id, "content": content,
            "protected_terms": json.dumps(terms)}
    return _parse_sse_events(client.post("/dashboard/chat/send-stream", data=data).text)


def test_changed_spelling_after_marking_sends_nothing(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    events = _send(client, "Hans Peter Müller kam am 03.11.2026.", ["Hans-Peter Müller"])  # Bindestrich entfernt
    assert events[0]["message_id"] is None and writer.received_payloads == []
    assert db_session.query(PrivacyDecision).count() == 0


def test_marked_multiword_names_never_appear_in_the_http_request(client, db_session: Session, monkeypatch) -> None:
    from tests.test_privacy_wire_payload import _provider, _Recorder

    recorder = _Recorder()
    for key, value in (("ANTHROPIC_BASE_URL", recorder.url), ("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1"),
                       ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                       ("NO_PROXY", "127.0.0.1,localhost")):
        monkeypatch.setenv(key, value)
    try:
        monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(_provider()))
        login_as_admin(db_session, client)
        events = _send(client, TEXT, ["Hans-Peter Müller", "Anna von Berg"])
        assert [e for e in events if e["kind"] == "done"][0]["message_id"] is not None
        wire = recorder.requests[0]["raw"].decode("utf-8")
        decoded = json.dumps(json.loads(wire), ensure_ascii=False)
        for secret in ("Hans-Peter", "Müller", "Anna von Berg", "Berg"):
            assert secret not in decoded
        for value in ("03.11.2026", "2.300,00 EUR", "§ 535 BGB"):
            assert value in decoded
    finally:
        recorder.server.shutdown()
        recorder.server.server_close()


def test_chat_page_receives_the_server_word_lists(client, db_session: Session) -> None:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/chat").text
    prefix = json.loads(re.search(r"var NAME_PREFIX_WORDS = (\[.*?\]);", page).group(1))
    particles = json.loads(re.search(r"var NAME_PARTICLES = (\[.*?\]);", page).group(1))
    assert set(prefix) == MANUAL_NAME_PREFIX_WORDS and set(particles) == NAME_PARTICLES  # eine Quelle


# --- Verwaltung gespeicherter Entscheidungen ------------------------------------------------------------------------


def _conversation_with_marking(client, db_session, writer, term="Zweigelt") -> ChatConversation:
    events = _send(client, f"Der Richter {term} hat den Termin auf den 22.07.2026 verlegt.", [term])
    return db_session.query(ChatConversation).filter_by(id=events[0]["conversation_id"]).one()


def _manage(client, matter_id: str, decision_id: str, action: str):
    page = client.get(f"/dashboard/matters/{matter_id}")
    from tests.auth_test_utils import extract_csrf

    return client.post(f"/dashboard/matters/{matter_id}/privacy-decisions/{decision_id}/{action}",
                       data={"csrf_token": extract_csrf(page.text)}, follow_redirects=False)


def test_matter_page_lists_decisions_and_revoking_takes_effect_on_the_next_request(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    conversation = _conversation_with_marking(client, db_session, writer)
    assert "Zweigelt" not in writer.received_payloads[-1].model_dump_json()
    page = client.get(f"/dashboard/matters/{conversation.matter_id}").text
    assert "Geschützte Namen" in page and "Zweigelt" in page and "Schutz aufheben" in page
    decision = db_session.query(PrivacyDecision).one()
    response = _manage(client, conversation.matter_id, decision.id, "revoke")
    assert response.status_code == 303 and response.headers["location"].endswith("#beteiligte")
    assert db_session.query(PrivacyDecision).count() == 0
    audit = db_session.query(AuditEvent).filter_by(event_type="privacy_decision_revoked").one()
    assert "Zweigelt" not in audit.details
    # aufgehoben -> wirkt nicht unbemerkt weiter (die automatische Erkennung findet diesen Namen nicht, §50)
    _send(client, "Der Richter Zweigelt hat den Termin erneut verlegt.", [], conversation_id=conversation.id)
    assert "Zweigelt" in writer.received_payloads[-1].model_dump_json()


def test_revoked_decision_no_longer_applies_to_drafts_of_the_matter(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    conversation = _conversation_with_marking(client, db_session, writer)
    decision = db_session.query(PrivacyDecision).one()
    draft_writer = FakeClaudeWritingProvider("Entwurf.")
    service = _working_drafting_service(draft_writer)
    service.create_draft(conversation.matter_id, "formulate_draft", db_session, attorney_anmerkungen="An Zweigelt schreiben.")
    assert "Zweigelt" not in draft_writer.received_payloads[-1].model_dump_json()
    _manage(client, conversation.matter_id, decision.id, "revoke")
    service.create_draft(conversation.matter_id, "formulate_draft", db_session, attorney_anmerkungen="An Zweigelt schreiben.")
    assert "Zweigelt" in draft_writer.received_payloads[-1].model_dump_json()


def test_staff_can_see_but_not_revoke(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    conversation = _conversation_with_marking(client, db_session, writer)
    decision = db_session.query(PrivacyDecision).one()
    staff = create_test_user(db_session, seed_roles(db_session)["mitarbeiter"], "mitarbeiter@kanzlei.test")
    client.cookies.clear()
    login(client, staff.email)
    page = client.get(f"/dashboard/matters/{conversation.matter_id}").text
    assert "Zweigelt" in page and "Schutz aufheben" not in page
    response = _manage(client, conversation.matter_id, decision.id, "revoke")
    assert response.status_code in (302, 303, 403)
    assert db_session.query(PrivacyDecision).count() == 1  # Schutz bleibt


def test_decision_of_another_matter_cannot_be_revoked_via_this_matter(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    first = _conversation_with_marking(client, db_session, writer, "Zweigelt")
    second = _conversation_with_marking(client, db_session, writer, "Brummelbeck")
    assert first.matter_id != second.matter_id
    foreign = db_session.query(PrivacyDecision).filter_by(matter_id=first.matter_id).one()
    response = _manage(client, second.matter_id, foreign.id, "revoke")
    assert response.status_code == 303 and "error=" in response.headers["location"]
    assert "Zweigelt" not in response.headers["location"]
    assert db_session.query(PrivacyDecision).count() == 2


def test_unsure_can_be_confirmed_and_not_personal_can_reopen_the_question(client, db_session: Session, writer) -> None:
    login_as_admin(db_session, client)
    conversation = _conversation_with_marking(client, db_session, writer)
    unsure = PrivacyDecision(matter_id=conversation.matter_id, conversation_id=None, term="Grün", decision="unsure")
    lifted = PrivacyDecision(matter_id=conversation.matter_id, conversation_id=conversation.id, term="Richter",
                             decision="not_personal")
    db_session.add_all([unsure, lifted])
    db_session.commit()
    page = client.get(f"/dashboard/matters/{conversation.matter_id}").text
    assert "Als Person bestätigen" in page and "Rückfrage wieder aktivieren" in page
    _manage(client, conversation.matter_id, unsure.id, "confirm")
    db_session.refresh(unsure)
    assert unsure.decision == "personal"
    _manage(client, conversation.matter_id, lifted.id, "revoke")
    assert db_session.query(PrivacyDecision).filter_by(id=lifted.id).count() == 0
    response = _manage(client, conversation.matter_id, db_session.query(PrivacyDecision).filter_by(term="Zweigelt").one().id,
                       "confirm")  # "personal" ist keine Unsicher-Entscheidung
    assert "error=" in response.headers["location"]


def test_failure_while_revoking_keeps_the_protection(client, db_session: Session, writer, monkeypatch) -> None:
    import app.web.privacy_decisions_router as router_module

    login_as_admin(db_session, client)
    conversation = _conversation_with_marking(client, db_session, writer)
    decision = db_session.query(PrivacyDecision).one()

    def broken(db, **kwargs):
        db.delete(db.query(PrivacyDecision).filter_by(id=kwargs["decision_id"]).one())
        raise RuntimeError("simulierter Fehler nach dem Loeschen, vor dem Commit")

    monkeypatch.setattr(router_module, "revoke_decision", broken)
    try:
        _manage(client, conversation.matter_id, decision.id, "revoke")
    except RuntimeError:
        pass
    assert db_session.query(PrivacyDecision).filter_by(id=decision.id).count() == 1


# --- Kleinschreibung (Grenze, keine Aenderung) ------------------------------------------------------------------------


@pytest.mark.parametrize("text,word", [
    ("Herr Lange hat angerufen. ich habe mit lange telefoniert.", "lange"),
    ("Frau Weiß schrieb, dass weiß die Kaution zurückfordert.", "weiß"),
])
def test_lowercase_name_written_sloppily_stays_masked(gateway, text, word) -> None:
    """Gegenprobe zur Grenze "wartete lange": spaCy taggt solche klein geschriebenen NAMEN wie das gewoehnliche Wort
    (ADV/VERB) - eine Regel, die kleine Wiederholungen freigibt, legte diese Namen offen. Deshalb bleibt es maskiert."""
    sent = _masked(gateway, text, {text.split()[1]})
    assert f" {word} " not in sent
