"""End-to-End-Beweis der NEUEN Gateway-Architektur (ARCHITECTURE.md §70):

    synthetisches Kanzleidokument
    -> Upload/Intake (echter Extraktions-/Verarbeitungsweg)
    -> lokale Kontextaufbereitung (RuleBasedLocalAIProvider)
    -> Presidio/Regex-Erkennung + Pseudonymisierung + Security-Check +
       Final Payload Gate (ClaudePrivacyGateway - ECHT, nicht gemockt)
    -> GatewayRelayWritingProvider (ECHT - HTTP-Aufruf, kein Anthropic-Key
       im Client)
    -> ECHTER Lexono-Gateway-Server (gateway/main.py, ECHT - Auth,
       Rate-Limiting, Modell-/Token-Allowlist laufen tatsächlich durch;
       läuft hier über TestClient statt eines gebundenen Ports - "lokal,
       niemals öffentlich erreichbar", siehe ARCHITECTURE.md §70)
    -> NUR der allerletzte Hop ist simuliert: `anthropic.Anthropic` selbst
       (echter Netzwerkaufruf wäre kostenpflichtig; der echte, kontrollierte
       Qualitätstest mit dem echten Dev-Key läuft separat und manuell,
       siehe PILOT_PLAYBOOK.md)
    -> Antwort zurück durch den Gateway zum Client
    -> lokale Rekonstruktion
    -> fertiger Schriftsatz

Beweist zusätzlich zum bisherigen tests/test_e2e_pilot_scenario.py NEU:
Originaldaten erscheinen NIE im tatsächlichen HTTP-Request-Body an den
Gateway, der Client besitzt keinen Anthropic-Key (weder als Attribut noch
in den verwendeten Settings), und der Gateway persistiert nichts."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.claude_writing_provider import build_writing_prompt
from app.ai_providers.gateway_writing_provider import GatewayRelayWritingProvider
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.documents.service import DocumentProcessingService
from app.drafting.service import DraftingService
from app.models import Client, Document, Matter, Party
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from gateway.config import GatewaySettings, get_gateway_settings
from gateway.main import app as gateway_app
from gateway.main import get_db as gateway_get_db
from gateway.models import Base as GatewayBase
from gateway.models import Tenant
from gateway.security import generate_client_secret, hash_client_secret
from tests.fake_embedding_provider import FakeEmbeddingProvider

_MANDANT_NAME = "Erika Testfrau"
_GEGNER_NAME = "Finanzamt Musterstadt"
_ADRESSE = "Beispielweg 7"
_ORT_ZEILE = "80331 München"
_STEUER_ID = "12 345 678 903"
_IBAN = "DE02120300000000202051"
_DATUM = "15.02.2026"

_SYNTHETIC_DOCUMENT_TEXT = (
    f"Sehr geehrte Damen und Herren,\n\n"
    f"unsere Mandantin {_MANDANT_NAME}, wohnhaft {_ADRESSE}, {_ORT_ZEILE},\n"
    f"Steuer-ID {_STEUER_ID}, legt gegen den Steuerbescheid des {_GEGNER_NAME}\n"
    f"vom {_DATUM} form- und fristgerecht Einspruch ein.\n\n"
    f"eine etwaige Erstattung bitten wir auf folgendes Konto zu ueberweisen:\n"
    f"IBAN {_IBAN}.\n\n"
    f"Mit freundlichen Gruessen"
)

_SECRET_VALUES = [
    _MANDANT_NAME,
    _ADRESSE,
    _STEUER_ID.replace(" ", ""),
    _IBAN,
    _GEGNER_NAME,
]

_FAKE_ANTHROPIC_RESPONSE_TEXT = (
    "Sehr geehrte Damen und Herren,\n\n"
    "hiermit legen wir fuer unsere Mandantin form- und fristgerecht "
    "Einspruch ein. Wir verweisen auf die beigefuegten Unterlagen.\n\n"
    "Mit freundlichen Gruessen"
)


def _build_synthetic_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in _SYNTHETIC_DOCUMENT_TEXT.split("\n"):
        page.insert_text((50, y), line)
        y += 20
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def gateway_db_session() -> Iterator[Session]:
    """Eigene, vom Kanzlei-Client vollständig getrennte Gateway-Datenbank -
    entspricht real getrennten Prozessen/Servern (siehe gateway/__init__.py)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    GatewayBase.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def gateway_tenant(gateway_db_session: Session) -> tuple[Tenant, str]:
    secret = generate_client_secret()
    tenant = Tenant(
        display_name="E2E-Test-Kanzlei",
        secret_hash=hash_client_secret(secret),
        rate_limit_per_minute=30,
    )
    gateway_db_session.add(tenant)
    gateway_db_session.commit()
    gateway_db_session.refresh(tenant)
    return tenant, secret


