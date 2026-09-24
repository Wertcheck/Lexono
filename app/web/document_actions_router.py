"""Dashboard-Router für Dokument-Bearbeitungsaktionen (18.09., Owner-
Direktive "WEITERARBEITEN" §5/§6 "fehlende Bearbeiten-Aktionen
implementieren, sofern eindeutig aus bestehendem Produktumfang/Referenzen
hervorgehend, keine Fake-UI").

ECHTER FUND (Referenzabgleich, `assets/ux-ui/`-Akte-Dokumente-Ansichten):
die Referenzen zeigen ein "..."-Kontextmenü pro Dokument mit "Vorschau
öffnen"/"Herunterladen"/"Umbenennen"/"Verschieben"/"Kopieren"/"Löschen".
"Vorschau öffnen" und "Herunterladen" existieren bereits
(`matters_router.py::matter_document_view`/`matter_document_download`).
"Umbenennen" fehlte projektweit vollständig - `Document.original_filename`
wird an mehreren Stellen angezeigt (Aktendetail, Dokumentansicht,
Chat-Anhänge), aber nirgends bearbeitet.

Bewusst NUR "Umbenennen" in dieser Runde, NICHT "Löschen"/"Verschieben"/
"Kopieren": ein Dokument endgültig zu entfernen ist bei einer Kanzlei eine
Aufbewahrungs-/Compliance-Frage (echte Mandantenunterlagen, ggf.
aufbewahrungspflichtig) - eine fachliche Entscheidung, keine rein
technische Lücke (CLAUDE.md Punkt 9). "Verschieben"/"Kopieren" setzen die
in `assets/ux-ui/` gezeigte Dokumentkategorien-Ordnerstruktur voraus, die
bereits als eigener, größerer FALL-3-Fund dokumentiert ist (siehe
OPEN_ISSUES.md, Akte-Detail-Tabs/Notizen/Schnellaktionen). "Umbenennen"
dagegen ist eine einzelne, in sich abgeschlossene, risikofreie Aktion auf
einem bereits vorhandenen Feld - identisch im Zuschnitt zu
`parties_router.py`.

**NACHTRAG (20.09., Owner-Direktive "WORKSTREAM A — DOKUMENTE
LOESCHBAR")**: die oben als Owner-Entscheidung offen gelassene
"Löschen"-Frage wurde jetzt explizit vom Owner angefordert - siehe
`app/documents/lifecycle.py` für die vollständige Begründung der dabei
getroffenen Architekturentscheidung (SOFT-DELETE statt Hard-Delete, um
genau die hier oben beschriebene Aufbewahrungs-/Compliance-Sorge zu
respektieren: das Dokument verschwindet aus der Anwendung, bleibt aber
als Datensatz UND Originaldatei vollständig erhalten und
wiederherstellbar). "Verschieben"/"Kopieren" bleiben weiterhin bewusst
NICHT gebaut (unverändert FALL-3, siehe oben).

ZWEITER FUND, GLEICHE RUNDE: die Referenzen zeigen zusätzlich einen
prominenten "Dokument hochladen"-Button auf der Akte-Dokumente-Ansicht -
im Produkt gab es projektweit KEINEN Weg, ein Dokument DIREKT zu einer
bereits bestehenden Akte hochzuladen (nur indirekt über den Chat-Anhang-
Weg `ChatService.attach_document` oder den Schriftsatz-Generator-Upload
`schriftsatz_router.py::_store_uploaded_document` - beide setzen einen
Chat/eine Entwurfserstellung voraus, keinen eigenständigen "nur ablegen"-
Weg). Neue Route `POST /{matter_id}/documents/upload` schließt das -
bewusst dieselbe, bereits etablierte, sicherheitsgeprüfte Speicherlogik
(Path-Traversal-Schutz, Größenlimit, SHA-256) wie die beiden bestehenden
Wege, hier als dritte, eigenständige Kopie (identisches, bereits
mehrfach im Projekt akzeptiertes Duplizierungsmuster - siehe Kommentar in
`schriftsatz_router.py::_store_uploaded_document`), NICHT als riskanter
nachträglicher Umbau der beiden bestehenden, bereits produktiv laufenden
Wege.

Bewusst NICHT in `matters_router.py` (dort ausdrücklich "rein LESEND",
siehe dortiges Moduldocstring) - eigener, kleiner Router, analog zur
bereits etablierten Trennung (`parties_router.py`).

Aktenisolation (CLAUDE.md) bleibt gewahrt: jedes Dokument wird über
`matter_id` UND `document_id` gemeinsam geprüft, exakt wie in
`matter_document_view`."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import require_role
from app.config import get_settings
from app.db.session import get_db
from app.documents.lifecycle import restore_document, soft_delete_document
from app.documents.service import DocumentProcessingService
from app.ingestion.stability import compute_sha256
from app.models import AuditEvent, Document, Matter, User

router = APIRouter(prefix="/dashboard/matters", tags=["dashboard-matters-documents"])

#: Gleiches Limit wie die beiden bestehenden Upload-Wege (Chat-Anhang,
#: Schriftsatz-Generator-Drag&Drop) - siehe Moduldocstring.
_MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB pro Datei


@router.post("/{matter_id}/document/{document_id}/rename")
def rename_document(
    matter_id: str,
    document_id: str,
    original_filename: str = Form(...),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    new_name = original_filename.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Dateiname darf nicht leer sein.")

    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.matter_id == matter.id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht in dieser Akte")

    old_name = document.original_filename or "(ohne Namen)"
    document.original_filename = new_name
    db.add(
        AuditEvent(
            entity_type="Document",
            entity_id=document.id,
            event_type="document_renamed",
            actor=current_user.email,
            details=f"Umbenannt: '{old_name}' -> '{new_name}'",
        )
    )
    db.commit()

    return RedirectResponse(
        url=f"/dashboard/matters/{matter.id}/document/{document.id}",
        status_code=303,
    )


@router.post("/{matter_id}/document/{document_id}/delete")
def delete_document(
    matter_id: str,
    document_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Loescht ein Dokument (Soft-Delete, siehe app/documents/lifecycle.py
    fuer die vollstaendige Begruendung) - Aktenisolation identisch zu
    `rename_document`: das Dokument muss tatsaechlich zu DIESER Akte
    gehoeren, sonst 404 statt eines irrefuehrenden "erfolgreich"."""
    matter = get_or_404(db, Matter, matter_id, "Akte")
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.matter_id == matter.id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht in dieser Akte")

    soft_delete_document(db, document, actor=current_user.email)

    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)


