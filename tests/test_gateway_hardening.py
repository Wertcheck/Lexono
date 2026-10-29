"""§61 P5 (17.10.2026): Haertung des Gateway-Relays - lokal, ohne Bereitstellung, ohne echten Anthropic-Aufruf.

- Nachrichtenschema: nur Rollen user/assistant, nur Textbloecke, cache_control nur "ephemeral" - genau das, was der
  Lexono-Client sendet. Alles andere (Bilder, Dokumente, tool_use, fremde Rollen, Zusatzschluessel) wird abgewiesen,
  bevor Anthropic erreicht wird (Missbrauchsbegrenzung bei kompromittierter Kanzlei-Credential).
- Speicherschutz: ein zu grosser Body wird VOR dem Einlesen/Parsen abgewiesen (Content-Length bzw. gestueckelt), auch
  ohne Authentifizierung - die praezise Pruefung der Route bleibt bestehen.
- Der echte Client-Payload (Schreib- und Pruefpfad) passt weiterhin durch."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import gateway.main as gateway_main
from app.ai_providers.claude_writing_provider import build_writing_prompt_cache_blocks
from app.privacy.gateway import ClaudePrivacyGateway
from tests.test_gateway import (  # noqa: F401  (Fixtures)
    _auth_header,
    _mock_anthropic_response,
    _relay_body,
    client,
    db_session,
    gateway_settings,
    tenant_credential,
)


def _post(client, tenant_credential, body):
    tenant, secret = tenant_credential
    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        anthropic_cls.return_value.messages.create.return_value = _mock_anthropic_response()
        response = client.post("/v1/relay/messages", json=body, headers=_auth_header(tenant, secret))
    return response, anthropic_cls.return_value.messages.create


@pytest.mark.parametrize("bad_content", [
    [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}],
    [{"type": "document", "source": {"type": "text", "data": "x"}}],
    [{"type": "tool_use", "id": "t1", "name": "web_search", "input": {}}],
    [{"type": "text", "text": "ok", "citations": []}],  # Zusatzschluessel
    [{"type": "text", "text": "ok", "cache_control": {"type": "persistent"}}],
    [{"type": "text", "text": 42}],
])
def test_non_text_or_unexpected_message_blocks_never_reach_anthropic(client, tenant_credential, bad_content) -> None:
    body = _relay_body()
    body["messages"] = [{"role": "user", "content": bad_content}]
    response, create = _post(client, tenant_credential, body)
    assert response.status_code == 422 and not create.called


@pytest.mark.parametrize("role", ["system", "tool", "developer", "USER"])
def test_unexpected_roles_are_rejected(client, tenant_credential, role) -> None:
    body = _relay_body()
    body["messages"][0]["role"] = role
    response, create = _post(client, tenant_credential, body)
    assert response.status_code == 422 and not create.called


@pytest.mark.parametrize("system", [
    [{"type": "image", "text": "x"}],
    [{"type": "text", "text": "x", "cache_control": {"type": "persistent", "ttl": "1h"}}],
])
def test_unexpected_system_blocks_are_rejected(client, tenant_credential, system) -> None:
    body = _relay_body()
    body["system"] = system
    response, create = _post(client, tenant_credential, body)
    assert response.status_code == 422 and not create.called


def test_the_real_client_payload_still_passes(client, tenant_credential) -> None:
    """Genau die Bloecke des Lexono-Schreibpfads (pseudonymisiert und versiegelt, mit cache_control "ephemeral")."""
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt="Die Kaution von 4.250,00 EUR ist zurückzuzahlen.",
        known_entities={"mandant": ["Henrike Wallbaum-Ostertag"]}, review_mode="mask",
    )
    assert result.allowed
    body = _relay_body()
    body["system"] = [{"type": "text", "text": "System", "cache_control": {"type": "ephemeral"}}]
    body["messages"] = [{"role": "user", "content": build_writing_prompt_cache_blocks(result.payload)}]
    response, create = _post(client, tenant_credential, body)
    assert response.status_code == 200 and create.call_count == 1
    sent = create.call_args.kwargs["messages"][0]["content"]
    assert all(block["type"] == "text" for block in sent)


def test_plain_string_content_stays_allowed(client, tenant_credential) -> None:
    body = _relay_body()
    body["messages"] = [{"role": "user", "content": "Hallo"}]
    response, create = _post(client, tenant_credential, body)
    assert response.status_code == 200 and create.called


def test_oversized_body_is_rejected_before_parsing_and_without_auth(client, monkeypatch) -> None:
    monkeypatch.setattr(gateway_main, "_max_body_bytes", lambda: 5_000)
    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        small = client.post("/v1/relay/messages", content=b"x" * 100, headers={"content-type": "application/json"})
        large = client.post("/v1/relay/messages", content=b"x" * 6_000, headers={"content-type": "application/json"})
    # ohne Credential antwortet die Route mit 401 - die 413 kommt also VOR Authentifizierung und Parsen
    assert small.status_code in (401, 422) and large.status_code == 413 and not anthropic_cls.called


def test_oversized_chunked_body_without_length_is_cut_off(client, monkeypatch) -> None:
    monkeypatch.setattr(gateway_main, "_max_body_bytes", lambda: 5_000)
    read = []

    def _chunks():
        for _ in range(100):
            read.append(1)
            yield b"y" * 1_000

    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        response = client.post("/v1/relay/messages", content=_chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413 and not anthropic_cls.called


def test_body_within_the_guard_still_gets_the_precise_route_check(client, tenant_credential, gateway_settings,
                                                                   monkeypatch) -> None:
    gateway_settings.max_request_bytes = 2_000
    monkeypatch.setattr(gateway_main, "_max_body_bytes", lambda: 4_000)
    body = _relay_body()
    body["messages"][0]["content"] = [{"type": "text", "text": "z" * 2_500}]
    response, create = _post(client, tenant_credential, body)
    assert response.status_code == 413 and "Größenlimit" in response.text and not create.called