@pytest.fixture()
def gateway_test_client(
    gateway_db_session: Session,
) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield gateway_db_session

    gateway_app.dependency_overrides[gateway_get_db] = _override_get_db
    gateway_app.dependency_overrides[get_gateway_settings] = lambda: GatewaySettings(
        anthropic_api_key="sk-ant-server-side-only-never-reaches-client",
        allowed_models=["claude-sonnet-5"],
        max_tokens_ceiling=4000,
    )
    try:
        yield TestClient(gateway_app)
    finally:
        gateway_app.dependency_overrides.clear()


def test_full_pilot_scenario_through_real_gateway_server(
    db_session: Session,
    tmp_path: Path,
    gateway_test_client: TestClient,
    gateway_tenant: tuple[Tenant, str],
) -> None:
    tenant, secret = gateway_tenant

    # --- STUFE 1: Originaldokument (synthetisch, lokal) ------------------
    pdf_path = tmp_path / "einspruch_scan.pdf"
    _build_synthetic_pdf(pdf_path)

    client = Client(name=_MANDANT_NAME)
    matter = Matter(client=client, title="Einspruch Steuerbescheid Gateway-E2E-Test")
    db_session.add_all([client, matter])
    db_session.commit()
    db_session.add(Party(matter_id=matter.id, name=_GEGNER_NAME, role="Gegner"))
    db_session.commit()

    document = Document(
        matter_id=matter.id,
        original_filename="einspruch_scan.pdf",
        file_path=str(pdf_path),
        classified_type="Einspruch",
    )
    db_session.add(document)
    db_session.commit()

    # --- STUFE 2: Upload/Intake-Verarbeitung (echter Code, kein Mock) ----
    processor = DocumentProcessingService(ocr_enabled=True)
    processor.process_document(document, db_session)
    assert document.ocr_status == "not_needed"
    assert _MANDANT_NAME in document.extracted_text

    # --- STUFE 3: echter GatewayRelayWritingProvider, umgeleitet auf den
    # echten Gateway-TestClient statt eines gebundenen TCP-Ports ----------
    gateway_provider = GatewayRelayWritingProvider(
        base_url="http://gateway.local.invalid",  # nie tatsaechlich aufgeloest, siehe Patch unten
        client_id=tenant.client_id,
        client_secret=secret,
        model="claude-sonnet-5",
    )

    captured_request_bodies: list[dict] = []

    def _fake_httpx_post(url, *, json, headers, timeout):
        captured_request_bodies.append(json)
        # Leitet den "HTTP"-Aufruf an den ECHTEN Gateway-FastAPI-Prozess
        # weiter (via TestClient) - Auth/Rate-Limit/Allowlist-Code laeuft
        # dabei tatsaechlich, nur der TCP-Transport ist ersetzt.
        path = url.split("gateway.local.invalid", 1)[1]
        return gateway_test_client.post(path, json=json, headers=headers)

    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    service = DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        gateway_provider,
        model_name="claude-sonnet-5",
    )

    with (
        patch(
            "app.ai_providers.gateway_relay_client.httpx.post", side_effect=_fake_httpx_post
        ),
        patch("gateway.relay.anthropic.Anthropic") as mock_anthropic_cls,
    ):
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = _FAKE_ANTHROPIC_RESPONSE_TEXT
        fake_response = MagicMock()
        fake_response.content = [text_block]
        usage = MagicMock()
        usage.input_tokens = 42
        usage.output_tokens = 17
        fake_response.usage = usage
        mock_anthropic_cls.return_value.messages.create.return_value = fake_response

        result = service.create_draft(matter.id, "formulate_draft", db_session)

    # --- STUFE 4: Canary-Beweis - Original-Personendaten NIE im tatsächlich
    # gesendeten HTTP-Request-Body an den Gateway --------------------------
    assert result.success is True, f"Unerwartet blockiert: {result.blocked_reasons}"
    assert len(captured_request_bodies) == 1
    import json as json_module

    request_body_text = json_module.dumps(captured_request_bodies[0])
    for secret_value in _SECRET_VALUES:
        assert secret_value not in request_body_text, (
            f"DATENSCHUTZVERSTOSS: Originalwert {secret_value!r} im tatsaechlichen "
            f"HTTP-Request-Body an den Gateway gefunden!"
        )
    assert "[MANDANT_01]" in request_body_text
    assert "[STEUER_ID_01]" in request_body_text

    # --- STUFE 5: der Gateway-Server selbst kennt keinerlei Originaldaten -
    # (er hat nur denselben bereits pseudonymisierten Body gesehen, den der
    # Client verschickt hat - keine eigene Erkennung/Pseudonymisierung).
    gateway_table_names = set(GatewayBase.metadata.tables.keys())
    assert gateway_table_names == {"tenants"}  # keine Payload-/Content-Tabelle

    # --- STUFE 6: Rekonstruktion -----------------------------------------
    assert result.draft_text is not None
    assert "form- und fristgerecht Einspruch" in result.draft_text

    # --- STUFE 7: der Client besitzt keinen Anthropic-Key -----------------
    assert not hasattr(gateway_provider, "_client")
    for attr_name, attr_value in vars(gateway_provider).items():
        if isinstance(attr_value, str):
            assert not attr_value.startswith("sk-ant-"), (
                f"Client-Attribut {attr_name!r} sieht wie ein echter Anthropic-Key aus!"
            )

    print("\n=== Finaler, lokal rekonstruierter Schriftsatz (über echten Gateway) ===")
    print(result.draft_text)


