"""Read-only Referenzliste der TATSÄCHLICH verwendeten System-Prompts
(Schritt 3, Teil 2, "Standard-Prompts" der Prompt-Bibliothek).

WICHTIGE ENTSCHEIDUNG: bewusst NICHT als Datenbankzeilen gespeichert -
diese drei Konstanten werden hier direkt aus den Modulen importiert, in
denen sie tatsächlich vom System verwendet werden. Eine in der DB
gespeicherte Kopie könnte veralten, sobald ein Entwickler den Wortlaut im
Code ändert, ohne auch die DB-Zeile zu aktualisieren - das würde der
Kanzlei eine falsche Transparenz vorspiegeln ("das benutzt die KI
angeblich", obwohl der tatsächliche Prompt längst ein anderer ist). Diese
Liste ist daher IMMER exakt das, was die Anwendung gerade tatsächlich an
Claude schickt - garantiert durch den direkten Import, nicht durch
Pflege-Disziplin.

Bewusst NICHT editierbar über das Dashboard (siehe app/prompt_library/
service.py: nur `PromptTemplate`-Einträge, also die separaten, editierbaren
Kanzlei-Prompts, sind veränderbar) - diese drei Texte sind sicherheitsseitig
gehärtet (Prompt-Injection-Schutz, siehe jeweilige Docstrings) und dürfen
nicht versehentlich über eine UI verändert werden."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai_providers.claude_writing_provider import CHAT_SYSTEM_PROMPT, WRITING_SYSTEM_PROMPT
from app.promptlayer.builder import SYSTEM_RULES, SYSTEM_RULES_VERSION
from app.review.provider import REVIEW_SYSTEM_PROMPT


@dataclass(frozen=True)
class SystemPromptReference:
    name: str
    description: str
    content: str


SYSTEM_PROMPT_REFERENCES: list[SystemPromptReference] = [
    SystemPromptReference(
        name="Lokaler Kontext-Aufbau (Prompt 16)",
        description=(
            "Feste, versionierte Systemregeln für den lokal aufgebauten Fallkontext "
            f"(Version {SYSTEM_RULES_VERSION}) - noch kein Claude-Aufruf, siehe "
            "app/promptlayer/builder.py."
        ),
        content=SYSTEM_RULES,
    ),
    SystemPromptReference(
        name="Schreib-Assistent (Entwurfserstellung)",
        description=(
            "Systemprompt für die eigentliche Textproduktion bei Claude, verwendet für "
            "Drafting-Zwecke (Schriftsatz-Generator, explizite Schriftsatz-/"
            "Entwurfsanfragen im Chat) - siehe app/ai_providers/claude_writing_provider.py."
        ),
        content=WRITING_SYSTEM_PROMPT,
    ),
    SystemPromptReference(
        name="Chat-Assistent (allgemeine Anfragen)",
        description=(
            "Systemprompt für den zentralen Chat bei normalen Fragen/Analysen/"
            "Textbearbeitung (Zweck \"chat_response\", Default - Drafting nur bei "
            "ausdrücklicher Aufforderung) - siehe app/chat/service.py::"
            "_looks_like_drafting_request und app/ai_providers/claude_writing_provider.py."
        ),
        content=CHAT_SYSTEM_PROMPT,
    ),
    SystemPromptReference(
        name="Review-Engine (Entwurfsprüfung)",
        description=(
            "Systemprompt für die unabhängige Prüfung eines Entwurfs bei Claude - siehe "
            "app/review/provider.py."
        ),
        content=REVIEW_SYSTEM_PROMPT,
    ),
]
