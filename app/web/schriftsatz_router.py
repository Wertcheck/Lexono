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

import json
import mimetypes
import re
import uuid
from urllib.parse import urlencode
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
from app.drafting import schriftsatz_operations
from app.drafting.quick_matter import create_quick_matter
from app.ingestion.stability import compute_sha256
from app.models import AuditEvent, Client, Document, Draft, Matter, User
from app.models.schriftsatz_operation import (
    OPERATION_GENERATE,
    OPERATION_UPLOAD,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    SchriftsatzOperation,
)
from app.privacy.api_logger import friendly_block_message
from app.privacy.decisions import InvalidPrivacyDecisionError, parse_manual_terms, record_manual_terms
from app.web.name_protection import install_name_protection
from app.web.service_factory import WritingProviderNotConfiguredError, get_drafting_service
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/tools/schriftsatz", tags=["dashboard-schriftsatz"])

templates = install_name_protection(Jinja2Templates(directory=TEMPLATES_DIR))  # §54 "Namen schützen"

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


def _redirect_with_error(message: str, matter_id: str | None = None) -> RedirectResponse:
    # §58: nur die Akten-ID (keine Personendaten, wie der bestehende ?matter_id=-Einstieg) - ein neuer Versuch nach
    # einem Teilfehler nutzt die bereits angelegte Akte statt einer weiteren Schnellakte
    params = {"error": message, **({"matter_id": matter_id} if matter_id else {})}
    return RedirectResponse(url=f"/dashboard/tools/schriftsatz?{urlencode(params)}", status_code=303)


