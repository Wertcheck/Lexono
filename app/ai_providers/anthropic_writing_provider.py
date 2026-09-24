"""AnthropicClaudeWritingProvider – erste konkrete Implementierung von
`ClaudeWritingProvider`, über das offizielle Anthropic-SDK.

DSGVO-/Datenschutz-Garantie (struktureller, nicht nur konventioneller
Schutz): Diese Klasse bekommt AUSSCHLIESSLICH eine `ClaudeRequestPayload`
(die sechs Allowlist-Felder, bereits pseudonymisiert und durch den
Security-Check aus Schritt 2 geprüft). Es gibt in dieser Datei keinen
Codepfad, der auf `Document`, `Matter`, `Message` oder andere
Mandantendaten-Modelle zugreifen könnte - der Datenfluss dorthin ist
bereits beim `ClaudePrivacyGateway` (Schritt 3) beendet.

Der API-Key wird ausschließlich zur Laufzeit aus der Konfiguration
gelesen (`SecretStr.get_secret_value()`), nie geloggt, nie im Klartext
gespeichert.
"""

from __future__ import annotations

from collections.abc import Generator

import anthropic

from app.ai_providers.claude_writing_provider import (
    ClaudeWritingResult,
    build_writing_prompt_cache_blocks,
    select_system_prompt,
)
from app.privacy.gateway_schema import ClaudeRequestPayload


class AnthropicClaudeWritingProvider:
    def __init__(self, *, api_key: str, model: str, max_tokens: int = 2000) -> None:
        if not api_key or not api_key.strip():
            raise ValueError(
                "api_key darf nicht leer sein - ANTHROPIC_API_KEY in .env setzen"
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        # Prompt-Caching (Schritt 3): je Zweck (siehe select_system_prompt)
        # weiterhin projektweit IDENTISCH fuer jeden Aufruf mit demselben
        # Zweck - als gecachter Block markiert, spart es ab dem zweiten
        # Aufruf innerhalb des Cache-Fensters Eingabe-Tokens. Der
        # Nachrichtentext trennt zusätzlich den wiederkehrenden
        # Aktenkontext vom variablen Schreibauftrag (siehe
        # build_writing_prompt_cache_blocks).
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # KEIN expliziter `temperature`-Parameter: fuer das aktuell
            # konfigurierte Modell (claude-sonnet-5) lehnt die Anthropic-API
            # diesen Parameter mit "temperature is deprecated for this
            # model" hart ab (echter Fund beim realen End-zu-Ende-Smoketest,
            # 20.08. - eine fruehere Annahme "temperature=0.0 fuer
            # deterministische Ausgabe" war mit reinen Mock-Tests nicht
            # aufgefallen). Modellseitiger Default gilt.
            system=[
                {
                    "type": "text",
                    "text": select_system_prompt(payload.schreibauftrag),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": build_writing_prompt_cache_blocks(payload)}],
        )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )

        token_count = None
        input_tokens = None
        output_tokens = None
        if response.usage is not None:
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            token_count = input_tokens + output_tokens

        return ClaudeWritingResult(
            text=text,
            token_count=token_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def write_stream(
        self, payload: ClaudeRequestPayload
    ) -> Generator[str, None, ClaudeWritingResult]:
        """Streaming-Variante von `write()` (13.09., Streaming-
        Architekturentscheidung, siehe app/drafting/service.py::
        DraftingService.create_draft_stream) - nutzt das offizielle
        Anthropic-SDK-Streaming (`messages.stream`, `with`-Block schliesst
        die zugrundeliegende HTTP-Verbindung auch bei vorzeitigem Abbruch
        korrekt, siehe Aufrufer). Gibt ROHE (weiterhin pseudonymisierte)
        Text-Deltas zurueck - identische Datenschutzgarantie wie `write()`,
        nur inkrementell statt am Stueck. Der Rueckgabewert (`return`, PEP
        380) traegt dieselben Token-Zaehlungen wie `write()`, aus
        `stream.get_final_message()` statt `response.usage`."""
        with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": select_system_prompt(payload.schreibauftrag),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": build_writing_prompt_cache_blocks(payload)}],
        ) as stream:
            yield from stream.text_stream

            final_message = stream.get_final_message()
            text = "".join(
                block.text for block in final_message.content if block.type == "text"
            )
            token_count = None
            input_tokens = None
            output_tokens = None
            if final_message.usage is not None:
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens
                token_count = input_tokens + output_tokens

            return ClaudeWritingResult(
                text=text,
                token_count=token_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
