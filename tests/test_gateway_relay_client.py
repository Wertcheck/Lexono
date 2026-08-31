"""Tests für die client-seitige Gateway-Anbindung (ARCHITECTURE.md §70):
app/ai_providers/gateway_relay_client.py, gateway_writing_provider.py,
app/review/gateway_review_provider.py.

Zentrale Sicherheitsaussage dieser Datei: keine dieser Klassen konstruiert
jemals `anthropic.Anthropic` - sie sprechen ausschließlich HTTP mit dem
Gateway. Siehe auch tests/test_no_ai_gateway_proxy.py für die dauerhafte
strukturelle Absicherung."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.ai_providers.gateway_relay_client import GatewayRelayError, call_gateway_messages
from app.ai_providers.gateway_writing_provider import GatewayRelayWritingProvider
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.review.gateway_review_provider import GatewayRelayReviewProvider


def _payload(**overrides) -> ClaudeRequestPayload:
    defaults = {
        "schreibauftrag": "formulate_draft",
        "anonymisierter_sachverhalt": "Sachverhalt mit [MANDANT_01]",
    }
    defaults.update(overrides)
    return ClaudeRequestPayload(**defaults)


def _mock_httpx_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    response.text = text
    return response


# --- call_gateway_messages ---


def test_call_gateway_messages_sends_bearer_credential_and_returns_result() -> None:
    with patch("app.ai_providers.gateway_relay_client.httpx.post") as mock_post:
        mock_post.return_value = _mock_httpx_response(
            200, {"text": "Antwort", "input_tokens": 10, "output_tokens": 5}
        )
        result = call_gateway_messages(
            base_url="http://127.0.0.1:8700",
            client_id="cid",
            client_secret="secret",
            model="claude-sonnet-5",
            max_tokens=500,
            system_blocks=[{"type": "text", "text": "System"}],
            message_blocks=[{"type": "text", "text": "Nachricht"}],
        )

    assert result.text == "Antwort"
    assert result.input_tokens == 10
    assert result.output_tokens == 5

    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer cid:secret"
    assert kwargs["json"]["model"] == "claude-sonnet-5"


def test_call_gateway_messages_never_constructs_anthropic_client() -> None:
    """Strukturelle Kontrolle: dieses Modul importiert `anthropic` gar
    nicht - es kann also gar keinen `anthropic.Anthropic`-Client bauen."""
    import app.ai_providers.gateway_relay_client as module

    assert "anthropic" not in dir(module)


def test_call_gateway_messages_raises_on_non_200_without_leaking_body() -> None:
    with patch("app.ai_providers.gateway_relay_client.httpx.post") as mock_post:
        mock_post.return_value = _mock_httpx_response(
            401, text="interne Details, die nicht durchsickern sollen"
        )
        with pytest.raises(GatewayRelayError) as exc_info:
            call_gateway_messages(
                base_url="http://127.0.0.1:8700",
                client_id="cid",
                client_secret="secret",
                model="claude-sonnet-5",
                max_tokens=500,
                system_blocks=[],
                message_blocks=[],
            )
    assert "interne Details" not in str(exc_info.value)


def test_call_gateway_messages_raises_on_network_error() -> None:
    with patch("app.ai_providers.gateway_relay_client.httpx.post") as mock_post:
        mock_post.side_effect = httpx.ConnectError("Verbindung fehlgeschlagen")
        with pytest.raises(GatewayRelayError):
            call_gateway_messages(
                base_url="http://127.0.0.1:8700",
                client_id="cid",
                client_secret="secret",
                model="claude-sonnet-5",
                max_tokens=500,
                system_blocks=[],
                message_blocks=[],
            )


# --- GatewayRelayWritingProvider ---


def test_gateway_writing_provider_returns_claude_writing_result() -> None:
    provider = GatewayRelayWritingProvider(
        base_url="http://127.0.0.1:8700",
        client_id="cid",
        client_secret="secret",
        model="claude-sonnet-5",
    )
    with patch("app.ai_providers.gateway_writing_provider.call_gateway_messages") as mock_call:
        from app.ai_providers.gateway_relay_client import GatewayRelayResult

        mock_call.return_value = GatewayRelayResult(
            text="Entwurfstext", input_tokens=20, output_tokens=8
        )
        result = provider.write(_payload())

    assert result.text == "Entwurfstext"
    assert result.token_count == 28
    assert result.input_tokens == 20
    assert result.output_tokens == 8


def test_gateway_writing_provider_rejects_blank_base_url() -> None:
    with pytest.raises(ValueError):
        GatewayRelayWritingProvider(
            base_url="   ", client_id="cid", client_secret="secret", model="claude-sonnet-5"
        )


def test_gateway_writing_provider_has_no_anthropic_client_attribute() -> None:
    provider = GatewayRelayWritingProvider(
        base_url="http://127.0.0.1:8700",
        client_id="cid",
        client_secret="secret",
        model="claude-sonnet-5",
    )
    assert not hasattr(provider, "_client")


# --- GatewayRelayReviewProvider ---


def test_gateway_review_provider_parses_json_response() -> None:
    provider = GatewayRelayReviewProvider(
        base_url="http://127.0.0.1:8700",
        client_id="cid",
        client_secret="secret",
        model="claude-sonnet-5",
    )
    review_json = json.dumps({"overall_assessment": "gut", "findings": []})
    with patch("app.review.gateway_review_provider.call_gateway_messages") as mock_call:
        from app.ai_providers.gateway_relay_client import GatewayRelayResult

        mock_call.return_value = GatewayRelayResult(
            text=review_json, input_tokens=15, output_tokens=6
        )
        result = provider.review(_payload())

    assert result.overall_assessment == "gut"
    assert result.input_tokens == 15
    assert result.output_tokens == 6


def test_gateway_review_provider_raises_valueerror_on_invalid_json() -> None:
    provider = GatewayRelayReviewProvider(
        base_url="http://127.0.0.1:8700",
        client_id="cid",
        client_secret="secret",
        model="claude-sonnet-5",
    )
    with patch("app.review.gateway_review_provider.call_gateway_messages") as mock_call:
        from app.ai_providers.gateway_relay_client import GatewayRelayResult

        mock_call.return_value = GatewayRelayResult(
            text="kein JSON", input_tokens=None, output_tokens=None
        )
        with pytest.raises(ValueError):
            provider.review(_payload())
