"""Tests fuer app/review/anthropic_review_provider.py (Prompt 18).

Ausschliesslich gegen einen gemockten Anthropic-Client - kein echter
API-Aufruf (kein API-Key in der Sandbox, echter Aufruf in einer Testsuite
unpassend)."""

from unittest.mock import MagicMock, patch

import pytest

from app.privacy.gateway_schema import ClaudeRequestPayload as _GatewayPayload, seal_payload


def ClaudeRequestPayload(**kwargs) -> _GatewayPayload:  # noqa: N802
    """Diese Tests pruefen den Provider, nicht den Gateway: sie simulieren eine vom Gateway freigegebene (versiegelte)
    Payload. Unversiegelte Payloads lehnt jeder Cloud-Provider ab (tests/test_privacy_review.py)."""
    return seal_payload(_GatewayPayload(**kwargs))
from app.review.anthropic_review_provider import AnthropicClaudeReviewProvider


def test_requires_non_blank_api_key() -> None:
    with pytest.raises(ValueError):
        AnthropicClaudeReviewProvider(api_key="  ", model="claude-sonnet-5")


@patch("app.review.anthropic_review_provider.anthropic.Anthropic")
def test_review_parses_valid_json_response(mock_anthropic_cls) -> None:
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_text_block = MagicMock()
    mock_text_block.type = "text"
    mock_text_block.text = (
        '{"findings": [{"category": "formaler_fehler", "severity": "niedrig", '
        '"description": "Anrede fehlt."}], "overall_assessment": "Kleinigkeit."}'
    )
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_client.messages.create.return_value = mock_response

    provider = AnthropicClaudeReviewProvider(api_key="test-key", model="claude-sonnet-5")
    payload = ClaudeRequestPayload(
        schreibauftrag="review_draft", anonymisierter_sachverhalt="Text mit [MANDANT_01]."
    )

    result = provider.review(payload)

    assert len(result.findings) == 1
    assert result.findings[0].category == "formaler_fehler"
    assert result.overall_assessment == "Kleinigkeit."


@patch("app.review.anthropic_review_provider.anthropic.Anthropic")
def test_review_raises_on_invalid_json(mock_anthropic_cls) -> None:
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_text_block = MagicMock()
    mock_text_block.type = "text"
    mock_text_block.text = "Das ist kein JSON."
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_client.messages.create.return_value = mock_response

    provider = AnthropicClaudeReviewProvider(api_key="test-key", model="claude-sonnet-5")
    payload = ClaudeRequestPayload(
        schreibauftrag="review_draft", anonymisierter_sachverhalt="Text"
    )

    with pytest.raises(ValueError):
        provider.review(payload)


@patch("app.review.anthropic_review_provider.anthropic.Anthropic")
def test_review_uses_review_system_prompt_not_writing_prompt(mock_anthropic_cls) -> None:
    from app.review.provider import REVIEW_SYSTEM_PROMPT

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_text_block = MagicMock()
    mock_text_block.type = "text"
    mock_text_block.text = '{"findings": [], "overall_assessment": "OK."}'
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_client.messages.create.return_value = mock_response

    provider = AnthropicClaudeReviewProvider(api_key="test-key", model="claude-sonnet-5")
    provider.review(
        ClaudeRequestPayload(schreibauftrag="review_draft", anonymisierter_sachverhalt="Text")
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    # Prompt-Caching (Schritt 3): system ist jetzt ein gecachter Content-Block
    # statt eines reinen Strings, siehe app/review/anthropic_review_provider.py.
    assert call_kwargs["system"] == [
        {
            "type": "text",
            "text": REVIEW_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_stable_sources_stay_identical_across_revisions_under_review() -> None:
    """Wie beim Schreib-Prompt: dieselben Quellen/derselbe zugrundeliegende
    Sachverhalt erzeugen bei zwei unterschiedlichen Entwurfsversionen einen
    byte-identischen gecachten Block - der zu prüfende Entwurfstext selbst
    (der sich pro Version ändert) bleibt bewusst ungecacht."""
    from app.review.provider import build_review_prompt_cache_blocks

    version_1 = ClaudeRequestPayload(
        schreibauftrag="review_draft",
        anonymisierter_sachverhalt="Entwurf Version 1.",
        anonymisierte_quellenverweise=["§ 355 AO."],
    )
    version_2 = ClaudeRequestPayload(
        schreibauftrag="review_draft",
        anonymisierter_sachverhalt="Entwurf Version 2 (gekürzt).",
        anonymisierte_quellenverweise=["§ 355 AO."],
    )

    blocks_1 = build_review_prompt_cache_blocks(version_1)
    blocks_2 = build_review_prompt_cache_blocks(version_2)

    assert blocks_1[0]["text"] == blocks_2[0]["text"]
    assert blocks_1[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks_1[1]["text"] != blocks_2[1]["text"]
    assert "cache_control" not in blocks_1[1]


def test_no_stable_context_falls_back_to_single_uncached_block() -> None:
    from app.review.provider import build_review_prompt_cache_blocks

    payload = ClaudeRequestPayload(
        schreibauftrag="review_draft", anonymisierter_sachverhalt="Nur ein Entwurf."
    )

    blocks = build_review_prompt_cache_blocks(payload)

    assert len(blocks) == 1
    assert "cache_control" not in blocks[0]
