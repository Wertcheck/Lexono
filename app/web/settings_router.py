"""Echte, bedienbare Einstellungsseite (20.08.) - ersetzt die bisherige rein
lesende Konfigurationsanzeige (app/web/account_router.py: account_privacy)
für die tatsächlich veränderbaren Werte: Scan-Ordner (INTAKE_WATCHED_FOLDERS),
E-Mail-Zugangsdaten (MAIL_*) und Aufbewahrungsfrist (RETENTION_DAYS).

Admin-only (wie Nutzerverwaltung/Systemstatus/Backup) - E-Mail-Zugangsdaten
sind ein Secret, Scan-Ordner-Pfade und die Aufbewahrungsfrist sind
kanzleiweite, nicht nutzerindividuelle Einstellungen.

Schreibpfad: app/setup/env_writer.py: update_env_values() ändert GEZIELT
einzelne Schlüssel der bestehenden `.env` (nicht die ganze Datei neu) und
lässt SESSION_SECRET_KEY & alle anderen, hier unbekannten Werte unangetastet.
Nach jedem Schreibvorgang `get_settings.cache_clear()` (siehe app/config/
settings.py, Docstring: "in Tests kann get_settings.cache_clear() genutzt
werden, um mit veränderten Umgebungsvariablen neu zu laden" - hier exakt
derselbe Mechanismus, nur zur Laufzeit statt in einem Test) - Änderungen
wirken damit SOFORT im laufenden Prozess, kein Neustart nötig.

E-Mail-Passwort wird NIE aus den bestehenden Settings vorausgefüllt
zurückgegeben (Formularfeld bleibt leer) und bei leerem Absenden NICHT
überschrieben - Standardmuster für Passwortfelder, verhindert außerdem,
dass das Passwort im gerenderten HTML sichtbar würde."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.permissions import PERMISSION_MATRIX, PermissionDeniedError, require_login, require_role
from app.config import get_settings
from app.db.session import get_db
from app.firm_profile import (
    InvalidPracticeAreaError,
    get_display_options,
    get_firm_profile,
    remove_practice_area,
    set_practice_areas,
)
from app.models import AuditEvent, Role, User
from app.setup.env_writer import update_env_values
from app.setup.paths import resolve_data_dir
from app.updater.checker import CURRENT_APP_VERSION
from app.web.download_staging import DOWNLOAD_STAGING_DIR
from app.web.template_paths import TEMPLATES_DIR

# --- "Benutzer"-Tab: Anzeige-Beschriftungen fuer die ECHTEN, bereits
# bestehenden PERM_*-Konstanten (06.10., Owner-Direktive "SETTINGS ->
# BENUTZER" - /permissions: "Keine Permission erfinden, nur damit die UI
# vollstaendig aussieht"). Diese Zuordnung erzeugt KEINE neue Berechtigung,
# sie uebersetzt lediglich die in app/auth/permissions.py bereits
# bestehenden Konstanten/PERMISSION_MATRIX in lesbaren Text fuer die
# "Rollen & Berechtigungen"-Karte. Ein evtl. zukuenftig neu hinzugefuegtes
# PERM_* ohne Eintrag hier wird nicht versteckt, sondern faellt sicher auf
# die rohe Konstante zurueck (siehe _permission_label unten).
_PERM_LABELS: dict[str, str] = {
    "dashboard:read": "Dashboard & Inhalte ansehen",
    "draft:manual_edit": "Entwürfe manuell bearbeiten",
    "instruction:create": "Anweisungen erstellen",
    "claude:call": "KI-Neugenerierung auslösen",
    "draft:approve": "Entwürfe freigeben",
    "draft:reject": "Entwürfe zurückweisen",
    "outbox:mark_sent": "Als versendet markieren",
    "user:manage": "Benutzer- & Rollenverwaltung",
    "client:manage": "Mandanten anlegen/bearbeiten",
    "client:delete": "Mandanten endgültig löschen",
}

# --- "Benutzer"-Tab: Aktivitätsverlauf (06.10., Owner-Direktive "SETTINGS
# -> BENUTZER" - /activity-log: "Nicht automatisch ein komplettes Audit-
# System bauen, wenn dies ausserhalb des Tasks liegt"). Bewusst NUR die
# bereits bestehenden, tatsaechlich nutzerverwaltungsbezogenen Event-Typen
# (entity_type="User", siehe app/auth/service.py) - kein system-weiter
# Audit-Viewer (es existieren >80 weitere Event-Typen in anderen Modulen,
# die hier fachlich nicht hingehoeren). Ein unbekannter Event-Typ faellt
# sicher auf eine aus dem Rohwert abgeleitete Beschriftung zurueck, statt
# eine fehlende Uebersetzung zu verschweigen oder abzustuerzen.
_USER_EVENT_TYPE_LABELS: dict[str, str] = {
    "login_succeeded": "Anmeldung erfolgreich",
    "login_failed": "Anmeldung fehlgeschlagen",
    "user_created": "Benutzer eingeladen/angelegt",
    "user_role_changed": "Rolle geändert",
    "user_activated": "Benutzer aktiviert",
    "user_deactivated": "Benutzer deaktiviert",
    "sessions_force_logged_out": "Sitzungen beendet",
    "password_changed": "Passwort geändert",
    "password_reset_by_admin": "Passwort durch Admin zurückgesetzt",
}

# Reihenfolge der drei real existierenden Rollen (app/models/role.py) fuer
# eine stabile, sinnvolle Anzeige ("Admin" zuerst, nicht alphabetisch
# "Admin, Anwalt, Mitarbeiter" zufaellig anders sortiert) - KEINE der
# Referenzbild-Rollen (Rechtsanwalt/Referendar/Assistenz/Sekretariat), die
# in dieser Codebasis nicht existieren (/roles: "NUR visuelle Beispiele").
_ROLE_DISPLAY_ORDER = ["admin", "anwalt", "mitarbeiter"]


def _permission_label(permission: str) -> str:
    return _PERM_LABELS.get(permission, permission)


def _role_sort_key(role: Role) -> tuple[int, str]:
    lowered = role.name.strip().lower()
    if lowered in _ROLE_DISPLAY_ORDER:
        return (_ROLE_DISPLAY_ORDER.index(lowered), role.name)
    return (len(_ROLE_DISPLAY_ORDER), role.name)


def _user_event_label(event_type: str) -> str:
    return _USER_EVENT_TYPE_LABELS.get(event_type, event_type.replace("_", " ").capitalize())


def _user_status_label(user: User) -> tuple[str, str]:
    """(Text, CSS-Modifier) - Status NUR aus tatsaechlich im Datenmodell
    nachweisbaren Feldern abgeleitet (/status: "Keine Fake-Statusanzeigen").
    `must_change_password=True` ist der einzige real existierende Hinweis
    auf "noch nicht aktiviert" (gesetzt bei create_user/reset_password, bis
    zum ersten erfolgreichen Passwortwechsel) - es gibt KEIN separates
    "invitation_pending"-Feld und KEINEN Online-Praesenz-Mechanismus, daher
    auch kein "Online"-Status wie im Referenzbild."""
    if not user.is_active:
        return "Deaktiviert", "outbound"
    if user.must_change_password:
        return "Einladung ausstehend", "unmatched"
    return "Aktiv", "matched"

router = APIRouter(prefix="/dashboard/settings", tags=["dashboard-settings"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _require_admin(current_user: User = Depends(require_login)) -> User:
    """`require_role(...)` (app/auth/permissions.py) ist bewusst NUR für
    zustandsverändernde POST-Routen gedacht - es verlangt unbedingt einen
    `csrf_token`-Formularwert, was für eine reine GET-Seitenanzeige falsch
    wäre (kein Formular-Body vorhanden). Für die GET-Seite hier exakt
    dasselbe Muster wie app/web/monitoring_router.py: `require_login` +
    manuelle Rollenprüfung."""
    if current_user.role is None or current_user.role.name.strip().lower() != "admin":
        raise PermissionDeniedError("Nur Administratoren können die Einstellungen einsehen")
    return current_user


def _env_path():
    return resolve_data_dir() / ".env"


def _apply(updates: dict) -> None:
    update_env_values(_env_path(), updates)
    get_settings.cache_clear()


def _redirect(
    success: str | None = None, error: str | None = None, *, tab: str | None = None
) -> RedirectResponse:
    """`tab` (06.10., Owner-Direktive "LEXONO - EINSTELLUNGEN UI REBUILD"):
    seit die Seite eine Tab-Navigation besitzt, wuerde ein Redirect ohne
    diesen Parameter den Nutzer nach JEDER Formularabsendung optisch auf
    "Allgemein" zuruecksetzen, selbst wenn z. B. "E-Mail" aktiv war - alle
    Aufrufer unten geben daher den Tab an, in dem ihr jeweiliges Formular
    tatsaechlich liegt (siehe settings.html)."""
    params = []
    if error:
        params.append(f"error={error}")
    if success:
        params.append(f"success={success}")
    if tab:
        params.append(f"tab={tab}")
    if not params:
        return RedirectResponse(url="/dashboard/settings", status_code=303)
    return RedirectResponse(url="/dashboard/settings?" + "&".join(params), status_code=303)


def _mail_provider_label(mail_host: str | None) -> str:
    """Menschlich lesbares Label fuer die verbundene Mailbox (06.10.,
    Owner-Direktive "SETTINGS -> E-MAIL") - rein kosmetische Ableitung aus
    dem bereits gespeicherten IMAP-Host, KEIN zweites Provider-
    Datenmodell. Es gibt weiterhin nur EINEN tatsaechlichen Provider-Typ
    ("imap", siehe app/mail/factory.py) - Microsoft 365/Google Workspace
    sind IMAP-kompatible Postfaecher, keine eigene OAuth-/Graph-/Gmail-
    API-Anbindung (die existiert in dieser Codebasis nicht)."""
    if not mail_host:
        return "E-Mail-Konto (IMAP/SMTP)"
    lowered = mail_host.lower()
    if "office365" in lowered or "outlook" in lowered:
        return "Microsoft 365 (IMAP/SMTP)"
    if "gmail" in lowered or "google" in lowered:
        return "Google Workspace (IMAP/SMTP)"
    return "E-Mail-Konto (IMAP/SMTP)"


def _cloud_ai_model_label(claude_model_name: str) -> str:
    """Menschlich lesbares Label fuer die Cloud-KI-Karte (06.10., Owner-
    Direktive "SETTINGS -> KI & DATENSCHUTZ") - leitet sich aus dem
    tatsaechlich konfigurierten `Settings.claude_model_name` ab, statt
    einen aus der Referenzgrafik uebernommenen Versionsstand ("Claude
    Sonnet 3.5") hart zu codieren, der von der echten Konfiguration
    abweichen koennte."""
    lowered = claude_model_name.lower()
    if "opus" in lowered:
        return "Claude Opus"
    if "haiku" in lowered:
        return "Claude Haiku"
    if "sonnet" in lowered:
        return "Claude Sonnet"
    return "Claude"


_VALID_DIALOGS = {
    "kanzleiinformationen", "standorte", "branding", "fachliche-schwerpunkte",
    "kanzlei-defaults", "kanzlei-einstellungen",
}


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    tab: str | None = None,
    open_dialog: str | None = None,
    created_email: str | None = None,
    created_password: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
) -> HTMLResponse:
    settings = get_settings()
    # Erreichbarkeit wird bewusst NICHT synchron bei jedem Seitenaufruf
    # geprueft (kein automatischer Netzwerkaufruf, siehe app/system_health/
    # service.py-Docstring) - die Seite bindet stattdessen denselben
    # Admin-Klick-Endpunkt wie die Systemstatus-Seite ein
    # (POST /dashboard/monitoring/check-api).
    #
    # Echte Datenquellen fuer die neue "Allgemein"-Tab-Struktur (06.10.,
    # Owner-Direktive "LEXONO - EINSTELLUNGEN UI REBUILD") - KEINE der
    # folgenden Werte ist aus der Referenz uebernommen, jede stammt aus
    # einer bereits bestehenden Lexono-Datenquelle (Direktive §2: "Keine
    # Beispielwerte aus der Referenz hardcoden, wenn Lexono dafuer bereits
    # echte Datenquellen besitzt").
    firm_profile = get_firm_profile(db)
    total_users = db.query(func.count(User.id)).scalar()
    active_users = db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar()
    gateway_configured = bool(settings.lexono_gateway_url) or settings.anthropic_api_key is not None

    # --- "Benutzer"-Tab (06.10., Owner-Direktive "SETTINGS -> BENUTZER") -
    # echte Nutzerverwaltung eingebettet in die Einstellungsseite statt des
    # bisherigen reinen Link-outs auf /dashboard/admin/users (dessen
    # POST-Endpunkte unveraendert wiederverwendet werden, siehe
    # app/web/users_router.py - nur die Zielseite der Redirects wurde
    # angepasst). Alle Werte stammen aus bereits bestehenden Modellen -
    # keine neue Nutzer-/Rollen-/Berechtigungs-Engine (/architecture-
    # decision: "Die bestehende Architektur ist die Source of Truth").
    all_users = db.query(User).order_by(User.email).all()
    db_roles = db.query(Role).order_by(Role.name).all()

    # "Letzte Anmeldung" (/inspect-Fund: User besitzt KEIN last_login_at-
    # Feld) - ehrlich aus dem bereits bestehenden, unveraenderlichen
    # Audit-Log abgeleitet statt einer neuen Spalte/Migration. Einfache
    # Python-seitige Reduktion (kein Fenster-SQL) - bei der ueberschaubaren
    # Groesse einer einzelnen Kanzlei voellig ausreichend und bewusst
    # genauso einfach gehalten wie der Rest dieses Routers.
    login_events = (
        db.query(AuditEvent.entity_id, AuditEvent.created_at)
        .filter(AuditEvent.entity_type == "User", AuditEvent.event_type == "login_succeeded")
        .order_by(AuditEvent.created_at.desc())
        .all()
    )
    last_login_by_user_id: dict[str, object] = {}
    for entity_id, created_at in login_events:
        last_login_by_user_id.setdefault(entity_id, created_at)

    user_rows = []
    for user in all_users:
        status_label, status_modifier = _user_status_label(user)
        user_rows.append(
            {
                "user": user,
                "role_name": user.role.name if user.role else "Keine Rolle",
                "status_label": status_label,
                "status_modifier": status_modifier,
                "last_login_at": last_login_by_user_id.get(user.id),
            }
        )

    # "Rollen & Berechtigungen" - liest EXAKT dieselbe PERMISSION_MATRIX,
    # die app/auth/permissions.py::has_permission serverseitig tatsaechlich
    # durchsetzt (/permissions: "UI darf NUR Berechtigungen anzeigen, die
    # das Backend tatsaechlich kennt") - keine zusaetzliche, separat
    # pflegbare Rechteliste nur fuer die Anzeige.
    role_permission_cards = [
        {
            "name": role.name,
            "description": role.description,
            "permissions": sorted(
                _permission_label(p)
                for p in PERMISSION_MATRIX.get(role.name.strip().lower(), frozenset())
            ),
        }
        for role in sorted(db_roles, key=_role_sort_key)
    ]

    # "Aktivitätsverlauf" - bewusst NUR nutzerverwaltungsbezogene Events
    # (entity_type="User"), siehe Begruendung bei _USER_EVENT_TYPE_LABELS
    # oben. `entity_id` ist bei login_succeeded/user_* immer eine echte
    # User-ID, bei login_failed mit unbekannter E-Mail jedoch die
    # versuchte E-Mail selbst (siehe AuthService.authenticate) - die
    # Zuordnung unten faellt in diesem Fall sicher auf den Rohwert zurueck.
    email_by_user_id = {user.id: user.email for user in all_users}
    recent_user_events_raw = (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_type == "User")
        .order_by(AuditEvent.created_at.desc())
        .limit(20)
        .all()
    )
    recent_user_events = [
        {
            "created_at": event.created_at,
            "subject_label": email_by_user_id.get(event.entity_id, event.entity_id),
            "action_label": _user_event_label(event.event_type),
            "details": event.details,
        }
        for event in recent_user_events_raw
    ]

    # "Sicherheit"-Karte - ausschliesslich real existierende Mechanismen
    # (/security-settings: "NICHT vortaeuschen"). PIN-Sperre bei
    # Inaktivitaet (app/auth/pin_lock.py, app/web/lock_router.py) IST echt
    # vorhanden, 2FA/TOTP existiert in dieser Codebasis nirgends
    # (bestaetigt per Grep im /inspect) - daher ehrlich als "Nicht
    # verfuegbar" dargestellt statt eines wirkungslosen Toggles.
    pin_configured = current_user.pin_hash is not None
    pin_lock_inactivity_minutes = settings.pin_lock_inactivity_minutes

    # --- "Kanzlei"-Tab (06.10., Owner-Direktive "SETTINGS -> KANZLEI") -
    # echtes Kanzlei-Profil eingebettet statt des bisherigen reinen
    # Link-outs auf /dashboard/settings/profile (dessen POST-Endpunkte
    # unveraendert wiederverwendet werden, siehe oben). Fachliche
    # Schwerpunkte (Kanzleifachprofil) UNVERAENDERT wie zuvor auf der
    # separaten Seite uebernommen - selbe Datenquelle, nur eingebettet.
    practice_area_options = get_display_options(db)

    # "Standorte & Adressen": Lexono hat KEIN Mehrstandort-/Location-Modell
    # (bestaetigt per Grep im /discover) - bewusst KEINE neue Location-
    # Engine gebaut (waere eine grosse neue Architektur fuer eine im
    # Referenzbild nur beispielhaft gezeigte Funktion). Stattdessen wird
    # die EINE echte, bereits vorhandene Anschrift ehrlich als einziger
    # "Hauptsitz"-Eintrag dargestellt - keine erfundenen Zweigstellen
    # (München/Hamburg aus der Referenz sind reine Designbeispiele).
    has_main_address = bool(firm_profile.street or firm_profile.postal_code or firm_profile.city)

    # "Kanzlei-Integrationen": weder Kalender- noch DMS-/beA-Anbindung
    # existiert in dieser Codebasis (bestaetigt per Grep) - ehrlich als
    # "Nicht verbunden" dargestellt, genau wie es das Referenzbild selbst
    # bereits fuer beide Karten zeigt. KEIN klickbarer "Verbinden"-Button
    # ohne echte Funktion dahinter (/no-fake-functionality).

    # "Kanzlei-Logo & Branding": die Lexono-Markenfarben sind ein fest
    # codiertes, kanzleiweit einheitliches Design-System (app/web/static/
    # css/app.css :root) - es gibt keinerlei Mechanismus, sie pro
    # Installation zu aendern. Die zwei echten, aktuellen CI-Farbwerte
    # werden daher NUR LESEND angezeigt (kein editierbares Eingabefeld,
    # das faelschlich Aenderbarkeit suggerieren wuerde).
    brand_colors = [
        {"label": "Primärfarbe (Lexono Grün)", "hex": "#249D74"},
        {"label": "Sekundärfarbe (Navy)", "hex": "#101828"},
    ]

    # Allowlist statt eines ungeprueften Query-Parameters (06.10.) - `tab`
    # landet unten per einfacher String-Interpolation in einem <script>-
    # Block (kein `tojson`-Filter in dieser Jinja-Umgebung registriert,
    # siehe settings.html); ein nicht erkannter Wert faellt sicher auf
    # "allgemein" zurueck, statt dass z. B. gar kein Tabpanel sichtbar waere
    # oder ein beliebiger String unvalidiert in den JS-Kontext gelangt.
    _valid_tabs = {
        "allgemein", "ki-datenschutz", "email", "benutzer", "kanzlei", "lizenz", "erweitert"
    }
    active_tab = tab if tab in _valid_tabs else "allgemein"
    # Gleiches Allowlist-Prinzip wie `active_tab` oben, fuer den neuen
    # Dialog-Auto-Reopen-Mechanismus (/error-handling: "Dialog bleibt
    # geoeffnet") - `open_dialog` landet ebenfalls in einem <script>-Block.
    active_dialog = open_dialog if open_dialog in _VALID_DIALOGS else None

    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Einstellungen",
        # Referenz zeigt "Einstellungen" als Ziel des Profilmenues, nicht
        # als Unterseite eines anderen Bereichs - derselbe bereits
        # bestehende Opt-out wie bei Chat/Posteingang/Kanzleiwissen (siehe
        # base.html), kein globaler Shell-Eingriff noetig.
        "hide_back_link": True,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "success": success,
        "error": error,
        "active_tab": active_tab,
        "active_dialog": active_dialog,
        # --- "Benutzer"-Tab ---
        "user_rows": user_rows,
        "roles": db_roles,
        "role_permission_cards": role_permission_cards,
        "recent_user_events": recent_user_events,
        "pin_configured": pin_configured,
        "pin_lock_inactivity_minutes": pin_lock_inactivity_minutes,
        "created_email": created_email,
        "created_password": created_password,
        # --- "Kanzlei"-Tab ---
        "practice_area_options": practice_area_options,
        "has_main_address": has_main_address,
        "brand_colors": brand_colors,
        "intake_watched_folders": settings.intake_watched_folders,
        "mail_host": settings.mail_host,
        "mail_port": settings.mail_port,
        "mail_username": settings.mail_username,
        "mail_mailbox": settings.mail_mailbox,
        "mail_use_ssl": settings.mail_use_ssl,
        "mail_configured": settings.mail_password is not None,
        "mail_provider_label": _mail_provider_label(settings.mail_host),
        "mail_auto_sync_enabled": settings.mail_auto_sync_enabled,
        "mail_poll_interval_seconds": settings.mail_poll_interval_seconds,
        "retention_days": settings.retention_days,
        "anthropic_api_key_configured": settings.anthropic_api_key is not None,
        # --- "KI & Datenschutz"-Tab: Endnutzer-Cloud-KI-Auswahl (06.10.,
        # Owner-Direktive "SETTINGS -> KI & DATENSCHUTZ") ---
        "cloud_ai_provider": settings.cloud_ai_provider,
        "cloud_ai_model_label": _cloud_ai_model_label(settings.claude_model_name),
        "cloud_ai_via_gateway": bool(settings.lexono_gateway_url),
        "local_ai_enabled": settings.local_ai_enabled,
        "local_ai_runtime": settings.local_ai_runtime,
        "ollama_base_url": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        # Bewusst KEIN synchroner Health-Check hier (derselbe Grundsatz wie
        # oben fuer die API-Erreichbarkeit) - wiederverwendet stattdessen
        # den beim Start bzw. periodisch ohnehin schon berechneten Zustand
        # aus app.state.local_ai_status (siehe app/main.py,
        # _run_silent_local_ai_check) - keine zweite Netzwerkoperation nur
        # fuer diese Seitenanzeige.
        "local_ai_status": getattr(request.app.state, "local_ai_status", None),
        # --- "Allgemein"-Tab: Anwendung/Benachrichtigungen/Daten & Speicher ---
        "ui_language": settings.ui_language,
        "ui_theme": settings.ui_theme,
        "start_with_system": settings.start_with_system,
        "auto_update_download_enabled": settings.auto_update_download_enabled,
        "desktop_notifications_enabled": settings.desktop_notifications_enabled,
        "email_notifications_enabled": settings.email_notifications_enabled,
        "deadline_reminder_lead_days": settings.deadline_reminder_lead_days,
        "data_dir": str(resolve_data_dir()),
        # --- rechte Spalte: Ihr Profil/Kanzlei/Lizenz/Systemstatus ---
        "firm_profile": firm_profile,
        "app_version": CURRENT_APP_VERSION,
        "total_users": total_users,
        "active_users": active_users,
        "gateway_configured": gateway_configured,
    }
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/local-ai")
def update_local_ai_settings(
    ollama_model: str = Form(...),
    ollama_base_url: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Aendert NUR das konfigurierte Modell-Tag/die Basis-URL der bereits
    aktivierten lokalen KI (dieselbe env-Schreiblogik wie update_mail_settings/
    update_retention oben) - KEIN Ersatz fuer den vollstaendigen
    Einrichtungsassistenten (`Lexono.exe setup`, siehe
    app/local_ai/setup_orchestrator.py::LocalAiSetupService.run_setup), der
    zusaetzlich Hardware-Erkennung, Ollama-Installation und den eigentlichen
    Modell-Download uebernimmt. Das hier gesetzte Modell muss lokal bereits
    vorhanden sein (z. B. per `ollama pull <tag>` oder besagtem Assistenten) -
    sonst zeigt der naechste Status-Check schlicht MODEL_MISSING an, exakt
    wie bei jeder anderen falschen Konfiguration.

    Formular lebt seit der Owner-Direktive "SETTINGS -> KI & DATENSCHUTZ"
    (06.10.) im Tab "Erweitert", nicht mehr in "KI & Datenschutz" - dieser
    Tab zeigt Endnutzern jetzt nur noch die Cloud-KI-Auswahl und den
    automatisch ermittelten Datenschutzstatus (/product-principle: "Lexono
    verwaltet die technische KI-Infrastruktur automatisch"), technische
    Local-AI-Parameter bleiben Admin-Ebene (siehe dortigen Tab)."""
    model = ollama_model.strip()
    base_url = ollama_base_url.strip()
    if not model:
        return _redirect(error="Modell-Tag darf nicht leer sein", tab="erweitert")
    if not base_url:
        return _redirect(error="Ollama-Basis-URL darf nicht leer sein", tab="erweitert")

    _apply({"OLLAMA_MODEL": model, "OLLAMA_BASE_URL": base_url})
    return _redirect(success="Lokale-KI-Einstellungen gespeichert", tab="erweitert")


@router.post("/intake-folders/add")
def add_intake_folder(
    path: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    path = path.strip()
    if not path:
        return _redirect(error="Pfad darf nicht leer sein", tab="erweitert")

    settings = get_settings()
    folders = list(settings.intake_watched_folders)
    if path in folders:
        return _redirect(
            error="Dieser Ordner ist bereits als Scan-Ordner eingetragen", tab="erweitert"
        )
    folders.append(path)

    _apply({"INTAKE_WATCHED_FOLDERS": folders})
    return _redirect(success="Scan-Ordner hinzugefügt", tab="erweitert")


@router.post("/intake-folders/remove")
def remove_intake_folder(
    path: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    settings = get_settings()
    folders = [f for f in settings.intake_watched_folders if f != path]

    _apply({"INTAKE_WATCHED_FOLDERS": folders})
    return _redirect(success="Scan-Ordner entfernt", tab="erweitert")


@router.post("/mail")
def update_mail_settings(
    mail_host: str = Form(""),
    mail_port: int = Form(993),
    mail_username: str = Form(""),
    mail_password: str = Form(""),
    mail_mailbox: str = Form("INBOX"),
    mail_use_ssl: bool = Form(False),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    updates: dict = {
        "MAIL_PROVIDER": "imap",
        "MAIL_HOST": mail_host.strip(),
        "MAIL_PORT": mail_port,
        "MAIL_USERNAME": mail_username.strip(),
        "MAIL_MAILBOX": mail_mailbox.strip() or "INBOX",
        "MAIL_USE_SSL": mail_use_ssl,
    }
    # Leeres Passwortfeld = "unveraendert lassen" (siehe Modul-Docstring) -
    # nur bei tatsaechlicher Eingabe ueberschreiben.
    if mail_password.strip():
        updates["MAIL_PASSWORD"] = mail_password.strip()

    _apply(updates)
    return _redirect(success="E-Mail-Einstellungen gespeichert", tab="email")


@router.post("/mail/disconnect")
def disconnect_mail_account(
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Trennt das verbundene E-Mail-Konto (06.10., Owner-Direktive
    "SETTINGS -> E-MAIL", /connection-management: "Tokens sicher
    entfernen/widerrufen, soweit moeglich"). Es gibt hier keine OAuth-
    Tokens zu widerrufen (IMAP-Zugangsdaten, kein OAuth, siehe app/mail/
    factory.py) - "sicher entfernen" bedeutet daher: das gespeicherte
    Passwort und die uebrigen Verbindungsdaten werden ERSATZLOS aus der
    `.env` entfernt (`value=None`, siehe update_env_values), nicht nur
    geleert. `MAIL_PROVIDER` wird ebenfalls entfernt, damit
    `build_mail_provider()` beim naechsten Anwendungsstart wieder `None`
    liefert (derselbe sichere "nicht konfiguriert"-Zustand wie vor der
    ersten Einrichtung)."""
    _apply({
        "MAIL_PROVIDER": None,
        "MAIL_HOST": None,
        "MAIL_PORT": None,
        "MAIL_USERNAME": None,
        "MAIL_PASSWORD": None,
        "MAIL_MAILBOX": None,
        "MAIL_USE_SSL": None,
    })
    return _redirect(success="E-Mail-Konto getrennt", tab="email")


#: Erlaubte Synchronisationsintervalle (06.10., Owner-Direktive
#: "SETTINGS -> E-MAIL") - entspricht den in der UI angebotenen
#: Formulierungen ("Alle 1/5/15/30 Minuten").
_SUPPORTED_MAIL_POLL_INTERVALS = {60, 300, 900, 1800}


@router.post("/mail/auto-sync")
def toggle_mail_auto_sync(
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Schaltet die automatische Synchronisation um - ECHT gewirkt (siehe
    app/main.py::_run_periodic_mail_ingestion, liest diesen Wert jede
    Iteration frisch ein), kein kosmetischer Schalter."""
    settings = get_settings()
    _apply({"MAIL_AUTO_SYNC_ENABLED": not settings.mail_auto_sync_enabled})
    return _redirect(success="Einstellung gespeichert", tab="email")


@router.post("/mail/interval")
def update_mail_poll_interval(
    mail_poll_interval_seconds: int = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    if mail_poll_interval_seconds not in _SUPPORTED_MAIL_POLL_INTERVALS:
        return _redirect(error="Ungültiges Synchronisationsintervall", tab="email")
    _apply({"MAIL_POLL_INTERVAL_SECONDS": mail_poll_interval_seconds})
    return _redirect(success="Synchronisationsintervall gespeichert", tab="email")


@router.post("/retention")
def update_retention(
    retention_days: int = Form(0),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    if retention_days < 0:
        return _redirect(error="Aufbewahrungsfrist darf nicht negativ sein", tab="erweitert")

    _apply({"RETENTION_DAYS": retention_days})
    return _redirect(success="Aufbewahrungsfrist gespeichert", tab="erweitert")


# --- "Allgemein"-Tab: Anwendung/Benachrichtigungen/Daten & Speicher (06.10.,
# Owner-Direktive "LEXONO - EINSTELLUNGEN UI REBUILD") - dieselbe .env-
# basierte Persistenz wie alle obigen Einstellungen (KEIN neues Datenmodell/
# keine Migration, siehe app/config/settings.py fuer die ausfuehrliche,
# ehrliche Begruendung, welche dieser Werte bereits eine tiefere technische
# Wirkung haben und welche (noch) nicht). ---

#: Erlaubte Schluessel fuer den generischen Toggle-Endpunkt unten, je mit
#: dem zugehoerigen .env-Schluessel - eine Allowlist statt eines beliebigen
#: Formularfelds verhindert, dass ueber diesen Endpunkt versehentlich ein
#: VOELLIG ANDERER (nicht boolescher) Settings-Wert umgeschaltet werden
#: koennte.
_GENERAL_TOGGLE_ENV_KEYS = {
    "start_with_system": "START_WITH_SYSTEM",
    "auto_update_download_enabled": "AUTO_UPDATE_DOWNLOAD_ENABLED",
    "desktop_notifications_enabled": "DESKTOP_NOTIFICATIONS_ENABLED",
    "email_notifications_enabled": "EMAIL_NOTIFICATIONS_ENABLED",
}


@router.post("/general/toggle")
def toggle_general_setting(
    key: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    env_key = _GENERAL_TOGGLE_ENV_KEYS.get(key)
    if env_key is None:
        return _redirect(error="Unbekannte Einstellung", tab="allgemein")

    settings = get_settings()
    current_value = getattr(settings, key)
    _apply({env_key: not current_value})
    return _redirect(success="Einstellung gespeichert", tab="allgemein")


#: Einzige tatsaechlich unterstuetzte Sprache (siehe Settings.ui_language-
#: Kommentar) - eine Allowlist statt freier Texteingabe, damit dieser
#: Endpunkt nicht versehentlich einen nicht funktionierenden Sprachcode
#: persistiert.
_SUPPORTED_UI_LANGUAGES = {"de"}
#: Einziges tatsaechlich unterstuetztes Erscheinungsbild (siehe
#: Settings.ui_theme-Kommentar).
_SUPPORTED_UI_THEMES = {"light"}
#: Reale Auswahloptionen fuer die Frist-Erinnerung-Vorlaufzeit.
_SUPPORTED_DEADLINE_REMINDER_LEAD_DAYS = {1, 3, 7}


@router.post("/general/language")
def update_ui_language(
    ui_language: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    if ui_language not in _SUPPORTED_UI_LANGUAGES:
        return _redirect(error="Nicht unterstützte Sprache", tab="allgemein")
    _apply({"UI_LANGUAGE": ui_language})
    return _redirect(success="Sprache gespeichert", tab="allgemein")


@router.post("/general/theme")
def update_ui_theme(
    ui_theme: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    if ui_theme not in _SUPPORTED_UI_THEMES:
        return _redirect(error="Nicht unterstütztes Design", tab="allgemein")
    _apply({"UI_THEME": ui_theme})
    return _redirect(success="Design gespeichert", tab="allgemein")


#: Einzig tatsaechlich unterstuetzter Cloud-KI-Anbieter (siehe
#: Settings.cloud_ai_provider-Kommentar) - Gemini/ChatGPT erscheinen im
#: Formular als sichtbar deaktivierte "Bald verfuegbar"-Optionen (siehe
#: settings.html), koennen serverseitig ueber diese Allowlist aber auch
#: bei einer manipulierten Anfrage nicht ausgewaehlt werden.
_SUPPORTED_CLOUD_AI_PROVIDERS = {"claude"}


@router.post("/cloud-ai")
def update_cloud_ai_provider(
    cloud_ai_provider: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Cloud-KI-Anbieterauswahl (06.10., Owner-Direktive "SETTINGS -> KI &
    DATENSCHUTZ") - rein eine Routing-Praeferenz, KEIN API-Key/Credential
    (die bleiben ausschliesslich serverseitig in app/ai_providers/
    factory.py, niemals im Client). Aktuell gibt es nur einen tatsaechlich
    implementierten Anbieter (Claude ueber das Lexono-Gateway bzw. direkt
    Anthropic, siehe factory.py::build_writing_provider) - die Allowlist
    verhindert, dass hier ein (noch) nicht unterstuetzter Anbieter
    persistiert werden kann."""
    if cloud_ai_provider not in _SUPPORTED_CLOUD_AI_PROVIDERS:
        return _redirect(error="Dieser KI-Anbieter ist noch nicht verfügbar", tab="ki-datenschutz")
    _apply({"CLOUD_AI_PROVIDER": cloud_ai_provider})
    return _redirect(success="Cloud-KI-Anbieter gespeichert", tab="ki-datenschutz")


@router.post("/general/deadline-reminder")
def update_deadline_reminder_lead_days(
    deadline_reminder_lead_days: int = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    if deadline_reminder_lead_days not in _SUPPORTED_DEADLINE_REMINDER_LEAD_DAYS:
        return _redirect(error="Ungültige Vorlaufzeit", tab="allgemein")
    _apply({"DEADLINE_REMINDER_LEAD_DAYS": deadline_reminder_lead_days})
    return _redirect(success="Frist-Erinnerung gespeichert", tab="allgemein")


@router.post("/cache/clear")
def clear_cache(
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """"Cache leeren" (Referenz-Karte "Daten & Speicher") - loescht ALLE
    Dateien im bestehenden, gemeinsamen Download-Staging-Verzeichnis
    (app/web/download_staging.py::DOWNLOAD_STAGING_DIR), das Backup-/
    Akten-/Mandanten-Exporte bereits heute als einzigen temporaeren
    Ablageort nutzen (siehe dortigen Moduldocstring) - kein neuer,
    paralleler Cache-Mechanismus. Im Unterschied zu `cleanup_stale_files`
    (loescht nur Dateien aelter als 15 Minuten, automatisch vor jedem
    Export) loescht diese Aktion auf expliziten Nutzerwunsch ALLES
    sofort, unabhaengig vom Alter. Fehler beim Loeschen einzelner Dateien
    (z. B. ein gerade laufender Download) werden verschluckt - dieselbe
    Fehlertoleranz wie bei `cleanup_stale_files`."""
    if DOWNLOAD_STAGING_DIR.exists():
        for entry in DOWNLOAD_STAGING_DIR.iterdir():
            try:
                if entry.is_file():
                    entry.unlink()
            except OSError:
                continue
    return _redirect(success="Cache geleert", tab="allgemein")


# --- Kanzlei-Profil (Name/Anschrift/Kontakt, 20.08.) ---
#
# Bewusst als eigenes DB-Modell (app/models/firm_profile.py) statt in der
# .env geführt wie die übrigen Einstellungen oben - Briefkopf-Stammdaten
# sind Anzeigedaten für Exporte (siehe app/export/docx_export_service.py),
# keine Infrastruktur-/Zugangskonfiguration. Löst den bisherigen
# Platzhalter unter "/dashboard/account/profile" ab (siehe
# app/web/placeholder_router.py und templates/account_overview.html).


_SETTINGS_KANZLEI_URL = "/dashboard/settings?tab=kanzlei"


def _redirect_profile(
    success: str | None = None, error: str | None = None, *, dialog: str | None = None
) -> RedirectResponse:
    """`dialog` (06.10., Owner-Direktive "SETTINGS -> KANZLEI UX REFACTOR"):
    seit die Kanzlei-Stammdaten/Branding/Fachbereiche-Formulare in Dialogen
    statt permanent sichtbaren Karten leben, würde ein Fehler-Redirect ohne
    diesen Parameter den gerade bearbeiteten Dialog schließen und die
    Fehlermeldung faktisch "verlieren" (/error-handling: "Dialog bleibt
    geöffnet"). `dialog` benennt dasselbe Dialog-Element, das settings.html
    per kleinem JS-Snippet beim Laden automatisch wieder öffnet, wenn ein
    `error` vorliegt - identisches Grundmuster wie der bereits bestehende
    `tab`-Parameter in `_redirect` oben, nur eine Ebene tiefer (welcher
    Dialog INNERHALB des "Kanzlei"-Tabs offen bleiben soll)."""
    params = []
    if error:
        params.append(f"error={error}")
        if dialog:
            params.append(f"open_dialog={dialog}")
    if success:
        params.append(f"success={success}")
    if not params:
        return RedirectResponse(url=_SETTINGS_KANZLEI_URL, status_code=303)
    return RedirectResponse(url=f"{_SETTINGS_KANZLEI_URL}&" + "&".join(params), status_code=303)


@router.get("/profile")
def firm_profile_page(
    current_user: User = Depends(_require_admin),
) -> RedirectResponse:
    """Die eigentliche Anzeige lebt seit der Owner-Direktive "SETTINGS ->
    KANZLEI" (06.10.) im "Kanzlei"-Tab von /dashboard/settings (siehe
    settings_page unten + settings.html) statt auf dieser separaten Seite -
    GET bleibt nur noch ein Redirect dorthin (keine zweite, parallel zu
    pflegende Kanzlei-Profil-UI). Alle POST-Endpunkte unten bleiben
    unveraendert bestehen, nur die Redirect-Ziele zeigen jetzt auf die
    Settings-Seite (gleiches Muster wie app/web/users_router.py aus der
    vorherigen Owner-Direktive "SETTINGS -> BENUTZER")."""
    return RedirectResponse(url=_SETTINGS_KANZLEI_URL, status_code=303)


@router.post("/profile")
def update_firm_profile(
    firm_name: str = Form(...),
    legal_form: str = Form(""),
    street: str = Form(""),
    address_addition: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    website: str = Form(""),
    signatory_name: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    firm_name = firm_name.strip()
    if not firm_name:
        return _redirect_profile(error="Kanzleiname darf nicht leer sein", dialog="kanzleiinformationen")

    profile = get_firm_profile(db)
    profile.firm_name = firm_name
    profile.legal_form = legal_form.strip() or None
    profile.street = street.strip() or None
    profile.address_addition = address_addition.strip() or None
    profile.postal_code = postal_code.strip() or None
    profile.city = city.strip() or None
    profile.phone = phone.strip() or None
    profile.email = email.strip() or None
    profile.website = website.strip() or None
    profile.signatory_name = signatory_name.strip() or None
    profile.updated_by_actor = current_user.email
    db.commit()

    return _redirect_profile(success="Kanzlei-Profil gespeichert")


# --- Kanzlei-Defaults/-Einstellungen (06.10., Owner-Direktive "SETTINGS ->
# KANZLEI FINAL UI/UX") - echte, verdrahtete Werte statt der zuvor bewusst
# ausgelassenen Fake-Panels (siehe app/models/firm_profile.py fuer die
# jeweilige Begruendung PRO Feld: `default_document_format` bestimmt
# tatsaechlich die Reihenfolge der PDF-/DOCX-Export-Links in
# draft_detail.html, `auto_number_new_matters` + `matter_reference_prefix`
# werden echt in app/web/matters_router.py::create_matter_action
# ausgewertet, `timezone` ist - identisch zu `Settings.ui_language`/
# `ui_theme` - aktuell ehrlich auf GENAU einen gueltigen Wert validiert.
_ALLOWED_DOCUMENT_FORMATS = {"pdf", "docx"}
_ALLOWED_TIMEZONES = {"Europe/Berlin"}


@router.post("/profile/defaults")
def update_firm_defaults(
    matter_reference_prefix: str = Form(""),
    default_document_format: str = Form(...),
    timezone: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    if default_document_format not in _ALLOWED_DOCUMENT_FORMATS:
        return _redirect_profile(
            error="Ungültiges Standard-Dokumentformat", dialog="kanzlei-defaults"
        )
    if timezone not in _ALLOWED_TIMEZONES:
        return _redirect_profile(
            error="Diese Zeitzone wird aktuell nicht unterstützt", dialog="kanzlei-defaults"
        )

    profile = get_firm_profile(db)
    profile.matter_reference_prefix = matter_reference_prefix.strip() or None
    profile.default_document_format = default_document_format
    profile.timezone = timezone
    profile.updated_by_actor = current_user.email
    db.commit()

    return _redirect_profile(success="Kanzlei-Defaults gespeichert")


@router.post("/profile/kanzlei-settings")
def update_firm_settings(
    auto_number_new_matters: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Checkbox-Formular (18.09.-Muster wie bei anderen Toggle-Formularen
    dieser Seite): ein abgehaktes Kaestchen sendet "on", ein nicht
    abgehaktes ueberhaupt kein Feld - `Form("")` + Wahrheitswert-Pruefung
    statt `Form(False)` (FastAPI kann einen fehlenden Bool-Feldwert nicht
    direkt als `False` interpretieren)."""
    profile = get_firm_profile(db)
    profile.auto_number_new_matters = auto_number_new_matters == "on"
    profile.updated_by_actor = current_user.email
    db.commit()

    return _redirect_profile(success="Kanzlei-Einstellungen gespeichert")


# --- Kanzleifachprofil: fachliche Schwerpunkte (03.10., Owner-Direktive
# "KANZLEIFACHPROFIL UND JURISTISCHE WISSENSSTEUERUNG") ---
#
# Eigene, kleine Endpunkte statt Teil des obigen Stammdaten-Formulars -
# andere Validierungsregel (Abgleich gegen PRACTICE_AREA_SUGGESTIONS statt
# freier Texteingabe) und ein eigener Lese-/Schreibpfad
# (app/firm_profile/practice_areas.py), siehe dortigen Moduldocstring.
# Dieselbe Admin-Berechtigung wie der Rest dieser Seite - kein neues
# Rollenmuster.


@router.post("/profile/practice-areas")
def update_firm_practice_areas(
    practice_areas: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    try:
        set_practice_areas(db, practice_areas, actor=current_user.email)
    except InvalidPracticeAreaError as exc:
        return _redirect_profile(error=str(exc), dialog="fachliche-schwerpunkte")
    return _redirect_profile(success="Fachliche Schwerpunkte gespeichert")


@router.post("/profile/practice-areas/{practice_area}/remove")
def remove_firm_practice_area(
    practice_area: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    """Einzelner Entfernen-Weg fuer einen gespeicherten, aber nicht mehr
    in `PRACTICE_AREA_SUGGESTIONS` erkannten Wert (siehe settings.html,
    Tab "Kanzlei" - fuer diese Werte gibt es keine Checkbox mehr in der
    Hauptliste).
    Funktioniert unveraendert auch fuer einen aktuell gueltigen Wert."""
    remove_practice_area(db, practice_area, actor=current_user.email)
    return _redirect_profile(success="Fachlicher Schwerpunkt entfernt")


# --- Logo & Unterschrift (20.08., "vollwertige Briefkopf- und Signatur-
# Verwaltung") ---
#
# Eigene, kleine Upload-Endpunkte statt Teil des obigen Stammdaten-Formulars
# - Bild-Uploads brauchen multipart/form-data UND eigene Validierung
# (Dateityp/Größe), unabhängig vom Text-Formular speicherbar (ein Logo
# tauschen soll nicht erfordern, gleichzeitig alle Textfelder erneut
# abzusenden). Gleiches Validierungsmuster wie beim Schriftsatz-Generator
# (app/web/schriftsatz_router.py: _validate_uploads/_store_uploaded_document)
# - dort für Aktendokumente (PDF/DOCX), hier für Bilder (PNG/JPG), jeweils
# mit Endungs-Allowlist + Größenlimit VOR jedem Datei-/DB-Zugriff.
_ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
_MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB - Logos/Unterschriften sind klein


def _validate_image_upload(upload: UploadFile) -> str | None:
    if not upload.filename:
        return "Bitte eine Datei auswählen"
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(_ALLOWED_IMAGE_EXTENSIONS))
        return f"Dateityp '{suffix or '?'}' wird nicht unterstützt (erlaubt: {allowed})"
    return None


def _store_profile_image(upload: UploadFile, storage_dir: Path) -> str:
    """Speichert die Datei unter einem zufälligen Dateinamen (verhindert
    Path-Traversal/Kollisionen über den Original-Dateinamen, gleiches
    Prinzip wie IntakeService.ingest_file) und gibt den vollen Zielpfad
    zurück. Größe wird HIER geprüft (nicht vorab) - `UploadFile.size` ist
    bei manchen Clients nicht zuverlässig gesetzt."""
    content = upload.file.read()
    if len(content) > _MAX_IMAGE_SIZE_BYTES:
        raise ValueError(
            f"Datei überschreitet die maximale Größe von "
            f"{_MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB"
        )
    storage_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename).suffix.lower()
    destination_path = storage_dir / f"{uuid.uuid4()}{suffix}"
    destination_path.write_bytes(content)
    return str(destination_path)


def _delete_if_exists(path_str: str | None) -> None:
    if not path_str:
        return
    path = Path(path_str)
    if path.exists():
        path.unlink()


@router.post("/profile/logo")
def upload_firm_logo(
    logo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    error = _validate_image_upload(logo)
    if error:
        return _redirect_profile(error=error, dialog="branding")

    profile = get_firm_profile(db)
    try:
        new_path = _store_profile_image(
            logo, Path(get_settings().firm_profile_asset_storage_dir)
        )
    except ValueError as exc:
        return _redirect_profile(error=str(exc), dialog="branding")

    old_path = profile.logo_path
    profile.logo_path = new_path
    profile.logo_original_filename = logo.filename
    profile.updated_by_actor = current_user.email
    db.commit()
    if old_path != new_path:
        _delete_if_exists(old_path)

    return _redirect_profile(success="Logo hochgeladen")


@router.post("/profile/logo/remove")
def remove_firm_logo(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    profile = get_firm_profile(db)
    old_path = profile.logo_path
    profile.logo_path = None
    profile.logo_original_filename = None
    profile.updated_by_actor = current_user.email
    db.commit()
    _delete_if_exists(old_path)

    return _redirect_profile(success="Logo entfernt")


@router.get("/profile/logo-file")
def firm_logo_file(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> FileResponse:
    """ECHTER FUND (20.09., beim Bauen der Briefkopf-Vorschau im
    Entwurf-Editor entdeckt): diese Route war bisher `_require_admin`-
    gesperrt, obwohl `export_draft_docx`/`export_draft_pdf`
    (app/web/drafts_router.py) exakt dieselben Bilddaten laengst OHNE
    Rolleneinschraenkung (`require_login`) in jede heruntergeladene PDF-/
    DOCX-Datei einbetten - ein Anwalt ohne Admin-Rolle konnte die
    identischen Bytes also bereits ueber jeden Export erhalten, nur die
    direkte Bildansicht war ihm verwehrt. Kein echtes Datenschutz-/
    Sicherheitsmerkmal (Kanzlei-eigenes, auf jedem versendeten Schreiben
    ohnehin sichtbares Branding, keine Mandantendaten) - reine
    Inkonsistenz, jetzt an das bereits etablierte, korrekte Berechtigungs-
    niveau der Export-Routen angeglichen."""
    profile = get_firm_profile(db)
    if not profile.logo_path or not Path(profile.logo_path).exists():
        raise HTTPException(status_code=404, detail="Kein Logo hinterlegt")
    return FileResponse(profile.logo_path)


@router.post("/profile/signature")
def upload_firm_signature(
    signature: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    error = _validate_image_upload(signature)
    if error:
        return _redirect_profile(error=error, dialog="branding")

    profile = get_firm_profile(db)
    try:
        new_path = _store_profile_image(
            signature, Path(get_settings().firm_profile_asset_storage_dir)
        )
    except ValueError as exc:
        return _redirect_profile(error=str(exc), dialog="branding")

    old_path = profile.signature_path
    profile.signature_path = new_path
    profile.signature_original_filename = signature.filename
    profile.updated_by_actor = current_user.email
    db.commit()
    if old_path != new_path:
        _delete_if_exists(old_path)

    return _redirect_profile(success="Unterschrift hochgeladen")


@router.post("/profile/signature/remove")
def remove_firm_signature(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    profile = get_firm_profile(db)
    old_path = profile.signature_path
    profile.signature_path = None
    profile.signature_original_filename = None
    profile.updated_by_actor = current_user.email
    db.commit()
    _delete_if_exists(old_path)

    return _redirect_profile(success="Unterschrift entfernt")


@router.get("/profile/signature-file")
def firm_signature_file(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> FileResponse:
    """Gleiche Begruendung wie `firm_logo_file` oben."""
    profile = get_firm_profile(db)
    if not profile.signature_path or not Path(profile.signature_path).exists():
        raise HTTPException(status_code=404, detail="Keine Unterschrift hinterlegt")
    return FileResponse(profile.signature_path)
