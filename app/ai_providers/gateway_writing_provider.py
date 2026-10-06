"""GatewayRelayWritingProvider - implementiert `ClaudeWritingProvider` über
den Lexono-Gateway statt eines direkten Anthropic-SDK-Aufrufs
(ARCHITECTURE.md §70).

Waehlt denselben System-Prompt (ueber `select_system_prompt`, je nach
Zweck) und baut dieselben Cache-Blöcke wie `AnthropicClaudeWritingProvider`
(unveränderte Funktionen aus `claude_writing_provider.py`) - der einzige
Unterschied ist das Transportziel: HTTP zum Gateway statt eines direkten
`anthropic.Anthropic`-Aufrufs. Besitzt strukturell keinen Anthropic-Key
(kein entsprechendes Feld im Konstruktor).

ECHTE WEBRECHERCHE (06.10., §0.6-§0.10) NICHT UNTERSTUETZT: anders als
`AnthropicClaudeWritingProvider` haengt dieser Provider bewusst KEIN
Web-Search-Tool an (`select_system_prompt` wird hier ohne
`web_search_available=True` aufgerufen - bleibt also beim ehrlichen
"kein Internetzugriff"-Systemprompt). Der eigentliche Claude-Aufruf
passiert auf einem separaten Lexono-Gateway-Server (`call_gateway_messages`,
fixe Request-Form ohne "tools"-Feld), der NICHT Teil dieses Repositories
ist - ein hier blind mitgeschicktes Tool-Flag koennte dort ignoriert
werden, was eine falsche Web-Zugriffs-Behauptung waere (siehe
Produktvorgabe §0.10 "Web-Capability muss ehrlich sein"). Um Websuche auch
fuer den Produktions-/Gateway-Pfad zu ermoeglichen, muss der separate
Gateway-Server selbst ein Web-Search-Tool anhaengen UND `call_gateway_messages`
entsprechend erweitert werden - ausserhalb des Umfangs dieses Repositories."""

from __future__ import annotations

from app.ai_providers.claude_writing_provider import (
    ClaudeWritingResult,
    build_writing_prompt_cache_blocks,
    select_system_prompt,
)
from app.ai_providers.gateway_relay_client import call_gateway_messages
from app.privacy.gateway_schema import ClaudeRequestPayload


class GatewayRelayWritingProvider:
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

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        result = call_gateway_messages(
            base_url=self._base_url,
            client_id=self._client_id,
            client_secret=self._client_secret,
            model=self.model,
            max_tokens=self.max_tokens,
            system_blocks=[
                {
                    "type": "text",
                    "text": select_system_prompt(payload.schreibauftrag),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            message_blocks=build_writing_prompt_cache_blocks(payload),
            timeout_seconds=self._timeout_seconds,
        )

        token_count = None
        if result.input_tokens is not None and result.output_tokens is not None:
            token_count = result.input_tokens + result.output_tokens

        return ClaudeWritingResult(
            text=result.text,
            token_count=token_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
