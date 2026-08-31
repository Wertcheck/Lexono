"""Dashboard-Router für die Fehler-/Retry-Übersicht (Prompt 31).

Zugriff für ALLE drei Rollen (nicht nur Admin/Anwalt): eine fehlgeschlagene
OCR/Intake-Verarbeitung ist eine operative Wiederherstellungsaktion ohne
Kostenrisiko (kein Claude-Aufruf) und ohne besondere Sensibilität - die
bestehende Rechte-Matrix (Prompt 26) sah diesen Bereich nicht vor, daher
hier bewusst die großzügigste sinnvolle Einstufung: lesen UND manuell
wiederholen dürfen alle angemeldeten Nutzer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_login, require_role
from app.db.session import get_db
from app.errors import ProcessingError, RetryService, mask_path_like
from app.models import User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/errors", tags=["dashboard-errors"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _to_display_row(error: ProcessingError) -> dict:
    """Baut eine fuer ALLE angemeldeten Nutzer (jede Rolle, unabhaengig von
    Aktenzuordnung, siehe Moduldocstring) sichere Anzeige-Fassung.

    SICHERHEITSKRITISCH (real gefundene Luecke): `entity_id`/`error_message`
    sind bei `entity_type="IntakeFile"` der volle, unveraenderte
    Quelldateipfad im ueberwachten Scan-Ordner und koennen daher einen
    echten Mandantennamen tragen (z. B. "Max_Mustermann_Steuerbescheid.pdf")
    - anders als bei einem `Document` (immer eine UUID). Die zugrunde
    liegende `ProcessingError`-Zeile MUSS den echten Pfad behalten (der
    Retry-Mechanismus braucht ihn, siehe RetryService.execute_retry) - hier
    wird nur die fuer die Dashboard-ANZEIGE bestimmte Kopie maskiert
    (dieselbe Regel, die `RetryService.record_failure` bereits fuer den
    Logeintrag/das AuditEvent anwendet, siehe app/errors/service.py)."""
    return {
        "id": error.id,
        "operation": error.operation,
        "entity_type": error.entity_type,
        "entity_id_display": mask_path_like(error.entity_id)[:8],
        "status": error.status,
        "attempt_count": error.attempt_count,
        "max_attempts": error.max_attempts,
        "error_message_display": mask_path_like(error.error_message),
    }


@router.get("", response_class=HTMLResponse)
def errors_list_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    retry_service = RetryService()
    errors = retry_service.list_all_unresolved(db)
    context = {
        "request": request,
        "active_nav": "Fehler",
        "errors": [_to_display_row(error) for error in errors],
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }
    return templates.TemplateResponse(request, "errors_list.html", context)


@router.post("/{error_id}/retry")
def retry_now(
    error_id: str,
    db: Session = Depends(get_db),
    # require_role() OHNE Rollen-/Berechtigungsargument: erzwingt weiterhin
    # Login + CSRF-Token (siehe app/auth/permissions.py) - konsistent mit
    # JEDER anderen mutierenden Aktion im Projekt - aber keine
    # Rolleneinschränkung, da alle drei Rollen retryen dürfen (s. o.).
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    """Löst SOFORT einen erneuten Versuch aus - ignoriert bewusst das
    Backoff-Zeitfenster (der Anwalt/Mitarbeiter weiß, dass er gerade
    manuell eingreift, z. B. nachdem er das zugrunde liegende Problem
    behoben hat, etwa Tesseract neu installiert)."""
    error = get_or_404(db, ProcessingError, error_id, "Fehlereintrag")
    retry_service = RetryService()
    try:
        retry_service.execute_retry(db, error, actor=current_user.email)
    except ValueError:
        pass  # unbekannte Operation - Fehlereintrag bleibt unverändert sichtbar
    return RedirectResponse(url="/dashboard/errors", status_code=303)
