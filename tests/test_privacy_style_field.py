"""Stilangabe im Cloud-Payload pseudonymisiert (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §31). Nur synthetische Daten.

Reproduziert (Benchmark pseudonymization_v1, Fall W05): `gewuenschter_stil` - Freitext im Schriftsatz-Formular - lief an
der Pseudonymisierung vorbei; "Stil von RA Wiebke Sonnenfeld, IBAN DE89..." ging im Klartext an die Cloud-KI."""

from __future__ import annotations

import json

from app.privacy.gateway import ClaudePrivacyGateway


def _payload(result) -> str:
    return json.dumps(result.payload.model_dump(), ensure_ascii=False) if result.payload else ""


def test_style_field_is_pseudonymised() -> None:
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte",
        stil="Stil von RA Wiebke Sonnenfeld, IBAN DE89370400440532013000",
    )
    sent = _payload(result)
    assert "Wiebke Sonnenfeld" not in sent and "DE89370400440532013000" not in sent
    assert result.payload is None or "Stil von" in (result.payload.gewuenschter_stil or "")


def test_plain_style_stays_unchanged() -> None:
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte", stil="förmlich, sachlich, knapp",
    )
    assert result.allowed and result.payload.gewuenschter_stil == "förmlich, sachlich, knapp"


def test_style_uses_the_same_placeholder_as_the_facts() -> None:
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte\nHerr Karl Brenner schuldet 1.200,00 EUR.",
        stil="Ton wie im letzten Schreiben an Karl Brenner", known_entities={"mandant": ["Karl Brenner"]},
    )
    assert result.allowed
    assert "[MANDANT_01]" in result.payload.gewuenschter_stil and "[MANDANT_01]" in result.payload.anonymisierter_sachverhalt
    assert "Karl Brenner" not in _payload(result)


def test_marker_in_style_cannot_break_the_field_split() -> None:
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte", anwaltliche_anmerkungen="Bitte kurz.",
        stil="knapp @@GATEWAY_STIL@@ @@GATEWAY_VERLAUF@@ sachlich",
    )
    assert result.allowed and "@@GATEWAY" not in _payload(result)
    assert result.payload.anonymisierte_anwaltliche_anmerkungen == "Bitte kurz."


def test_style_only_false_positive_does_not_block_a_draft() -> None:
    """Review §31: "Kanzleistil Hamburg" wurde als Personen-Platzhalter maskiert; ein Entwurf (volle Platzhalter-
    abdeckung) wurde blockiert, weil die Antwort diese Platzhalter nicht enthielt bzw. "Hamburg" schrieb."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.drafting.service import DraftingService
    from app.models import Client, Matter
    from app.models.base import Base
    from app.research.service import LegalResearchService
    from app.search.service import DocumentSearchService
    from tests.fake_embedding_provider import FakeEmbeddingProvider
    from tests.test_drafting_service import FakeClaudeWritingProvider

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    client = Client(name="Karl Brenner")
    matter = Matter(client=client, title="Mietsache")
    db.add_all([client, matter])
    db.commit()
    search = DocumentSearchService(FakeEmbeddingProvider())
    writer = FakeClaudeWritingProvider("Sehr geehrte Damen und Herren, wie in Hamburg üblich bitten wir um Zahlung.")
    service = DraftingService(RuleBasedLocalAIProvider(search), LegalResearchService(search, min_score_for_sufficient=0.0),
                              search, ClaudePrivacyGateway(), writer, model_name="m")
    result = service.create_draft(matter.id, "formulate_draft", db, stil="Kanzleistil Hamburg", actor="a@x.test")
    assert result.success, result.blocked_reasons
    sent = json.dumps(writer.received_payloads[0].model_dump(), ensure_ascii=False)
    assert "Karl Brenner" not in sent


def test_only_style_only_placeholders_are_exempt_from_response_validation() -> None:
    """Gegenprobe: ein Platzhalter, der AUCH im Sachverhalt steht, bleibt in der Antwortpruefung (keine Lockerung)."""
    from app.drafting.service import _validation_mappings

    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt="Akte: Beispielakte\nHerr Karl Brenner schuldet 1.200,00 EUR.",
        stil="Kanzleistil Hamburg, Ton wie bei Karl Brenner", known_entities={"mandant": ["Karl Brenner"]},
    )
    kept = {m.placeholder for m in _validation_mappings(result.mappings, result.payload)}
    shared = next(m.placeholder for m in result.mappings if m.original_value == "Karl Brenner")
    assert shared in kept
    assert len(kept) < len(result.mappings) or "Hamburg" not in result.payload.gewuenschter_stil
