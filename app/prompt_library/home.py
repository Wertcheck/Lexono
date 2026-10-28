"""Standard-Prompts der Chat-Startseite: Limit, Standardwerte, Zustand als Dict.

`HOME_PROMPT_LIMIT` ist die einzige Quelle für die Obergrenze (Service,
Router, Modal und Tests). Die Standardwerte entsprechen den früher fest in
chat.html verdrahteten Schnellaktionen; die Migration übernimmt sie in die
Datenbank, damit bestehende Startseiten-Prompts nicht verloren gehen."""

from __future__ import annotations

HOME_PROMPT_LIMIT = 4
NAME_MAX = 100
DESCRIPTION_MAX = 200
CONTENT_MAX = 2000

DEFAULT_HOME_PROMPTS: list[dict[str, object]] = [
    {
        "name": "Dokument analysieren",
        "description": "Inhalte verstehen und auswerten",
        "content": "Bitte analysiere das angehängte Dokument und fasse die wichtigsten Punkte zusammen.",
        "icon": "document",
        "attach_on_use": True,
    },
    {
        "name": "Schriftsatz erstellen",
        "description": "Z. B. an Mandanten, Gericht oder Behörde",
        "content": "Bitte erstelle einen Schriftsatz-Entwurf zu folgendem Sachverhalt: ",
        "icon": "printer",
        "attach_on_use": False,
    },
    {
        "name": "Zusammenfassen",
        "description": "Wesentliche Inhalte auf den Punkt bringen",
        "content": "Bitte fasse das angehängte Dokument in wenigen Sätzen zusammen.",
        "icon": "bulb",
        "attach_on_use": True,
    },
]
