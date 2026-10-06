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

import hashlib
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
    CLIENT_TYPE_SUGGESTIONS,
    PRACTICE_AREA_SUGGESTIONS,
    ClientHasMattersError,
    ClientValidationError,
    archive_client,
    count_clients,
    create_client,
    delete_client,
    list_clients,
    reactivate_client,
    update_client,
)
from app.db.session import get_db
from app.documents.rendering import document_file_size_label
from app.documents.shell_icons import get_shell_icon_data_uri
from app.models import AuditEvent, Client, Deadline, Document, Matter, Message, Note, Task, User
from app.web.download_staging import (
    DOWNLOAD_STAGING_DIR as _DOWNLOAD_STAGING_DIR,
    cleanup_stale_files,
    delete_after_send,
)
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/clients", tags=["dashboard-clients"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Temporäres Verzeichnis für Datenauszug-Downloads - gleicher Ablageort wie
# app/web/backup_router.py, zentral in app/web/download_staging.py definiert
# (06.10., vorher hier unabhängig dupliziert), inkl. automatischer Löschung
# nach dem Download.

_MAX_IMPORT_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


_ALLOWED_PAGE_SIZES = (10, 20, 50)

# Deterministische Avatar-Darstellung (03.10., Owner-Direktive
# "REFERENZGETREUE MANDANTENUEBERSICHT" §3.4: "Initialen duerfen
# deterministisch aus den echten Mandantennamen gebildet werden. Die
# Avatarfarbe darf ebenfalls deterministisch sein, sofern sie keine
# zusaetzliche Persistenz erfordert.") - reine Anzeigelogik, NICHTS wird
# in der DB gespeichert, bei jedem Seitenaufruf neu aus dem echten Namen/
# der echten ID berechnet.
_AVATAR_COLOR_COUNT = 8


def _client_initials(name: str) -> str:
    """Reale Mandantennamen sind uneinheitlich formatiert (gegen die echte
    Produktions-DB geprueft): "Nachname, Vorname" (z. B. "Müller, Anna"),
    "Vorname Nachname" (z. B. "Sabine Schmidt") UND Firmennamen mit
    mehreren Woertern (z. B. "Becker GmbH", "Handwerk Schmidt & Söhne").
    Bei einem Komma wird davon ausgegangen, dass "Nachname, Vorname"
    vorliegt (Referenzabgleich bestaetigt: "Müller, Anna" -> Initialen
    "AM", also Vorname-Initiale ZUERST) - sonst werden die ersten beiden
    durch Leerzeichen getrennten Woerter verwendet (deckt sowohl
    "Vorname Nachname" als auch Firmennamen ab)."""
    name = (name or "").strip()
    if not name:
        return "–"
    if "," in name:
        nachname, _, vorname = name.partition(",")
        vorname = vorname.strip()
        nachname = nachname.strip()
        if vorname and nachname:
            return (vorname[0] + nachname[0]).upper()
    tokens = [t for t in name.split() if t]
    if len(tokens) >= 2:
        return (tokens[0][0] + tokens[1][0]).upper()
    if tokens:
        return tokens[0][:2].upper()
    return "–"


