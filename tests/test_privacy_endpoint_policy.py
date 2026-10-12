"""Zulaessige Netzwerkziele fuer KI-Aufrufe (Sicherheitspruefung 13.10.2026, app/ai_providers/endpoint_policy.py).

Cloud: das Anthropic-SDK uebernahm still ANTHROPIC_BASE_URL - Anfragen UND API-Schluessel haetten an einen beliebigen,
auch unverschluesselten Host gehen koennen. Lokale KI: OLLAMA_BASE_URL war frei auf fremde Rechner setzbar.
Keine echte Netzwerkverbindung in diesen Tests; synthetischer Schluesselwert."""

from __future__ import annotations

import ssl

import httpx
import pytest

from app.ai_providers.endpoint_policy import (
    OFFICIAL_ANTHROPIC_BASE_URL,
    CloudEndpointNotAllowedError,
    is_loopback_url,
    resolve_anthropic_base_url,
)
from app.ai_providers.factory import ProviderNotConfiguredError, build_local_llm_provider, build_review_provider, build_writing_provider
from app.ai_providers.local_llm_provider import LocalLLMUnavailableError
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.config.settings import Settings

KEY = "sk-ant-test-synthetic-0000"


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:11434", True), ("http://127.0.0.1:18080", True), ("http://127.5.6.7:1", True),
        ("http://[::1]:11434", True), ("http://192.168.1.20:11434", False), ("https://ollama.example.com", False),
        ("http://localhost.example.com:11434", False), ("http://0.0.0.0:11434", False), ("not a url", False),
    ],
)
def test_loopback_detection(url, local) -> None:
    assert is_loopback_url(url) is local


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, OFFICIAL_ANTHROPIC_BASE_URL),
        ({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}, "https://api.anthropic.com"),
        # lokale QA-Erfassung - nur mit ausdruecklicher QA-Freigabe
        ({"ANTHROPIC_BASE_URL": "http://127.0.0.1:18080", "LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT": "1"}, "http://127.0.0.1:18080"),
    ],
)
def test_allowed_cloud_endpoints(env, expected) -> None:
    assert resolve_anthropic_base_url(env) == expected


@pytest.mark.parametrize("flag", [None, "", "0", "true"])
def test_loopback_cloud_endpoint_requires_the_explicit_qa_flag(flag) -> None:
    env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:18080"}
    if flag is not None:
        env["LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT"] = flag
    with pytest.raises(CloudEndpointNotAllowedError):
        resolve_anthropic_base_url(env)


@pytest.mark.parametrize("target", ["http://proxy.example.invalid", "https://api.anthropic.com.evil.invalid", "http://api.anthropic.com"])
def test_other_cloud_endpoints_are_refused(target) -> None:
    with pytest.raises(CloudEndpointNotAllowedError):
        resolve_anthropic_base_url({"ANTHROPIC_BASE_URL": target})


def test_factory_refuses_to_build_cloud_providers_for_a_foreign_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy.example.invalid")
    settings = Settings(anthropic_api_key=KEY, lexono_gateway_url=None)
    with pytest.raises(ProviderNotConfiguredError):
        build_writing_provider(settings)
    with pytest.raises(ProviderNotConfiguredError):
        build_review_provider(settings)


def test_factory_pins_the_official_https_endpoint_with_certificate_verification(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    provider = build_writing_provider(Settings(anthropic_api_key=KEY, lexono_gateway_url=None))
    assert str(provider._client.base_url).rstrip("/") == OFFICIAL_ANTHROPIC_BASE_URL
    context = provider._client._client._transport._pool._ssl_context  # SDK-intern (httpx2), nur lesend
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_remote_local_ai_endpoint_is_refused_without_explicit_release(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("Netz")))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("Netz")))
    provider = build_local_llm_provider(
        Settings(local_ai_enabled=True, ollama_base_url="http://192.168.1.20:11434", local_ai_allow_remote_endpoint=False)
    )
    assert provider.remote_blocked
    with pytest.raises(LocalLLMUnavailableError, match="nicht ausdruecklich freigegeben"):
        provider.generate_structured("Synthetischer Text", {"type": "object"})
    with pytest.raises(LocalLLMUnavailableError):
        provider.list_local_models()
    with pytest.raises(LocalLLMUnavailableError):
        provider.pull_model()
    health = provider.check_health()
    assert not health.reachable and "nicht ausdruecklich freigegeben" in health.error
    provider.prefill("x")
    provider.warm_up()
    assert calls == [], "ohne Freigabe darf keine Verbindung zum fremden Rechner entstehen"


def test_local_ai_calls_ignore_proxy_settings(monkeypatch) -> None:
    """Review 13.10.2026: httpx uebernahm HTTP_PROXY bzw. den Windows-System-Proxy auch fuer 127.0.0.1 - der Text fuer
    die lokale KI ging dann unverschluesselt an den Proxy-Rechner."""
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example.invalid:8080")
    seen = []

    def _record(*args, **kwargs):
        seen.append(kwargs.get("trust_env"))
        raise httpx.ConnectError("synthetisch")

    monkeypatch.setattr(httpx, "post", _record)
    monkeypatch.setattr(httpx, "get", _record)
    provider = OllamaLocalLLMProvider(base_url="http://127.0.0.1:11434", model="m")
    provider.check_health()
    with pytest.raises(LocalLLMUnavailableError):
        provider.generate_structured("Synthetischer Text", {"type": "object"})
    with pytest.raises(LocalLLMUnavailableError):
        provider.list_local_models()
    with pytest.raises(LocalLLMUnavailableError):
        provider.pull_model()
    provider.prefill("x")
    provider.warm_up()
    assert seen and all(value is False for value in seen), seen


def test_cloud_health_probe_does_not_contact_a_refused_endpoint(monkeypatch) -> None:
    from app.ai_providers.cloud_health import probe_target

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy.example.invalid")
    assert probe_target(Settings(anthropic_api_key=KEY, lexono_gateway_url=None)) is None
    monkeypatch.delenv("ANTHROPIC_BASE_URL")
    assert probe_target(Settings(anthropic_api_key=KEY, lexono_gateway_url=None)) == ("api.anthropic.com", 443, True)


def test_gateway_relay_pins_the_official_endpoint(monkeypatch) -> None:
    from unittest.mock import patch

    from gateway import relay

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy.example.invalid")
    with patch("gateway.relay.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = RuntimeError("kein Aufruf noetig")
        with pytest.raises(RuntimeError):
            relay.call_anthropic(api_key=KEY, model="m", request=relay.RelayRequest(model="m", max_tokens=1, system=[], messages=[]))
    assert mock_cls.call_args.kwargs["base_url"] == OFFICIAL_ANTHROPIC_BASE_URL


def test_local_and_explicitly_released_endpoints_keep_working() -> None:
    assert not OllamaLocalLLMProvider(base_url="http://localhost:11434", model="m").remote_blocked
    assert not OllamaLocalLLMProvider(base_url="http://127.0.0.1:11434", model="m").remote_blocked
    released = build_local_llm_provider(
        Settings(local_ai_enabled=True, ollama_base_url="http://192.168.1.20:11434", local_ai_allow_remote_endpoint=True)
    )
    assert not released.remote_blocked
