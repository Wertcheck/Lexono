"""Der eigentliche Anthropic-Relay-Aufruf - serverseitig, mit dem echten Key.

`gateway/` ist die EINZIGE Stelle im gesamten Repository, die
`anthropic.Anthropic(api_key=<echter Key>)` MIT einem tatsächlich
produktiv nutzbaren Key konstruiert (siehe
`tests/test_no_ai_gateway_proxy.py` für die strukturelle Absicherung
dieser Aussage). Hält weder Request- noch Response-Body länger im
Speicher als für den einzelnen Aufruf nötig - keine Persistenz."""

from __future__ import annotations

import anthropic

from gateway.schemas import RelayRequest, RelayResponse


class RelayError(Exception):
    """Anthropic-seitiger Fehler (z. B. Rate-Limit bei Anthropic selbst,
    ungültiges Modell, Netzwerkfehler) - vom Router in eine generische
    502-Antwort ohne interne Details übersetzt."""


def call_anthropic(
    *, api_key: str, request: RelayRequest, timeout_seconds: float = 60.0
) -> RelayResponse:
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
    try:
        response = client.messages.create(
            model=request.model,
            max_tokens=request.max_tokens,
            system=[block.model_dump(exclude_none=True) for block in request.system],
            messages=[block.model_dump(exclude_none=True) for block in request.messages],
        )
    except anthropic.APIError as exc:
        raise RelayError(str(exc)) from exc

    text = "".join(block.text for block in response.content if block.type == "text")

    input_tokens = None
    output_tokens = None
    if response.usage is not None:
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

    return RelayResponse(text=text, input_tokens=input_tokens, output_tokens=output_tokens)