@router.get("", response_class=HTMLResponse)
def schriftsatz_generator_page(
    request: Request,
    error: str | None = None,
    matter_id: str | None = None,
    client_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    return _render_generator(request, db, current_user, error=error, matter_id=matter_id, client_id=client_id)


def _render_generator(
    request: Request,
    db: Session,
    current_user: User,
    *,
    error: str | None = None,
    matter_id: str | None = None,
    client_id: str | None = None,
    uploaded_documents: list[Document] | None = None,
    form_values: dict[str, str] | None = None,
    upload_repeated: bool = False,
) -> HTMLResponse:
    open_matters = (
        db.query(Matter).filter(Matter.status == "open", Matter.deleted_at.is_(None))  # §26: keine geloeschten Akten
        .order_by(Matter.updated_at.desc()).all()
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
        # §57: nach "nur hochladen" - Hinweis, Links zur Pruefung, bisherige Eingaben (nichts davon wurde gesendet)
        "uploaded_documents": uploaded_documents or [],
        "form_values": form_values or {},
        # §58: JEDE ausgelieferte Seite bekommt eine neue Vorgangs-ID - ein bewusster neuer Vorgang ist immer moeglich,
        # eine Wiederholung DESSELBEN Requests (F5, Doppelklick) wird erkannt
        "operation_token": schriftsatz_operations.issue_token(current_user.id),
        "upload_repeated": upload_repeated,
    }
    response = templates.TemplateResponse(request, "schriftsatz_generator.html", context)
    # §58: nicht aus dem Browser-Cache (Zurueck-Button) mit einer bereits verbrauchten Vorgangs-ID wiederherstellen
    response.headers["Cache-Control"] = "no-store"
    return response


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
            # §60: nur eine kurze technische Endung in die (URL-)Meldung, keine Namensbestandteile
            shown = suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else "?"
            return f"Dateityp '{shown}' wird nicht unterstützt (erlaubt: {allowed})."
    return None


def _store_uploaded_document(
    upload: UploadFile, matter_id: str, storage_dir: Path, db: Session, *, actor: str, document_id: str | None = None
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
        # §58 (Review): ohne Dateinamen - die Meldung landet in der Redirect-URL (Browserverlauf, Zugriffsprotokoll)
        raise ValueError(
            "Eine Datei überschreitet die maximale Größe von "
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
    # §61 P2: mit vorab vergebener Dokument-ID ein FESTER Pfad - die Wiederaufnahme ueberschreibt die Datei eines
    # abgebrochenen Versuchs, statt eine verwaiste Kopie abzulegen
    destination_filename = f"{document_id or uuid.uuid4()}_{safe_filename}"
    destination_path = storage_dir / destination_filename
    destination_path.write_bytes(content)

    mime_type, _ = mimetypes.guess_type(upload.filename)

    document = Document(
        **({"id": document_id} if document_id else {}),
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


@router.post("/generate", response_model=None)
def generate_schriftsatz(
    request: Request,
    matter_id: str = Form(""),
    new_matter_title: str = Form(""),
    new_client_name: str = Form(""),
    new_client_id: str = Form(""),
    stil: str = Form(""),
    vorlage: str = Form(""),
    attorney_anmerkungen: str = Form(""),
    protected_terms: str = Form(""),
    operation_token: str = Form(""),
    documents: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLAUDE_CALL)),
) -> RedirectResponse | HTMLResponse:
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
    # §54 "Namen schützen": VOR jeder Persistenz gegen genau den Text geprueft, der in die Anfrage geht - ungueltige
    # oder veraltete Markierung -> nichts angelegt, nichts gesendet (Meldung ohne Begriff, steht in der URL).
    try:
        manual_terms = parse_manual_terms(protected_terms, "\n".join((stil, vorlage, attorney_anmerkungen)))
    except InvalidPrivacyDecisionError as exc:
        return _redirect_with_error(str(exc))

    settings = get_settings()
    form_values = {"stil": stil, "vorlage": vorlage, "attorney_anmerkungen": attorney_anmerkungen}

    # §58 Idempotenz: serverseitig ausgestellte, signierte, nutzergebundene Vorgangs-ID - fehlt sie oder ist sie
    # ungueltig, wird NICHTS angelegt (fail-closed, keine reine UI-Sperre).
    operation_id = schriftsatz_operations.read_token(operation_token, current_user.id)
    if operation_id is None:
        return _redirect_with_error(
            "Der Vorgang ist abgelaufen oder ungültig - bitte erneut absenden. Es wurde nichts angelegt oder gesendet."
        )

    if matter_id.strip():
        matter = db.query(Matter).filter_by(id=matter_id.strip(), deleted_at=None).first()  # §26
        if matter is None:
            return _redirect_with_error("Gewählte Akte wurde nicht gefunden.")
    else:
        matter = None

    kind = OPERATION_UPLOAD if any(upload.filename for upload in documents) else OPERATION_GENERATE
    contents = _upload_contents(documents)
    fingerprint = schriftsatz_operations.request_fingerprint(
        [kind, matter_id.strip(), new_matter_title, new_client_name, new_client_id, stil, vorlage,
         attorney_anmerkungen, protected_terms],
        contents,
    )
    # §61 P2: alle IDs vorab vergeben - jeder Schritt "anlegen, falls nicht vorhanden" (Wiederaufnahme eines Uploads)
    plan = {"matter_id": str(uuid.uuid4()) if matter is None else None,
            "documents": [str(uuid.uuid4()) for _ in contents]}
    run_kwargs = dict(kind=kind, matter=matter, settings=settings, new_matter_title=new_matter_title,
                      new_client_name=new_client_name, new_client_id=new_client_id, manual_terms=manual_terms,
                      documents=documents, form_values=form_values)
    # vor dem Anlegen als "laeuft in diesem Prozess" vermerkt (keine Luecke fuer eine gleichzeitige Wiederholung)
    with schriftsatz_operations.running(operation_id):
        existing = schriftsatz_operations.claim(db, operation_id, user_id=current_user.id, kind=kind,
                                                fingerprint=fingerprint, owner=schriftsatz_operations.new_owner(),
                                                plan=json.dumps(plan))
        if existing is None:
            # Ein unerwarteter Fehler laesst eine GENERIERUNG auf "processing" (unklar, ob die Cloud-Anfrage erfolgte):
            # eine Wiederholung fuehrt sie nie erneut aus, sondern schlaegt kontrolliert fehl.
            return _run_operation(request, db, current_user, operation_id=operation_id, plan=plan, recorded=[],
                                  **run_kwargs)
    if existing is not None:
        if existing.fingerprint != fingerprint:
            # Alte Vorgangs-ID, aber eine GEAENDERTE Anfrage (z. B. ueber "Zurueck" bearbeitet): weder still auf das
            # alte Ergebnis umleiten noch ausfuehren - neue Seite (neue ID), Eingaben bleiben erhalten
            return _render_generator(
                request, db, current_user, matter_id=matter.id if matter is not None else None,
                form_values=form_values,
                error=("Das Formular wurde seit dem ersten Absenden geändert (z. B. über „Zurück“). Es wurde nichts "
                       "ausgeführt. Bitte die Eingaben prüfen und erneut absenden."
                       + (" Ausgewählte Dateien bitte erneut wählen." if kind == OPERATION_UPLOAD else "")),
            )
        # Wiederholung DESSELBEN Vorgangs (F5, erneutes Absenden, Doppelklick, paralleler Request, Wiederholung nach
        # verlorener Antwort): keine weitere Akte, keine Dokumente, kein Entwurf, keine KI-Anfrage
        return _repeated_operation(request, db, current_user, existing, form_values, run_kwargs)


def _upload_contents(documents: list[UploadFile]) -> list[tuple[str, bytes]]:
    """Name + Inhalt je Datei fuer den Fingerabdruck; der Lesezeiger wird fuer das spaetere Speichern zurueckgesetzt."""
    result = []
    for upload in documents:
        if upload.filename:
            content = upload.file.read()
            upload.file.seek(0)
            result.append((upload.filename, content))
    return result


_REPEAT_WAIT_SECONDS = 10.0
_REPEAT_POLL_SECONDS = 0.25


def _await_running_operation(db: Session, operation: SchriftsatzOperation) -> SchriftsatzOperation:
    """Wartet, solange der Besitzer NACHWEISLICH laeuft (nicht nach Uhrzeit) - hoechstens `_REPEAT_WAIT_SECONDS`."""
    import time

    deadline = time.monotonic() + _REPEAT_WAIT_SECONDS
    while (operation.status == STATUS_PROCESSING
           and schriftsatz_operations.owner_state(operation) == schriftsatz_operations.ALIVE
           and time.monotonic() < deadline):
        time.sleep(_REPEAT_POLL_SECONDS)
        operation = schriftsatz_operations.load(db, operation.id, model=SchriftsatzOperation) or operation
    return operation


def _operation_matter_id(db: Session, operation: SchriftsatzOperation) -> str | None:
    """Akte des Vorgangs - auch wenn der Abbruch zwischen dem Anlegen der Schnellakte (geplante ID) und ihrem Vermerk am
    Vorgang lag (Review §61 P2): sonst legte der angebotene bewusste Neustart eine ZWEITE Schnellakte an."""
    if operation.matter_id:
        return operation.matter_id
    try:
        planned = json.loads(operation.plan or "{}").get("matter_id")
    except (ValueError, AttributeError):
        return None
    matter = db.get(Matter, planned) if planned else None
    return matter.id if matter is not None and matter.deleted_at is None else None


def _audit_operation(db: Session, operation: SchriftsatzOperation, event_type: str, actor: str) -> None:
    """Nachvollziehbarkeit (CLAUDE.md): Uebernahme/Fortsetzung/kontrollierter Fehlschlag - ohne Inhalte."""
    db.add(AuditEvent(entity_type="SchriftsatzOperation", entity_id=operation.id, event_type=event_type, actor=actor,
                      details=f"Vorgang {operation.kind} nach hartem Abbruch (Besitzer beendet)"))
    db.commit()


def _repeated_operation(
    request: Request, db: Session, current_user: User, operation: SchriftsatzOperation, form_values: dict[str, str],
    run_kwargs: dict | None = None,
) -> RedirectResponse | HTMLResponse:
    if operation.user_id != current_user.id:
        return _redirect_with_error("Der Vorgang ist ungültig. Es wurde nichts angelegt oder gesendet.")
    # §61 P2: ein laufender Vorgang wird nur bei NACHWEISLICH beendetem Besitzer uebernommen (atomar, genau ein
    # Request) - Zeit allein gibt nie frei. Ein Upload wird fortgesetzt (keine Cloud), eine Generierung nie.
    for _ in range(3):
        operation = _await_running_operation(db, operation)
        if (operation.status != STATUS_PROCESSING
                or schriftsatz_operations.owner_state(operation) != schriftsatz_operations.DEAD):
            break
        with schriftsatz_operations.running(operation.id):
            if schriftsatz_operations.take_over_operation(db, operation):
                try:
                    plan = json.loads(operation.plan or "{}")
                    if not isinstance(plan, dict):
                        raise ValueError("Plan ungueltig")
                except ValueError:
                    plan = {}  # nicht lesbar: kontrollierter Fehlschlag statt wiederholter 500er
                if operation.kind == OPERATION_UPLOAD and run_kwargs and "documents" in plan:
                    _audit_operation(db, operation, "schriftsatz_operation_resumed", current_user.email)
                    try:
                        return _run_operation(request, db, current_user, operation_id=operation.id, plan=plan,
                                              recorded=schriftsatz_operations.document_ids(operation), **run_kwargs)
                    except Exception:
                        schriftsatz_operations.update(db, operation.id, status=STATUS_FAILED)
                        raise
                schriftsatz_operations.update(db, operation.id, status=STATUS_FAILED)
                _audit_operation(db, operation, "schriftsatz_operation_interrupted", current_user.email)
                if operation.kind == OPERATION_GENERATE:
                    return _render_generator(
                        request, db, current_user, matter_id=_operation_matter_id(db, operation),
                        form_values=form_values,
                        error="Die Generierung wurde unterbrochen. Ob die KI-Anfrage bereits gesendet wurde, ist unklar "
                              "- sie wird nicht automatisch wiederholt. Bitte die Entwürfe der Akte prüfen; eine neue "
                              "Generierung ist bewusst möglich.",
                    )
                return _render_generator(
                    request, db, current_user, matter_id=_operation_matter_id(db, operation), form_values=form_values,
                    error="Der unterbrochene Upload ließ sich nicht sicher fortsetzen. Es wurde nichts gelöscht; bitte "
                          "die Akte prüfen und bei Bedarf neu hochladen.",
                )
        operation = schriftsatz_operations.load(db, operation.id, model=SchriftsatzOperation) or operation
    if operation.status == STATUS_DONE and operation.kind == OPERATION_GENERATE:
        if operation.draft_id and db.get(Draft, operation.draft_id) is not None:
            return RedirectResponse(url=f"/dashboard/drafts/{operation.draft_id}/edit", status_code=303)
        return _render_generator(
            request, db, current_user, matter_id=operation.matter_id, form_values=form_values,
            error="Dieser Schriftsatz wurde bereits generiert; der Entwurf ist nicht mehr vorhanden. Es wurde nichts "
                  "erneut gesendet.",
        )
    ids = schriftsatz_operations.document_ids(operation)
    # §26: geloeschte Dokumente bzw. Akten erscheinen auch in einer Wiederholung nicht
    stored = (
        db.query(Document).join(Matter, Matter.id == Document.matter_id)
        .filter(Document.id.in_(ids), Document.deleted_at.is_(None), Matter.deleted_at.is_(None)).all()
        if ids else []
    )
    stored.sort(key=lambda document: ids.index(document.id))
    if operation.status == STATUS_DONE and operation.kind == OPERATION_UPLOAD:
        return _render_generator(request, db, current_user, matter_id=operation.matter_id, uploaded_documents=stored,
                                 form_values=form_values, upload_repeated=True)
    if operation.status == STATUS_FAILED:
        message = "Dieser Vorgang ist bereits fehlgeschlagen und wird nicht wiederholt."
    elif schriftsatz_operations.owner_state(operation) == schriftsatz_operations.ALIVE:
        message = ("Dieser Vorgang wird gerade noch verarbeitet. Bitte einen Moment warten und dann die Akte bzw. die "
                   "Entwürfe prüfen - nicht neu starten, sonst entsteht ein zweiter Vorgang.")
    else:
        # Besitzer nicht pruefbar (anderer Rechner, Altbestand): NICHT uebernehmen - auch nicht nach Ablauf einer Zeit
        message = ("Dieser Vorgang wurde unterbrochen, und es lässt sich nicht sicher feststellen, ob er noch läuft. "
                   "Er wird nicht fortgesetzt; bitte die Akte prüfen, ein neuer Vorgang ist möglich.")
    message += " Es wurde nichts doppelt angelegt und nichts erneut gesendet."
    if stored:
        names = ", ".join(document.original_filename or "Dokument" for document in stored)
        message += f" Bereits angelegte Dokumente (in der Akte prüfen): {names}."
    return _render_generator(request, db, current_user, error=message, matter_id=_operation_matter_id(db, operation),
                             form_values=form_values)


def _run_operation(
    request: Request,
    db: Session,
    current_user: User,
    *,
    operation_id: str,
    kind: str,
    matter: Matter | None,
    settings,
    new_matter_title: str,
    new_client_name: str,
    new_client_id: str,
    manual_terms: list[str],
    documents: list[UploadFile],
    form_values: dict[str, str],
    plan: dict,
    recorded: list[str],
) -> RedirectResponse | HTMLResponse:
    """Der EINE Request, der den Vorgang angelegt (oder nach einem harten Abbruch uebernommen) hat. Jeder Schritt prueft
    zuerst, ob sein Ergebnis unter der vorab vergebenen ID schon existiert; Fortschrittsmarker im Plan erkennen ein
    inzwischen geloeschtes Objekt (kontrollierter Fehlschlag statt Wiederbelebung, nichts geloescht)."""
    from app.documents.readiness import PROCESSING, assess_document

    def _failed(message: str) -> RedirectResponse:
        schriftsatz_operations.update(db, operation_id, status=STATUS_FAILED)
        return _redirect_with_error(message, matter.id if matter is not None else None)

    def _mark(step: str) -> None:
        plan.setdefault("done", []).append(step)
        schriftsatz_operations.update(db, operation_id, plan=json.dumps(plan))

    done = set(plan.get("done") or [])
    unclear = "Der unterbrochene Vorgang ließ sich nicht sicher fortsetzen. Es wurde nichts gelöscht; bitte die Akte prüfen."
    stil = form_values["stil"]
    vorlage = form_values["vorlage"]
    attorney_anmerkungen = form_values["attorney_anmerkungen"]
    if matter is None:
        # Akte muss VOR den Uploads existieren, siehe Moduldocstring Punkt 1.
        planned = plan.get("matter_id")
        existing_matter = db.get(Matter, planned) if planned else None
        if not planned or (existing_matter is None and "matter" in done) or (
                existing_matter is not None and existing_matter.deleted_at is not None):
            return _failed(unclear)
        if existing_matter is None:
            existing_matter = create_quick_matter(
                db,
                title=new_matter_title.strip() or None,
                client_name=new_client_name.strip() or None,
                client_id=new_client_id.strip() or None,
                actor=current_user.email,
                matter_id=planned,
            )
            _mark("matter")
        matter = existing_matter
    schriftsatz_operations.update(db, operation_id, matter_id=matter.id)

    # Dieselbe aktenweite Entscheidung wie "Als Person behandeln" (Chat-Rueckfrage) - VOR Uploads und Cloud-Anfrage
    # gespeichert; `create_draft` laedt sie (`_prepare_and_gate`). Scheitert das Speichern: nichts senden.
    if "terms" not in done:  # bei der Wiederaufnahme nicht doppelt (Audit/Lern-Statistik)
        try:
            record_manual_terms(db, matter_id=matter.id, conversation_id=None, terms=manual_terms,
                                user_id=current_user.id, actor=current_user.email)
        except Exception:
            return _failed("Die Namensmarkierung konnte nicht gespeichert werden - es wurde nichts gesendet.")
        _mark("terms")

    processor = DocumentProcessingService(
        ocr_enabled=settings.ocr_enabled,
        ocr_languages=settings.ocr_languages,
        tesseract_cmd=settings.tesseract_cmd,
    )
    storage_dir = Path(settings.schriftsatz_upload_storage_dir)
    stored: list[Document] = []
    uploads = [upload for upload in documents if upload.filename]
    planned_documents = plan.get("documents") or []
    if len(planned_documents) != len(uploads):
        return _failed(unclear)
    try:
        for upload, document_id in zip(uploads, planned_documents):
            document = db.get(Document, document_id)
            if document is None and document_id in recorded:  # schon angelegt und vermerkt, jetzt verschwunden
                return _failed(unclear)
            if document is None:
                document = _store_uploaded_document(
                    upload, matter.id, storage_dir, db, actor=current_user.email, document_id=document_id
                )
                schriftsatz_operations.add_document(db, operation_id, document.id)  # sofort nach dem Anlegen
                recorded.append(document.id)
                processor.process_document(document, db, actor=current_user.email)
            elif document.deleted_at is not None or document.matter_id != matter.id:
                return _failed(unclear)
            else:
                if document_id not in recorded:
                    schriftsatz_operations.add_document(db, operation_id, document_id)
                    recorded.append(document_id)
                if assess_document(db, document).state == PROCESSING:
                    processor.process_document(document, db, actor=current_user.email)  # lokal, keine KI
            stored.append(document)
    except ValueError as exc:
        return _failed(str(exc))

    # §57: Upload und KI-Verarbeitung sind getrennte Vorgaenge (serverseitig erzwungen). Mit Dokumenten wird NUR
    # gespeichert und lokal ausgelesen - KEIN create_draft, keine Cloud. Der Nutzer prueft/markiert die Dokumente in
    # der Aktenansicht und startet die Generierung danach bewusst (erneut "Schriftsatz generieren", ohne Dateien).
    if kind == OPERATION_UPLOAD:
        schriftsatz_operations.update(db, operation_id, status=STATUS_DONE)
        return _render_generator(
            request, db, current_user, matter_id=matter.id, uploaded_documents=stored, form_values=form_values,
        )

    try:
        drafting_service = get_drafting_service()
    except WritingProviderNotConfiguredError as exc:
        return _failed(str(exc))

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
        return _failed(f"Generierung blockiert: {safe_message}")
    schriftsatz_operations.update(db, operation_id, status=STATUS_DONE, draft_id=result.draft_id)

    # ECHTER FUND, LIVE REPRODUZIERT (05.10., Owner-Direktive "Referenz-
    # getreuen Dokumenten-Editor umsetzen und Chat-zu-Editor-Workflow
    # vollständig absichern"): zeigte bisher auf den reinen Viewer
    # (draft_detail.html, "Original/Entwurf/KI-Assistent"-Spalten) statt
    # auf den beim Dokumenten-Editor-Umbau (04.10.) neu gebauten Rich-
    # Text-Editor (app/web/draft_editor_router.py) - dieser Router wurde
    # beim damaligen Umbau nicht mitaktualisiert (nur chat.html's
    # "Vollständigen Editor öffnen"-Link wurde seinerzeit korrigiert,
    # dieser zweite, unabhängige Einstiegspunkt - die "Schreiben
    # erstellen"-Schnellaktion auf der Mandantendetailseite - blieb
    # unentdeckt). Der Viewer bleibt unter /dashboard/drafts/{id}
    # weiterhin vollständig erreichbar (siehe dortiger "Im Editor
    # öffnen"-Link für den umgekehrten Weg).
    return RedirectResponse(url=f"/dashboard/drafts/{result.draft_id}/edit", status_code=303)
