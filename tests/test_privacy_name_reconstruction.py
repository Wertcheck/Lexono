"""§53: Rueckfuehrung zusammengesetzter Namen ("Frau von [PERSON_02]" wurde zu "Frau von Anna von Berg"), Apostrophe und
laengere Namen bei "Namen schützen".

Rueckfuehrung ist rein lokal (nach der Cloud) - die Pruefungen hier aendern am gesendeten Inhalt nichts; der
HTTP-Nachweis belegt das fuer Apostroph-Namen zusaetzlich. Nur synthetische Daten."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

import app.web.chat_router as chat_router_module
from app.privacy.decisions import InvalidPrivacyDecisionError, validate_manual_terms
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.pseudonymizer import PseudonymMapping, Pseudonymizer
from app.privacy.review import PrivacyDecisions
from tests.auth_test_utils import login_as_admin
from tests.test_web_chat import (  # noqa: F401 - Fixtures
    FakeClaudeWritingProvider,
    _csrf,
    _parse_sse_events,
    _working_drafting_service,
    client,
    db_session,
)


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _run(gateway, text: str, terms=frozenset()):
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: x", anwaltliche_anmerkungen=text,
        privacy_decisions=PrivacyDecisions(personal=frozenset(terms), not_personal=frozenset()), review_mode="mask",
    )
    assert result.allowed, result.reasons
    return result.payload.model_dump()["anonymisierte_anwaltliche_anmerkungen"], result.mappings


# --- Rundweg: Antwort = Payload -> exakt der Originaltext ------------------------------------------------------------


@pytest.mark.parametrize("text,terms", [
    ("Anna von Berg erschien. Frau von Berg widersprach am 03.11.2026.", {"Anna von Berg"}),
    ("Anna von Berg erschien. Frau von Berg widersprach am 03.11.2026.", set()),
    ("Dr. Hans-Peter Müller erschien. Herr Müller verlangte 2.300,00 EUR nach § 535 BGB.", {"Hans-Peter Müller"}),
    ("Herr Müller schrieb zuerst. Später erschien Dr. Hans-Peter Müller persönlich.", {"Hans-Peter Müller"}),
    ("Hans-Peter Müllers Antrag wies Richter Brummelbeck zurück; Brummelbeck vertagte.", {"Hans-Peter Müller", "Brummelbeck"}),
    ("Anna von Berg und Bernd von Berg stritten. Frau von Berg zahlte.", {"Anna von Berg", "Bernd von Berg"}),
    ("Prof. Dr. Erika Wiebe erstattete das Gutachten; Frau Wiebe bestätigte es (Az. 4 C 112/26).", set()),
    ("Seán O'Brien klagte am 03.11.2026; Herr O’Brien verlangte 1.200,00 EUR.", {"O'Brien"}),
    ("Maria del Carmen de la Fuente y Santos erschien. Frau de la Fuente y Santos bestätigte § 280 BGB.",
     {"Maria del Carmen de la Fuente y Santos"}),
])
def test_round_trip_restores_the_exact_original(gateway, text, terms) -> None:
    masked, mappings = _run(gateway, text, terms)
    assert gateway.reconstruct_response(masked, mappings) == text


def test_without_salutation_the_full_name_is_restored(gateway) -> None:
    """Bewusst: ohne Anrede/Titel/Rolle davor der Vollname - im Rubrum muss die Partei vollstaendig stehen."""
    masked, mappings = _run(gateway, "Herr Brenner widerspricht; Karl Brenner unterschrieb.")
    placeholder = mappings[0].placeholder
    assert gateway.reconstruct_response(f"In Sachen {placeholder} gegen die Beklagte.", mappings) == \
        "In Sachen Karl Brenner gegen die Beklagte."


# --- Frei formulierte Antworten (Cloud-KI schreibt anders als der Quelltext) -----------------------------------------


@pytest.fixture(scope="module")
def two_bergs(gateway):
    text = ("Anna von Berg und Bernd von Berg stritten am 03.11.2026. Dr. Hans-Peter Müller vertrat sie; Herr Müller "
            "berief sich auf § 535 BGB.")
    masked, mappings = _run(gateway, text, {"Anna von Berg", "Bernd von Berg"})
    by_value = {m.original_value: m.placeholder for m in mappings}
    return mappings, by_value["Anna von Berg"], by_value["Bernd von Berg"], next(p for v, p in by_value.items() if "Müller" in v)


@pytest.mark.parametrize("answer,expected", [
    ("Frau von {anna} und Herr von {bernd} sind Geschwister.", "Frau von Berg und Herr von Berg sind Geschwister."),
    ("Der Brief von {anna} ging ein.", "Der Brief von Anna von Berg ging ein."),  # Praeposition, kein Namenszusatz
    ("In Sachen {anna} gegen {bernd}.", "In Sachen Anna von Berg gegen Bernd von Berg."),
    ("Sehr geehrter Herr {mueller},", "Sehr geehrter Herr Müller,"),
    ("Ihr Mandant Dr. {mueller} erschien.", "Ihr Mandant Dr. Hans-Peter Müller erschien."),  # kein "Dr. Dr."
    ("Herrn Dr. {mueller} zur Kenntnis.", "Herrn Dr. Hans-Peter Müller zur Kenntnis."),
])
def test_free_answers_get_no_duplicates_and_no_foreign_forms(gateway, two_bergs, answer, expected) -> None:
    mappings, anna, bernd, mueller = two_bergs
    text = answer.format(anna=anna, bernd=bernd, mueller=mueller)
    assert gateway.reconstruct_response(text, mappings) == expected


def test_context_forms_never_cross_to_another_placeholder() -> None:
    mappings = [
        PseudonymMapping("[PERSON_01]", "person", "Anna von Berg", (), (("frau", "von Berg"),)),
        PseudonymMapping("[PERSON_02]", "person", "Bernd von Berg"),
    ]
    out = Pseudonymizer().reconstruct("Frau [PERSON_01] und Frau [PERSON_02].", mappings)
    assert out == "Frau von Berg und Frau Bernd von Berg."


def test_non_person_placeholders_are_restored_unchanged() -> None:
    mappings = [PseudonymMapping("[AKTENZEICHEN_01]", "aktenzeichen", "4 C 112/26"),
                PseudonymMapping("[ORT_01]", "ort", "Bad Berg")]
    out = Pseudonymizer().reconstruct("Frau von [ORT_01], Az. [AKTENZEICHEN_01], § 535 BGB, 2.300,00 EUR.", mappings)
    assert out == "Frau von Bad Berg, Az. 4 C 112/26, § 535 BGB, 2.300,00 EUR."


# --- Apostrophe und laengere Namen ----------------------------------------------------------------------------------


@pytest.mark.parametrize("term", ["O'Brien", "D’Angelo", "Dell'Anna Rossi", "Maria del Carmen de la Fuente y Santos"])
def test_apostrophe_and_long_names_are_accepted(term) -> None:
    assert validate_manual_terms([term], f"Es erschien {term}.") == [term]


def test_marking_matches_the_other_apostrophe_form() -> None:
    assert validate_manual_terms(["O'Brien"], "Es erschien O’Brien.") == ["O'Brien"]


@pytest.mark.parametrize("term", ["'Brien", "O''Brien", "O'", "O'Brien'", "O'Brien'); DROP TABLE x;--", "O'Brien\nX",
                                  "A B C D E F G H I", "O'Brian"])
def test_invalid_or_manipulated_apostrophe_names_are_rejected(term) -> None:
    with pytest.raises(InvalidPrivacyDecisionError):
        validate_manual_terms([term], "Es erschien Seán O'Brien am 03.11.2026.")


def test_name_longer_than_the_storage_limit_is_rejected() -> None:
    long_name = "Maximiliane Konstantina " + "von der " * 6 + "Brummelbeckhausen"
    with pytest.raises(InvalidPrivacyDecisionError):
        validate_manual_terms([long_name], f"Es erschien {long_name}.")


def test_apostrophe_name_never_reaches_the_http_request(client, db_session: Session, monkeypatch) -> None:
    from tests.test_privacy_wire_payload import _provider, _Recorder

    recorder = _Recorder()
    for key, value in (("ANTHROPIC_BASE_URL", recorder.url), ("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1"),
                       ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                       ("NO_PROXY", "127.0.0.1,localhost")):
        monkeypatch.setenv(key, value)
    try:
        monkeypatch.setattr(chat_router_module, "get_drafting_service", lambda: _working_drafting_service(_provider()))
        login_as_admin(db_session, client)
        text = "Seán O'Brien klagte am 03.11.2026; Herr O’Brien verlangte 1.200,00 EUR nach § 280 BGB."
        response = client.post("/dashboard/chat/send-stream", data={
            "csrf_token": _csrf(client), "conversation_id": "", "content": text, "protected_terms": json.dumps(["O'Brien"]),
        })
        events = _parse_sse_events(response.text)
        assert [e for e in events if e["kind"] == "done"][0]["message_id"] is not None
        decoded = json.dumps(json.loads(recorder.requests[0]["raw"].decode("utf-8")), ensure_ascii=False)
        assert "Brien" not in decoded and "brien" not in decoded.lower()  # weder O'Brien noch O’Brien
        for value in ("03.11.2026", "1.200,00 EUR", "§ 280 BGB"):
            assert value in decoded
    finally:
        recorder.server.shutdown()
        recorder.server.server_close()
