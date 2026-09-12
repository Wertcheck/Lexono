"""Dashboard-Router für die Mandantendatenbank (20.08.) – löst den
bisherigen ehrlichen Platzhalter unter `/dashboard/clients` ab (siehe
app/web/placeholder_router.py).

CRM-Uebersicht mit Suche/Filter, "Mandant anlegen"-Modal, CSV-/Excel-
Massenimport (app/clients/import_service.py) und Detailansicht pro
Mandant (verknuepfte Akten/Nachrichten/Dokumente + DSGVO-Aktionen). Reine
UI-/Upload-Fassade vor app/clients/service.py bzw. app/clients/
export_service.py - keine Fachlogik hier im Router selbst (gleiches
Prinzip wie app/web/schriftsatz_router.py).

Rechte (siehe app/auth/permissions.py): Lesen fuer alle drei Rollen
(`require_login`, wie jede andere Dashboard-Liste). Anlegen/Bearbeiten/
Import/Archivieren = PERM_CLIENT_MANAGE (Admin+Anwalt, analog zur
bestehenden Einschraenkung "Mitarbeiter legt keine neuen Akten an").
Endgueltiges Loeschen = PERM_CLIENT_DELETE (nur Admin, irreversibel).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import PERM_CLIENT_DELETE, PERM_CLIENT_MANAGE, require_login, require_role
from app.clients.export_service import ClientExportService
from app.clients.import_service import ImportFileError, ImportResult, import_clients, parse_csv, parse_xlsx
from app.clients.service import (
    PRACTICE_AREA_SUGGESTIONS,
    ClientHasMattersError,
    ClientValidationError,
    archive_client,
    create_client,
    delete_client,
    list_clients,
    reactivate_client,
    update_client,
)
from app.db.session import get_db
from app.models import AuditEvent, Client, Document, Matter, Message, User
from app.web.download_staging import cleanup_stale_files, delete_after_send
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/clients", tags=["dashboard-clients"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Eigenes, temporäres Verzeichnis für Datenauszug-Downloads - gleiches
# Muster wie app/web/backup_router.py (_DOWNLOAD_STAGING_DIR), inkl.
# automatischer Löschung nach dem Download (app/web/download_staging.py).
_DOWNLOAD_STAGING_DIR = Path(tempfile.gettempdir()) / "lexono_dashboard_exports"

_MAX_IMPORT_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


def _active_users(db: Session) -> list[User]:
    return db.query(User).filter_by(is_active=True).order_by(User.email).all()


def _list_page_context(
    request: Request,
    db: Session,
    current_user: User,
    *,
    search: str,
    practice_area: str,
    responsible_user_id: str,
    status: str,
    import_result: ImportResult | None = None,
    import_error: str | None = None,
    error: str | None = None,
) -> dict:
    rows = list_clients(
        db,
        search=search or None,
        practice_area=practice_area or None,
        responsible_user_id=responsible_user_id or None,
        status=status,
    )
    return {
        "request": request,
        "active_nav": "Mandantendatenbank",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "rows": rows,
        "search": search,
        "practice_area": practice_area,
        "responsible_user_id": responsible_user_id,
        "status": status,
        "practice_areas": PRACTICE_AREA_SUGGESTIONS,
        "users": _active_users(db),
        "import_result": import_result,
        "import_error": import_error,
        "error": error,
    }


@router.get("", response_class=HTMLResponse)
def clients_list_page(
    request: Request,
    q: str = "",
    practice_area: str = "",
    responsible_user_id: str = "",
    status: str = "active",
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    context = _list_page_context(
        request,
        db,
        current_user,
        search=q,
        practice_area=practice_area,
        responsible_user_id=responsible_user_id,
        status=status,
        error=error,
    )
    return templates.TemplateResponse(request, "clients_list.html", context)


@router.post("/create")
def create_client_action(
    request: Request,
    name: str = Form(...),
    client_number: str = Form(...),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    practice_area: str = Form(""),
    responsible_user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLIENT_MANAGE)),
) -> RedirectResponse:
    try:
        client = create_client(
            db,
            name=name,
            client_number=client_number,
            contact_email=contact_email,
            contact_phone=contact_phone,
            practice_area=practice_area,
            responsible_user_id=responsible_user_id or None,
            actor=current_user.email,
        )
    except ClientValidationError as exc:
        return RedirectResponse(url=f"/dashboard/clients?error={exc}", status_code=303)
    return RedirectResponse(url=f"/dashboard/clients/{client.id}", status_code=303)


@router.post("/import", response_class=HTMLResponse)
def import_clients_action(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLIENT_MANAGE)),
) -> HTMLResponse:
    """Rendert die Liste direkt neu (kein Redirect) - ein Import-Ergebnis
    (Anzahl angelegt + Zeile-fuer-Zeile-Fehlerliste) laesst sich nicht
    sinnvoll in einen Redirect-Query-Parameter packen (siehe
    app/clients/import_service.py: ImportResult kann beliebig viele
    Zeilenfehler enthalten)."""
    import_result: ImportResult | None = None
    import_error: str | None = None

    suffix = Path(file.filename or "").suffix.lower()
    content = file.file.read()
    if len(content) > _MAX_IMPORT_FILE_SIZE_BYTES:
        import_error = (
            f"Datei überschreitet die maximale Größe von "
            f"{_MAX_IMPORT_FILE_SIZE_BYTES // (1024 * 1024)} MB."
        )
    else:
        try:
            if suffix == ".csv":
                rows = parse_csv(content)
            elif suffix in (".xlsx", ".xlsm"):
                rows = parse_xlsx(content)
            else:
                raise ImportFileError(
                    f"Dateityp '{suffix or '?'}' wird nicht unterstützt (erlaubt: .csv, .xlsx)."
                )
            import_result = import_clients(db, rows, actor=current_user.email)
        except ImportFileError as exc:
            import_error = str(exc)

    context = _list_page_context(
        request,
        db,
        current_user,
        search="",
        practice_area="",
        responsible_user_id="",
        status="active",
        import_result=import_result,
        import_error=import_error,
    )
    return templates.TemplateResponse(request, "clients_list.html", context)


def _client_detail_context(
    request: Request, db: Session, current_user: User, client: Client, *, error: str | None = None
) -> dict:
    matters = (
        db.query(Matter)
        .filter(Matter.client_id == client.id)
        .order_by(Matter.updated_at.desc())
        .all()
    )
    matter_ids = [m.id for m in matters]
    messages = (
        db.query(Message)
        .filter(Message.matter_id.in_(matter_ids))
        .order_by(Message.created_at.desc())
        .limit(50)
        .all()
        if matter_ids
        else []
    )
    documents = (
        db.query(Document)
        .filter(Document.matter_id.in_(matter_ids))
        .order_by(Document.created_at.desc())
        .limit(50)
        .all()
        if matter_ids
        else []
    )
    open_matters = [m for m in matters if m.status == "open"]
    return {
        "request": request,
        "active_nav": "Mandantendatenbank",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "client": client,
        "matters": matters,
        "messages": messages,
        "documents": documents,
        "practice_areas": PRACTICE_AREA_SUGGESTIONS,
        "users": _active_users(db),
        # Fuer die "Mit lokaler KI arbeiten"-Kachel (siehe Modul-/Template-
        # Docstring): bei genau EINER offenen Akte direkt verlinkbar, sonst
        # muss zwischen mehreren Akten gewaehlt werden (Aktenisolation -
        # ein KI-Aufruf bezieht sich immer auf genau eine Akte).
        "single_open_matter_id": open_matters[0].id if len(open_matters) == 1 else None,
        "error": error,
    }


@router.get("/{client_id}", response_class=HTMLResponse)
def client_detail_page(
    client_id: str,
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    client = get_or_404(db, Client, client_id, "Mandant")
    context = _client_detail_context(request, db, current_user, client, error=error)
    return templates.TemplateResponse(request, "client_detail.html", context)


@router.post("/{client_id}/update")
def update_client_action(
    client_id: str,
    name: str = Form(...),
    client_number: str = Form(...),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    practice_area: str = Form(""),
    responsible_user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLIENT_MANAGE)),
) -> RedirectResponse:
    client = get_or_404(db, Client, client_id, "Mandant")
    try:
        update_client(
            db,
            client,
            name=name,
            client_number=client_number,
            contact_email=contact_email,
            contact_phone=contact_phone,
            practice_area=practice_area,
            responsible_user_id=responsible_user_id or None,
            actor=current_user.email,
        )
    except ClientValidationError as exc:
        return RedirectResponse(
            url=f"/dashboard/clients/{client_id}?error={exc}", status_code=303
        )
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=303)


@router.post("/{client_id}/archive")
def archive_client_action(
    client_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLIENT_MANAGE)),
) -> RedirectResponse:
    client = get_or_404(db, Client, client_id, "Mandant")
    archive_client(db, client, actor=current_user.email)
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=303)


@router.post("/{client_id}/reactivate")
def reactivate_client_action(
    client_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLIENT_MANAGE)),
) -> RedirectResponse:
    client = get_or_404(db, Client, client_id, "Mandant")
    reactivate_client(db, client, actor=current_user.email)
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=303)


@router.post("/{client_id}/delete")
def delete_client_action(
    client_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLIENT_DELETE)),
) -> RedirectResponse:
    client = get_or_404(db, Client, client_id, "Mandant")
    try:
        delete_client(db, client, actor=current_user.email)
    except ClientHasMattersError as exc:
        return RedirectResponse(
            url=f"/dashboard/clients/{client_id}?error={exc}", status_code=303
        )
    return RedirectResponse(url="/dashboard/clients", status_code=303)


@router.post("/{client_id}/export")
def export_client_action(
    client_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLIENT_MANAGE)),
) -> FileResponse:
    client = get_or_404(db, Client, client_id, "Mandant")
    cleanup_stale_files(_DOWNLOAD_STAGING_DIR)
    service = ClientExportService()
    archive_path = service.export_client(client.id, db, _DOWNLOAD_STAGING_DIR)
    db.add(
        AuditEvent(
            entity_type="Client",
            entity_id=client.id,
            event_type="client_data_export",
            actor=current_user.email,
            details=f"DSGVO-Datenauszug erstellt: {client.name}",
        )
    )
    db.commit()
    return FileResponse(
        archive_path,
        filename=archive_path.name,
        media_type="application/zip",
        background=delete_after_send(archive_path),
    )