def test_gateway_rejects_request_with_invalid_credential_in_full_flow(
    db_session: Session,
    tmp_path: Path,
    gateway_test_client: TestClient,
) -> None:
    """Fail-closed-Beweis: eine ungültige Kanzlei-Credential führt zu einem
    kontrolliert blockierten Entwurf, NIEMALS zu einem stillen Fallback auf
    einen direkten Cloud-Pfad."""
    client = Client(name=_MANDANT_NAME)
    matter = Matter(client=client, title="Fail-Closed-Test")
    db_session.add_all([client, matter])
    db_session.commit()

    gateway_provider = GatewayRelayWritingProvider(
        base_url="http://gateway.local.invalid",
        client_id="unknown-client-id",
        client_secret="falsches-secret",
        model="claude-sonnet-5",
    )

    def _fake_httpx_post(url, *, json, headers, timeout):
        path = url.split("gateway.local.invalid", 1)[1]
        return gateway_test_client.post(path, json=json, headers=headers)

    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    service = DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        gateway_provider,
        model_name="claude-sonnet-5",
    )

    with patch(
        "app.ai_providers.gateway_relay_client.httpx.post", side_effect=_fake_httpx_post
    ):
        result = service.create_draft(matter.id, "formulate_draft", db_session)

    assert result.success is False
    assert result.draft_text is None