@router.post("/{matter_id}/document/{document_id}/restore")
def restore_document_action(
    matter_id: str,
    document_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Macht ein Loeschen rueckgaengig - siehe app/documents/lifecycle.py.
    Bewusst OHNE `deleted_at IS NULL`-Filter (Gegenteil von `delete_document`
    - hier wird gezielt ein BEREITS geloeschtes Dokument gesucht)."""
    matter = get_or_404(db, Matter, matter_id, "Akte")
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.matter_id == matter.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht in dieser Akte")

    restore_document(db, document, actor=current_user.email)

    return RedirectResponse(
        url=f"/dashboard/matters/{matter.id}?show_deleted=1", status_code=303
    )


def _store_uploaded_document(
    upload: UploadFile, matter_id: str, storage_dir: Path, db: Session, *, actor: str
) -> Document | None:
    """Identische Sicherheitslogik wie `schriftsatz_router.py::
    _store_uploaded_document`/`ChatService.attach_document` (Path-Traversal-
    Schutz, Größenlimit, SHA-256) - siehe Moduldocstring, warum hier bewusst
    eine dritte, eigenständige Kopie statt eines riskanten Umbaus der beiden
    bestehenden Wege."""
    if not upload.filename:
        return None

    content = upload.file.read()
    if len(content) > _MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(
            f"Datei '{upload.filename}' überschreitet die maximale Größe von "
            f"{_MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
        )

    storage_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(upload.filename).name or "unbenannt"
    destination_path = storage_dir / f"{uuid.uuid4()}_{safe_filename}"
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
    db.flush()  # document.id fuer das AuditEvent benoetigt
    db.add(
        AuditEvent(
            entity_type="Document",
            entity_id=document.id,
            event_type="document_uploaded_to_matter",
            actor=actor,
            details=f"Dokument direkt zur Akte hochgeladen: {upload.filename}",
        )
    )
    db.commit()
    db.refresh(document)
    return document


@router.post("/{matter_id}/documents/upload")
def upload_documents(
    matter_id: str,
    documents: list[UploadFile] = File(default=[]),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    settings = get_settings()
    storage_dir = Path(settings.schriftsatz_upload_storage_dir)
    processor = DocumentProcessingService(
        ocr_enabled=settings.ocr_enabled,
        ocr_languages=settings.ocr_languages,
        tesseract_cmd=settings.tesseract_cmd,
    )
    try:
        for upload in documents:
            document = _store_uploaded_document(
                upload, matter.id, storage_dir, db, actor=current_user.email
            )
            if document is not None:
                processor.process_document(document, db, actor=current_user.email)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/dashboard/matters/{matter.id}?error={exc}", status_code=303
        )

    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)
