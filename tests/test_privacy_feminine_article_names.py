"""Nachname nach femininem Artikel (25.10.2026). Benchmark-Restlecks (bench_article_noun_persons 11, bench_name_followups
26) nachgestellt: "die Zimmermann", "mit der König", "die Stuckenschneider" gingen im Klartext an den Provider. Zwei enge
Lesarten in review._feminine_article_name_reading - Genus-Unstimmigkeit (Artikel feminin Singular, Wort maskulin/neutral)
und "die" + unbekanntes Wort (kein femininer Fach-Kopf). Rueckfrage bzw. vorsichtige Maskierung, keine sichere Erkennung.

Ausschliesslich synthetische Daten; geprueft wird der Payload-Sachverhalt bzw. die Leitung (Loopback-Recorder)."""

from __future__ import annotations

import pytest

from app.ai_providers.local_ai_provider import _normalize_document_text
from app.privacy.gateway import ClaudePrivacyGateway
from tests.test_upload_without_ai import _ask, _pdf_bytes, _upload, _wire, client, db_session, recorder, writer  # noqa: F401


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _document_payload(gateway, text: str, mode: str = "mask"):
    """Wie scripts/bench_article_noun_persons.py: Dokumentpfad, Akte + Dokumentkopf."""
    return gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Beispielakte\n[Vertrag] " + _normalize_document_text(text),
        anwaltliche_anmerkungen="Fasse das Dokument zusammen.", review_mode=mode,
    )


PROTECTED = [
    # Benchmark-Faelle
    ("Nach Rücksprache am 12.03.2026 hat die Zimmermann die Unterlagen übergeben.", "Zimmermann"),
    ("Die Stuckenschneider ist bislang nicht eingegangen.", "Stuckenschneider"),
    ("Nach Rücksprache am 12.03.2026 hat die Grotelueschen die Unterlagen übergeben.", "Grotelueschen"),
    ("Im Termin wurde mit der König die Zahlung von 1.200,00 EUR erörtert.", "König"),
    ("Nach Rücksprache hat die Kaiser die Unterlagen übergeben.", "Kaiser"),
    ("Im Termin wurde mit der Vogel die Zahlung von 1.200,00 EUR erörtert.", "Vogel"),
    # neue synthetische Namen (nicht im Benchmark)
    ("Nach Rücksprache hat die Kaufmann die Unterlagen übergeben.", "Kaufmann"),
    ("Im Termin wurde mit der Schäfer die Zahlung erörtert.", "Schäfer"),
    ("Die Wendebrink ist bislang nicht erschienen.", "Wendebrink"),
    ("Nach Rücksprache hat die Ostkamp die Unterlagen übergeben.", "Ostkamp"),
]


@pytest.mark.parametrize("text, name", PROTECTED)
def test_name_after_a_feminine_article_is_not_sent_in_clear_text(gateway, text: str, name: str) -> None:
    result = _document_payload(gateway, text)
    assert result.allowed, result.reasons
    assert name not in result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize("text, name", PROTECTED[:4])
def test_ask_mode_asks_instead_of_sending(gateway, text: str, name: str) -> None:
    result = _document_payload(gateway, text, "ask")
    if result.review_required:
        assert result.payload is None and name in {c.term for c in result.review_candidates}
    else:
        assert name not in result.payload.anonymisierter_sachverhalt


# --- Gegenproben: Artikel und Wort stimmen ueberein, Fachwoerter, Gesetzeszitate ------------------------------------

READABLE = [
    ("Die Akte wurde übergeben.", "Akte"),
    ("Die Leiter ist umgefallen.", "Leiter"),
    ("Mit der Kammer wurde der Streitwert von 12.400,00 EUR erörtert.", "Kammer"),
    ("Die Höhe der Miete ist streitig.", "Höhe"),
    ("Die Abnahmeerklärung ist bislang nicht eingegangen.", "Abnahmeerklärung"),
    ("Die Abschlagsrechnung ist bislang nicht eingegangen.", "Abschlagsrechnung"),
    ("Die Kostenfestsetzung ist bislang nicht eingegangen.", "Kostenfestsetzung"),
    ("Die Bauhandwerkersicherung ist bislang nicht eingegangen.", "Bauhandwerkersicherung"),
    ("Die Fischer haben die Netze eingeholt.", "Fischer"),
    ("Die Mutter des Klägers war anwesend.", "Mutter"),
    ("Die Richterin hat den Vergleich protokolliert.", "Richterin"),
    ("Die Vermieterin hat die Kaution einbehalten.", "Vermieterin"),
    ("Die Gesellschaft hat die Zahlung verweigert.", "Gesellschaft"),
    ("Die Klägerin stützt sich auf Art. 3 GG und § 823 BGB.", "Art. 3 GG und § 823 BGB"),
]


