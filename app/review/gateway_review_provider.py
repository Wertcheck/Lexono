"""GatewayRelayReviewProvider - implementiert `ClaudeReviewProvider` über
den Lexono-Gateway statt eines direkten Anthropic-SDK-Aufrufs
(ARCHITECTURE.md §70). Siehe app/ai_providers/gateway_writing_provider.py
für das identische Architekturmuster."""

from __future__ import annotations

import json

from app.ai_providers.gateway_relay_client import call_gateway_messages
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.review.provider import REVIEW_SYSTEM_PROMPT, build_review_prompt_cache_blocks
from app.review.schema import ReviewResult


class GatewayRelayReviewProvider:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        model: str,
        max_tokens: int = 2000,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url darf nicht leer sein")
        self._base_url = base_url
        self._client_id = client_id
        self._client_secret = client_secret
        self.model = model
        self.max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds

    def review(self, payload: ClaudeRequestPayload) -> ReviewResult:
        result = call_gateway_messages(
            base_url=self._base_url,
            client_id=self._client_id,
            client_secret=self._client_secret,
            model=self.model,
            max_tokens=self.max_tokens,
            system_blocks=[
                {
                    "type": "text",
                    "text": REVIEW_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            message_blocks=build_review_prompt_cache_blocks(payload),
            timeout_seconds=self._timeout_seconds,
        )

        try:
            parsed = json.loads(result.text.strip())
        except json.JSONDecodeError as exc:
            raise ValueError("Review-Engine: Antwort war kein valides JSON") from exc

        return ReviewResult(
            **parsed, input_tokens=result.input_tokens, output_tokens=result.output_tokens
        )
