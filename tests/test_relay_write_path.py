"""§44: Produktiver Schreibpfad ueber das Lexono-Relay (ARCHITECTURE.md §70) - Token-Grenzen, Denkmodus,
Abschneiden, Timeout, Wiederholungen, Fehlerbehandlung. Echter Relay-Endpunkt (FastAPI-TestClient, Transport ersetzt),
Anthropic-SDK gefaelscht - kein Netz, nur synthetische Daten.

Reproduzierter Ausgangsfehler: Mit Standard-Settings (ohne CLAUDE_MAX_TOKENS in der .env) schickte der Client
max_tokens=4096, das Relay erlaubt hoechstens 4000 -> 400 fuer JEDE Schreibanfrage ("Interner Fehler bei der
Textproduktion")."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_providers.factory import build_review_provider, build_writing_provider
from app.ai_providers.gateway_relay_client import GatewayRelayError
from app.config.settings import Settings
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload, UnsealedPayloadError
from gateway.config import GatewaySettings, get_gateway_settings
from gateway.main import app, get_db
from gateway.models import Base, Tenant
from gateway.security import generate_client_secret, hash_client_secret

ROOT = Path(__file__).resolve().parents[1]
TEXT = "Sehr geehrte Damen und Herren, wir zeigen an, dass die Anlage mangelhaft ist."


@pytest.fixture()
def relay() -> Iterator[dict]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db: Session = sessionmaker(bind=engine)()
    secret = generate_client_secret()
    tenant = Tenant(display_name="Synthetische Kanzlei", secret_hash=hash_client_secret(secret),
                    rate_limit_per_minute=30)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    def _db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_gateway_settings] = lambda: GatewaySettings(anthropic_api_key="sk-ant-fake-key")
    client = TestClient(app)
    posts: list[dict] = []

    def fake_post(url, json, headers, timeout):  # noqa: A002 - Signatur wie httpx.post im Relay-Client
        posts.append({"json": json, "timeout": timeout})
        return client.post("/v1/relay/messages", json=json, headers=headers)

    settings = Settings(_env_file=None, lexono_gateway_url="http://127.0.0.1:8700",
                        lexono_gateway_client_id=tenant.client_id, lexono_gateway_client_secret=secret)
    try:
        with patch("app.ai_providers.gateway_relay_client.httpx.post", side_effect=fake_post), \
                patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
            anthropic_cls.return_value.messages.create.return_value = _response()
            yield {"settings": settings, "posts": posts, "anthropic": anthropic_cls, "client": client,
                   "headers": {"Authorization": f"Bearer {tenant.client_id}:{secret}"}}
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def _response(text: str = TEXT, stop_reason: str = "end_turn") -> MagicMock:
    block = MagicMock()
    block.type, block.text = "text", text
    response = MagicMock()
    response.content, response.stop_reason = [block], stop_reason
    response.usage.input_tokens, response.usage.output_tokens = 120, 40
    return response


def _payload(purpose: str = "formulate_draft") -> ClaudeRequestPayload:
    return ClaudePrivacyGateway().prepare_request(purpose=purpose, sachverhalt="Die Anlage ist mangelhaft.").payload


# --- erfolgreiche Schreibanfrage mit Standard-Settings ------------------------------------------------------------

def test_default_settings_write_through_the_relay_succeeds(relay) -> None:
    result = build_writing_provider(relay["settings"]).write(_payload())
    assert result.text == TEXT and (result.input_tokens, result.output_tokens) == (120, 40)
    sent = relay["posts"][0]["json"]
    assert sent["max_tokens"] == 4000 <= GatewaySettings().max_tokens_ceiling
    assert sent["thinking"] == {"type": "disabled"}
    sdk_client = relay["anthropic"].call_args.kwargs
    assert (sdk_client["timeout"], sdk_client["max_retries"]) == (55.0, 0)
    create = relay["anthropic"].return_value.messages.create.call_args.kwargs
    assert create["thinking"] == {"type": "disabled"} and create["max_tokens"] == 4000


def test_drafting_service_creates_a_draft_through_the_relay_with_default_settings(relay) -> None:
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.drafting.service import DraftingService
    from app.models import Base as AppBase
    from app.research.service import LegalResearchService
    from app.search.service import DocumentSearchService
    from tests.fake_embedding_provider import FakeEmbeddingProvider

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AppBase.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    search = DocumentSearchService(FakeEmbeddingProvider())
    service = DraftingService(RuleBasedLocalAIProvider(), LegalResearchService(search, min_score_for_sufficient=0.0),
                              search, ClaudePrivacyGateway(), build_writing_provider(relay["settings"]),
                              model_name="claude-sonnet-5")
    try:
        result = service.create_draft(None, "formulate_draft", db, new_matter_title="Relayakte")
        assert result.success is True, result.blocked_reasons
        assert relay["posts"] and relay["posts"][0]["json"]["max_tokens"] == 4000
    finally:
        db.close()
        engine.dispose()


# --- Konsistenz der Grenzen (Code-Standards und Vorlagen) -----------------------------------------------------------

def _template_value(path: Path, key: str) -> str:
    match = re.search(rf"^{key}=(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{key} fehlt in {path.name}"
    return match.group(1).strip()


def test_client_and_relay_limits_are_consistent() -> None:
    client_tokens = Settings.model_fields["claude_max_tokens"].default
    relay = GatewaySettings.model_fields
    ceiling = relay["max_tokens_ceiling"].default
    assert client_tokens <= ceiling
    assert int(_template_value(ROOT / ".env.example", "CLAUDE_MAX_TOKENS")) <= int(
        _template_value(ROOT / ".env.gateway.example", "MAX_TOKENS_CEILING"))
    client_timeout = Settings.model_fields["lexono_gateway_timeout_seconds"].default
    for timeout, retries in ((relay["anthropic_timeout_seconds"].default, relay["anthropic_max_retries"].default),
                             (float(_template_value(ROOT / ".env.gateway.example", "ANTHROPIC_TIMEOUT_SECONDS")),
                              int(_template_value(ROOT / ".env.gateway.example", "ANTHROPIC_MAX_RETRIES")))):
        assert (retries + 1) * timeout < client_timeout  # kein Versuch laeuft laenger als der Client wartet


def test_relay_settings_reject_unbounded_retries_and_timeouts() -> None:
    for kwargs in ({"anthropic_max_retries": 3}, {"anthropic_max_retries": -1}, {"anthropic_timeout_seconds": 0}):
        with pytest.raises(ValidationError):
            GatewaySettings(**kwargs)


# --- Token-Grenze und Fehlerbehandlung ---------------------------------------------------------------------------

def test_too_many_tokens_fail_closed_without_upstream_call_and_status_is_logged(relay, caplog) -> None:
    from app.drafting.service import _log_provider_exception

    settings = relay["settings"].model_copy(update={"claude_max_tokens": 4096})
    with pytest.raises(GatewayRelayError) as error:
        build_writing_provider(settings).write(_payload())
    assert error.value.status_code == 400
    relay["anthropic"].return_value.messages.create.assert_not_called()
    with caplog.at_level(logging.WARNING):
        _log_provider_exception(error.value)
    assert "HTTP-Status: 400" in caplog.text and "Anlage" not in caplog.text


def test_relay_only_accepts_disabled_thinking(relay) -> None:
    body = {"model": "claude-sonnet-5", "max_tokens": 500, "system": [{"type": "text", "text": "System"}],
            "messages": [{"role": "user", "content": "Hallo"}]}
    for thinking in ({"type": "enabled", "budget_tokens": 2000}, {"type": "adaptive"}, {}):
        response = relay["client"].post("/v1/relay/messages", json={**body, "thinking": thinking},
                                        headers=relay["headers"])
        assert response.status_code == 422
    relay["anthropic"].return_value.messages.create.assert_not_called()
    assert relay["client"].post("/v1/relay/messages", json=body, headers=relay["headers"]).status_code == 200
    assert "thinking" not in relay["anthropic"].return_value.messages.create.call_args.kwargs  # wie bisher


def test_review_path_through_the_relay_is_unchanged(relay) -> None:
    relay["anthropic"].return_value.messages.create.return_value = _response(
        '{"bewertung": "ok", "hinweise": []}')
    try:
        build_review_provider(relay["settings"]).review(_payload("review_draft"))
    except Exception:  # noqa: BLE001 - die Bewertung der Antwort ist hier nicht Gegenstand
        pass
    assert "thinking" not in relay["posts"][0]["json"]
    assert "thinking" not in relay["anthropic"].return_value.messages.create.call_args.kwargs


# --- Abschneiden sichtbar machen ------------------------------------------------------------------------------------

def test_truncated_answer_is_reported_by_the_relay_and_logged_without_content(relay, caplog) -> None:
    relay["anthropic"].return_value.messages.create.return_value = _response("Sehr geehrte Damen und Her",
                                                                             stop_reason="max_tokens")
    with caplog.at_level(logging.WARNING, logger="app.ai_providers.gateway_writing_provider"):
        result = build_writing_provider(relay["settings"]).write(_payload())
    assert result.text == "Sehr geehrte Damen und Her"  # Verhalten wie im direkten Pfad: Text bleibt beim Aufrufer
    assert "abgeschnitten" in caplog.text and "Damen" not in caplog.text


# --- Timeout und Wiederholungen ------------------------------------------------------------------------------------

def test_upstream_timeout_is_one_attempt_and_a_contentless_502(relay) -> None:
    relay["anthropic"].return_value.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())
    with pytest.raises(GatewayRelayError) as error:
        build_writing_provider(relay["settings"]).write(_payload())
    assert error.value.status_code == 502
    assert relay["anthropic"].return_value.messages.create.call_count == 1
    assert relay["anthropic"].call_args.kwargs["max_retries"] == 0


def test_client_timeout_raises_once_without_retry() -> None:
    settings = Settings(_env_file=None, lexono_gateway_url="http://127.0.0.1:8700", lexono_gateway_client_id="cid",
                        lexono_gateway_client_secret="lxg_secret_synthetisch")
    with patch("app.ai_providers.gateway_relay_client.httpx.post",
               side_effect=httpx.ReadTimeout("Zeitueberschreitung")) as post:
        with pytest.raises(GatewayRelayError) as error:
            build_writing_provider(settings).write(_payload())
    assert post.call_count == 1 and error.value.status_code is None
    assert post.call_args.kwargs["timeout"] == settings.lexono_gateway_timeout_seconds


# --- Datenschutz- und Zweckpruefung unveraendert -------------------------------------------------------------------

def test_unsealed_payload_never_reaches_the_relay(relay) -> None:
    payload = ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Klartext Max Muster")
    with pytest.raises(UnsealedPayloadError):
        build_writing_provider(relay["settings"]).write(payload)
    assert relay["posts"] == []
    relay["anthropic"].assert_not_called()
