"""Schema für das Drafting-Ergebnis."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    """Ein Eintrag der Quellenliste - verweist auf eine tatsächliche
    `Source`-Zeile (Prompt 14/15), niemals eine erfundene Fundstelle."""

    source_id: str
    title: str
    reference: str | None = None
    url: str | None = None


class KnowledgeItemReference(BaseModel):
    """Ein verwendetes Wissenselement - verweist auf eine tatsächliche
    `KnowledgeItem`-Zeile (Prompt 12), stets bereits freigegeben."""

    knowledge_item_id: str
    title: str


class DraftingResult(BaseModel):
    success: bool
    draft_id: str | None = None
    draft_text: str | None = None
    source_list: list[SourceReference] = Field(default_factory=list)
    knowledge_items_used: list[KnowledgeItemReference] = Field(default_factory=list)
    # z. B. "Keine ausreichende Rechtsquelle gefunden fuer ..." - Punkte,
    # die der Anwalt vor Freigabe explizit pruefen sollte.
    open_review_points: list[str] = Field(default_factory=list)
    # z. B. Hinweise auf unbestaetigte Fristen - weichere Warnungen als
    # offene Pruefungen, aber ebenfalls nicht verschwiegen.
    uncertainties: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    # Human in the Loop (12.10.2026): `review_required` -> vor jedem Cloud-Aufruf muss der Anwalt die unsicheren
    # Begriffe entscheiden (nur lokal anzeigen, nie loggen). Kandidaten sind app.privacy.review.ReviewCandidate.
    review_required: bool = False
    review_candidates: list = Field(default_factory=list)
    # Dokumentstatus (13.10.2026, app/documents/readiness.py): Dokumente, auf die sich die Anfrage bezieht, deren Inhalt
    # aber nicht ausgewertet werden kann - es ist KEIN Cloud-Aufruf erfolgt. Elemente sind DocumentReadiness.
    documents_not_ready: list = Field(default_factory=list)
