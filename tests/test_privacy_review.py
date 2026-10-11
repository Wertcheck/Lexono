"""Human in the Loop, Pre-Cloud-Gate und datensparsames Lernen (12.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md).

Ausschliesslich synthetische Daten. Echter Presidio-/spaCy-Stack."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import ChatConversation, Client, Matter, User
from app.models.base import Base
from app.models.privacy_decision import PrivacyDecision, PrivacyFeedbackStat
from app.privacy.decisions import (
    InvalidPrivacyDecisionError,
    delete_for_conversation,
    load_decisions,
    record_decision,
)
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import (
    ClaudeRequestPayload,
    UnsealedPayloadError,
    is_gateway_sealed,
    require_gateway_seal,
)
from app.privacy.review import PrivacyDecisions, detect_decision_terms, find_review_candidates

SACH = "Akte: Beispielakte\n[Vertrag] "


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


def _prepare(gateway, text, *, mode="ask", decisions=None, anmerkungen="Erstelle ein Schreiben."):
    return gateway.prepare_request(
        purpose="formulate_draft",
        sachverhalt=SACH + text,
        anwaltliche_anmerkungen=anmerkungen,
        privacy_decisions=decisions,
        review_mode=mode,
    )


# --- Kandidaten -------------------------------------------------------------------------------------------------------

UNCERTAIN = [
    ("Richter", "Wir bestätigen den Eingang des Schreibens von Richter vom 16.03.2024."),
    ("Fuerst", "Dazu erklärt Fuerst, die Anlage sei mangelhaft."),
]


@pytest.mark.parametrize(("term", "text"), UNCERTAIN)
def test_ambiguous_surnames_trigger_a_review_and_nothing_reaches_the_cloud(gateway, term, text) -> None:
    result = _prepare(gateway, text)
    assert result.allowed is False
    assert result.review_required is True
    assert result.payload is None  # ohne Payload ist kein Cloud-Aufruf moeglich
    candidate = next(c for c in result.review_candidates if c.term == term)
    assert term not in candidate.context_before and candidate.context_before  # minimaler Kontext um den Begriff


@pytest.mark.parametrize(
    "text",
    [
        "Der Kläger macht Gewährleistungsansprüche nach § 634 BGB geltend. Die Frist lief am 30.04.2026 ab.",
        "Die Mängelrüge ging per E-Mail ein. Der Auftragnehmer verweigerte die Nacherfüllung.",
        "Schadensersatz ist zu leisten. Verzug liegt vor. Kündigung ist wirksam. Zahlung erfolgte nicht.",
        "Der Vermieter beruft sich auf Eigenbedarf nach § 573 BGB. Die Kaution wurde nicht zurückgezahlt.",
    ],
)
def test_ordinary_legal_text_causes_no_review(gateway, text) -> None:
    result = _prepare(gateway, text)
    assert result.allowed is True
    assert result.review_candidates == []


@pytest.mark.parametrize(
    "instruction",
    ["Überarbeite den Entwurf und kürze ihn.", "Fasse das Dokument zusammen.", "Berücksichtige § 634 BGB und die VOB/B."],
)
def test_instructions_cause_no_review(gateway, instruction) -> None:
    result = _prepare(gateway, "Die Anlage ist mangelhaft.", anmerkungen=instruction)
    assert result.allowed is True


def test_the_system_generated_matter_title_line_is_never_asked_about(gateway) -> None:
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt="Akte: Testakte", anwaltliche_anmerkungen="Was steht in § 558 BGB?",
        review_mode="ask",
    )
    assert result.allowed is True


# --- Modus "mask" (nicht-interaktive Pfade) -----------------------------------------------------------------------------


def test_mask_mode_pseudonymizes_uncertain_terms_and_seals_the_payload(gateway) -> None:
    result = _prepare(gateway, UNCERTAIN[0][1], mode="mask")
    assert result.allowed is True
    assert "Richter" in result.auto_masked_terms
    assert "Richter" not in result.payload.anonymisierter_sachverhalt
    assert is_gateway_sealed(result.payload)


def test_default_mode_is_fail_closed_masking(gateway) -> None:
    result = gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + UNCERTAIN[1][1])
    assert result.allowed is True and "Fuerst" not in result.payload.anonymisierter_sachverhalt


def test_candidate_search_failure_blocks(gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.privacy.gateway as gw

    def boom(*args, **kwargs):
        raise RuntimeError("Analyse fehlgeschlagen")

    monkeypatch.setattr(gw, "find_review_candidates", boom)
    for mode in ("ask", "mask"):
        result = _prepare(gateway, "Die Anlage ist mangelhaft.", mode=mode)
        assert result.allowed is False and result.payload is None


# --- Entscheidungen ---------------------------------------------------------------------------------------------------


def test_personal_decision_masks_the_term_on_word_boundaries_only(gateway) -> None:
    decisions = PrivacyDecisions(personal=frozenset({"Richter"}))
    result = _prepare(gateway, "Schreiben von Richter vom 16.03.2024. Der Schiedsrichter entschied.", decisions=decisions)
    assert result.allowed is True
    text = result.payload.anonymisierter_sachverhalt
    assert "Richter vom" not in text and "Schiedsrichter" in text
    assert [s.value for s in detect_decision_terms("Richterin Richter", {"Richter"})] == ["Richter"]


def test_not_personal_decision_only_lifts_the_question(gateway) -> None:
    decisions = PrivacyDecisions(not_personal=frozenset({"Richter"}))
    result = _prepare(gateway, UNCERTAIN[0][1], decisions=decisions)
    assert result.allowed is True
    assert "Richter" in result.payload.anonymisierter_sachverhalt


def test_not_personal_decision_never_unmasks_an_automatic_detection(gateway) -> None:
    decisions = PrivacyDecisions(not_personal=frozenset({"Marquardt", "Herr Marquardt"}))
    result = _prepare(gateway, "Herr Marquardt hat die Abnahme erklärt.", decisions=decisions)
    assert result.allowed is True
    assert "Marquardt" not in result.payload.anonymisierter_sachverhalt


def test_find_candidates_skips_decided_terms() -> None:
    text = "Schreiben von Richter vom 16.03.2024."
    assert [c.term for c in find_review_candidates(text, context_source=text)] == ["Richter"]
    decided = PrivacyDecisions(not_personal=frozenset({"Richter"}))
    assert find_review_candidates(text, context_source=text, decisions=decided) == []


# --- Siegel: Pre-Cloud-Gate serverseitig erzwungen ----------------------------------------------------------------------


def test_unsealed_payload_is_rejected_by_every_cloud_provider() -> None:
    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider
    from app.ai_providers.gateway_writing_provider import GatewayRelayWritingProvider
    from app.review.anthropic_review_provider import AnthropicClaudeReviewProvider
    from app.review.gateway_review_provider import GatewayRelayReviewProvider

    raw = ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Herr Marquardt, Kiel")
    with pytest.raises(UnsealedPayloadError):
        require_gateway_seal(raw)
    for provider_cls in (AnthropicClaudeWritingProvider, GatewayRelayWritingProvider):
        provider = provider_cls.__new__(provider_cls)
        with pytest.raises(UnsealedPayloadError):
            provider.write(raw)
    for provider_cls in (AnthropicClaudeReviewProvider, GatewayRelayReviewProvider):
        provider = provider_cls.__new__(provider_cls)
        with pytest.raises(UnsealedPayloadError):
            provider.review(raw)
    stream_provider = AnthropicClaudeWritingProvider.__new__(AnthropicClaudeWritingProvider)
    with pytest.raises(UnsealedPayloadError):
        next(stream_provider.write_stream(raw))


def test_a_sealed_payload_changed_after_the_gate_is_rejected(gateway) -> None:
    result = _prepare(gateway, "Die Anlage ist mangelhaft.")
    assert is_gateway_sealed(result.payload)
    tampered = result.payload.model_copy(update={"anonymisierter_sachverhalt": "Herr Marquardt, Kiel"})
    assert not is_gateway_sealed(tampered)
    result.payload.anonymisierter_sachverhalt = "Herr Marquardt, Kiel"
    assert not is_gateway_sealed(result.payload)


def test_local_note_extension_is_checked_and_resealed(gateway) -> None:
    result = gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + "Herr Marquardt hat unterschrieben.")
    placeholder = next(m.placeholder for m in result.mappings if m.original_value.endswith("Marquardt"))
    ok, reasons = gateway.extend_with_local_note(result.payload, f"Zusammenfassung: {placeholder} hat unterschrieben.", result.mappings)
    assert reasons == [] and is_gateway_sealed(ok)
    leaked, reasons = gateway.extend_with_local_note(result.payload, "Zusammenfassung: Herr Marquardt hat unterschrieben.", result.mappings)
    assert leaked is None and reasons
    unknown, reasons = gateway.extend_with_local_note(result.payload, "Zusammenfassung: [PERSON_99] hat unterschrieben.", result.mappings)
    assert unknown is None and reasons


# --- Persistenz und Lernen ----------------------------------------------------------------------------------------------


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _setup(db: Session):
    user = User(email="anwalt@kanzlei.test", password_hash="x", display_name="Anwalt")
    client_a, client_b = Client(name="Mandant A"), Client(name="Mandant B")
    matter_a, matter_b = Matter(client=client_a, title="Akte A"), Matter(client=client_b, title="Akte B")
    db.add_all([user, client_a, client_b, matter_a, matter_b])
    db.flush()
    conv_1 = ChatConversation(matter_id=matter_a.id, user_id=user.id, title="Chat 1")
    conv_2 = ChatConversation(matter_id=matter_a.id, user_id=user.id, title="Chat 2")
    db.add_all([conv_1, conv_2])
    db.flush()
    return user, matter_a, matter_b, conv_1, conv_2


def test_decision_scopes_matter_isolation_and_contradictions(db: Session) -> None:
    user, matter_a, matter_b, conv_1, conv_2 = _setup(db)
    common = dict(user_id=user.id, actor=user.email)
    record_decision(db, matter_id=matter_a.id, conversation_id=conv_1.id, term="Richter", decision="personal", **common)
    record_decision(db, matter_id=matter_a.id, conversation_id=conv_1.id, term="Regen", decision="not_personal", **common)
    record_decision(db, matter_id=matter_a.id, conversation_id=conv_2.id, term="Fuerst", decision="unsure", **common)
    record_decision(db, matter_id=matter_a.id, conversation_id=conv_2.id, term="Richter", decision="not_personal", **common)

    in_conv_1 = load_decisions(db, matter_id=matter_a.id, conversation_id=conv_1.id)
    in_conv_2 = load_decisions(db, matter_id=matter_a.id, conversation_id=conv_2.id)
    other_matter = load_decisions(db, matter_id=matter_b.id, conversation_id=None)
    # "personal"/"unsure" aktenweit, "not_personal" nur in der eigenen Unterhaltung
    assert in_conv_1.personal == {"Richter", "Fuerst"} and in_conv_1.not_personal == {"Regen"}
    # Widerspruch: aktenweit "personal" gewinnt gegen "not_personal" der Unterhaltung (fail-closed)
    assert "Richter" in in_conv_2.personal and "Richter" not in in_conv_2.not_personal
    # Aktenisolation
    assert other_matter.is_empty()


def test_deleting_a_conversation_removes_only_its_own_decisions(db: Session) -> None:
    user, matter_a, _, conv_1, _ = _setup(db)
    record_decision(db, matter_id=matter_a.id, conversation_id=conv_1.id, term="Richter", decision="personal", user_id=user.id, actor="a")
    record_decision(db, matter_id=matter_a.id, conversation_id=conv_1.id, term="Regen", decision="not_personal", user_id=user.id, actor="a")
    assert delete_for_conversation(db, conv_1.id) == 1
    remaining = load_decisions(db, matter_id=matter_a.id, conversation_id=conv_1.id)
    assert remaining.personal == {"Richter"} and remaining.not_personal == frozenset()


def test_learning_statistics_contain_no_clear_text(db: Session) -> None:
    user, matter_a, _, conv_1, _ = _setup(db)
    for _ in range(2):
        record_decision(db, matter_id=matter_a.id, conversation_id=conv_1.id, term="Wessolowski", decision="personal", user_id=user.id, actor="a")
    stats = db.query(PrivacyFeedbackStat).all()
    assert len(stats) == 1 and stats[0].count == 2
    dump = json.dumps([[s.term_hmac, s.decision, s.features_json] for s in stats])
    assert "wessolowski" not in dump.lower()
    from app.models import AuditEvent

    assert all("Wessolowski" not in (e.details or "") for e in db.query(AuditEvent).all())


@pytest.mark.parametrize(("term", "decision"), [("", "personal"), ("Richter", "egal"), ("x" * 81, "personal"), ("[PERSON_01]", "personal"), ("a@@b", "personal")])
def test_invalid_decisions_are_rejected(db: Session, term, decision) -> None:
    user, matter_a, _, conv_1, _ = _setup(db)
    with pytest.raises(InvalidPrivacyDecisionError):
        record_decision(db, matter_id=matter_a.id, conversation_id=conv_1.id, term=term, decision=decision, user_id=user.id, actor="a")
    assert db.query(PrivacyDecision).count() == 0


def test_review_context_shows_whole_words_only() -> None:
    from app.privacy.review import _context

    source = "In der Sache Beispielmann ./. Musterbau GmbH bestaetigen wir\nden Eingang des Schreibens von Richter vom 16.03.2024.\nDie Klaegerin verlangt Schadensersatz in Hoehe von 12.400,00 EUR."
    before, after = _context(source, "Richter")
    assert before.endswith("Schreibens von ") and not before.startswith("sterbau")
    assert after.startswith(" vom 16.03.2024.") and not after.endswith(" H")
    assert "\n" not in before + after


# --- Restlecks (12.10.2026): Feldgrenzen und formgleiche Singular/Plural-Namen -------------------------------------------


@pytest.mark.parametrize("instruction", ["Richter hat angerufen und bittet um Rückruf.", "Fuerst erklärt, dass die Anlage mangelhaft ist."])
def test_ambiguous_name_at_the_start_of_the_instruction_field_is_asked(gateway, instruction) -> None:
    """Ohne Feldgrenze verschmolz "Akte: Beispielakte" mit dem ersten Wort der Anmerkung; "Richter" wurde ausserdem
    als Plural getaggt, obwohl das finite Verb im Singular steht."""
    result = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte", anwaltliche_anmerkungen=instruction, review_mode="ask"
    )
    assert result.review_required and result.payload is None
    masked = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte", anwaltliche_anmerkungen=instruction
    )
    assert instruction.split()[0] not in masked.payload.anonymisierte_anwaltliche_anmerkungen


@pytest.mark.parametrize(
    "instruction",
    [
        "Die Richter haben die Berufung zurückgewiesen. Fasse das Urteil zusammen.",
        "Zahlungen erfolgten nicht fristgerecht. Erstelle eine Mahnung.",
        "Mängel bestehen weiterhin. Formuliere eine Nachfrist.",
        "Gilt das auch für Gewerbemietverträge?",
    ],
)
def test_real_plural_nouns_are_not_asked(gateway, instruction) -> None:
    result = gateway.prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte", anwaltliche_anmerkungen=instruction, review_mode="ask"
    )
    assert not result.review_required


# --- Restklassen (12.10.2026): Agens nach Umbruch, Ueberschriften, Aufzaehlungen neben ersetzten Namen -------------------

RESIDUAL_CASES = [
    ("Stuckenschneider", "Die Unterlagen wurden am 12.03.2026 durch\nStuckenschneider geprüft und dem Auftraggeber übergeben."),
    ("Fischer", "Zeugenaussage Fischer\nDer Zeuge schildert den Ablauf."),
    ("Grotelueschen", "Protokoll\nGrotelueschen, Zeuge\nDie Anlage wurde besichtigt."),
    ("Fuerst", "Zeugen sind Herr Marquardt, Fuerst sowie Herr Marquardt."),
]


@pytest.mark.parametrize(("name", "text"), RESIDUAL_CASES)
def test_residual_leak_classes_never_reach_the_payload_in_plaintext(gateway, name, text) -> None:
    masked = _prepare(gateway, text, mode="mask")
    assert masked.allowed and name not in masked.payload.anonymisierter_sachverhalt
    asked = _prepare(gateway, text, mode="ask")
    # Entweder automatisch erkannt (dann ohnehin ersetzt) oder als Rueckfrage - nie ungeprueft im Klartext.
    if asked.allowed:
        assert name not in asked.payload.anonymisierter_sachverhalt
    else:
        assert asked.review_required and name in [c.term for c in asked.review_candidates]


# Frueher bekannte Grenzen, behoben am 12.10.2026 (xfail aufgehoben nach Verifikation im Benchmark und im Payload):
# "Lange" - Lemma "lange" (klein, als Adverb gelesen) scheiterte am grossschreibungsempfindlichen Formgleichheits-Abgleich;
# "Zeugenaussage Richter" - die Analysefassung glaettete den Zeilenumbruch nach der Ueberschrift als weichen PDF-Umbruch.
@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("Lange", "Die Unterlagen wurden am 12.03.2026 durch\nLange geprüft und dem Auftraggeber übergeben."),
        ("Richter", "Zeugenaussage Richter\nDer Zeuge schildert den Ablauf."),
    ],
)
def test_former_limits_are_now_protected(gateway, name, text) -> None:
    masked = _prepare(gateway, text, mode="mask")
    assert masked.allowed and name not in masked.payload.anonymisierter_sachverhalt
    asked = _prepare(gateway, text, mode="ask")
    assert asked.review_required and name in [c.term for c in asked.review_candidates]


@pytest.mark.parametrize(
    "text",
    [
        "Die Anlage wurde gestern geprüft.\nDer Zeuge schildert den Ablauf.",  # echte Zeilen mit Verb: keine Ueberschrift
        "Dachfläche Nordseite\nDie Feuchtigkeit wurde festgestellt.",
        "Die Prüfung erfolgte durch Lange Zeiträume hindurch nicht.",  # Grossschreibung am Fehlbeispiel, kein Agens-Partizip
    ],
)
def test_heading_and_case_insensitive_lemma_rules_have_no_false_alarms(gateway, text) -> None:
    result = _prepare(gateway, text, mode="ask")
    assert not result.review_required, [c.term for c in result.review_candidates]


@pytest.mark.parametrize("term", ["Bescheid", "Flachdach", "Zugluft"])
def test_terms_not_read_as_a_person_after_herr_are_never_asked(gateway, term) -> None:
    """Fachwort-Fehlalarme (12.10.2026): ein Wort, das selbst im staerksten Namensrahmen "Herr X hat unterschrieben"
    nicht als Person gelesen wird, loest keine Rueckfrage und keine vorsichtshalber-Maskierung aus."""
    for template in ("Name: {X}  Funktion: Zeuge", "Zeuge  |  {X}  |  Hamburg  |  geladen", "{X} ist erforderlich."):
        result = _prepare(gateway, template.format(X=term), mode="ask")
        assert term not in [c.term for c in result.review_candidates]


@pytest.mark.parametrize("name", ["Richter", "Fischer", "Lange", "Fuerst"])
def test_ambiguous_surnames_still_pass_the_herr_frame(name) -> None:
    from app.privacy import presidio_ner as pn

    assert pn._name_probe_frames(name)[0]


@pytest.mark.parametrize(
    "text",
    [
        "Die Zahlungen wurden durch Überweisungen beglichen.",
        "Wer haftet bei Mängeln der Dämmung?",
        "Mängelanzeige Dachfläche\nDie Feuchtigkeit wurde festgestellt.",
        "Kündigung Mietvertrag\nDer Vermieter kündigte fristgerecht.",
        "Gewährleistung, Minderung sowie Rücktritt kommen in Betracht.",
        "Die Prüfung erfolgte durch Sachverständige der Kammer.",
    ],
)
def test_residual_rules_do_not_flag_legal_vocabulary(gateway, text) -> None:
    result = _prepare(gateway, text, mode="ask")
    assert not result.review_required, [c.term for c in result.review_candidates]
