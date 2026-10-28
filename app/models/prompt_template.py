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
    # Chat-Startseite (28.10.2026, "Standard-Prompts verwalten"): hoechstens vier Vorlagen gleichzeitig aktiv
    # (serverseitig geprueft, app/prompt_library/start_prompts.py); `sort_order` bestimmt die Reihenfolge in der
    # Verwaltung und damit die Position der aktivierten Karten auf der Startseite. `icon` aus einer festen Liste,
    # `start_action` legt fest, was die Karte tut: Text ins Eingabefeld ("prefill"), zusaetzlich Dateiauswahl
    # ("prefill_attach") oder Aktenauswahl ("open_matter").
    show_on_start: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    start_action: Mapped[str] = mapped_column(String(20), nullable=False, default="prefill", server_default="prefill")