@pytest.mark.parametrize("question", [
    "Wie ist die Muster Consulting AG im Handelsregister eingetragen?",
    "Welche Pflichten hat die Bau Werk GmbH nach § 241 BGB?",
])
def test_feminine_article_before_a_firm_name_is_no_name_signal(gateway, question: str) -> None:
    """"die" gehoert zur Firma (die Firma/Gesellschaft) - keine Genus-Lesart vor einer Rechtsform (Regression 25.10.2026)."""
    result = gateway.prepare_request(purpose="chat_response", sachverhalt="Allgemeine Frage ohne Aktenbezug.",
                                     anwaltliche_anmerkungen=question, skip_general_knowledge_pseudonymization=True,
                                     review_mode="ask")
    assert not result.review_required, [c.term for c in result.review_candidates]
    assert result.allowed


@pytest.mark.parametrize("text, word", READABLE)
def test_matching_article_terms_and_citations_stay_readable(gateway, text: str, word: str) -> None:
    result = _document_payload(gateway, text)
    assert result.allowed and word in result.payload.anonymisierter_sachverhalt


# --- dokumentierte Grenzen (bewusst offen, siehe Bericht 25.10.2026) -------------------------------------------------


@pytest.mark.xfail(strict=True, reason="'Der' + Gattungswort-Nachname als Subjekt: nicht von 'Der Bauer hat die Pacht "
                   "gezahlt' (Negativfall, muss lesbar bleiben) unterscheidbar - Bedeutungswissen ueber das Verb fehlt")
@pytest.mark.parametrize("name", ["Bauer"])
def test_known_limit_masculine_article_common_noun_surname(gateway, name: str) -> None:
    result = _document_payload(gateway, f"Der {name} hat am 12.03.2026 angerufen.")
    assert name not in result.payload.anonymisierter_sachverhalt


@pytest.mark.parametrize("name", ["Winter", "Vogel"])
def test_masculine_article_surname_as_acting_party_is_masked(gateway, name: str) -> None:
    """26.10.2026 (review._surname_noun_as_party): als Subjekt einer Parteihandlung erreichen diese Woerter jetzt die
    bestehende Pruefung mit dem staerksten Namensrahmen und werden maskiert; vorher dokumentierte Grenze. Fuer "Bauer"
    bleibt sie bestehen (Negativfall unten) - dort fragt der Chat nur nach (ask_only)."""
    result = _document_payload(gateway, f"Der {name} hat am 12.03.2026 angerufen.")
    assert name not in result.payload.anonymisierter_sachverhalt


def test_documented_negative_role_noun_with_party_act_stays_readable(gateway) -> None:
    result = _document_payload(gateway, "Der Bauer hat die Pacht für das Feld gezahlt.")
    assert result.allowed and "Der Bauer hat die Pacht" in result.payload.anonymisierter_sachverhalt


# --- Leitung (Loopback): kein Klartext im tatsaechlich gesendeten Auftrag ------------------------------------------


def test_chat_wire_does_not_contain_the_name(client, recorder) -> None:
    conversation_id = _upload(client, data=_pdf_bytes(["Vergleich vom 05.12.2026 über 1.100,00 EUR."]))[0][
        "conversation_id"]
    events = _ask(client, conversation_id, "Nach Rücksprache hat die Kaufmann die Unterlagen übergeben.")
    # Entweder Rueckfrage (dann nichts gesendet) oder Versand ohne den Namen - nie Klartext auf der Leitung.
    if any(e["kind"] == "review" for e in events):
        assert recorder.requests == []
    else:
        assert len(recorder.requests) == 1 and "Kaufmann" not in _wire(recorder.requests[0])
