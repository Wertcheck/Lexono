"""Auswahl der Icons für Standard-Prompts (Verwaltungs-Modal + Chat-Startseite).

Schlüssel -> Makro in app/web/templates/_icons.html. Nur Icons, die dort
tatsächlich existieren - das Modal bietet genau diese Auswahl an."""

from __future__ import annotations

DEFAULT_ICON = "document"

PROMPT_ICONS: dict[str, str] = {
    "document": "Dokument",
    "printer": "Schriftsatz",
    "bulb": "Idee",
    "folder": "Akte",
    "scale": "Waage",
    "search": "Suche",
    "book": "Buch",
    "clock": "Frist",
    "chat": "Chat",
    "pencil": "Stift",
    "users": "Personen",
    "envelope": "E-Mail",
}

# Akzentfarbe je Icon (Klassen chat-quick-action--tint-* / icon--*): stabil pro Icon,
# damit Verwaltungsliste und Startseite denselben Prompt gleich einfärben.
PROMPT_ICON_TINTS: dict[str, str] = {
    "document": "green", "book": "green", "pencil": "green",
    "printer": "blue", "chat": "blue", "envelope": "blue",
    "bulb": "purple", "search": "purple", "users": "purple",
    "folder": "orange", "clock": "orange", "scale": "red",
}
