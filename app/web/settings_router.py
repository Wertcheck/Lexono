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
from sqlalchemy.orm import Session

from app.auth.permissions import PermissionDeniedError, require_login, require_role
from app.config import get_settings
from app.db.session import get_db
from app.firm_profile import get_firm_profile
from app.models import User
from app.setup.env_writer import update_env_values
from app.setup.paths import resolve_data_dir
from app.web.template_paths import TEMPLATES_DIR

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


def _redirect(success: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        return RedirectResponse(url=f"/dashboard/settings?error={error}", status_code=303)
    if success:
        return RedirectResponse(url=f"/dashboard/settings?success={success}", status_code=303)
    return RedirectResponse(url="/dashboard/settings", status_code=303)


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    current_user: User = Depends(_require_admin),
) -> HTMLResponse:
    settings = get_settings()
    # Erreichbarkeit wird bewusst NICHT synchron bei jedem Seitenaufruf
    # geprueft (kein automatischer Netzwerkaufruf, siehe app/system_health/
    # service.py-Docstring) - die Seite bindet stattdessen denselben
    # Admin-Klick-Endpunkt wie die Systemstatus-Seite ein
    # (POST /dashboard/monitoring/check-api).
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Einstellungen",
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "success": success,
        "error": error,
        "intake_watched_folders": settings.intake_watched_folders,
        "mail_host": settings.mail_host,
        "mail_port": settings.mail_port,
        "mail_username": settings.mail_username,
        "mail_mailbox": settings.mail_mailbox,
        "mail_use_ssl": settings.mail_use_ssl,
        "mail_configured": settings.mail_password is not None,
        "retention_days": settings.retention_days,
        "anthropic_api_key_configured": settings.anthropic_api_key is not None,
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
    wie bei jeder anderen falschen Konfiguration."""
    model = ollama_model.strip()
    base_url = ollama_base_url.strip()
    if not model:
        return _redirect(error="Modell-Tag darf nicht leer sein")
    if not base_url:
        return _redirect(error="Ollama-Basis-URL darf nicht leer sein")

    _apply({"OLLAMA_MODEL": model, "OLLAMA_BASE_URL": base_url})
    return _redirect(success="Lokale-KI-Einstellungen gespeichert")


@router.post("/intake-folders/add")
def add_intake_folder(
    path: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    path = path.strip()
    if not path:
        return _redirect(error="Pfad darf nicht leer sein")

    settings = get_settings()
    folders = list(settings.intake_watched_folders)
    if path in folders:
        return _redirect(error="Dieser Ordner ist bereits als Scan-Ordner eingetragen")
    folders.append(path)

    _apply({"INTAKE_WATCHED_FOLDERS": folders})
    return _redirect(success="Scan-Ordner hinzugefügt")


@router.post("/intake-folders/remove")
def remove_intake_folder(
    path: str = Form(...),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    settings = get_settings()
    folders = [f for f in settings.intake_watched_folders if f != path]

    _apply({"INTAKE_WATCHED_FOLDERS": folders})
    return _redirect(success="Scan-Ordner entfernt")


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
    return _redirect(success="E-Mail-Einstellungen gespeichert")


@router.post("/retention")
def update_retention(
    retention_days: int = Form(0),
    current_user: User = Depends(require_role("admin")),
) -> RedirectResponse:
    if retention_days < 0:
        return _redirect(error="Aufbewahrungsfrist darf nicht negativ sein")

    _apply({"RETENTION_DAYS": retention_days})
    return _redirect(success="Aufbewahrungsfrist gespeichert")


# --- Kanzlei-Profil (Name/Anschrift/Kontakt, 20.08.) ---
#
# Bewusst als eigenes DB-Modell (app/models/firm_profile.py) statt in der
# .env geführt wie die übrigen Einstellungen oben - Briefkopf-Stammdaten
# sind Anzeigedaten für Exporte (siehe app/export/docx_export_service.py),
# keine Infrastruktur-/Zugangskonfiguration. Löst den bisherigen
# Platzhalter unter "/dashboard/account/profile" ab (siehe
# app/web/placeholder_router.py und templates/account_overview.html).


def _redirect_profile(success: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        return RedirectResponse(url=f"/dashboard/settings/profile?error={error}", status_code=303)
    if success:
        return RedirectResponse(url=f"/dashboard/settings/profile?success={success}", status_code=303)
    return RedirectResponse(url="/dashboard/settings/profile", status_code=303)


@router.get("/profile", response_class=HTMLResponse)
def firm_profile_page(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
) -> HTMLResponse:
    profile = get_firm_profile(db)
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Kanzlei-Profil & Briefkopf",
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "success": success,
        "error": error,
        "profile": profile,
    }
    return templates.TemplateResponse(request, "firm_profile.html", context)


@router.post("/profile")
def update_firm_profile(
    firm_name: str = Form(...),
    street: str = Form(""),
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
        return _redirect_profile(error="Kanzleiname darf nicht leer sein")

    profile = get_firm_profile(db)
    profile.firm_name = firm_name
    profile.street = street.strip() or None
    profile.postal_code = postal_code.strip() or None
    profile.city = city.strip() or None
    profile.phone = phone.strip() or None
    profile.email = email.strip() or None
    profile.website = website.strip() or None
    profile.signatory_name = signatory_name.strip() or None
    profile.updated_by_actor = current_user.email
    db.commit()

    return _redirect_profile(success="Kanzlei-Profil gespeichert")


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
        return _redirect_profile(error=error)

    profile = get_firm_profile(db)
    try:
        new_path = _store_profile_image(
            logo, Path(get_settings().firm_profile_asset_storage_dir)
        )
    except ValueError as exc:
        return _redirect_profile(error=str(exc))

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
        return _redirect_profile(error=error)

    profile = get_firm_profile(db)
    try:
        new_path = _store_profile_image(
            signature, Path(get_settings().firm_profile_asset_storage_dir)
        )
    except ValueError as exc:
        return _redirect_profile(error=str(exc))

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
