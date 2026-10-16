"""Gespeicherte Datenschutz-Entscheidungen einer Akte verwalten (PRIVACY_REVIEW_ARCHITECTURE.md §52, 16.10.2026).

Entscheidungen entstehen in der Chat-Rueckfrage ("Als Person behandeln" / "Kein personenbezogenes Datum" / "Unsicher")
und ueber "Namen schützen" im Eingabefeld. Sie gelten aktenweit (bzw. "Kein personenbezogenes Datum" nur in ihrer
Unterhaltung) und wirken in allen Pfaden derselben Akte (Chat, Entwurfspruefung, Schriftsatz, Orchestrator). Bisher
waren sie nirgends sichtbar und nicht aufhebbar.

Eigener kleiner Router nach dem Muster von `parties_router.py` (Beteiligte): Anzeige im Reiter "Beteiligte" der
Akte, hier nur die zwei schreibenden Aktionen. Aktenisolation: jede Aktion prueft, dass die Entscheidung zu GENAU der Akte
der URL gehoert. Aufheben/Bestaetigen nur mit `PERM_PRIVACY_DECISION_MANAGE` (Admin, Anwalt). Faellt das Speichern aus,
wird zurueckgerollt - die Entscheidung (und damit der Schutz) bleibt bestehen."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import PERM_PRIVACY_DECISION_MANAGE, require_role
from app.db.session import get_db
from app.models import Matter, User
from app.privacy.decisions import InvalidPrivacyDecisionError, confirm_unsure_decision, revoke_decision

router = APIRouter(prefix="/dashboard/matters", tags=["dashboard-matters-privacy-decisions"])


def _back(matter_id: str, error: str | None = None) -> RedirectResponse:
    # Fehlertext ohne Begriff (landet in der URL); Anker oeffnet den Reiter "Beteiligte"
    suffix = f"?error={quote(error)}" if error else ""
    return RedirectResponse(url=f"/dashboard/matters/{matter_id}{suffix}#beteiligte", status_code=303)


@router.post("/{matter_id}/privacy-decisions/{decision_id}/revoke")
def revoke_privacy_decision(
    matter_id: str,
    decision_id: str,
    current_user: User = Depends(require_role(permission=PERM_PRIVACY_DECISION_MANAGE)),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    try:
        revoke_decision(db, matter_id=matter.id, decision_id=decision_id, actor=current_user.email)
        db.commit()
    except InvalidPrivacyDecisionError as exc:
        db.rollback()
        return _back(matter.id, str(exc))
    except Exception:
        db.rollback()  # fail-safe: Entscheidung bleibt bestehen
        raise
    return _back(matter.id)


@router.post("/{matter_id}/privacy-decisions/{decision_id}/confirm")
def confirm_privacy_decision(
    matter_id: str,
    decision_id: str,
    current_user: User = Depends(require_role(permission=PERM_PRIVACY_DECISION_MANAGE)),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    try:
        confirm_unsure_decision(db, matter_id=matter.id, decision_id=decision_id, user_id=current_user.id,
                                actor=current_user.email)
        db.commit()
    except InvalidPrivacyDecisionError as exc:
        db.rollback()
        return _back(matter.id, str(exc))
    except Exception:
        db.rollback()
        raise
    return _back(matter.id)
