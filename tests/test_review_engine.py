"""Tests fuer app/review/engine.py (Prompt 18).

Schwerpunkt: die erneute Pseudonymisierung des bereits rekonstruierten
Draft.content, und dass Findings danach wieder lokal rekonstruiert
werden."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.models import ApiCallLog, AuditEvent, Client, Draft, Matter, ReviewFinding
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.review.engine import ReviewEngine
from app.review.schema import Finding, ReviewResult
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider


class FakeReviewProvider:
    def __init__(self, result: ReviewResult | None = None) -> None:
        self.result = result or ReviewResult(
            findings=[], overall_assessment="Alles in Ordnung."
        )
        self.received_payloads: list[ClaudeRequestPayload] = []

    def review(self, payload: ClaudeRequestPayload) -> ReviewResult:
        self.received_payloads.append(payload)
        return self.result


class FailingReviewProvider:
    def review(self, payload: ClaudeRequestPayload) -> ReviewResult:
        raise RuntimeError("Simulierter Parsing-Fehler mit Text: Max Mustermann")


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _draft(db: Session, content: str, client_name: str = "Max Mustermann", title: str = "Testakte") -> Draft:
    client = Client(name=client_name)
    matter = Matter(client=client, title=title)
    draft = Draft(matter=matter, content=content)
    db.add_all([client, matter, draft])
    db.commit()
    return draft


def _engine(review_provider=None, gateway=None) -> ReviewEngine:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return ReviewEngine(
        RuleBasedLocalAIProvider(),
        research_service,
        gateway or ClaudePrivacyGateway(),
        review_provider or FakeReviewProvider(),
        model_name="claude-sonnet-5",
    )


class _AlwaysBlockSecurityCheck:
    """Deterministischer Test-Stub statt eines organischen Text-Triggers
    (frueher: "Festgesetzte Einkommensteuer", das zufaellig die
    Grossschreibungs-Heuristik ausloeste - seit deren POS-Tag-Verfeinerung,
    13.09., nicht mehr zuverlaessig genug fuer Tests, die NUR das
    downstream-Verhalten bei EINEM BELIEBIGEN Block pruefen wollen, siehe
    dieselbe Loesung in tests/test_chat_service.py)."""

    def check(self, pseudonymized_text, mappings, *, purpose):
        from app.privacy.security_check_schema import SecurityCheckResult

        return SecurityCheckResult(
            passed=False,
            reasons=["Möglicherweise nicht erkannte Namen/Entitäten gefunden: ['Test Person']"],
        )


def test_requires_draft_id(db_session: Session) -> None:
    with pytest.raises(ValueError):
        _engine().review_draft("", db_session)


def test_raises_for_unknown_draft(db_session: Session) -> None:
    with pytest.raises(ValueError):
        _engine().review_draft("nicht-vorhanden", db_session)


def test_draft_content_is_pseudonymized_before_sending(db_session: Session) -> None:
    """Kernanforderung: der bereits rekonstruierte Draft.content darf NIE
    im Klartext an den Review-Provider gehen."""
    draft = _draft(db_session, "Sehr geehrter Herr Max Mustermann, wir bestätigen den Eingang.")
    provider = FakeReviewProvider()
    engine = _engine(provider)

    engine.review_draft(draft.id, db_session)

    assert len(provider.received_payloads) == 1
    sent_text = provider.received_payloads[0].anonymisierter_sachverhalt
    assert "Max Mustermann" not in sent_text
    assert "[MANDANT_01]" in sent_text


def test_findings_are_reconstructed_to_real_values(db_session: Session) -> None:
    draft = _draft(db_session, "Sehr geehrter Herr Max Mustermann,")
    result = ReviewResult(
        findings=[
            Finding(
                category="formaler_fehler",
                severity="niedrig",
                description="Datum fehlt bei [MANDANT_01].",
            )
        ],
        overall_assessment="In Ordnung, siehe Hinweis zu [MANDANT_01].",
    )
    engine = _engine(FakeReviewProvider(result))

    outcome = engine.review_draft(draft.id, db_session)

    assert outcome.success is True
    assert "Max Mustermann" in outcome.findings[0].description
    assert "[MANDANT_01]" not in outcome.findings[0].description
    assert "Max Mustermann" in outcome.overall_assessment


def test_findings_are_persisted(db_session: Session) -> None:
    draft = _draft(db_session, "Text ohne PII.")
    result = ReviewResult(
        findings=[
            Finding(category="frist", severity="hoch", description="Frist unklar."),
            Finding(category="formaler_fehler", severity="niedrig", description="Anrede prüfen."),
        ],
        overall_assessment="Zwei Punkte zu klären.",
    )
    engine = _engine(FakeReviewProvider(result))

    engine.review_draft(draft.id, db_session)

    persisted = db_session.query(ReviewFinding).filter_by(draft_id=draft.id).all()
    assert len(persisted) == 2


def test_review_sets_draft_status_to_legal_review(db_session: Session) -> None:
    draft = _draft(db_session, "Text ohne PII.")
    engine = _engine()

    engine.review_draft(draft.id, db_session)

    db_session.refresh(draft)
    assert draft.status == "legal_review"


def test_review_creates_audit_event(db_session: Session) -> None:
    draft = _draft(db_session, "Text ohne PII.")
    engine = _engine()

    engine.review_draft(draft.id, db_session)

    events = db_session.query(AuditEvent).filter_by(
        entity_id=draft.id, event_type="draft_reviewed"
    ).all()
    assert len(events) == 1


def test_blocked_draft_creates_no_findings_and_no_status_change(db_session: Session) -> None:
    """`review_draft` als Zweck ist intern fest verdrahtet
    (app/review/engine.py: `_REVIEW_PURPOSE`) und immer erlaubt - kann hier
    also nicht als Block-Ausloeser dienen. Nutzt stattdessen einen
    deterministischen Security-Check-Stub (`_AlwaysBlockSecurityCheck`),
    der EINEN BELIEBIGEN Block erzwingt, unabhaengig vom konkreten
    Heuristik-Mechanismus - dieser Test prueft nur das downstream-
    Verhalten bei einem Block, nicht die Heuristik selbst."""
    draft = _draft(db_session, "Ein ganz normaler Entwurfstext.")
    engine = _engine(gateway=ClaudePrivacyGateway(security_check=_AlwaysBlockSecurityCheck()))

    outcome = engine.review_draft(draft.id, db_session)

    assert outcome.success is False
    assert len(outcome.blocked_reasons) > 0
    db_session.refresh(draft)
    assert draft.status == "draft"  # unveraendert
    assert db_session.query(ReviewFinding).count() == 0


def test_blocked_review_is_logged_without_pii(db_session: Session) -> None:
    draft = _draft(db_session, "Ein ganz normaler Entwurfstext.")
    engine = _engine(gateway=ClaudePrivacyGateway(security_check=_AlwaysBlockSecurityCheck()))

    engine.review_draft(draft.id, db_session)

    logs = db_session.query(ApiCallLog).filter_by(result_status="blocked", purpose="review_draft").all()
    assert len(logs) == 1
    assert "Test Person" not in (logs[0].error_status or "")


def test_provider_exception_is_handled_without_crashing(db_session: Session) -> None:
    draft = _draft(db_session, "Text ohne PII.")
    engine = _engine(FailingReviewProvider())

    outcome = engine.review_draft(draft.id, db_session)

    assert outcome.success is False
    logs = db_session.query(ApiCallLog).filter_by(result_status="error").all()
    assert len(logs) == 1
    assert "Max Mustermann" not in (logs[0].error_status or "")


def test_review_never_touches_other_matter(db_session: Session) -> None:
    """Aktenisolation - dasselbe wiederkehrende Muster wie im gesamten Projekt."""
    draft_a = _draft(db_session, "Text A.", client_name="Mandant A", title="Akte A")
    client_b = Client(name="Mandant B")
    matter_b = Matter(client=client_b, title="Akte B")
    draft_b = Draft(matter=matter_b, content="Text B.")
    db_session.add_all([client_b, matter_b, draft_b])
    db_session.commit()

    engine = _engine()
    engine.review_draft(draft_a.id, db_session)

    db_session.refresh(draft_b)
    assert draft_b.status == "draft"  # unveraendert
    assert db_session.query(ReviewFinding).filter_by(draft_id=draft_b.id).count() == 0
