"""PromptTemplate – editierbarer Kanzlei-Prompt-Baustein (Schritt 3,
Teil 2, "Standard-Prompts"-Bibliothek).

Bewusst GETRENNT von `Policy` (Prompt 16, Kanzleiregeln, direkt in den
lokalen Fallkontext eingebunden, siehe app/promptlayer/) - diese Vorlagen
sind eine reine REFERENZBIBLIOTHEK für Mitarbeitende (kopierbare
Textbausteine mit Platzhaltern wie `{Mandant}`, `{Frist}`,
`{Dokumententext}`), NICHT automatisch Teil eines Claude-API-Aufrufs. Eine
Anbindung an die eigentliche Drafting-Pipeline wäre ein separater, deutlich
größerer und sicherheitsrelevanter Schritt (Prompt-Injection-Härtung,
siehe WRITING_SYSTEM_PROMPT) - hier bewusst NICHT vorgenommen.

Versionierung analog zum etablierten Projektmuster (Policy/KnowledgeItem):
jede inhaltliche Änderung erhöht `version`, überschreibt aber dieselbe
Zeile (keine Versionshistorie mehrerer Zeilen - anders als bei `Policy`,
wo mehrere Versionen parallel in der DB bleiben, weil dort die vorherige
Version ggf. weiter aktiv sein könnte). Hier reicht der einfache Zähler als
Änderungsindikator, da es keine "aktive vs. inaktive Version" gibt - eine
Vorlage ist immer die aktuellste."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PromptTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "prompt_templates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by_actor: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Startseiten-Verwaltung ("Standard-Prompts verwalten", Modal im Chat):
    # `sort_order` ist die Reihenfolge der GESAMTEN Liste (Drag-and-drop) und
    # bestimmt damit auch die Reihenfolge der auf der Chat-Startseite
    # gezeigten Prompts; `show_on_home` markiert die (höchstens
    # HOME_PROMPT_LIMIT) dort angezeigten Prompts - NICHT automatisch die
    # obersten vier. `icon` ist ein Schlüssel aus app/prompt_library/icons.py,
    # `attach_on_use` öffnet nach dem Vorbefüllen den Dateidialog (wie früher
    # bei "Dokument analysieren").
    icon: Mapped[str] = mapped_column(String(32), nullable=False, default="document", server_default="document")
    show_on_home: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    attach_on_use: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
