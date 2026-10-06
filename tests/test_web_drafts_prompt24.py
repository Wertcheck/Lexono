"""Tests für die Prompt-24-Erweiterungen von app/web/drafts_router.py:
Listenansicht, Original-Split, Quellen-/Findings-/Audit-Panels,
Aktionsleiste (Freigeben/Zurückweisen/Neu generieren/Prüfen).

Gleiches Testmuster wie tests/test_web_drafts.py (Prompt 23).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.drafts_router as drafts_router_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.db.session import get_db
from app.drafting.service import DraftingService
from app.main import app
from app.models import (
    AuditEvent,
    Client,
    Document,
    Draft,
    DraftKnowledgeItemLink,
    DraftSourceLink,
    KnowledgeItem,
    Matter,
    Message,
    ReviewFinding,
    Source,
)
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.review.engine import ReviewEngine
from app.review.schema import Finding, ReviewResult
from app.search.service import DocumentSearchService
from app.web.service_factory import WritingProviderNotConfiguredError
from tests.auth_test_utils import extract_csrf, login_as_admin
from tests.fake_embedding_provider import FakeEmbeddingProvider


class FakeClaudeWritingProvider:
    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        return ClaudeWritingResult(text="Neu generierte Antwort.", token_count=10)


class FakeReviewProvider:
    def __init__(self, findings: list[Finding] | None = None) -> None:
        self.findings = findings if findings is not None else []

    def review(self, payload: ClaudeRequestPayload) -> ReviewResult:
        return ReviewResult(findings=self.findings, overall_assessment="Testbewertung.")


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def seeded(db_session: Session) -> dict[str, str]:
    client_ = Client(name="Synthetischer Testmandant GmbH")
    matter = Matter(client=client_, title="Einspruch Steuerbescheid 2025")
    message = Message(
        matter=matter,
        direction="inbound",
        sender="j.mueller@steuerkanzlei-test.invalid",
        body_text="Testinhalt der Original-Nachricht.",
    )
    db_session.add_all([client_, matter, message])
    db_session.commit()

    document = Document(
        matter_id=matter.id,
        message_id=message.id,
        original_filename="steuerbescheid_test.pdf",
        file_path="/data/test.pdf",
    )
    draft = Draft(matter_id=matter.id, message_id=message.id, content="Ursprünglicher Entwurfstext.")
    source = Source(title="Testquelle", source_type="Gesetz", approval_level="freigegeben")
    knowledge_item = KnowledgeItem(
        title="Testwissenselement", content="Test", approval_status="approved"
    )
    db_session.add_all([document, draft, source, knowledge_item])
    db_session.commit()

    db_session.add(DraftSourceLink(draft_id=draft.id, source_id=source.id))
    db_session.add(
        DraftKnowledgeItemLink(draft_id=draft.id, knowledge_item_id=knowledge_item.id)
    )
    db_session.commit()

    return {
        "matter_id": matter.id,
        "message_id": message.id,
        "draft_id": draft.id,
        "source_id": source.id,
        "knowledge_item_id": knowledge_item.id,
    }


def _working_drafting_service() -> DraftingService:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        FakeClaudeWritingProvider(),
        model_name="claude-sonnet-5",
    )


def _working_review_engine(findings: list[Finding] | None = None) -> ReviewEngine:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return ReviewEngine(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        ClaudePrivacyGateway(),
        FakeReviewProvider(findings),
        model_name="claude-sonnet-5",
    )


# --- Listenansicht ---


def test_drafts_list_returns_200_and_shows_matter(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/drafts")
    assert response.status_code == 200
    assert "Einspruch Steuerbescheid 2025" in response.text


def test_drafts_list_shows_only_latest_version_by_default(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    v1 = db_session.get(Draft, seeded["draft_id"])
    v2 = Draft(
        matter_id=v1.matter_id,
        content="Version 2",
        version=2,
        previous_version_id=v1.id,
    )
    db_session.add(v2)
    db_session.commit()

    response = client.get("/dashboard/drafts")
    assert response.status_code == 200
    assert f'/dashboard/drafts/{v2.id}' in response.text
    assert f'/dashboard/drafts/{v1.id}' not in response.text


def test_drafts_list_show_all_versions_includes_both(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    v1 = db_session.get(Draft, seeded["draft_id"])
    v2 = Draft(
        matter_id=v1.matter_id, content="Version 2", version=2, previous_version_id=v1.id
    )
    db_session.add(v2)
    db_session.commit()

    response = client.get("/dashboard/drafts", params={"show_all_versions": "true"})
    assert f'/dashboard/drafts/{v1.id}' in response.text
    assert f'/dashboard/drafts/{v2.id}' in response.text


def test_drafts_list_filters_by_status(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/drafts", params={"status": "approved"})
    assert response.status_code == 200
    assert "Einspruch Steuerbescheid 2025" not in response.text


# --- Original-Split ---


def test_draft_detail_shows_original_message(client: TestClient, seeded: dict) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert "Testinhalt der Original-Nachricht." in response.text
    assert "steuerbescheid_test.pdf" in response.text


def test_draft_without_message_shows_empty_state(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    matter_id = seeded["matter_id"]
    orphan_draft = Draft(matter_id=matter_id, content="Entwurf ohne Nachrichtenbezug.")
    db_session.add(orphan_draft)
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{orphan_draft.id}")
    assert response.status_code == 200
    assert "Kein Bezug zu einer eingehenden Nachricht" in response.text


# --- Quellen-/Wissens-Panel ---


def test_draft_detail_shows_linked_sources_and_knowledge(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert "Testquelle" in response.text
    assert "Testwissenselement" in response.text


# --- Findings-Panel ---


def test_draft_detail_shows_review_findings(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    db_session.add(
        ReviewFinding(
            draft_id=seeded["draft_id"],
            category="fehlende_quelle",
            severity="hoch",
            description="Testfinding fehlende Quelle.",
        )
    )
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert "Testfinding fehlende Quelle." in response.text
    assert "finding-item--hoch" in response.text


def test_draft_detail_without_findings_shows_hint(client: TestClient, seeded: dict) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert "Noch keine Prüfung durchgeführt." in response.text


# --- Audit-Log-Panel ---


def test_draft_detail_shows_audit_events_for_draft_and_instructions(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    db_session.add(
        AuditEvent(
            entity_type="Draft",
            entity_id=seeded["draft_id"],
            event_type="draft_created",
            actor="system",
            details="Test",
        )
    )
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert "draft_created" in response.text


# --- Aktion: Freigeben ---


def test_approve_sets_status_and_redirects(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/approve",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    draft = db_session.get(Draft, seeded["draft_id"])
    assert draft.status == "approved"


def test_approve_does_not_send_anything(client: TestClient, seeded: dict) -> None:
    """Grundregel: kein automatischer Versand, unabhängig von der Freigabe.

    PRÄZISIERT am 14.09. (NICHT abgeschwächt): dieser Test nagelte bisher
    den Satz "Postausgang mit Versandfunktion existiert noch nicht" fest -
    und hat damit eine INHALTLICH FALSCHE Aussage der Oberfläche
    abgesichert. Seit Prompt 25 existiert der Postausgang sehr wohl
    (`approve_draft` -> `OutboxService.add_to_outbox`, `/dashboard/outbox`);
    was NICHT existiert, ist eine Versandfähigkeit. Geprüft wird deshalb
    jetzt die Garantie selbst statt einer veralteten Formulierung: kein
    automatischer Versand, Postausgang als reine Warteschlange.
    """
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    # Whitespace normalisieren: die Aussage steht im Template über mehrere
    # Zeilen. Ein Test, der an der Zeilenumbruchstelle scheitert, prüft die
    # Formatierung statt der Garantie.
    page = " ".join(response.text.split())
    assert "Warteschlange ohne Versandfunktion" in page
    assert "Es wird in keinem Fall automatisch etwas versendet" in page
    # Die widerlegte Behauptung darf nicht zurückkehren.
    assert "existiert noch nicht" not in page


# --- Aktion: Zurückweisen ---


def test_reject_requires_comment(client: TestClient, seeded: dict) -> None:
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/reject",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 422


def test_reject_sets_status_rejected(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/reject",
        data={"comment": "Fehlerhafte Rechtsgrundlage.", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    draft = db_session.get(Draft, seeded["draft_id"])
    assert draft.status == "rejected"


# --- Aktion: Neu generieren ---


def test_regenerate_creates_new_version(
    client: TestClient, db_session: Session, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        drafts_router_module, "get_drafting_service", lambda: _working_drafting_service()
    )

    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/regenerate",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    new_id = response.headers["location"].rsplit("/", 1)[-1]
    new_draft = db_session.get(Draft, new_id)
    # 05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS" Phase D:
    # KI-generierter Inhalt wird jetzt zu Editor-HTML gewandelt (siehe
    # app/drafting/markdown_to_draft_html.py) statt roh gespeichert.
    assert new_draft.content == "<p>Neu generierte Antwort.</p>"
    assert new_draft.content_format == "html"
    assert new_draft.previous_version_id == seeded["draft_id"]


def test_regenerate_without_api_key_shows_friendly_error(
    client: TestClient, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise():  # noqa: ANN202
        raise WritingProviderNotConfiguredError("nicht konfiguriert")

    monkeypatch.setattr(drafts_router_module, "get_drafting_service", _raise)

    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/regenerate",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


# --- Aktion: Entwurf prüfen (Review-Engine) ---


def test_review_persists_findings_and_redirects_back(
    client: TestClient, db_session: Session, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unauffaelliger Text ohne Namensmuster - vermeidet eine Blockierung
    # durch die (erwuenscht strenge) Security-Check-Heuristik, die sonst
    # z. B. "Ursprünglicher Entwurfstext" als moeglichen unerkannten Namen
    # werten koennte (siehe app/privacy/security_check.py).
    draft = db_session.get(Draft, seeded["draft_id"])
    draft.content = "Testinhalt."
    db_session.commit()

    findings = [Finding(category="frist", severity="mittel", description="Testfinding.")]
    monkeypatch.setattr(
        drafts_router_module, "get_review_engine", lambda: _working_review_engine(findings)
    )

    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/review",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/drafts/{seeded['draft_id']}"
    stored = db_session.query(ReviewFinding).filter_by(draft_id=seeded["draft_id"]).all()
    assert len(stored) == 1
    assert stored[0].description == "Testfinding."


def test_review_without_api_key_shows_friendly_error(
    client: TestClient, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise():  # noqa: ANN202
        raise WritingProviderNotConfiguredError("nicht konfiguriert")

    monkeypatch.setattr(drafts_router_module, "get_review_engine", _raise)

    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/review",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
