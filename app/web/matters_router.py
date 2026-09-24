"""Dashboard-Router für "Akten" (13.09., UI/UX-Überarbeitung) – löst den
bisherigen ehrlichen Platzhalter unter `/dashboard/matters` ab (siehe
app/web/placeholder_router.py).

Ursprünglich bewusst rein LESEND (kein Anlegen hier - Akten entstanden nur
über die bestehenden Wege: automatisch beim Schriftsatz-Generator/Chat
ohne gewählte Akte, siehe app/drafting/quick_matter.py::
create_quick_matter). **UPDATE (18.09., Owner-Direktive "WEITERARBEITEN",
Referenzabgleich `assets/ux-ui/13_akten_uebersicht.png`)**: die Referenz
zeigt "+ Neue Akte" als primäre Aktion oben rechts - identisch im Muster
zu "Mandant anlegen" (bereits vorhanden, `clients_list.html`). ECHTER
FUND: das Fehlen dieses manuellen Wegs war ein Hauptgrund für die real in
der Produktions-DB beobachtete "Schnellentwurf"-Häufung (18.09., 38 von 53
Akten, siehe OPEN_ISSUES.md) - ohne eine Möglichkeit, direkt eine
sauber benannte, einem Mandanten zugeordnete Akte anzulegen, blieb nur der
Schriftsatz-Generator-Umweg mit automatisch generiertem Titel. Jetzt: EIN
zusätzlicher, expliziter Schreibpfad (`POST .../create`), der bestehende
automatische Weg bleibt unverändert bestehen. Restliche Routen bleiben
lesend - dieselbe Trennung wie app/web/quality_router.py.

Aktenisolation (CLAUDE.md) bleibt gewahrt: jede Abfrage ist strikt nach
`matter_id` gefiltert, exakt wie in app/ai_providers/local_ai_provider.py.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.audit.service import AuditLogService
from app.chat.document_preview import build_document_preview
from app.documents.rendering import (
    DEFAULT_PAGE_DPI,
    THUMBNAIL_DPI,
    DocumentRenderError,
    determine_viewer_mode,
    render_page_png,
)
from app.auth.permissions import require_login, require_role
from app.clients.service import PRACTICE_AREA_SUGGESTIONS
from app.db.session import get_db
from app.models import (
    AuditEvent,
    ChatConversation,
    Client,
    Deadline,
    Document,
    Draft,
    Matter,
    Message,
    Note,
    Party,
    Task,
    User,
)
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/matters", tags=["dashboard-matters"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("", response_class=HTMLResponse)
def matters_list_page(
    request: Request,
    search: str = "",
    status: str = "",
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    rows = (
        db.query(Matter)
        .order_by(Matter.updated_at.desc())
        .all()
    )
    if search:
        needle = search.lower()
        rows = [
            m
            for m in rows
            if needle in (m.title or "").lower()
            or needle in (m.reference_number or "").lower()
            or needle in (m.client.name or "").lower()
        ]
    if status:
        rows = [m for m in rows if m.status == status]

    context = {
        "request": request,
        "active_nav": "Akten",
        "current_user": current_user,
        "rows": rows,
        "search": search,
        "status": status,
        # Fuer das "Akte anlegen"-Formular (18.09.) - siehe Moduldocstring.
        "clients": db.query(Client).order_by(Client.name.asc()).all(),
        "practice_areas": PRACTICE_AREA_SUGGESTIONS,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "error": error,
    }
    return templates.TemplateResponse(request, "matters_list.html", context)


@router.post("/create")
def create_matter_action(
    title: str = Form(...),
    client_id: str = Form(...),
    reference_number: str = Form(""),
    practice_area: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Legt eine Akte MANUELL an (18.09., siehe Moduldocstring) - ergänzt,
    ersetzt NICHT, den bestehenden automatischen Weg
    (`create_quick_matter`). Bewusst ohne dessen Platzhalter-Mandant-Logik:
    hier wählt der Anwalt den Mandanten explizit aus bereits vorhandenen
    Mandanten aus, ein manuelles Anlegen OHNE Mandant ergibt fachlich
    keinen Sinn (anders als beim spontanen Schnellentwurf aus dem Chat)."""
    title = title.strip()
    reference_number = reference_number.strip() or None
    if not title:
        return RedirectResponse(
            url="/dashboard/matters?error=Bitte einen Titel angeben.", status_code=303
        )
    client = db.get(Client, client_id)
    if client is None:
        return RedirectResponse(
            url="/dashboard/matters?error=Bitte einen gültigen Mandanten auswählen.",
            status_code=303,
        )
    if reference_number:
        existing = (
            db.query(Matter).filter(Matter.reference_number == reference_number).first()
        )
        if existing is not None:
            return RedirectResponse(
                url=(
                    f"/dashboard/matters?error=Aktenzeichen '{reference_number}' "
                    "ist bereits vergeben."
                ),
                status_code=303,
            )

    matter = Matter(
        client_id=client.id,
        title=title,
        reference_number=reference_number,
        practice_area=practice_area.strip() or None,
        status="open",
    )
    db.add(matter)
    db.flush()
    db.add(
        AuditEvent(
            entity_type="Matter",
            entity_id=matter.id,
            event_type="matter_created_manually",
            actor=current_user.email,
            details=f"Akte manuell angelegt: {matter.title} (Mandant: {client.name})",
        )
    )
    db.commit()

    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)


