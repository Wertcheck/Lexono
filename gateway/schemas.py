"""Wire-Schema des Gateway-Relay-Endpunkts.

Bewusst generisch/an Anthropics eigener Messages-API orientiert (`model`,
`max_tokens`, `system`, `messages`) statt an das interne
`ClaudeRequestPayload`-Allowlist-Schema aus `app/privacy/gateway_schema.py`
gekoppelt - siehe ARCHITECTURE.md §70 für die Begründung. Die
Privacy-Garantie entsteht vollständig VOR diesem Aufruf (clientseitig);
der Gateway muss das Allowlist-Schema selbst nicht kennen."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# §61 P5 (17.10.2026): nur das, was der Lexono-Client tatsaechlich sendet (app/ai_providers/gateway_writing_provider.py,
# app/review/gateway_review_provider.py: Textbloecke, Rolle "user", optional cache_control "ephemeral"). Vorher nahm das
# Relay beliebige Rollen und Block-Typen an (Bilder, Dokumente, tool_use ...) - eine kompromittierte Kanzlei-Credential
# haette den zentralen Schluessel fuer Inhalte nutzen koennen, die Lexono nie sendet. Kein Ersatz fuer die clientseitige
# Datenschutzpruefung (die bleibt die eigentliche Garantie), sondern Begrenzung des Missbrauchs.
_ALLOWED_ROLES = ("user", "assistant")
_ALLOWED_CACHE_CONTROL = {"type": "ephemeral"}
_TEXT_BLOCK_KEYS = {"type", "text", "cache_control"}


def _check_cache_control(value: dict | None) -> dict | None:
    if value is not None and value != _ALLOWED_CACHE_CONTROL:
        raise ValueError('cache_control: nur {"type": "ephemeral"} zulaessig')
    return value


class SystemBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    cache_control: dict | None = None

    _cache_control = field_validator("cache_control")(classmethod(lambda cls, value: _check_cache_control(value)))


class MessageBlock(BaseModel):
    role: str
    content: list[dict] | str

    @field_validator("role")
    @classmethod
    def only_conversation_roles(cls, value: str) -> str:
        if value not in _ALLOWED_ROLES:
            raise ValueError("role: nur user oder assistant zulaessig")
        return value

    @field_validator("content")
    @classmethod
    def only_text_blocks(cls, value: list[dict] | str) -> list[dict] | str:
        if isinstance(value, str):
            return value
        for block in value:
            if (set(block) - _TEXT_BLOCK_KEYS or block.get("type") != "text"
                    or not isinstance(block.get("text"), str)):
                raise ValueError("content: nur Textbloecke zulaessig")
            _check_cache_control(block.get("cache_control"))
        return value


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
