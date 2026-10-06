"""Nutzerverwaltung (Prompt 26) – AUSSCHLIESSLICH für die Rolle "admin".

Jede Route hier verwendet `Depends(require_role("admin"))` (POST) bzw.
`Depends(require_login)` + expliziter Rollencheck (GET) - siehe
app/auth/permissions.py. Ein Mitarbeiter oder Anwalt, der die URL direkt
aufruft, bekommt serverseitig 403, unabhängig davon, dass der
entsprechende Sidebar-Link im UI gar nicht erst angezeigt wird.

Die eigentliche Anzeige lebt seit der Owner-Direktive "SETTINGS ->
BENUTZER" (06.10.) im "Benutzer"-Tab von /dashboard/settings (siehe
app/web/settings_router.py + settings.html) statt auf einer separaten
Seite - GET "" ist deshalb nur noch ein Redirect dorthin (keine zweite,
parallel zu pflegende Nutzerverwaltungs-UI). Alle POST-Endpunkte bleiben
unveraendert hier und werden von der neuen UI direkt angesprochen, nur
die Redirect-Ziele zeigen jetzt auf die Settings-Seite."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import PermissionDeniedError, require_login, require_role
from app.auth.service import UserAlreadyExistsError, UserService
from app.db.session import get_db
from app.models import User

router = APIRouter(prefix="/dashboard/admin/users", tags=["dashboard-admin-users"])

_SETTINGS_BENUTZER_URL = "/dashboard/settings?tab=benutzer"


def _require_admin_read(current_user: User = Depends(require_login)) -> User:
    """GET-Variante der Admin-Beschränkung (kein CSRF-Formularfeld bei
    GET-Requests nötig, aber dieselbe Rollenprüfung)."""
    if current_user.role is None or current_user.role.name.strip().lower() != "admin":
        raise PermissionDeniedError("Nur Administratoren können Nutzer verwalten")
    return current_user


@router.get("")
def list_users_page(
    current_user: User = Depends(_require_admin_read),
) -> RedirectResponse:
    return RedirectResponse(url=_SETTINGS_BENUTZER_URL, status_code=303)


@router.post("")
def create_user(
    request: Request,
    email: str = Form(...),
    role_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    try:
        _user, password = UserService().create_user(
            db, email=email, role_name=role_name, actor=current_user.email
        )
    except (UserAlreadyExistsError, ValueError) as exc:
        return RedirectResponse(
            url=f"{_SETTINGS_BENUTZER_URL}&error={exc}", status_code=303
        )
    # Das initiale Passwort wird EINMALIG per Redirect-Query-Parameter an
    # die Admin-Ansicht zurückgegeben, damit der Admin es dem neuen Nutzer
    # mitteilen kann - es wird an keiner Stelle geloggt oder gespeichert
    # (siehe UserService.create_user). Nach dem ersten Login MUSS der neue
    # Nutzer es ändern (must_change_password=True).
    return RedirectResponse(
        url=f"{_SETTINGS_BENUTZER_URL}&created_email={email}&created_password={password}",
        status_code=303,
    )


@router.post("/{user_id}/role")
def change_user_role(
    user_id: str,
    role_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    user = get_or_404(db, User, user_id, "Nutzer")
    try:
        UserService().set_role(db, user, role_name, actor=current_user.email)
    except ValueError as exc:
        return RedirectResponse(url=f"{_SETTINGS_BENUTZER_URL}&error={exc}", status_code=303)
    return RedirectResponse(url=_SETTINGS_BENUTZER_URL, status_code=303)


@router.post("/{user_id}/deactivate")
def deactivate_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    user = get_or_404(db, User, user_id, "Nutzer")
    if user.id == current_user.id:
        return RedirectResponse(
            url=f"{_SETTINGS_BENUTZER_URL}&error=Sie können sich nicht selbst deaktivieren",
            status_code=303,
        )
    try:
        UserService().set_active(db, user, False, actor=current_user.email)
    except ValueError as exc:
        return RedirectResponse(url=f"{_SETTINGS_BENUTZER_URL}&error={exc}", status_code=303)
    return RedirectResponse(url=_SETTINGS_BENUTZER_URL, status_code=303)


@router.post("/{user_id}/activate")
def activate_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    user = get_or_404(db, User, user_id, "Nutzer")
    UserService().set_active(db, user, True, actor=current_user.email)
    return RedirectResponse(url=_SETTINGS_BENUTZER_URL, status_code=303)


@router.post("/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Schließt eine echte Lücke (19.09., gefunden beim Login-Referenzbild-
    Abgleich für "Passwort vergessen?"): bisher konnte ein Admin das
    Passwort eines KOLLEGEN (Anwalt/Mitarbeiter) nirgends zurücksetzen -
    `scripts/reset_admin_password.py` wirkt nur auf Admin-Konten. Bewusst
    auch für das eigene Konto erlaubt (anders als deactivate_user) - ein
    Admin, der sein eigenes Passwort vergisst, aber noch angemeldet ist,
    darf sich selbst helfen."""
    user = get_or_404(db, User, user_id, "Nutzer")
    _user, password = UserService().reset_password(db, user, actor=current_user.email)
    return RedirectResponse(
        url=f"{_SETTINGS_BENUTZER_URL}&created_email={user.email}&created_password={password}",
        status_code=303,
    )


@router.post("/{user_id}/force-logout")
def force_logout_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Beendet alle laufenden Sessions eines Nutzers sofort, ohne das
    Passwort zu ändern (Prompt 29) - z. B. bei einem gestohlenen Gerät."""
    user = get_or_404(db, User, user_id, "Nutzer")
    UserService().force_logout(db, user, actor=current_user.email)
    return RedirectResponse(url=_SETTINGS_BENUTZER_URL, status_code=303)