@router.post("/{matter_id}/update")
def update_matter_action(
    matter_id: str,
    title: str = Form(...),
    reference_number: str = Form(""),
    practice_area: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """"Bearbeiten" (18.09., siehe Moduldocstring) - Referenz
    `13_akten_uebersicht.png` zeigt "Bearbeiten" im "..."-Menü jeder Akte.
    Bewusst NUR die Stammdaten (Titel/Aktenzeichen/Rechtsgebiet), NICHT den
    Mandanten (ein Aktenwechsel des Mandanten wäre ein eigener, schwerer
    fachlicher Vorgang, keine reine Bearbeitung) und NICHT den Status
    (dafür `archive`/`reopen` unten, ein bewusst separater, expliziter
    Schritt statt eines beiläufig mitgeänderten Felds)."""
    matter = get_or_404(db, Matter, matter_id, "Akte")
    title = title.strip()
    reference_number = reference_number.strip() or None
    if not title:
        return RedirectResponse(
            url=f"/dashboard/matters/{matter_id}?error=Bitte einen Titel angeben.",
            status_code=303,
        )
    if reference_number:
        existing = (
            db.query(Matter)
            .filter(Matter.reference_number == reference_number, Matter.id != matter_id)
            .first()
        )
        if existing is not None:
            return RedirectResponse(
                url=(
                    f"/dashboard/matters/{matter_id}?error=Aktenzeichen "
                    f"'{reference_number}' ist bereits vergeben."
                ),
                status_code=303,
            )

    matter.title = title
    matter.reference_number = reference_number
    matter.practice_area = practice_area.strip() or None
    db.add(
        AuditEvent(
            entity_type="Matter",
            entity_id=matter.id,
            event_type="matter_updated",
            actor=current_user.email,
            details=f"Aktendaten geändert: {matter.title}",
        )
    )
    db.commit()

    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)


@router.post("/{matter_id}/archive")
def archive_matter_action(
    matter_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    matter.status = "closed"
    db.add(
        AuditEvent(
            entity_type="Matter",
            entity_id=matter.id,
            event_type="matter_archived",
            actor=current_user.email,
            details=f"Akte abgeschlossen: {matter.title}",
        )
    )
    db.commit()
    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)


