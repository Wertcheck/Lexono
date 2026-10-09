"""Tests für app/web/draft_editor_router.py (04.10., Dokumenten-Editor).

Gleiches Testmuster wie tests/test_web_drafts.py (In-Memory-SQLite über
app.dependency_overrides, StaticPool, `login_as_admin`). `get_editor_
service_for_ai_edit` wird mit einem Fake-Writing-Provider überschrieben
(kein echter Claude-API-Aufruf), `get_editor_service` nutzt die
Standard-Factory (drafting_service=None reicht für Autosave/Discard/
Als-Vorlage-speichern)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.attorney_instructions.service import AttorneyInstructionService
from app.db.session import get_db
from app.drafting.editor_service import EditorService
from app.drafting.service import DraftingService
from app.drafting.versioning import AI_SUGGESTION_DISCARDED_STATUS
from app.main import app
from app.models import Client, Draft, DocumentTemplate, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from app.web.service_factory import get_editor_service, get_editor_service_for_ai_edit
from tests.auth_test_utils import extract_csrf, login_as_admin
from tests.fake_embedding_provider import FakeEmbeddingProvider


class FakeClaudeWritingProvider:
    def __init__(self, response_text: str = "Neuer KI-Entwurfstext.") -> None:
        self.response_text = response_text

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        return ClaudeWritingResult(text=self.response_text, token_count=10)


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _working_editor_service_for_ai_edit() -> EditorService:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    drafting_service = DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        FakeClaudeWritingProvider(),
        model_name="claude-sonnet-5",
    )
    return EditorService(AttorneyInstructionService(drafting_service))


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_editor_service_for_ai_edit] = _working_editor_service_for_ai_edit
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
    db_session.add_all([client_, matter])
    db_session.commit()
    draft = Draft(matter_id=matter.id, content="Ursprünglicher Entwurfstext.")
    db_session.add(draft)
    db_session.commit()
    return {"matter_id": matter.id, "draft_id": draft.id}


def _csrf(client: TestClient, draft_id: str) -> str:
    page = client.get(f"/dashboard/drafts/{draft_id}/edit")
    assert page.status_code == 200
    return extract_csrf(page.text)


# --- Seite --------------------------------------------------------------


def test_editor_page_renders_with_breadcrumb_and_toolbar(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}/edit")
    assert response.status_code == 200
    assert "Schreiben erstellen" in response.text
    assert "Entwurf v1" in response.text
    assert 'class="draft-editor-toolbar"' in response.text
    assert 'class="draft-editor-statusbar"' in response.text
    assert "KI-Assistent" in response.text
    assert "Vorlagen" in response.text
    assert "Zur Entwurfsprüfung (Viewer)" in response.text


def test_editor_page_has_four_fixed_ai_suggestions(client: TestClient, seeded: dict) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}/edit")
    for label in ["Formulierung präzisieren", "Text kürzen", "Rechtliche Prüfung", "Alternative Formulierungen"]:
        assert label in response.text


def test_editor_page_disables_surface_when_not_draft_status(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    draft = db_session.get(Draft, seeded["draft_id"])
    draft.status = "approved"
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}/edit")
    assert response.status_code == 200
    assert 'contenteditable="false"' in response.text
    assert "eingefroren" in response.text


def test_editor_page_redirects_discarded_draft_to_visible_ancestor(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    v1 = db_session.get(Draft, seeded["draft_id"])
    v2 = Draft(
        matter_id=v1.matter_id, content="Verworfener Vorschlag", version=2,
        previous_version_id=v1.id, status=AI_SUGGESTION_DISCARDED_STATUS,
    )
    db_session.add(v2)
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{v2.id}/edit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/drafts/{v1.id}/edit"


# --- Autosave -------------------------------------------------------------


def test_autosave_updates_content_and_reports_saved_true(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    csrf = _csrf(client, seeded["draft_id"])
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/autosave",
        data={
            "content": "<p>Autosave-Text</p>",
            "subject": "Betreff A",
            "recipient": "Empfänger B",
            "content_format": "html",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    assert body["last_autosaved_at"] is not None

    db_session.expire_all()
    draft = db_session.get(Draft, seeded["draft_id"])
    assert draft.content == "<p>Autosave-Text</p>"
    assert draft.subject == "Betreff A"
    assert draft.recipient == "Empfänger B"
    assert draft.version == 1


def test_autosave_strips_script_tag(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    csrf = _csrf(client, seeded["draft_id"])
    client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/autosave",
        data={
            "content": "<p>Text</p><script>alert(1)</script>",
            "content_format": "html",
            "csrf_token": csrf,
        },
    )
    db_session.expire_all()
    draft = db_session.get(Draft, seeded["draft_id"])
    assert "<script" not in draft.content


def test_autosave_reports_saved_false_when_draft_is_frozen(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    csrf = _csrf(client, seeded["draft_id"])
    draft = db_session.get(Draft, seeded["draft_id"])
    draft.status = "approved"
    db_session.commit()

    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/autosave",
        data={"content": "Versuchte Änderung", "content_format": "text", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["saved"] is False

    db_session.expire_all()
    assert db_session.get(Draft, seeded["draft_id"]).content == "Ursprünglicher Entwurfstext."


def test_autosave_without_csrf_token_is_rejected(client: TestClient, seeded: dict) -> None:
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/autosave",
        data={"content": "x", "content_format": "text"},
    )
    assert response.status_code == 422  # Form(...) ohne csrf_token fehlt


# --- KI-Bearbeitung --------------------------------------------------------


def test_ai_edit_creates_new_version_and_returns_it_as_proposal(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    csrf = _csrf(client, seeded["draft_id"])
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/ai-edit",
        data={
            "instruction_text": "Formuliere präziser.",
            "purpose": "improve_clarity",
            "selected_text": "",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    new_draft_id = body["new_draft_id"]
    assert new_draft_id != seeded["draft_id"]
    # 05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS" Phase D:
    # KI-generierter Inhalt wird jetzt zu Editor-HTML gewandelt (siehe
    # app/drafting/markdown_to_draft_html.py) statt roh gespeichert - die
    # KI-Vorschau (app_draft_editor.js) nutzt bereits korrekt
    # `data.content_format` und rendert "html" als echtes `innerHTML`
    # statt per escapeHtml()+<br> als Klartext.
    assert body["content"] == "<p>Neuer KI-Entwurfstext.</p>"
    assert body["content_format"] == "html"

    db_session.expire_all()
    original = db_session.get(Draft, seeded["draft_id"])
    new_draft = db_session.get(Draft, new_draft_id)
    assert original.content == "Ursprünglicher Entwurfstext."  # unveraendert
    assert new_draft.version == 2
    assert new_draft.previous_version_id == original.id


def test_ai_edit_discard_marks_new_version_without_deleting_it(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    csrf = _csrf(client, seeded["draft_id"])
    ai_response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/ai-edit",
        data={"instruction_text": "Kürze.", "purpose": "optimize_style", "csrf_token": csrf},
    ).json()
    new_draft_id = ai_response["new_draft_id"]

    discard_csrf = _csrf(client, seeded["draft_id"])
    response = client.post(
        f"/dashboard/drafts/{new_draft_id}/ai-edit/discard",
        data={"csrf_token": discard_csrf},
    )
    assert response.status_code == 200
    assert response.json()["discarded"] is True

    db_session.expire_all()
    new_draft = db_session.get(Draft, new_draft_id)
    assert new_draft is not None  # nicht geloescht
    assert new_draft.status == AI_SUGGESTION_DISCARDED_STATUS


# --- Als Vorlage speichern -------------------------------------------------


def test_save_as_template_creates_document_template(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    csrf = _csrf(client, seeded["draft_id"])
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/save-as-template",
        data={"name": "Standard-Einspruch", "category": "Einspruch", "csrf_token": csrf},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    db_session.expire_all()
    template = db_session.get(DocumentTemplate, body["template_id"])
    assert template.name == "Standard-Einspruch"
    assert template.content == "Ursprünglicher Entwurfstext."


def test_save_as_template_rejects_blank_name(client: TestClient, seeded: dict) -> None:
    csrf = _csrf(client, seeded["draft_id"])
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/save-as-template",
        data={"name": "   ", "csrf_token": csrf},
    )
    assert response.status_code == 422
    assert response.json()["success"] is False


def test_editor_page_shows_letterhead_and_signature_from_firm_profile(
    db_session: Session, client: TestClient, seeded: dict[str, str]
) -> None:
    """ECHTER FUND (Real-E2E 09.10.): die Editor-Seite uebergab dem Template keinen Briefkopf-/
    Signatur-Kontext - der Briefkopf aus dem Kanzlei-Profil erschien nie im Editor."""
    from app.firm_profile import get_firm_profile

    profile = get_firm_profile(db_session)
    profile.firm_name = "Kanzlei Beispiel (QA)"
    profile.street = "Beispielweg 1"
    profile.postal_code = "00000"
    profile.city = "Musterstadt"
    profile.signatory_name = "RA Test Beispiel"
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}/edit")

    assert response.status_code == 200
    assert 'class="document-page__letterhead"' in response.text
    assert "Kanzlei Beispiel (QA)" in response.text
    assert "Beispielweg 1, 00000 Musterstadt" in response.text
    assert 'class="document-page__signature"' in response.text
    assert "RA Test Beispiel" in response.text


def test_editor_warns_when_a_newer_version_exists(
    db_session: Session, client: TestClient, seeded: dict[str, str]
) -> None:
    """Eine Chat-Ueberarbeitung legt eine neuere Fassung an - der Editor einer aelteren Fassung
    weist darauf hin und verlinkt die aktuelle."""
    v1 = db_session.get(Draft, seeded["draft_id"])
    v2 = Draft(
        matter_id=v1.matter_id, content="<p>Neu</p>", version=v1.version + 1, status="draft",
        content_format="html", previous_version_id=v1.id,
    )
    db_session.add(v2)
    db_session.commit()

    old_page = client.get(f"/dashboard/drafts/{v1.id}/edit")
    new_page = client.get(f"/dashboard/drafts/{v2.id}/edit")

    assert "Es gibt eine neuere Fassung" in old_page.text
    assert f"/dashboard/drafts/{v2.id}/edit" in old_page.text
    assert "Es gibt eine neuere Fassung" not in new_page.text
