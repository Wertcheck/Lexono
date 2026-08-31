"""Wire-Schema des Gateway-Relay-Endpunkts.

Bewusst generisch/an Anthropics eigener Messages-API orientiert (`model`,
`max_tokens`, `system`, `messages`) statt an das interne
`ClaudeRequestPayload`-Allowlist-Schema aus `app/privacy/gateway_schema.py`
gekoppelt - siehe ARCHITECTURE.md §70 für die Begründung. Die
Privacy-Garantie entsteht vollständig VOR diesem Aufruf (clientseitig);
der Gateway muss das Allowlist-Schema selbst nicht kennen."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SystemBlock(BaseModel):
    type: str = "text"
    text: str
    cache_control: dict | None = None


class MessageBlock(BaseModel):
    role: str
    content: list[dict] | str


class RelayRequest(BaseModel):
    model: str
    max_tokens: int = Field(gt=0)
    system: list[SystemBlock]
    messages: list[MessageBlock]


class RelayResponse(BaseModel):
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