def _client_avatar_color_index(client_id: str) -> int:
    """Stabil ueber Prozessneustarts hinweg (anders als Pythons
    `hash()` fuer Strings, das pro Prozess zufaellig gesalzen ist) -
    `hashlib` liefert denselben Wert fuer dieselbe ID auf jedem Rechner
    und bei jedem Seitenaufruf, ohne irgendetwas zu speichern."""
    digest = hashlib.md5(client_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % _AVATAR_COLOR_COUNT


#: Mandanten-Detailseite (03.10., Owner-Direktive "INDIVIDUELLE
#: MANDANTENDETAILSEITE", Referenzabgleich `30_mandant_detail.png` §5.7):
#: die Referenz zeigt eine Dateigroesse je Dokument ("1,2 MB") - es gibt
#: dafuer KEIN gespeichertes Feld auf `Document` (gegengeprueft,
#: app/models/document.py). Statt dafuer eine neue Spalte/Migration
#: einzufuehren (keine nachgewiesene technische Notwendigkeit - die reale
#: Datei liegt bereits vollstaendig am Dateisystem vor), wird die Groesse
#: direkt von der tatsaechlichen Datei gelesen. Die Formatierung selbst
#: lebt seit 05.10. (Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS"
#: §19/§20) in app/documents/rendering.py::document_file_size_label - die
#: Chat-Dokumentvorschau braucht dieselbe Logik ein zweites Mal, daher
#: dorthin verschoben statt hier dupliziert zu bleiben.
_document_file_size_label = document_file_size_label


def _document_display_rows(documents: list[Document]) -> list[dict]:
    """Kombiniert jedes `Document` mit seiner echten Dateigroesse und dem
    nativen Windows-Shell-Icon (siehe app/documents/shell_icons.py) zu
    einem fertigen Anzeige-Dict - einmal pro Seitenaufruf berechnet
    (Icon-Extraktion selbst ist bereits pro Dateiendung gecacht), nicht
    mehrfach im Template."""
    rows = []
    for document in documents:
        filename = document.original_filename or document.id
        rows.append(
            {
                "document": document,
                "size_label": _document_file_size_label(document.file_path),
                "shell_icon_uri": get_shell_icon_data_uri(filename),
            }
        )
    return rows


def _build_page_numbers(page: int, total_pages: int) -> list[int | str]:
    """Identische Logik wie app/web/matters_router.py::_build_page_numbers
    (kompakte Seitenzahl-Liste mit "…"-Ellipsen) - bewusst hier dupliziert
    statt in ein gemeinsames Modul extrahiert, um das bereits verifizierte
    Verhalten der Akten-Seite in dieser Aufgabe nicht anzutasten (kleinstes
    robustes Risiko, siehe Owner-Direktive §Phase A Regel 5)."""
    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    window = {1, total_pages, page - 1, page, page + 1}
    window = {p for p in window if 1 <= p <= total_pages}
    result: list[int | str] = []
    for p in range(1, total_pages + 1):
        if p in window:
            result.append(p)
        elif result and result[-1] != "…":
            result.append("…")
    return result


def _active_users(db: Session) -> list[User]:
    return db.query(User).filter_by(is_active=True).order_by(User.email).all()


def _list_page_context(
    request: Request,
    db: Session,
    current_user: User,
    *,
    search: str,
    practice_area: str,
    client_type: str = "",
    responsible_user_id: str,
    status: str,
    sort: str = "updated_desc",
    page: int = 1,
    page_size: int = 10,
    import_result: ImportResult | None = None,
    import_error: str | None = None,
    error: str | None = None,
) -> dict:
    if sort not in ("updated_desc", "name_asc", "name_desc"):
        sort = "updated_desc"
    if page_size not in _ALLOWED_PAGE_SIZES:
        page_size = 10
    if page < 1:
        page = 1

    total_count = count_clients(
        db,
        search=search or None,
        practice_area=practice_area or None,
        client_type=client_type or None,
        responsible_user_id=responsible_user_id or None,
        status=status,
    )
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)

    rows = list_clients(
        db,
        search=search or None,
        practice_area=practice_area or None,
        client_type=client_type or None,
        responsible_user_id=responsible_user_id or None,
        status=status,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return {
        "request": request,
        "active_nav": "Mandanten",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "rows": rows,
        "search": search,
        "practice_area": practice_area,
        "client_type": client_type,
        "client_types": CLIENT_TYPE_SUGGESTIONS,
        "responsible_user_id": responsible_user_id,
        "status": status,
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_numbers": _build_page_numbers(page, total_pages),
        "practice_areas": PRACTICE_AREA_SUGGESTIONS,
        "users": _active_users(db),
        "import_result": import_result,
        "import_error": import_error,
        "error": error,
        "client_initials": _client_initials,
        "client_avatar_color_index": _client_avatar_color_index,
    }


@router.get("", response_class=HTMLResponse)
def clients_list_page(
    request: Request,
    q: str = "",
    practice_area: str = "",
    client_type: str = "",
    responsible_user_id: str = "",
    status: str = "all",
    sort: str = "updated_desc",
    page: int = 1,
    page_size: int = 10,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """`status`-Standard jetzt "all" statt "active" (03.10., Owner-
    Direktive "REFERENZGETREUE MANDANTENUEBERSICHT", echter
    Verhaltensabgleich gegen `29_mandanten_uebersicht.png`): die Referenz
    zeigt in der Standardansicht sowohl "Aktiv"- als auch "Inaktiv"-
    Mandanten gleichzeitig (siehe Zeile "Schulz, Lisa") - eine bewusste,
    dokumentierte Verhaltensaenderung (siehe DECISIONS.md), kein
    Versehen."""
    context = _list_page_context(
        request,
        db,
        current_user,
        search=q,
        practice_area=practice_area,
        client_type=client_type,
        responsible_user_id=responsible_user_id,
        status=status,
        sort=sort,
        page=page,
        page_size=page_size,
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
    client_type: str = Form(""),
    city: str = Form(""),
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
            client_type=client_type,
            city=city,
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
        status="all",
        import_result=import_result,
        import_error=import_error,
    )
    return templates.TemplateResponse(request, "clients_list.html", context)


def _client_detail_context(
    request: Request, db: Session, current_user: User, client: Client, *, error: str | None = None
) -> dict:
    matters = (
        db.query(Matter)
        .filter(Matter.client_id == client.id, Matter.deleted_at.is_(None))
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
        .filter(Document.matter_id.in_(matter_ids), Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
        .limit(50)
        .all()
        if matter_ids
        else []
    )
    open_matters = [m for m in matters if m.status == "open"]
    # "Aufgaben & Fristen" (19.09., UI/UX-Referenzabgleich
    # "30_mandant_detail.png"): ein Mandant traegt selbst keine Aufgaben/
    # Fristen (die haengen strukturell an einer Akte) - hier ueber ALLE
    # Akten dieses Mandanten aggregiert, dieselben real existierenden
    # Modelle wie auf der Akte-Detailseite, keine neue Datenquelle.
    tasks = (
        db.query(Task)
        .filter(Task.matter_id.in_(matter_ids))
        .order_by(Task.created_at.desc())
        .all()
        if matter_ids
        else []
    )
    deadlines = (
        db.query(Deadline)
        .filter(Deadline.matter_id.in_(matter_ids))
        .order_by(Deadline.due_date.asc())
        .all()
        if matter_ids
        else []
    )
    # Notizen (19.09., UI/UX-Referenzabgleich, siehe app/models/note.py) -
    # echte Mandanten-Notizen, nicht ueber Akten aggregiert.
    notes = (
        db.query(Note)
        .filter(Note.client_id == client.id)
        .order_by(Note.created_at.desc())
        .all()
    )
    return {
        "request": request,
        "active_nav": "Mandanten",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "client": client,
        "matters": matters,
        "messages": messages,
        "documents": documents,
        # Fertige Anzeige-Zeilen (echte Dateigroesse + natives Shell-Icon,
        # siehe _document_display_rows) fuer die neue Dokumentenkarte/-tab
        # (03.10., Referenzabgleich `30_mandant_detail.png` §5.7/§5.8) -
        # `documents` selbst bleibt unveraendert (Rueckwaertskompatibilitaet
        # zu `documents|length` in den bestehenden Tab-Ueberschriften).
        "document_rows": _document_display_rows(documents),
        "tasks": tasks,
        "deadlines": deadlines,
        "notes": notes,
        "practice_areas": PRACTICE_AREA_SUGGESTIONS,
        "client_types": CLIENT_TYPE_SUGGESTIONS,
        "users": _active_users(db),
        # Fuer die neue Mandantenkopf-/Stammdatenkarte (03.10.,
        # Referenzabgleich `30_mandant_detail.png` §3/§5.2/§5.4) - exakt
        # dieselben, bereits auf der Mandantenuebersicht verifizierten
        # deterministischen Avatar-Funktionen, hier fuer den groesseren
        # Kopf-Avatar wiederverwendet statt einer zweiten Implementierung.
        "client_initials": _client_initials,
        "client_avatar_color_index": _client_avatar_color_index,
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
    client_type: str = Form(""),
    city: str = Form(""),
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
            client_type=client_type,
            city=city,
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
