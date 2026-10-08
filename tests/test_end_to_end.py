"""End-to-End-Test (Prompt 30).

Anders als die bisherigen Testdateien (die jeweils EIN Modul/EINEN
Endpunkt isoliert prüfen) verfolgt dieser Test EINE durchgängige
Fallreise über die gesamte, in den Prompts 04-29 gebaute Kette hinweg -
über die ECHTE HTTP-/Dashboard-Schicht (FastAPI TestClient), nicht durch
direkte Service-Aufrufe:

  Synthetischer Fall (Prompt 29)
    -> Entwurf erstellen (Prompt 17)
    -> Entwurfsansicht ansehen: Original/Entwurf/Quellen/Findings/Audit
       (Prompt 22-24)
    -> Anwaltliche Anmerkung speichern (Prompt 23)
    -> Anmerkung anwenden -> neue Version (Prompt 23)
    -> Review-Engine-Prüfung (Prompt 18)
    -> Manuelle Bearbeitung -> weitere Version (Prompt 24)
    -> Freigabe -> automatische Postausgang-Übergabe (Prompt 24/25)
    -> Als versendet markieren (Prompt 25)
    -> vollständiger, lückenloser Audit-Trail über die gesamte Reise
       (Prompt 19)

Zusätzlich: Rollentrennung (Prompt 26) UND Cross-Matter-Isolation
innerhalb DERSELBEN Reise geprüft, nicht nur isoliert wie in den
jeweiligen Fach-Testdateien.

WICHTIG, ehrlich benannt: der erste Entwurf wird noch direkt über
`DraftingService` erzeugt (nicht über einen Dashboard-Button) - es gibt
aktuell KEINEN UI-Trigger im Posteingang, um aus einer Nachricht einen
Entwurf zu erstellen (dokumentierte offene Lücke, siehe ARCHITECTURE.md
§36, Punkt 1). Der Rest der Reise läuft vollständig über echte
HTTP-Requests gegen das Dashboard.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.drafts_router as drafts_router_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.attorney_instructions.service import AttorneyInstructionService
from app.auth.security import hash_password
from app.db.session import get_db
from app.drafting.service import DraftingService
from app.main import app
from app.models import (
    AttorneyInstruction,
    AuditEvent,
    Draft,
    OutboxEntry,
    ReviewFinding,
    Role,
    User,
)
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.privacy.security_check import SecurityCheckService
from app.research.service import LegalResearchService
from app.review.engine import ReviewEngine
from app.review.provider import ClaudeReviewProvider
from app.review.schema import Finding, ReviewResult
from app.search.service import DocumentSearchService
from app.synthetic_data import SyntheticDataGenerator
from tests.fake_embedding_provider import FakeEmbeddingProvider

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None
    return match.group(1)


class _FakeWritingProvider:
    """Erzeugt einen erkennbar unterschiedlichen Text je Aufruf, damit
    Versionen im Test klar auseinandergehalten werden können.

    ECHTER FUND (06.10., Owner-Direktive "Schriftsatz-Workflow,
    Pseudonymisierung, lokale KI und DIN-A4-Dokumentdarstellung", Phase 8):
    die deterministische Platzhalter-Integritaetspruefung
    (app/privacy/security_check.py::check_response_placeholder_integrity)
    laeuft jetzt IMMER (nicht mehr nur bei konfigurierter lokaler KI, siehe
    app/drafting/service.py::_finish_non_streaming_stream) - ein Fake, der
    JEDEN im Payload tatsaechlich vorhandenen Mapping-Platzhalter ignoriert,
    verletzt damit fuer Zwecke ausserhalb von `_RELAXED_COVERAGE_PURPOSES`
    (z. B. "improve_draft", der Anmerkung-Anwenden-Schritt dieser Reise)
    dieselbe Regel, die ein real instruiertes Claude einhaelt ("verwende
    NUR vorhandene Platzhalter" - UND haelt sie damit implizit auch ein,
    wenn sie bereits im Sachverhalt vorkommen). Echot deshalb jeden im
    Payload gefundenen Platzhalter in der Antwort mit - realistischeres
    Verhalten, keine Lockerung der eigentlichen Pruefung."""

    def __init__(self) -> None:
        self.call_count = 0

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        self.call_count += 1
        import re

        placeholders = sorted(set(re.findall(r"\[[A-Z_]+_\d{2}\]", payload.anonymisierter_sachverhalt or "")))
        placeholder_suffix = f" Betrifft: {' '.join(placeholders)}." if placeholders else ""
        return ClaudeWritingResult(
            text=f"KI-generierte Antwort, Version {self.call_count}.{placeholder_suffix}",
            token_count=42,
        )


class _FakeReviewProvider(ClaudeReviewProvider):
    def review(self, payload: ClaudeRequestPayload) -> ReviewResult:
        return ReviewResult(
            findings=[
                Finding(
                    category="formaler_fehler",
                    severity="niedrig",
                    description="Aktenzeichen sollte in der Betreffzeile stehen.",
                )
            ],
            overall_assessment="Entwurf inhaltlich in Ordnung, kleinere Formmängel.",
        )


class _AlwaysAllowSecurityCheck(SecurityCheckService):
    """NUR für die Workflow-Reise in diesem Test: umgeht bewusst die
    Security-Check-Heuristik. Deren tatsächliches Verhalten ist bereits
    dediziert geprüft
    (test_realistic_scenario_texts_no_longer_trigger_unrecognized_entity_heuristic
    unten, sowie test_security_review.py/test_prompt_injection_documents.py) -
    seit der POS-Tag-Verfeinerung (13.09., siehe dort) loest die Heuristik
    bei realistischem deutschem Rechtstext nicht mehr false-positiv aus,
    dieser Bypass bleibt aber als zusaetzliche Isolation bestehen: dieser
    Test prueft die WORKFLOW-MASCHINERIE (Versionierung, Rollen,
    Audit-Trail, Postausgang), nicht die Heuristik-Praezision."""

    def check(self, pseudonymized_text, mappings, *, purpose, unrecognized_name_scan_text=None, skip_residual_categories=frozenset(), residual_ignore_ranges=None):  # noqa: ANN001
        from app.privacy.security_check_schema import SecurityCheckResult

        return SecurityCheckResult(passed=True, reasons=[])


def _drafting_service(writing_provider: _FakeWritingProvider) -> DraftingService:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(security_check=_AlwaysAllowSecurityCheck()),
        writing_provider,
        model_name="claude-sonnet-5",
    )


def _review_engine() -> ReviewEngine:
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    return ReviewEngine(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        ClaudePrivacyGateway(security_check=_AlwaysAllowSecurityCheck()),
        _FakeReviewProvider(),
        model_name="claude-sonnet-5",
    )


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
def app_client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def roles_and_users(db_session: Session) -> dict[str, User]:
    admin_role = Role(name="Admin")
    anwalt_role = Role(name="Anwalt")
    mitarbeiter_role = Role(name="Mitarbeiter")
    db_session.add_all([admin_role, anwalt_role, mitarbeiter_role])
    db_session.commit()

    def _user(email: str, role: Role) -> User:
        u = User(
            email=email,
            role_id=role.id,
            is_active=True,
            password_hash=hash_password("TestPasswort123"),
            must_change_password=False,
        )
        db_session.add(u)
        return u

    anwalt = _user("anwalt@kanzlei.test", anwalt_role)
    mitarbeiter = _user("mitarbeiter@kanzlei.test", mitarbeiter_role)
    db_session.commit()
    return {"anwalt": anwalt, "mitarbeiter": mitarbeiter}


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/dashboard/login",
        data={"email": email, "password": "TestPasswort123", "next": "/dashboard/inbox"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "lexono_session" in response.cookies


def test_full_case_journey_from_synthetic_data_to_sent_outbox(
    app_client: TestClient,
    db_session: Session,
    roles_and_users: dict[str, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --- 0. Synthetischer Fall (Prompt 29) - EIN Fall für die Hauptreise,
    # EIN zweiter, unabhängiger Fall zur späteren Isolationsprüfung.
    # "betriebspruefung" historisch gewählt, weil es (vor der POS-Tag-
    # Verfeinerung der Heuristik, siehe
    # test_realistic_scenario_texts_no_longer_trigger_unrecognized_entity_heuristic
    # unten) eines der wenigen Szenarien war, das die alte, rein
    # grossschreibungsbasierte Heuristik nicht auslöste - diese Instanz
    # nutzt ohnehin `_AlwaysAllowSecurityCheck` (s.o.) und ist von der
    # Heuristik unabhängig, die Wahl selbst hat aber historischen Bezug.
    generator = SyntheticDataGenerator(seed=1)
    main_case = generator.generate_case(db_session, scenario_key="betriebspruefung")
    other_case = generator.generate_case(db_session, scenario_key="einspruch_steuerbescheid")
    generator.generate_shared_knowledge_base(db_session)

    # --- 1. Ohne Login: kein Zugriff (Grundvoraussetzung der ganzen Reise) ---
    denied = app_client.get("/dashboard/inbox", follow_redirects=False)
    assert denied.status_code == 303
    assert "/dashboard/login" in denied.headers["location"]

    # --- 2. Anwalt meldet sich an und sieht den Fall im Posteingang ---
    _login(app_client, "anwalt@kanzlei.test")
    inbox = app_client.get("/dashboard/inbox")
    assert inbox.status_code == 200
    assert main_case.matter.title in inbox.text or main_case.message.subject in inbox.text

    # --- 3. Ersten Entwurf erstellen (direkter Service-Aufruf, siehe
    # Moduldocstring - kein Dashboard-Trigger dafür vorhanden). ---
    writing_provider = _FakeWritingProvider()
    drafting_service = _drafting_service(writing_provider)
    result = drafting_service.create_draft(
        main_case.matter.id, "formulate_draft", db_session, actor="anwalt@kanzlei.test"
    )
    assert result.success is True
    draft_v1_id = result.draft_id

    # DraftingService.create_draft verknüpft aktuell KEINE message_id
    # (dokumentierte offene Lücke, siehe ARCHITECTURE.md §36, Punkt 1 -
    # "kein UI-Trigger im Posteingang, um aus einer Nachricht einen
    # Entwurf zu erstellen"). Für diese Reise wird die Verknüpfung hier
    # manuell nachgezogen, um den "Original"-Bereich der Entwurfsansicht
    # sinnvoll mitzuprüfen - simuliert, was ein künftiger UI-Trigger tun
    # würde.
    draft_v1 = db_session.get(Draft, draft_v1_id)
    draft_v1.message_id = main_case.message.id
    db_session.commit()

    # --- 4. Entwurfsansicht: Original + Entwurf + Quellen + leere Findings ---
    draft_page = app_client.get(f"/dashboard/drafts/{draft_v1_id}")
    assert draft_page.status_code == 200
    assert main_case.message.body_text[:30] in draft_page.text
    assert "Version 1" in draft_page.text
    assert "Noch keine Prüfung durchgeführt" in draft_page.text

    # --- 5. Anmerkung speichern (kein Regenerieren) ---
    csrf = _csrf(draft_page.text)
    save_response = app_client.post(
        f"/dashboard/drafts/{draft_v1_id}/instructions",
        data={
            "instruction_text": "Auf die Werbungskosten in Zeile 14 ausdrücklich eingehen.",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert save_response.status_code == 303
    assert db_session.query(AttorneyInstruction).count() == 1
    saved_instruction = db_session.query(AttorneyInstruction).first()
    assert saved_instruction.status == "open"

    # --- 6. Anmerkung anwenden -> neue Version (v2) ---
    monkeypatch.setattr(
        drafts_router_module,
        "get_attorney_instruction_service",
        lambda: AttorneyInstructionService(drafting_service),
    )
    draft_page = app_client.get(f"/dashboard/drafts/{draft_v1_id}")
    csrf = _csrf(draft_page.text)
    apply_response = app_client.post(
        f"/dashboard/drafts/{draft_v1_id}/instructions/apply",
        data={
            "instruction_text": "Zusätzlich auf die Fristwahrung hinweisen.",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert apply_response.status_code == 303
    draft_v2_id = apply_response.headers["location"].rsplit("/", 1)[-1]
    draft_v2 = db_session.get(Draft, draft_v2_id)
    assert draft_v2.previous_version_id == draft_v1_id
    assert draft_v2.version == 2
    applied_instruction = (
        db_session.query(AttorneyInstruction).filter_by(status="applied").first()
    )
    assert applied_instruction is not None
    assert applied_instruction.resulting_draft_id == draft_v2_id

    # --- 7. Review-Engine-Prüfung auf v2 ---
    monkeypatch.setattr(drafts_router_module, "get_review_engine", _review_engine)
    draft_page = app_client.get(f"/dashboard/drafts/{draft_v2_id}")
    csrf = _csrf(draft_page.text)
    review_response = app_client.post(
        f"/dashboard/drafts/{draft_v2_id}/review",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert review_response.status_code == 303
    findings = db_session.query(ReviewFinding).filter_by(draft_id=draft_v2_id).all()
    assert len(findings) == 1
    assert findings[0].category == "formaler_fehler"

    # --- 8. Manuelle Bearbeitung -> weitere Version (v3) ---
    draft_page = app_client.get(f"/dashboard/drafts/{draft_v2_id}")
    csrf = _csrf(draft_page.text)
    manual_edit_response = app_client.post(
        f"/dashboard/drafts/{draft_v2_id}/manual-edit",
        data={
            "content": "Handbearbeiteter, finaler Entwurfstext nach anwaltlicher Prüfung.",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert manual_edit_response.status_code == 303
    draft_v3_id = manual_edit_response.headers["location"].rsplit("/", 1)[-1]
    draft_v3 = db_session.get(Draft, draft_v3_id)
    assert draft_v3.previous_version_id == draft_v2_id
    assert draft_v3.version == 3

    # --- 9. Mitarbeiter darf v3 NICHT freigeben (Rollentrennung mitten
    # in der echten Reise, nicht nur isoliert getestet) ---
    mitarbeiter_client = TestClient(app)
    app.dependency_overrides[get_db] = lambda: db_session
    _login(mitarbeiter_client, "mitarbeiter@kanzlei.test")
    mitarbeiter_draft_page = mitarbeiter_client.get(f"/dashboard/drafts/{draft_v3_id}")
    mitarbeiter_csrf = _csrf(mitarbeiter_draft_page.text)
    forbidden = mitarbeiter_client.post(
        f"/dashboard/drafts/{draft_v3_id}/approve",
        data={"csrf_token": mitarbeiter_csrf},
    )
    assert forbidden.status_code == 403
    # Aber lesen darf der Mitarbeiter:
    assert mitarbeiter_draft_page.status_code == 200
    assert "Handbearbeiteter" in mitarbeiter_draft_page.text

    # --- 10. Anwalt gibt v3 frei -> automatische Postausgang-Übergabe ---
    draft_page = app_client.get(f"/dashboard/drafts/{draft_v3_id}")
    csrf = _csrf(draft_page.text)
    approve_response = app_client.post(
        f"/dashboard/drafts/{draft_v3_id}/approve",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert approve_response.status_code == 303
    db_session.expire_all()
    assert draft_v3.status == "approved"
    outbox_entry = db_session.query(OutboxEntry).filter_by(draft_id=draft_v3_id).first()
    assert outbox_entry is not None
    assert outbox_entry.status == "pending"

    # --- 11. Als versendet markieren ---
    outbox_page = app_client.get("/dashboard/outbox")
    assert main_case.matter.title in outbox_page.text
    csrf = _csrf(outbox_page.text)
    mark_sent_response = app_client.post(
        f"/dashboard/outbox/{outbox_entry.id}/mark-sent",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert mark_sent_response.status_code == 303
    db_session.expire_all()
    assert outbox_entry.status == "sent"
    assert outbox_entry.sent_by == "anwalt@kanzlei.test"

    # --- 12. Vollständiger, lückenloser Audit-Trail über die gesamte Reise ---
    relevant_ids = {draft_v1_id, draft_v2_id, draft_v3_id, applied_instruction.id, outbox_entry.id}
    all_event_types = {
        e.event_type for e in db_session.query(AuditEvent).all() if e.entity_id in relevant_ids
    }
    expected_event_types = {
        "draft_created",
        "attorney_instruction_created",
        "attorney_instruction_applied",
        "draft_version_created",
        "draft_manual_edit",
        "draft_approved",
        "draft_added_to_outbox",
        "draft_marked_sent",
    }
    assert expected_event_types <= all_event_types

    # --- 13. Cross-Matter-Isolation: der zweite, unabhängige Fall bleibt
    # während der GESAMTEN Reise vollständig unberührt. ---
    assert db_session.query(Draft).filter_by(matter_id=other_case.matter.id).count() == 0
    other_matter_audit_events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.entity_id == other_case.matter.id)
        .all()
    )
    assert {e.event_type for e in other_matter_audit_events} == {"synthetic_case_generated"}


def test_realistic_scenario_texts_no_longer_trigger_unrecognized_entity_heuristic(
    db_session: Session,
) -> None:
    """ECHTER FUND, BEHOBEN (Abnahme-Test, 13.09.): dieser Test
    dokumentierte urspruenglich einen realen Nutzbarkeits-Fund - die
    Security-Check-Heuristik fuer "moeglicherweise unerkannte Namen" (zwei
    aufeinanderfolgende grossgeschriebene Woerter) loeste bei
    realistischem deutschem Verwaltungs-/Rechtstext ("Festgesetzte
    Einkommensteuer", "Mahnung Zahlungsverzug", "Fristlose Kuendigung")
    FALSCH aus, weil im Deutschen ALLE Substantive grossgeschrieben
    werden - 4 von 6 realistischen Szenarien wurden faelschlich
    blockiert, obwohl keine Namen vorkamen.

    Root Cause: die Heuristik konnte Adjektiv+Substantiv-Phrasen nicht
    von Vorname+Nachname unterscheiden. Fix: `security_check.py::
    _find_possible_unrecognized_names` nutzt jetzt optional spaCys
    POS-Tags (app/privacy/presidio_ner.py::get_pos_tags, DIESELBE bereits
    geladene Pipeline, keine zusaetzliche Speicherlast) - ein Kandidat
    gilt nur noch dann als namensverdaechtig, wenn BEIDE Woerter als
    PROPN (Eigenname) getaggt sind, nicht nur grossgeschrieben. Bewusst
    KEINE Lockerung "auf Verdacht" - eine echte, real gemessene
    linguistische Unterscheidung.

    Dieser Test prueft jetzt das GEGENTEIL des urspruenglichen Fundes:
    ALLE sechs realistischen Szenarien muessen jetzt durchlaufen."""
    from app.privacy.gateway import ClaudePrivacyGateway
    from app.synthetic_data.scenarios import SCENARIOS

    gateway = ClaudePrivacyGateway()
    format_kwargs = dict(
        jahr=2024,
        jahr_von=2022,
        mandant="Max Mustermann",
        mandant_kurz="max",
        betrag="5.000",
        bescheid_datum="01.01.2026",
        pruefungsbeginn="01.02.2026",
    )
    blocked_scenarios: list[str] = []
    for scenario in SCENARIOS:
        title = scenario.matter_title_template.format(**format_kwargs)
        document_excerpt = scenario.document_extracted_text_template.format(**format_kwargs)
        # Exakt derselbe Aufbau wie RuleBasedLocalAIProvider._build_sachverhalt.
        full_sachverhalt = f"Akte: {title}\n[{scenario.classified_type}] {document_excerpt}"
        result = gateway.prepare_request(
            purpose="formulate_draft",
            sachverhalt=full_sachverhalt,
            known_entities={"mandant": ["Max Mustermann"]},
        )
        if not result.allowed:
            blocked_scenarios.append(scenario.key)

    assert blocked_scenarios == []


def test_unauthenticated_journey_is_blocked_at_every_step(
    app_client: TestClient, db_session: Session
) -> None:
    """Ergänzender End-to-End-Beweis: OHNE Login ist JEDER Schritt der
    Reise blockiert, nicht nur der erste."""
    generator = SyntheticDataGenerator(seed=2)
    case = generator.generate_case(db_session)
    writing_provider = _FakeWritingProvider()
    result = _drafting_service(writing_provider).create_draft(
        case.matter.id, "formulate_draft", db_session, actor="system"
    )
    draft_id = result.draft_id

    for path in (
        "/dashboard/inbox",
        "/dashboard/drafts",
        f"/dashboard/drafts/{draft_id}",
        "/dashboard/outbox",
    ):
        response = app_client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert "/dashboard/login" in response.headers["location"]

    for path, data in (
        (f"/dashboard/drafts/{draft_id}/approve", {"csrf_token": "x"}),
        (f"/dashboard/drafts/{draft_id}/manual-edit", {"content": "x", "csrf_token": "x"}),
    ):
        response = app_client.post(path, data=data, follow_redirects=False)
        assert response.status_code == 303
        assert "/dashboard/login" in response.headers["location"]
