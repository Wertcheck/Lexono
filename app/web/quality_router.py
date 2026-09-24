"""Web-Router für Draft Quality Ratings (Prompt 43; Sicherheits-Fix Prompt 46;
UI-Anbindung 18.09., Owner-Direktive "WEITERARBEITEN" Fortsetzung).

GEFUNDEN+BEHOBEN im Zuge der Prompt-46-Testverifikation: dieser Router lag
ursprünglich unter dem Prefix "/api/drafts" mit einem POST-Endpunkt - verletzt
die bestehende, testabgesicherte Architekturregel (siehe app/api/__init__.py:
"/api/... bewusst NUR lesende Endpunkte", strukturell erzwungen durch
`test_no_unprotected_api_path_exists_for_restricted_actions`). Zusätzlich war
der Endpunkt durch einen kaputten Import (`get_current_user`, existierte nie
in `app.auth.security`) faktisch nicht ladbar.

Verschoben unter "/dashboard/drafts" (derselbe Prefix wie
`app/web/drafts_router.py` für dieselbe Art Aktion "etwas an einem Entwurf
tun") mit Login+CSRF-Schutz (`Depends(require_role())` - ohne Rollen-
/Berechtigungsargument, also bewusst NUR Login+CSRF, keine zusätzliche
Rolleneinschränkung, passend zum ursprünglichen Code-Ziel "irgendein
angemeldeter Nutzer"). Der POST-Endpunkt nimmt jetzt Formular-Felder statt
eines JSON-Bodys entgegen, damit der CSRF-Token als Formularfeld mitgeschickt
werden kann - identisches Muster zu jeder anderen mutierenden Dashboard-Route
im Projekt (drafts_router.py/outbox_router.py/users_router.py/errors_router.py).

ECHTER FUND (18.09., systematische Suche nach verwaisten Endpunkten): trotz
dieser sorgfältigen Sicherheits-Härtung (Prompt 46) blieb der Router
komplett UNVERLINKT - kein Template, kein JS rief ihn je auf
(`grep -rl "quality" app/web/templates/` fand nichts), keine
Owner-Entscheidung dokumentierte ein bewusstes Zurückstellen. Der
POST-Endpunkt gab bisher JSON zurück (`response_model=...`), passend zu
einem gedachten Fetch-Aufruf - es gab aber projektweit keinen einzigen
Aufrufer. Jetzt auf das etablierte Formular-POST-plus-Redirect-Muster
umgestellt (wie jede andere Dashboard-Mutation), das GET-Auslesen
(`get_draft_ratings`/`get_draft_quality_stats`) bleibt unverändert JSON
(dient künftigen/externen Lesezugriffen, wird von `draft_detail_page`
selbst direkt über den Service gelesen, nicht über HTTP)."""

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.permissions import require_login, require_role
from app.db.session import get_db
from app.models import User
from app.quality.schema import (
    DraftQualityRatingInput,
    DraftQualityRatingOutput,
)
from app.quality.service import DraftQualityService

router = APIRouter(prefix="/dashboard/drafts", tags=["dashboard-draft-quality"])


@router.post("/{draft_id}/ratings")
def record_quality_rating(
    draft_id: str,
    content_quality: int | None = Form(None),
    usefulness: int | None = Form(None),
    completeness: int | None = Form(None),
    language_quality: int | None = Form(None),
    comment: str | None = Form(None),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Neue Qualitätsbewertung für einen freigegebenen Entwurf speichern.

    Der Anwalt kann einen bereits freigegebenen Entwurf bewerten (1-5 Skalen
    und/oder Kommentar). Mehrere Bewertungen pro Entwurf sind möglich.

    **Anforderung:** Der Entwurf muss Status "approved" haben.
    """
    input_data = DraftQualityRatingInput(
        content_quality=content_quality,
        usefulness=usefulness,
        completeness=completeness,
        language_quality=language_quality,
        comment=comment,
    )
    service = DraftQualityService(db)

    try:
        service.record_rating(
            draft_id=draft_id,
            rated_by_user_id=current_user.id,
            input_data=input_data,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/dashboard/drafts/{draft_id}?error={e}", status_code=303
        )

    return RedirectResponse(url=f"/dashboard/drafts/{draft_id}", status_code=303)


@router.get("/{draft_id}/ratings", response_model=list[DraftQualityRatingOutput])
def get_draft_ratings(
    draft_id: str,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> list[DraftQualityRatingOutput]:
    """Alle Bewertungen für einen Entwurf abrufen (neueste zuerst).

    Zeigt, welche Anwälte diesen Entwurf bewertet haben und mit welchen Skalen/Kommentaren.
    """
    service = DraftQualityService(db)
    ratings = service.get_ratings_for_draft(draft_id)
    return [DraftQualityRatingOutput.model_validate(r) for r in ratings]


@router.get("/{draft_id}/quality-stats")
def get_draft_quality_stats(
    draft_id: str,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregierte Qualitätsstatistiken für einen Entwurf.

    Gibt Durchschnitte für jede Bewertungsskala sowie einen Gesamt-Durchschnitt zurück.
    """
    service = DraftQualityService(db)
    stats = service.compute_stats(draft_id)

    return {
        "draft_id": stats.draft_id,
        "total_ratings": stats.total_ratings,
        "average_content_quality": stats.avg_content_quality,
        "average_usefulness": stats.avg_usefulness,
        "average_completeness": stats.avg_completeness,
        "average_language_quality": stats.avg_language_quality,
        "average_overall": stats.avg_overall,
    }


@router.get("/matters/{matter_id}/quality-overview")
def get_matter_quality_overview(
    matter_id: str,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """Übersicht aller Bewertungen für eine Akte.

    Zeigt, wie viele Entwürfe bewertet wurden und durchschnittliche Qualitätswerte.
    """
    service = DraftQualityService(db)
    ratings = service.get_ratings_by_matter(matter_id)

    if not ratings:
        return {
            "matter_id": matter_id,
            "total_ratings": 0,
            "unique_drafts_rated": 0,
            "average_overall_quality": None,
        }

    # Eindeutige Entwürfe zählen
    unique_drafts = set(r.draft_id for r in ratings)

    # Durchschnitte berechnen
    all_scores = []
    for rating in ratings:
        scores = [
            s
            for s in [
                rating.content_quality,
                rating.usefulness,
                rating.completeness,
                rating.language_quality,
            ]
            if s is not None
        ]
        all_scores.extend(scores)

    avg_overall = sum(all_scores) / len(all_scores) if all_scores else None

    return {
        "matter_id": matter_id,
        "total_ratings": len(ratings),
        "unique_drafts_rated": len(unique_drafts),
        "average_overall_quality": avg_overall,
    }
