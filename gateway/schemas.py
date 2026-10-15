"""Wire-Schema des Gateway-Relay-Endpunkts.

Bewusst generisch/an Anthropics eigener Messages-API orientiert (`model`,
`max_tokens`, `system`, `messages`) statt an das interne
`ClaudeRequestPayload`-Allowlist-Schema aus `app/privacy/gateway_schema.py`
gekoppelt - siehe ARCHITECTURE.md §70 für die Begründung. Die
Privacy-Garantie entsteht vollständig VOR diesem Aufruf (clientseitig);
der Gateway muss das Allowlist-Schema selbst nicht kennen."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


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
    # §44: optional und NUR {"type": "disabled"} - der Schreibpfad schaltet den Denkmodus wie im direkten Pfad ab
    # (Fund 05.10.: Denken verbrauchte max_tokens, Texte brachen ab). Andere Werte (z. B. ein Denkbudget) werden
    # abgelehnt, damit ueber das Relay kein kostenintensiver Denkmodus angefordert werden kann. Ohne Feld: wie bisher.
    thinking: dict | None = None

    @field_validator("thinking")
    @classmethod
    def only_disabled_thinking(cls, value: dict | None) -> dict | None:
        if value is not None and value != {"type": "disabled"}:
            raise ValueError('thinking: nur {"type": "disabled"} zulaessig')
        return value


class RelayResponse(BaseModel):
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    # §44: "max_tokens" = abgeschnitten; vorher verworfen, ein Abbruch war fuer den Client unsichtbar
    stop_reason: str | None = None
