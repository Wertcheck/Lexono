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

_ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"


class RelayError(Exception):
    """Anthropic-seitiger Fehler (z. B. Rate-Limit bei Anthropic selbst,
    ungültiges Modell, Netzwerkfehler) - vom Router in eine generische
    502-Antwort ohne interne Details übersetzt."""


def call_anthropic(
    *, api_key: str, model: str, request: RelayRequest, timeout_seconds: float = 55.0, max_retries: int = 0
) -> RelayResponse:
    """`model` ist das zentral in `GatewaySettings.default_model`
    konfigurierte Modell - bewusst NICHT `request.model` (Umsetzungsplan
    Punkt 1: zentrale Modellsteuerung, siehe gateway/main.py::relay_messages).
    `request.model` wird dort weiterhin gegen die Allowlist geprueft, aber
    hier nicht mehr verwendet."""
    # Ziel ausdruecklich festgelegt (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §21): ohne base_url uebernaehme das SDK
    # still die Umgebungsvariable ANTHROPIC_BASE_URL - samt echtem Schluessel an einen beliebigen Host.
    # §44: ausdrueckliche Wiederholungszahl (Standard 0) statt SDK-Standard 2 - siehe GatewaySettings.
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries,
                                 base_url=_ANTHROPIC_API_BASE_URL)
    # `thinking` nur, wenn der Client es setzt (Schema laesst nur {"type": "disabled"} zu) - Review-Pfad unveraendert
    extra = {"thinking": request.thinking} if request.thinking is not None else {}
    try:
        response = client.messages.create(
            model=model,
            max_tokens=request.max_tokens,
            system=[block.model_dump(exclude_none=True) for block in request.system],
            messages=[block.model_dump(exclude_none=True) for block in request.messages],
            **extra,
        )
    except anthropic.APIError as exc:
        raise RelayError(str(exc)) from exc

    text = "".join(block.text for block in response.content if block.type == "text")

    input_tokens = None
    output_tokens = None
    if response.usage is not None:
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

    stop_reason = getattr(response, "stop_reason", None)
    return RelayResponse(text=text, input_tokens=input_tokens, output_tokens=output_tokens,
                         stop_reason=stop_reason if isinstance(stop_reason, str) else None)
