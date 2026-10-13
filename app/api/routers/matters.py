"""Akten-Endpunkte (Prompt 21)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import LimitParam, OffsetParam, get_or_404
from app.api.schemas import MatterOut
from app.db.session import get_db
from app.models import Matter

router = APIRouter(prefix="/api/matters", tags=["matters"])


@router.get("", response_model=list[MatterOut])
def list_matters(
    db: Session = Depends(get_db),
    client_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="z. B. 'open', 'closed'."),
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
) -> list[Matter]:
    # Datenschutz-Pruefung §27: soft-geloeschte Akten auch hier ausblenden.
    query = db.query(Matter).filter(Matter.deleted_at.is_(None))
    if client_id is not None:
        query = query.filter(Matter.client_id == client_id)
    if status is not None:
        query = query.filter(Matter.status == status)
    return (
        query.order_by(Matter.created_at.desc()).offset(offset).limit(limit).all()
    )


@router.get("/{matter_id}", response_model=MatterOut)
def get_matter(matter_id: str, db: Session = Depends(get_db)) -> Matter:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    if matter.deleted_at is not None:
        # Datenschutz-Pruefung §27: wie ein geloeschtes Dokument (404).
        raise HTTPException(status_code=404, detail="Akte nicht gefunden")
    return matter
