"""Dashboard-Router für das manuelle Anlegen einer Frist (18.09., Owner-
Direktive "WEITERARBEITEN" Fortsetzung).

ECHTER FUND: `app.models.Deadline` wurde projektweit AUSSCHLIESSLICH
automatisch angelegt - entweder vom `DeadlineAnalysisService`
(app/deadlines/service.py, aus einem Dokument extrahiert) oder vom
Synthetic-Data-Generator. Es gab KEINEN Weg, eine Frist manuell
einzutragen, die NICHT aus einem Dokument stammt - z. B. ein telefonisch
mitgeteilter Gerichtstermin, eine mündlich vereinbarte Nachfrist, oder
schlicht eine dem Anwalt bereits bekannte Frist, bevor ein Dokument
dazu überhaupt eingegangen ist. Für eine Kanzlei ist das ein reales
Bedürfnis - nicht jede Frist entsteht aus einem eingescannten Schreiben.

Bewusst NICHT dasselbe Modell-Feld-Set wie eine system-erkannte Frist:
`document_id`/`confidence`/`reasoning` bleiben `None` (es gibt keine
Quelle/Erkennungsgüte für eine manuell eingetragene Frist), und
`review_status` startet direkt als "confirmed" statt "unreviewed" - die
"nie automatisch verbindlich"-Regel (siehe Moduldocstring
app/models/deadline.py) gilt für eine AUTOMATISCH ERKANNTE Frist, deren
Richtigkeit noch niemand geprüft hat. Eine Frist, die ein Anwalt selbst
einträgt, IST bereits die menschliche Prüfung - sie als "unreviewed"
zu markieren, würde nur eine unnötige Selbstbestätigung erzwingen.

Bewusst NICHT in `matters_router.py` (Matter selbst) oder
`tasks_router.py` (dort nur die Prüf-AKTION einer bereits bestehenden
Frist, kein Anlegeweg) - eigener, kleiner Router, analog zur bereits
etablierten Trennung (`parties_router.py`, `document_actions_router.py`).

Aktenisolation (CLAUDE.md) bleibt gewahrt: jede Frist ist über `matter_id`
an genau eine Akte gebunden."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_role
from app.db.session import get_db
from app.models import AuditEvent, Deadline, Matter, User

router = APIRouter(prefix="/dashboard/matters", tags=["dashboard-matters-deadlines"])


@router.post("/{matter_id}/deadlines")
def create_deadline(
    matter_id: str,
    source_text: str = Form(...),
    due_date: str = Form(...),
    # Prioritaet (03.10., Owner-Direktive "AUFGABEN & FRISTEN"): optionales
    # Formularfeld, bewusst mit Default "" statt Pflichtfeld - bestehende
    # Aufrufer (z. B. das "Frist hinzufügen"-Modal auf der Aktenseite vor
    # diesem Feld) duerfen dadurch unveraendert ohne Prioritaet weiter-
    # funktionieren.
    priority: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    label = source_text.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Bitte eine Bezeichnung angeben.")
    try:
        parsed_due_date = date.fromisoformat(due_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ungültiges Datum.") from exc

    deadline = Deadline(
        matter_id=matter.id,
        source_text=label,
        due_date=parsed_due_date,
        review_status="confirmed",
        priority=(priority or "").strip() or None,
    )
    db.add(deadline)
    db.flush()
    db.add(
        AuditEvent(
            entity_type="Deadline",
            entity_id=deadline.id,
            event_type="deadline_added_manually",
            actor=current_user.email,
            details=f"Frist manuell angelegt: {label} ({parsed_due_date.isoformat()})",
        )
    )
    db.commit()

    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)