@router.post("/{matter_id}/reopen")
def reopen_matter_action(
    matter_id: str,
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")
    matter.status = "open"
    db.add(
        AuditEvent(
            entity_type="Matter",
            entity_id=matter.id,
            event_type="matter_reopened",
            actor=current_user.email,
            details=f"Akte wieder geöffnet: {matter.title}",
        )
    )
    db.commit()
    return RedirectResponse(url=f"/dashboard/matters/{matter.id}", status_code=303)


@router.get("/{matter_id}", response_class=HTMLResponse)
def matter_detail_page(
    matter_id: str,
    request: Request,
    error: str | None = None,
    show_deleted: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    matter = get_or_404(db, Matter, matter_id, "Akte")

    # Geloeschte Dokumente (20.09., Workstream A) sind standardmaessig
    # ausgeblendet (Soft-Delete, siehe app/documents/lifecycle.py) - der
    # optionale `show_deleted`-Query-Parameter (von restore_document_action
    # nach einer Wiederherstellung gesetzt) zeigt sie zusaetzlich, DEUTLICH
    # markiert, an - kein separates Papierkorb-Modell noetig.
    documents_query = db.query(Document).filter(Document.matter_id == matter_id)
    if not show_deleted:
        documents_query = documents_query.filter(Document.deleted_at.is_(None))
    documents = documents_query.order_by(Document.created_at.desc()).all()
    deleted_documents_count = (
        db.query(Document)
        .filter(Document.matter_id == matter_id, Document.deleted_at.isnot(None))
        .count()
    )
    deadlines = (
        db.query(Deadline)
        .filter(Deadline.matter_id == matter_id)
        .order_by(Deadline.due_date.asc())
        .all()
    )
    tasks = (
        db.query(Task)
        .filter(Task.matter_id == matter_id)
        .order_by(Task.created_at.desc())
        .all()
    )
    messages = (
        db.query(Message)
        .filter(Message.matter_id == matter_id)
        .order_by(Message.created_at.desc())
        .all()
    )
    conversations = (
        db.query(ChatConversation)
        .filter(ChatConversation.matter_id == matter_id)
        .order_by(ChatConversation.updated_at.desc())
        .all()
    )
    drafts = (
        db.query(Draft)
        .filter(Draft.matter_id == matter_id)
        .order_by(Draft.created_at.desc())
        .all()
    )
    # "Beteiligte" (17.09., siehe app/web/parties_router.py fuer die volle
    # Begruendung): `Party` wird bereits produktiv fuer die Pseudonymisierung
    # gelesen, hatte bisher aber nirgends einen Anzeige-/Anlegeweg.
    parties = (
        db.query(Party)
        .filter(Party.matter_id == matter_id)
        .order_by(Party.created_at.asc())
        .all()
    )
    # Verlauf (18.09., Owner-Direktive "WEITERARBEITEN" Fortsetzung):
    # `AuditLogService.list_events_for_matter` existierte bereits
    # vollstaendig und aktenisolations-geprueft (app/audit/service.py,
    # bereits von app/api/routers/audit.py genutzt), war aber auf der
    # Akte-Detailseite selbst nirgends sichtbar - identisches Fund-Muster
    # wie bei den Draft-Quality-Ratings. `draft_detail.html` zeigt einen
    # Audit-Log NUR fuer die eine Entwurfsversion; hier der VOLLE,
    # aktenweite Verlauf (Dokumente/Fristen/Entwuerfe/Beteiligte/...).
    audit_events = AuditLogService().list_events_for_matter(matter_id, db)
    # Notizen (19.09., UI/UX-Referenzabgleich, siehe app/models/note.py):
    # neuestes zuerst, wie ueberall sonst auf dieser Seite.
    notes = (
        db.query(Note)
        .filter(Note.matter_id == matter_id)
        .order_by(Note.created_at.desc())
        .all()
    )

    context = {
        "request": request,
        "active_nav": "Akten",
        "current_user": current_user,
        "matter": matter,
        "documents": documents,
        "deleted_documents_count": deleted_documents_count,
        "show_deleted": show_deleted,
        "deadlines": deadlines,
        "tasks": tasks,
        "messages": messages,
        "conversations": conversations,
        "drafts": drafts,
        "parties": parties,
        "audit_events": audit_events,
        "notes": notes,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "practice_areas": PRACTICE_AREA_SUGGESTIONS,
        "error": error,
    }
    return templates.TemplateResponse(request, "matter_detail.html", context)


@router.get("/{matter_id}/document/{document_id}", response_class=HTMLResponse)
def matter_document_view(
    matter_id: str,
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """Dokumentansicht INNERHALB der Akte (14.09.).

    ECHTER FUND beim UI-Durchgang: Dokumente einer Akte waren in der
    Aktenansicht zwar aufgelistet, aber nicht zu oeffnen - es gab
    projektweit nur EINEN Dokument-Viewer (`chat_router.py::
    chat_document_view`), und der setzt eine Chat-Unterhaltung voraus
    (`get_attached_document` joint ueber `ChatMessageDocument`). Fuer jedes
    Dokument, das NICHT ueber den Chat hochgeladen wurde - also
    insbesondere jeden Mail-Anhang - existierte damit ueberhaupt keine
    Moeglichkeit, es im Produkt anzusehen. Der Gold-Workflow-Schritt
    "Dokument speichern und wiederfinden" endete hier.

    Klassifikation nach §5 der UI-Direktive: INTEGRATION GAP - die
    Vorschau-Logik (`build_document_preview`, inkl. PII-Hervorhebung) und
    die Daten existierten laengst, nur der Zugang aus der Akte fehlte.
    Deshalb bewusst WIEDERVERWENDUNG derselben Vorschau statt einer
    zweiten Darstellungslogik.

    Aktenisolation (CLAUDE.md): das Dokument muss tatsaechlich zu DIESER
    Akte gehoeren - sonst 404, nicht etwa "irgendein Dokument anzeigen".
    """
    matter = get_or_404(db, Matter, matter_id, "Akte")
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.matter_id == matter_id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht in dieser Akte")

    # "Erkannte Fristen" (16.09., UI/UX-Sweep - Referenz `24_dokument_
    # editor_ki_assistent.png` zeigt vom System erkannte Fristen zu einem
    # Dokument). Reine Anzeige bereits vorhandener `Deadline`-Zeilen
    # (`DeadlineAnalysisService` legt sie automatisch nach der Text-
    # extraktion an, siehe app/deadlines/service.py) - KEIN neues
    # Aufgaben-/Prioritaets-Datenmodell und KEINE "Uebernehmen"-Aktion
    # (das waere der bereits dokumentierte FALL-3-Fund "Aufgaben &
    # Fristen", siehe OPEN_ISSUES.md - hier nur ehrliche Sichtbarkeit
    # dessen, was der Extractor bereits gefunden hat).
    document_deadlines = (
        db.query(Deadline)
        .filter(Deadline.document_id == document_id)
        .order_by(Deadline.due_date.asc())
        .all()
    )

    # ECHTE VISUELLE DOKUMENTANSICHT (20.09., Owner-Direktive
    # "PRIORITAETSERGAENZUNG: ECHTER DOKUMENTVIEWER"): STRIKT getrennt von
    # der Textvorschau oben (`build_document_preview`, PII-Hervorhebung) -
    # diese liest die tatsaechlich gespeicherte Originaldatei
    # (`document.file_path`) und stellt sie visuell dar (echtes Seiten-
    # Rendering fuer PDF/DOCX via app/documents/rendering.py, direktes
    # Bild fuer Bildformate, Textansicht fuer .txt, ehrlicher Fallback
    # fuer alles andere). Siehe Modul-Docstring von rendering.py fuer die
    # Begruendung der Trennung.
    viewer_mode = determine_viewer_mode(Path(document.file_path))

    context = {
        "request": request,
        "active_nav": "Akten",
        "current_user": current_user,
        "matter": matter,
        "document": document,
        "preview": build_document_preview(document.extracted_text),
        "document_deadlines": document_deadlines,
        "viewer_mode": viewer_mode,
        "viewer_page_dpi": DEFAULT_PAGE_DPI,
        "viewer_thumbnail_dpi": THUMBNAIL_DPI,
        # Fuer die KI-Aktionen-Formulare (16.09., UI/UX-Sweep) - dieselbe
        # Route bleibt lesend, die Formulare posten an app/web/chat_router.py.
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }
    return templates.TemplateResponse(request, "matter_document.html", context)


@router.get("/{matter_id}/document/{document_id}/page/{page_number}.png")
def matter_document_page_image(
    matter_id: str,
    document_id: str,
    page_number: int,
    dpi: int = Query(default=DEFAULT_PAGE_DPI),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> Response:
    """Liefert EIN echtes gerenderetes Seitenbild (PNG) der tatsaechlich
    gespeicherten Originaldatei - genutzt sowohl fuer die Thumbnail-Leiste
    (niedriges `dpi`) als auch die grosse Hauptansicht (hoeheres `dpi`) im
    Dokumentviewer (`matter_document.html`). Dieselbe Aktenisolations-
    Pruefung wie `matter_document_view`/`matter_document_download`.

    Bewusst als eigene, einfache Bild-Route statt eines groesseren
    Streaming-/Multi-Page-Endpunkts - der Viewer laedt jede Seite einzeln
    per <img src="...">, exakt wie Thumbnails/Hauptbild in der Referenz
    (`28_dokument_vorschau_export.png`)."""
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.matter_id == matter_id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht in dieser Akte")

    try:
        png_bytes = render_page_png(Path(document.file_path), page_number, dpi=dpi)
    except DocumentRenderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(content=png_bytes, media_type="image/png")


@router.get("/{matter_id}/document/{document_id}/download")
def matter_document_download(
    matter_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> FileResponse:
    """Dokument-Download (16.09., UI/UX-Sweep - Referenz
    `02_chat_dokumentkontext.png` zeigt "Herunterladen" als Aktion).
    Bewusst weiterhin rein lesend (kein DB-Schreibzugriff, siehe Modul-
    docstring) - `document.file_path` ist ein serverseitig beim Upload
    erzeugter Pfad (siehe app/chat/service.py::attach_document), kein
    Nutzereingabe-Pfad, daher keine Path-Traversal-Pruefung noetig (anders
    als beim Dateinamen selbst, der nur als Download-Dateiname verwendet
    wird). Dieselbe Aktenisolations-Pruefung wie `matter_document_view`."""
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.matter_id == matter_id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht in dieser Akte")

    file_path = Path(document.file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht mehr auf dem Server vorhanden")

    return FileResponse(
        path=file_path,
        filename=document.original_filename or file_path.name,
        media_type=document.mime_type or "application/octet-stream",
    )
