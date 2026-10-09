"""Klartext eines gespeicherten Entwurfs (fuer die Chat-Ueberarbeitung auf Basis des Editorstands)."""

from __future__ import annotations

from app.export.html_content import parse_html_content
from app.models import Draft


def draft_plain_text(draft: Draft) -> str:
    """Absaetze/Listenpunkte als Klartext, Absaetze durch Leerzeilen getrennt. `content_format`
    "text" wird unveraendert zurueckgegeben, "html" ueber denselben Parser wie der Export."""
    if draft.content_format != "html":
        return draft.content
    blocks = []
    for block in parse_html_content(draft.content):
        text = "".join(run.text for run in block.runs).strip()
        if block.kind == "li_bullet":
            text = f"- {text}"
        elif block.kind == "li_number":
            text = f"{block.number or 1}. {text}"
        blocks.append(text)
    return "\n\n".join(blocks)
