"""Profil-/Einstellungen-Bereich (Prompt 49, juristische Menüstruktur).

Bewusst getrennt vom generischen `app/web/placeholder_router.py`: die drei
Routen hier zeigen ECHTE Daten (angemeldeter Nutzer, tatsächliche
Konfigurationswerte) statt einer reinen "in Vorbereitung"-Meldung. Zwei der
vier vom Anwalt vorgegebenen Unterpunkte ("Kanzlei-Profil & Briefkopf",
"System & Lizenz") sind weiterhin ehrliche Platzhalter (kein Backend dafür
vorhanden) und werden daher in `placeholder_router.py` geführt, nicht hier.

WICHTIG zur "Anonymisierung & Datenschutz"-Seite (`privacy_page` unten):
zeigt AUSSCHLIESSLICH bereits im System vorhandene, tatsächlich verifizierte
Ja/Nein-/Wert-Fakten - identisches Muster zu `app/web/monitoring_router.py`
(Prompt 32/33): "reine Ja/Nein-Konfigurationsstatus, NIE die tatsächlichen
Werte/Schlüssel selbst". Bewusst KEIN Bezug auf § 43e BRAO oder eine andere
Rechtsvorschrift, KEINE Aussage "konform"/"compliant" - das wäre eine
rechtliche Bewertung, die weder dieses System noch eine KI autonom treffen
darf (CLAUDE.md: "keine autonome rechtliche Entscheidung", "niemals
Rechtsquellen erfinden"). Die Pseudonymisierung vor jedem Claude-API-Aufruf
ist zudem architektonisch fest verankert (Privacy Gateway, siehe
ARCHITECTURE.md §27) und NICHT vom Nutzer abschaltbar - deshalb eine reine
Status-ANZEIGE ("aktiv"), kein Schalter, der fälschlich suggerieren würde,
dieser Schutz ließe sich hier deaktivieren.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import require_login, require_role
from app.auth.pin_lock import PinLockService, PinValidationError
from app.config import get_settings
from app.db.session import get_db
from app.models import User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/account", tags=["dashboard-account"])

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("", response_class=HTMLResponse)
def account_overview(
    request: Request, current_user: User = Depends(require_login)
) -> HTMLResponse:
    is_admin = bool(
        current_user.role and current_user.role.name.strip().lower() == "admin"
    )
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Profil & Einstellungen",
        "is_admin": is_admin,
    }
    return templates.TemplateResponse(request, "account_overview.html", context)


@router.get("/me", response_class=HTMLResponse)
def account_me(
    request: Request, error: str | None = None, current_user: User = Depends(require_login)
) -> HTMLResponse:
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Mein Konto & Abmelden",
        "error": error,
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }
    return templates.TemplateResponse(request, "account_me.html", context)


@router.post("/me/set-pin")
def set_pin(
    new_pin: str = Form(...),
    new_pin_confirm: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    """Richtet die PIN-Sperre ein/ändert sie (Schritt 3) - siehe
    app/auth/pin_lock.py für das Bedrohungsmodell (schwächeres,
    zusätzliches Geheimnis für "kurz weg vom Schreibtisch", kein Ersatz
    für das Passwort)."""
    if new_pin != new_pin_confirm:
        return RedirectResponse(
            url="/dashboard/account/me?error=PINs stimmen nicht überein", status_code=303
        )
    try:
        PinLockService().set_pin(db, current_user, new_pin)
    except PinValidationError as exc:
        return RedirectResponse(url=f"/dashboard/account/me?error={exc}", status_code=303)
    return RedirectResponse(url="/dashboard/account/me", status_code=303)


@router.post("/me/clear-pin")
def clear_pin(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    PinLockService().clear_pin(db, current_user)
    return RedirectResponse(url="/dashboard/account/me", status_code=303)


@router.post("/me/display-name")
def set_display_name(
    display_name: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    """Anzeigename setzen/entfernen (18.09., Owner-Direktive "WEITERARBEITEN"
    Fortsetzung - gefunden per systematischer Suche nach gelesenen, aber nie
    beschriebenen Modellfeldern).

    ECHTER FUND: `User.display_name` wird an mehreren Stellen gelesen
    (`{{ user.display_name or user.email }}` - Mandanten-Übersicht/-Detail,
    Dokumentgenerator-Platzhalter), aber projektweit NIE geschrieben - weder
    die Nutzeranlage (`UserService.create_user`, nur E-Mail+Rolle) noch
    irgendeine andere Stelle bot ein Eingabefeld dafür. Das Feld zeigte
    dadurch strukturell IMMER die rohe E-Mail-Adresse statt eines lesbaren
    Namens, obwohl der Lesecode erkennbar für einen echten Anzeigenamen
    gebaut wurde.

    Bewusst hier (Selbstbedienung auf der "Mein Konto"-Seite), NICHT in
    `users_router.py`s Admin-Anlageformular: wie jemand angezeigt werden
    möchte, weiß die Person selbst am besten - ein Admin, der ein Konto für
    einen Kollegen anlegt, kennt dessen bevorzugte Anzeigeform meist nicht.
    Leeres Feld entfernt den Anzeigenamen wieder (fällt dann ehrlich auf die
    bereits bestehende `or user.email`-Fallback-Logik zurück)."""
    current_user.display_name = display_name.strip() or None
    db.commit()
    return RedirectResponse(url="/dashboard/account/me", status_code=303)


@router.get("/privacy", response_class=HTMLResponse)
def account_privacy(
    request: Request, current_user: User = Depends(require_login)
) -> HTMLResponse:
    """Rein technische, tatsächlich verifizierte Fakten - siehe Modul-Docstring
    für die Begründung, warum hier bewusst keine Rechtsbehauptung und kein
    (fälschlich abschaltbar wirkender) Schalter für die Pseudonymisierung
    steht."""
    settings = get_settings()
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Anonymisierung & Datenschutz",
        # Fest verankert im Privacy Gateway (ARCHITECTURE.md §27), nicht ueber
        # settings.* konfigurierbar - daher hier bewusst als Konstante statt
        # aus einem (nicht existierenden) Einstellungswert gelesen.
        "pii_pseudonymization_active": True,
        "claude_api_configured": settings.anthropic_api_key is not None,
        "session_cookie_secure": settings.resolved_session_cookie_secure,
        "retention_days": settings.retention_days,
    }
    return templates.TemplateResponse(request, "account_privacy.html", context)
