"""Login/Logout/Passwortänderung (Prompt 26).

Bewusst EIGENER Router, nicht in drafts_router.py/outbox_router.py
eingemischt - Authentifizierung ist eine eigene Zuständigkeit.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import get_current_user_optional, require_login
from app.auth.rate_limit import login_rate_limiter
from app.auth.service import AuthService, UserService
from app.auth.session import SESSION_COOKIE_NAME, create_session_token
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
from app.db.session import get_db
from app.models import User
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard", tags=["dashboard-auth"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _set_session_cookie(
    response, token: str, settings: Settings, *, persistent: bool = True
) -> None:
    """`persistent=False` setzt bewusst KEIN `max_age` (echtes
    Browser-/WebView-Session-Cookie, endet mit der Sitzung statt nach
    `session_max_age_seconds` fortzubestehen) - Gegenstück zur
    "Angemeldet bleiben"-Checkbox auf der Login-Seite (19.09., neues
    Login-Referenzbild). Ändert NICHTS an der serverseitigen
    Session-Gültigkeit selbst (`read_session_token` prüft weiterhin
    unabhängig vom Cookie-Attribut dieselbe `session_max_age_seconds`-
    Grenze, siehe app/auth/session.py) - nur ob das Cookie einen
    App-Neustart übersteht. Default `True` erhält das bisherige,
    einzige Verhalten für den einzigen bestehenden Aufrufer unverändert."""
    cookie_kwargs: dict = {
        "key": SESSION_COOKIE_NAME,
        "value": token,
        "httponly": True,  # per JavaScript nicht auslesbar (mindert XSS-Risiko)
        "secure": settings.resolved_session_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    if persistent:
        cookie_kwargs["max_age"] = settings.session_max_age_seconds
    response.set_cookie(**cookie_kwargs)


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = "/dashboard/chat",  # noqa: A002
    error: str | None = None,
    current_user: User | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    if current_user is not None:
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"request": request, "next": next, "error": error}
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard/chat"),  # noqa: A002
    remember_me: bool = Form(False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    normalized_email = email.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    # Zwei unabhängige Schlüssel (Prompt 29): pro E-Mail (klassisches
    # Brute-Force gegen EIN Konto) UND pro IP (verhindert, dass dieselbe
    # Quelle viele VERSCHIEDENE Konten durchprobiert). Schon EINE der
    # beiden Sperren blockiert den Versuch.
    email_key = f"email:{normalized_email}"
    ip_key = f"ip:{client_ip}"
    if login_rate_limiter.is_locked_out(email_key) or login_rate_limiter.is_locked_out(
        ip_key
    ):
        # Operatives Log fuer Betriebsueberwachung (Prompt 32) - bewusst
        # NUR die IP, NIEMALS die E-Mail-Adresse (waere personenbezogen).
        # Das AuditEvent (login_failed, siehe AuthService.authenticate)
        # bleibt die massgebliche, vollstaendige Nachvollziehbarkeitsquelle -
        # dieses Log dient nur der schnellen operativen Sicht (z. B. beim
        # Live-Mitlesen der Konsole), nicht als Ersatz.
        logger.warning("Login-Sperre aktiv fuer IP %s (zu viele Fehlversuche)", client_ip)
        return RedirectResponse(
            url=(
                "/dashboard/login?error=Zu viele Fehlversuche - bitte in 15 Minuten "
                f"erneut versuchen&next={next}"
            ),
            status_code=303,
        )

    user = AuthService().authenticate(email, password, db)
    if user is None:
        login_rate_limiter.record_failure(email_key)
        login_rate_limiter.record_failure(ip_key)
        # Bewusst dieselbe, generische Fehlermeldung fuer "unbekannte
        # E-Mail" und "falsches Passwort" (siehe AuthService.authenticate).
        return RedirectResponse(
            url=f"/dashboard/login?error=E-Mail oder Passwort falsch&next={next}",
            status_code=303,
        )
    login_rate_limiter.record_success(email_key)
    login_rate_limiter.record_success(ip_key)

    token, _csrf = create_session_token(user.id, settings)
    target = "/dashboard/change-password" if user.must_change_password else next
    response = RedirectResponse(url=target, status_code=303)
    _set_session_cookie(response, token, settings, persistent=remember_me)
    return response


@router.get("/password-help", response_class=HTMLResponse)
def password_help_page(request: Request) -> HTMLResponse:
    """Ziel des "Passwort vergessen?"-Links auf der Login-Seite (19.09.,
    neues Login-Referenzbild). BEWUSST kein automatisierter E-Mail-
    Reset-Flow: Lexono hat aktuell keine Ausgehend-E-Mail-Infrastruktur
    fuer System-Mails und ein token-basierter Self-Service-Reset waere
    eine eigene, nicht ungefragt zu treffende Sicherheitsentscheidung
    (Token-Ablauf, Versandweg, Missbrauchsschutz). Stattdessen wird EHRLICH
    der tatsaechliche, heute reale Stand erklaert: `scripts/
    reset_admin_password.py` / `Lexono.exe reset-admin-password` wirkt
    weiterhin nur auf Admin-Konten (Server-Zugriff noetig), aber seit
    19.09. gibt es zusaetzlich einen echten Admin-Oberflaechen-Weg fuer
    JEDEN Nutzer (`users_router.py::reset_user_password`, "Passwort
    zurücksetzen" in der Nutzerverwaltung) - schliesst die urspruenglich
    hier gefundene Luecke ("kein Reset-Weg fuer Nicht-Admin-Konten"),
    siehe .agentic/OPEN_ISSUES.md fuer den vollen Fund-zu-Fix-Verlauf.
    Diese Seite bewusst OHNE Login (die Person, die sie braucht, kann
    sich per Definition nicht anmelden)."""
    return templates.TemplateResponse(request, "password_help.html", {"request": request})


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    """Logout ist bewusst KEIN GET (zustandsverändernd) - CSRF-Schutz via
    Form-Feld wäre hier möglich, aber ein Logout-CSRF hat kein
    ausnutzbares Schadenspotenzial (der Angreifer könnte höchstens den
    NUTZER selbst ausloggen) - deshalb hier ohne csrf_token-Pflicht, um
    das Formular denkbar einfach zu halten."""
    response = RedirectResponse(url="/dashboard/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    error: str | None = None,
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    context = {
        "request": request,
        "error": error,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "forced": current_user.must_change_password,
        "current_user": current_user,
        "active_nav": None,
    }
    return templates.TemplateResponse(request, "change_password.html", context)


@router.post("/change-password")
def change_password_submit(
    request: Request,
    csrf_token: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> RedirectResponse:
    from app.auth.permissions import verify_csrf_token
    from app.auth.security import verify_password

    verify_csrf_token(request, csrf_token)

    if not verify_password(current_password, current_user.password_hash):
        return RedirectResponse(
            url="/dashboard/change-password?error=Aktuelles Passwort ist falsch",
            status_code=303,
        )
    if len(new_password) < 10:
        return RedirectResponse(
            url="/dashboard/change-password?error=Neues Passwort muss mindestens 10 Zeichen haben",
            status_code=303,
        )
    if new_password != new_password_confirm:
        return RedirectResponse(
            url="/dashboard/change-password?error=Passwörter stimmen nicht überein",
            status_code=303,
        )

    UserService().change_password(db, current_user, new_password, actor=current_user.email)
    # Direkt zur Login-Seite umleiten und die (alte) Session-Cookie
    # löschen - NICHT über /dashboard/logout umleiten, da das eine
    # POST-only-Route ist und ein 303-Redirect vom Browser als GET
    # ausgeführt würde (405).
    response = RedirectResponse(
        url="/dashboard/login?error=Passwort geändert - bitte neu anmelden", status_code=303
    )
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
