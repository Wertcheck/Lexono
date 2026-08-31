"""Tests für app/ai_providers/factory.py (Prompt 34; seit §63 ausschließlich
Anthropic/Claude, siehe ARCHITECTURE.md §63 - Ollama vollständig entfernt,
kein `ai_mode`-Schalter mehr)."""

from __future__ import annotations

import pytest

from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider
from app.ai_providers.factory import (
    ProviderNotConfiguredError,
    build_local_llm_provider,
    build_review_provider,
    build_writing_provider,
)
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.config.settings import Settings
from app.review.anthropic_review_provider import AnthropicClaudeReviewProvider


def _settings_with_key(**overrides) -> Settings:
    defaults = {"anthropic_api_key": "sk-ant-test-key-00000000000000000000"}
    defaults.update(overrides)
    return Settings(**defaults)


# --- Provider-Auswahl ---


def test_build_writing_provider_returns_anthropic_provider() -> None:
    settings = _settings_with_key()
    provider = build_writing_provider(settings)
    assert isinstance(provider, AnthropicClaudeWritingProvider)


def test_build_review_provider_returns_anthropic_provider() -> None:
    settings = _settings_with_key()
    provider = build_review_provider(settings)
    assert isinstance(provider, AnthropicClaudeReviewProvider)


def test_build_writing_provider_uses_configured_model_and_max_tokens() -> None:
    settings = _settings_with_key(claude_model_name="claude-opus-4-8", claude_max_tokens=500)
    provider = build_writing_provider(settings)
    assert provider.model == "claude-opus-4-8"
    assert provider.max_tokens == 500


# --- Fehlende Zugangsdaten ---


def test_build_writing_provider_raises_without_api_key() -> None:
    settings = Settings(anthropic_api_key=None)
    with pytest.raises(ProviderNotConfiguredError):
        build_writing_provider(settings)


def test_build_review_provider_raises_without_api_key() -> None:
    settings = Settings(anthropic_api_key=None)
    with pytest.raises(ProviderNotConfiguredError):
        build_review_provider(settings)


def test_build_writing_provider_raises_when_api_key_is_blank() -> None:
    settings = Settings(anthropic_api_key="   ")
    with pytest.raises(ProviderNotConfiguredError):
        build_writing_provider(settings)


# --- Lokale KI (§65) ---


def test_build_local_llm_provider_returns_none_when_disabled() -> None:
    settings = Settings(local_ai_enabled=False)
    assert build_local_llm_provider(settings) is None


def test_build_local_llm_provider_returns_ollama_provider_when_enabled() -> None:
    settings = Settings(
        local_ai_enabled=True, ollama_base_url="http://localhost:12345", ollama_model="qwen3:8b"
    )
    provider = build_local_llm_provider(settings)
    assert isinstance(provider, OllamaLocalLLMProvider)
    assert provider.base_url == "http://localhost:12345"
    assert provider.model == "qwen3:8b"


def test_settings_rejects_unsupported_local_ai_runtime() -> None:
    with pytest.raises(Exception):  # noqa: PT011 - pydantic ValidationError
        Settings(local_ai_runtime="lmstudio")


def test_settings_local_ai_disabled_by_default() -> None:
    assert Settings().local_ai_enabled is False


# --- Rückwärtskompatibilität (bestehender Code/Tests nutzen den alten Namen) ---


def test_service_factory_reexports_provider_not_configured_error_as_old_name() -> None:
    from app.web.service_factory import WritingProviderNotConfiguredError

    assert WritingProviderNotConfiguredError is ProviderNotConfiguredError


# --- Lexono-Gateway (§70) ---


def _settings_with_gateway(**overrides) -> Settings:
    defaults = {
        "lexono_gateway_url": "http://127.0.0.1:8700",
        "lexono_gateway_client_id": "test-client-id",
        "lexono_gateway_client_secret": "lxg_secret_test",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_build_writing_provider_uses_gateway_when_url_configured() -> None:
    from app.ai_providers.gateway_writing_provider import GatewayRelayWritingProvider

    settings = _settings_with_gateway()
    provider = build_writing_provider(settings)
    assert isinstance(provider, GatewayRelayWritingProvider)


def test_build_review_provider_uses_gateway_when_url_configured() -> None:
    from app.review.gateway_review_provider import GatewayRelayReviewProvider

    settings = _settings_with_gateway()
    provider = build_review_provider(settings)
    assert isinstance(provider, GatewayRelayReviewProvider)


def test_gateway_takes_priority_over_direct_anthropic_key_when_both_present() -> None:
    """Ist eine Gateway-URL konfiguriert, wird IMMER der Gateway-Pfad
    verwendet - auch wenn zufaellig zusaetzlich ein anthropic_api_key
    gesetzt ist (z. B. in einer Entwicklungsumgebung mit beidem). Sicherer
    Default: niemals versehentlich am Gateway vorbei direkt zu Anthropic."""
    from app.ai_providers.gateway_writing_provider import GatewayRelayWritingProvider

    settings = _settings_with_gateway(anthropic_api_key="sk-ant-should-be-ignored")
    provider = build_writing_provider(settings)
    assert isinstance(provider, GatewayRelayWritingProvider)


def test_build_writing_provider_raises_when_gateway_url_set_without_credentials() -> None:
    settings = Settings(lexono_gateway_url="http://127.0.0.1:8700")
    with pytest.raises(ProviderNotConfiguredError):
        build_writing_provider(settings)


def test_build_review_provider_raises_when_gateway_url_set_without_credentials() -> None:
    settings = Settings(lexono_gateway_url="http://127.0.0.1:8700")
    with pytest.raises(ProviderNotConfiguredError):
        build_review_provider(settings)


def test_direct_anthropic_mode_still_works_without_any_gateway_config() -> None:
    """Regression: bestehende Entwicklungsumgebungen (nur ANTHROPIC_API_KEY
    in .env, keine Gateway-Variablen) funktionieren unveraendert."""
    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider

    settings = _settings_with_key()
    provider = build_writing_provider(settings)
    assert isinstance(provider, AnthropicClaudeWritingProvider)
