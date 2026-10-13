"""Entwurf-Endpunkte (Prompt 21).

Bewusst nur lesend - Freigabe/Ablehnung/Ueberarbeitung bleiben den
bestehenden Services vorbehalten (DraftFeedbackService,
WorkflowStateMachine), bis eine echte Zugriffskontrolle existiert
(Prompt 26).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import LimitParam, OffsetParam, get_or_404
from app.api.schemas import DraftOut
from app.db.session import get_db
from app.models import Draft, Matter

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


@router.get("", response_model=list[DraftOut])
def list_drafts(
    db: Session = Depends(get_db),
    matter_id: str | None = Query(default=None),
    status: str | None = Query(
        default=None, description="z. B. 'draft', 'legal_review', 'approved'."
    ),
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
) -> list[Draft]:
    # Datenschutz-Pruefung §27: Entwuerfe geloeschter Akten liefert die
    # REST-API nicht aus (Web-Entwurfsliste bewusst unveraendert).
    query = db.query(Draft).filter(Draft.matter.has(Matter.deleted_at.is_(None)))
    if matter_id is not None:
        query = query.filter(Draft.matter_id == matter_id)
    if status is not None:
        query = query.filter(Draft.status == status)
    return (
        query.order_by(Draft.created_at.desc()).offset(offset).limit(limit).all()
    )


@router.get("/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: str, db: Session = Depends(get_db)) -> Draft:
    draft = get_or_404(db, Draft, draft_id, "Entwurf")
    if draft.matter is None or draft.matter.deleted_at is not None:
        # Datenschutz-Pruefung §27: wie ein geloeschtes Dokument (404).
        raise HTTPException(status_code=404, detail="Entwurf nicht gefunden")
    return draft
