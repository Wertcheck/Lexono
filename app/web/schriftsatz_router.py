"""Schriftsatz-Generator (20.08.) – löst den bisherigen ehrlichen
Platzhalter unter `/dashboard/tools/schriftsatz` ab.

Bewusst KEINE neue KI-/Privacy-Architektur: nutzt exakt denselben Weg wie
jede andere Entwurfserstellung im Dashboard (`get_drafting_service()` ->
`DraftingService.create_draft`, siehe app/web/drafts_router.py), inkl.
desselben Privacy-Gateways und Claude-Providers (app/ai_providers/factory.py).
Dieser Router ist reine UI-/Upload-Fassade davor.

Zwei Besonderheiten gegenüber den bestehenden Draft-Aktionen:
1. `matter_id` ist optional - fehlt sie, wird VOR dem eigentlichen
   `create_draft`-Aufruf über `create_quick_matter`
   (app/drafting/quick_matter.py) eine neue Akte angelegt, DAMIT die
   Drag&Drop-Dokumente unten schon eine `matter_id` haben, wenn sie als
   `Document` gespeichert werden (dieselbe Hilfsfunktion, die auch
   `DraftingService.create_draft(matter_id=None)` intern nutzt).
2. Hochgeladene Dateien (PDF/DOCX) werden GENAUSO behandelt wie ein Fund im
   überwachten Scan-Ordner: unter `settings.schriftsatz_upload_storage_dir`
   gespeichert, als `Document` angelegt und über
   `DocumentProcessingService.process_document` (app/documents/service.py -
   dieselbe Instanz-Bauweise wie app/errors/service.py:237) extrahiert/OCR-
   verarbeitet, BEVOR `create_draft` aufgerufen wird - nur so liest
   `RuleBasedLocalAIProvider.prepare_draft_context` ihren Inhalt
   (`Document.extracted_text` der Akte, siehe app/ai_providers/
   local_ai_provider.py).

Zweck ist FEST `"formulate_draft"` (aus `ALLOWED_PURPOSES`, siehe
app/privacy/security_check.py) - kein Nutzer-Freitextfeld dafür, exakt
dasselbe Prinzip wie `_REGENERATE_PURPOSE` in app/web/drafts_router.py.
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.permissions import PERM_CLAUDE_CALL, require_login, require_role
from app.config import get_settings
from app.db.session import get_db
from app.documents.extraction import SUPPORTED_TEXT_EXTENSIONS
from app.documents.service import DocumentProcessingService
from app.drafting.quick_matter import create_quick_matter
from app.ingestion.stability import compute_sha256
from app.models import Client, Document, Matter, User
from app.privacy.api_logger import friendly_block_message
from app.web.service_factory import WritingProviderNotConfiguredError, get_drafting_service
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/tools/schriftsatz", tags=["dashboard-schriftsatz"])

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Fest verdrahteter Schreibauftrag (siehe Moduldocstring) - kein
# Nutzer-Freitextfeld, um versehentlich einen nicht erlaubten Zweck zu
# erzeugen (ALLOWED_PURPOSES, app/privacy/security_check.py).
_PURPOSE = "formulate_draft"

# Nur Formate, die app/documents/extraction.py tatsächlich verarbeiten kann
# (".txt" bewusst ausgenommen - fuer diesen Generator sind nur Akten-
# Dokumente/Vorlagen relevant, kein Klartext-Upload). Ein klassisches
# ".doc" (Word 97-2003) wird bewusst mit einer klaren Fehlermeldung
# abgelehnt statt still als "unsupported_format" zu enden.
_ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx"}
assert _ALLOWED_UPLOAD_EXTENSIONS <= SUPPORTED_TEXT_EXTENSIONS

_MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB pro Datei
_MAX_UPLOAD_FILES = 10


def _redirect_with_error(message: str) -> RedirectResponse:
    return RedirectResponse(url=f"/dashboard/tools/schriftsatz?error={message}", status_code=303)


@router.get("", response_class=HTMLResponse)
def schriftsatz_generator_page(
    request: Request,
    error: str | None = None,
    matter_id: str | None = None,
    client_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    open_matters = (
        db.query(Matter).filter(Matter.status == "open").order_by(Matter.updated_at.desc()).all()
    )
    # Vorauswahl der Akte per Query-Param (20.08.) - genutzt von der
    # Mandantendatenbank ("Mit lokaler KI arbeiten", siehe
    # app/web/clients_router.py/templates/client_detail.html): NUR die
    # `matter_id` (keine Mandanten-Personendaten) wandert durch die URL,
    # die eigentlichen Daten werden serverseitig anhand dieser ID geladen -
    # nichts Personenbezogenes landet in Query-String/Browser-Verlauf.
    # Ungueltige/fremde IDs werden stillschweigend ignoriert (einfach keine
    # Vorauswahl) statt eines Fehlers - kein sicherheitsrelevanter Fall.
    preselected_matter_id = (
        matter_id if matter_id and any(m.id == matter_id for m in open_matters) else None
    )
    # `client_id` (17.09., ECHTER FUND - siehe create_quick_matter fuer die
    # volle Begruendung): identisches Prinzip wie `matter_id` oben - nur die
    # ID wandert durch die URL, der Mandantenname wird serverseitig anhand
    # dieser ID geladen. Nur genutzt, wenn KEINE Akte vorausgewaehlt ist
    # (ein Mandant OHNE Akte, sonst waere `matter_id` bereits der praezisere
    # Weg). Ungueltige/fremde ID wird ebenso stillschweigend ignoriert.
    preselected_client = (
        db.get(Client, client_id) if client_id and not preselected_matter_id else None
    )
    context = {
        "request": request,
        "active_nav": "Schriftsatz-Generator",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "open_matters": open_matters,
        "preselected_matter_id": preselected_matter_id,
        "preselected_client": preselected_client,
        "error": error,
        "allowed_upload_extensions": sorted(_ALLOWED_UPLOAD_EXTENSIONS),
        "max_upload_files": _MAX_UPLOAD_FILES,
    }
    return templates.TemplateResponse(request, "schriftsatz_generator.html", context)


def _validate_uploads(files: list[UploadFile]) -> str | None:
    """Gibt eine Fehlermeldung zurück (oder None), OHNE bereits etwas zu
    speichern - Validierung muss VOR jedem Dateisystem-/DB-Zugriff
    abgeschlossen sein."""
    if len(files) > _MAX_UPLOAD_FILES:
        return f"Höchstens {_MAX_UPLOAD_FILES} Dateien pro Vorgang erlaubt."
    for upload in files:
        if not upload.filename:
            continue
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in _ALLOWED_UPLOAD_EXTENSIONS:
            allowed = ", ".join(sorted(_ALLOWED_UPLOAD_EXTENSIONS))
            return f"Dateityp '{suffix or '?'}' wird nicht unterstützt (erlaubt: {allowed})."
    return None


def _store_uploaded_document(
    upload: UploadFile, matter_id: str, storage_dir: Path, db: Session, *, actor: str
) -> Document | None:
    """Speichert EINE hochgeladene Datei sicher unter `storage_dir` und legt
    den zugehörigen `Document`-Datensatz an. Gibt None zurück, wenn das
    Feld leer/übersprungen war (z. B. ein leeres Drag&Drop-Formular-Feld) -
    kein Fehlerfall.

    Größenprüfung erfolgt HIER (nicht in `_validate_uploads`), weil erst
    hier tatsächlich gelesen wird - `UploadFile.size` ist bei manchen
    Clients nicht zuverlässig vorab gesetzt."""
    if not upload.filename:
        return None

    content = upload.file.read()
    if len(content) > _MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(
            f"Datei '{upload.filename}' überschreitet die maximale Größe von "
            f"{_MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
        )

    storage_dir.mkdir(parents=True, exist_ok=True)
    # SICHERHEITSKRITISCH (Path-Traversal, real verifiziert): `upload.filename`
    # stammt direkt aus dem vom Client gesendeten `Content-Disposition`-Header
    # und kann Verzeichnistrenner/".."-Segmente enthalten (z. B.
    # "../../../evil.txt") - ungefiltert in den Zielpfad eingebaut, wuerde die
    # Datei ausserhalb von `storage_dir` geschrieben (Path.__truediv__
    # interpretiert eingebettete "/"/"\\" im String als weitere
    # Pfadsegmente). `.name` behaelt nur den letzten Pfadbestandteil - exakt
    # dasselbe Muster wie IntakeService.ingest_file, mail/service.py und
    # settings_router.py::_store_profile_image. Der rohe `upload.filename`
    # bleibt weiterhin als reines Anzeige-/Metadatenfeld
    # (`original_filename` unten) erhalten - nur der tatsaechliche
    # Dateisystempfad wird saniert.
    safe_filename = Path(upload.filename).name or "unbenannt"
    destination_filename = f"{uuid.uuid4()}_{safe_filename}"
    destination_path = storage_dir / destination_filename
    destination_path.write_bytes(content)

    mime_type, _ = mimetypes.guess_type(upload.filename)

    document = Document(
        matter_id=matter_id,
        file_path=str(destination_path),
        original_filename=upload.filename,
        content_hash=compute_sha256(destination_path),
        mime_type=mime_type,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.post("/generate")
def generate_schriftsatz(
    request: Request,
    matter_id: str = Form(""),
    new_matter_title: str = Form(""),
    new_client_name: str = Form(""),
    new_client_id: str = Form(""),
    stil: str = Form(""),
    vorlage: str = Form(""),
    attorney_anmerkungen: str = Form(""),
    documents: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLAUDE_CALL)),
) -> RedirectResponse:
    """Kostenpflichtiger Claude-Aufruf (KI-Textproduktion) - NUR
    Anwalt/Admin (PERM_CLAUDE_CALL, gleiche Berechtigung wie
    `regenerate_draft`/`review_draft` in app/web/drafts_router.py). Volle
    Redirect-Aktion ohne HTMX, gleiches Muster wie der Rest des Draft-
    Kontrollflusses (siehe app/web/drafts_router.py-Moduldocstring: "volle
    Seiten-Redirects nach jeder Aktion machen den Kontrollfluss einfacher
    nachvollziehbar")."""
    error = _validate_uploads(documents)
    if error:
        return _redirect_with_error(error)

    settings = get_settings()

    if matter_id.strip():
        matter = db.query(Matter).filter_by(id=matter_id.strip()).first()
        if matter is None:
            return _redirect_with_error("Gewählte Akte wurde nicht gefunden.")
    else:
        # Akte muss VOR den Uploads existieren, siehe Moduldocstring Punkt 1.
        matter = create_quick_matter(
            db,
            title=new_matter_title.strip() or None,
            client_name=new_client_name.strip() or None,
            client_id=new_client_id.strip() or None,
            actor=current_user.email,
        )

    processor = DocumentProcessingService(
        ocr_enabled=settings.ocr_enabled,
        ocr_languages=settings.ocr_languages,
        tesseract_cmd=settings.tesseract_cmd,
    )
    storage_dir = Path(settings.schriftsatz_upload_storage_dir)
    try:
        for upload in documents:
            document = _store_uploaded_document(
                upload, matter.id, storage_dir, db, actor=current_user.email
            )
            if document is not None:
                processor.process_document(document, db, actor=current_user.email)
    except ValueError as exc:
        return _redirect_with_error(str(exc))

    try:
        drafting_service = get_drafting_service()
    except WritingProviderNotConfiguredError as exc:
        return _redirect_with_error(str(exc))

    result = drafting_service.create_draft(
        matter.id,
        _PURPOSE,
        db,
        stil=stil.strip() or None,
        vorlage=vorlage.strip() or None,
        attorney_anmerkungen=attorney_anmerkungen.strip() or None,
        actor=current_user.email,
    )

    if not result.success:
        safe_message = friendly_block_message(result.blocked_reasons)
        return _redirect_with_error(f"Generierung blockiert: {safe_message}")

    return RedirectResponse(url=f"/dashboard/drafts/{result.draft_id}", status_code=303)
