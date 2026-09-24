"""Chat – neue zentrale Arbeitsoberfläche des Dashboards (UI-Überarbeitung).

Login führt jetzt hierher (siehe app/web/auth_router.py/app/web/router.py) -
Monitoring/Administration bleiben unverändert über die Navigation
erreichbar, sind aber nicht mehr die erste Seite nach dem Login.

Bewusst KEIN direkter Cloud-Aufruf, KEINE zweite KI-/Privacy-Architektur:
jede Nachricht durchläuft `ChatService.send_message` ->
`DraftingService.create_draft` -> `ClaudePrivacyGateway` - denselben,
bereits vollständig getesteten Weg wie der Schriftsatz-Generator
(app/web/schriftsatz_router.py, identisches Muster für Login/CSRF/
Rollenprüfung/Upload-Sicherheit - siehe dort für die ausführliche
Begründung der einzelnen Bausteine, hier nicht wiederholt)."""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import (
    PERM_CLAUDE_CALL,
    PermissionDeniedError,
    require_login,
    require_role,
)
from app.chat.document_preview import build_document_preview
from app.chat.service import ChatService
from app.config import get_settings
from app.db.session import get_db
from app.documents.extraction import SUPPORTED_TEXT_EXTENSIONS
from app.models import (
    AuditEvent,
    ChatConversation,
    ChatMessage,
    Document,
    DraftKnowledgeItemLink,
    DraftSourceLink,
    LawSection,
    Matter,
    Message,
    User,
)
from app.web.service_factory import WritingProviderNotConfiguredError, get_drafting_service
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/chat", tags=["dashboard-chat"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Gleiche Formate wie der Schriftsatz-Generator (app/web/schriftsatz_router.py)
# - der Chat nutzt denselben Dokumentverarbeitungsweg, keine eigene Logik.
# Groessenlimit siehe app/chat/service.py::_MAX_UPLOAD_SIZE_BYTES (dort
# geprueft, wo der Inhalt tatsaechlich gelesen wird).
_ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx"}
assert _ALLOWED_UPLOAD_EXTENSIONS <= SUPPORTED_TEXT_EXTENSIONS


def _get_chat_service() -> ChatService:
    settings = get_settings()
    return ChatService(settings.chat_upload_storage_dir)


def _require_own_conversation(
    db: Session, conversation_id: str, current_user: User
) -> ChatConversation:
    """Lädt eine Konversation und stellt sicher, dass sie dem
    angemeldeten Nutzer gehört - eine Konversation ist eine private
    Arbeitsfläche, kein geteiltes Dashboard-Objekt wie z. B. eine Akte."""
    conversation = get_or_404(db, ChatConversation, conversation_id, "Unterhaltung")
    if conversation.user_id != current_user.id:
        raise PermissionDeniedError("Diese Unterhaltung gehört einer anderen Person")
    return conversation


@router.get("", response_class=HTMLResponse)
def chat_home(
    request: Request,
    error: str | None = None,
    new: bool = False,
    matter: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """Neue Startseite nach dem Login: zeigt die zuletzt aktive
    Unterhaltung, oder einen ruhigen Leerzustand, wenn noch keine
    existiert (siehe chat.html).

    `?new=1` (01.09., real gefundener Bug): "Neuen Chat starten"/das
    "+"-Icon in der Unterhaltungsliste verlinkten bisher BEIDE einfach
    auf diese Route ohne Parameter - bei bereits vorhandenem
    Unterhaltungsverlauf zeigte das faelschlich wieder die zuletzt
    aktive Unterhaltung statt eines echten Leerzustands. Erzwingt
    `active_conversation=None`, unabhaengig von vorhandenem Verlauf -
    kein neuer Datensatz wird angelegt (die erste Nachricht legt wie
    bisher eine neue Unterhaltung an, siehe chat_send weiter unten)."""
    chat_service = _get_chat_service()
    conversations = chat_service.list_conversations(db, user=current_user)
    active_conversation = None if new else (conversations[0] if conversations else None)

    # `?matter=<id>` (14.09.): aus der Aktendetailansicht heraus einen Chat
    # MIT dieser Akte beginnen. ECHTER FUND beim UI-Durchgang: die
    # Aktenseite musste bisher woertlich einraeumen, dass das "noch nicht
    # moeglich" sei - ein neuer Chat legte immer eine eigene, leere
    # Schnellakte an (`matter_id=None` fest verdrahtet, siehe chat_send).
    # Der Anwalt stand damit in einer vollstaendig gefuellten Akte und
    # konnte genau mit dieser NICHT im Chat weiterarbeiten. Die Faehigkeit
    # existierte im Service laengst (`ChatService.create_conversation`
    # nimmt `matter_id` entgegen) - sie war nur nie mit der Weboberflaeche
    # verbunden. Die Akte wird hier nur VORGEMERKT (Formularfeld), ein
    # Datensatz entsteht weiterhin erst mit der ersten Nachricht.
    pending_matter = get_or_404(db, Matter, matter, "Akte") if matter else None

    return _render_chat_page(
        request,
        db,
        current_user,
        conversations,
        active_conversation,
        error,
        pending_matter=pending_matter,
    )


@router.get("/{conversation_id}", response_class=HTMLResponse)
def chat_conversation_page(
    conversation_id: str,
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    chat_service = _get_chat_service()
    conversations = chat_service.list_conversations(db, user=current_user)
    active_conversation = _require_own_conversation(db, conversation_id, current_user)

    return _render_chat_page(request, db, current_user, conversations, active_conversation, error)


@router.get(
    "/{conversation_id}/document/{document_id}",
    response_class=HTMLResponse,
    response_model=None,
)
def chat_document_view(
    conversation_id: str,
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse | RedirectResponse:
    """Dokument-Workspace (Masterprompt V2, Task #62): zeigt ein im Chat
    angehängtes Dokument inline mit hervorgehobenen erkannten
    Mandantendaten + Kontextleiste, bei eingeklappter Unterhaltungsliste -
    der Chat-Verlauf/Composer derselben Konversation bleibt daneben
    weiterhin nutzbar (kein separates, isoliertes "Dokumenten-Modul").

    Der extrahierte Dokumenttext (`Document.extracted_text`) wird HIER, bei
    jedem Aufruf frisch, durch dieselben Erkennungsdetektoren geschickt wie
    der echte Pseudonymisierungspfad (siehe app/chat/document_preview.py) -
    es wird nichts zusätzlich dauerhaft gespeichert."""
    chat_service = _get_chat_service()
    conversations = chat_service.list_conversations(db, user=current_user)
    active_conversation = _require_own_conversation(db, conversation_id, current_user)
    document = chat_service.get_attached_document(
        db, conversation=active_conversation, document_id=document_id
    )
    if document is None:
        return _redirect_with_error(
            active_conversation.id, "Dokument wurde in dieser Unterhaltung nicht gefunden."
        )

    preview = build_document_preview(document.extracted_text)
    return _render_chat_page(
        request,
        db,
        current_user,
        conversations,
        active_conversation,
        viewing_document=document,
        document_preview=preview,
    )


def _render_chat_page(
    request: Request,
    db: Session,
    current_user: User,
    conversations: list[ChatConversation],
    active_conversation: ChatConversation | None,
    error: str | None = None,
    viewing_document: Document | None = None,
    document_preview=None,
    pending_matter: Matter | None = None,
) -> HTMLResponse:
    settings = get_settings()
    # Phase 3 (§71): war bisher nur auf den direkten Dev-Modus geprueft
    # (anthropic_api_key) - eine echte Kanzlei-Installation im
    # Produktionsmodus hat KEINEN anthropic_api_key gesetzt, sondern
    # ausschliesslich lexono_gateway_url (siehe ARCHITECTURE.md §70) und
    # haette den Cloud-KI-Status faelschlich als "nicht konfiguriert"
    # angezeigt, obwohl der Gateway-Pfad korrekt eingerichtet war. Deckt
    # jetzt beide Faelle ab, analog zur Auswahllogik in
    # app/ai_providers/factory.py::build_writing_provider.
    provider_configured = bool(settings.lexono_gateway_url) or settings.anthropic_api_key is not None
    messages = active_conversation.messages if active_conversation else []
    # UI/UX-Überarbeitung, Phase 4 (13.09.): reale Akten-Auswahl für die
    # "Aktenbezug"-Karte (Chat einer bestehenden Akte zuordnen, mehrfach
    # angefragt - siehe DECISIONS.md) - einfache Liste, keine Suche, da
    # eine typische Kanzlei-Installation überschaubar viele Akten hat;
    # bei Bedarf später um Suche erweiterbar, ohne diese Struktur zu
    # ändern.
    other_matters = (
        db.query(Matter)
        .filter(Matter.id != active_conversation.matter_id)
        .order_by(Matter.updated_at.desc())
        .limit(200)
        .all()
        if active_conversation
        else []
    )
    context = {
        "request": request,
        "active_nav": "Chat",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "conversations": conversations,
        "pending_matter": pending_matter,
        "active_conversation": active_conversation,
        "messages": messages,
        "message_sources": _gather_message_sources(db, messages),
        "other_matters": other_matters,
        "allowed_upload_extensions": sorted(_ALLOWED_UPLOAD_EXTENSIONS),
        "provider_configured": provider_configured,
        # Kein erfundener Zwischenzustand, solange der stille Startcheck
        # (app/main.py::_run_silent_local_ai_check) noch läuft oder in
        # Tests kein Lifespan durchlief - siehe chat.html für die
        # entsprechende "wird geprüft..."-Darstellung.
        "local_ai_status": getattr(request.app.state, "local_ai_status", None),
        "error": error,
        # Dokument-Workspace (Task #62) - beide None ausserhalb von
        # chat_document_view, chat.html schaltet darueber zwischen dem
        # normalen Zwei-Spalten-Chat und dem Drei-Spalten-Workspace um.
        "viewing_document": viewing_document,
        "document_preview": document_preview,
    }
    return templates.TemplateResponse(request, "chat.html", context)


def _gather_message_sources(db: Session, messages: list[ChatMessage]) -> dict[str, list[dict]]:
    """UI/UX-Überarbeitung, Phase 4 (13.09.): "Quellen & Verweise"-Karte
    unter einer KI-Antwort - AUSSCHLIESSLICH real bereits zum
    Erstellungszeitpunkt persistierte Verknüpfungen
    (`DraftSourceLink`/`DraftKnowledgeItemLink`, siehe app/models/
    draft_reference_links.py - identisches Muster wie draft_detail.html),
    NIE neu berechnet/erfunden. Nur Nachrichten mit `draft_id` (echte
    KI-Antwort, keine blockierte/technische Meldung) liefern überhaupt
    einen Eintrag.

    Erweiterung (13.09., Anbindung der Gesetzesbibliothek): Nachrichten mit
    `law_section_id` statt `draft_id` (siehe app/chat/service.py::
    _answer_pure_norm_question - Direktantwort aus der lokalen Gesetzes-
    bibliothek OHNE Draft/Claude-Aufruf) liefern denselben Kartenaufbau, mit
    dem echten, aus amtlichen Metadaten abgeleiteten `source_url` als Link
    ("Originalquelle öffnen ↗", siehe chat.html) - kein Sonderfall im
    Template nötig, da dieselbe {"title", "reference", "url"}-Form
    verwendet wird."""
    draft_ids = [m.draft_id for m in messages if m.draft_id]
    law_section_ids = [m.law_section_id for m in messages if m.law_section_id]
    if not draft_ids and not law_section_ids:
        return {}

    result: dict[str, list[dict]] = {}
    if draft_ids:
        source_links = (
            db.query(DraftSourceLink).filter(DraftSourceLink.draft_id.in_(draft_ids)).all()
        )
        knowledge_links = (
            db.query(DraftKnowledgeItemLink)
            .filter(DraftKnowledgeItemLink.draft_id.in_(draft_ids))
            .all()
        )
        by_draft: dict[str, list[dict]] = {}
        for link in source_links:
            by_draft.setdefault(link.draft_id, []).append(
                {"title": link.source.title, "reference": link.source.reference, "url": link.source.url}
            )
        for link in knowledge_links:
            by_draft.setdefault(link.draft_id, []).append(
                {"title": link.knowledge_item.title, "reference": "Kanzlei-Wissen", "url": None}
            )
        for message in messages:
            if message.draft_id and message.draft_id in by_draft:
                result[message.id] = by_draft[message.draft_id]

    if law_section_ids:
        sections = {
            section.id: section
            for section in db.query(LawSection).filter(LawSection.id.in_(law_section_ids)).all()
        }
        for message in messages:
            section = sections.get(message.law_section_id) if message.law_section_id else None
            if section is not None:
                reference = section.source_name
                if section.last_updated:
                    reference = f"{reference}, Stand: {section.last_updated}"
                result[message.id] = [
                    {
                        "title": section.title or f"{section.law_code} {section.section_number}",
                        "reference": reference,
                        "url": section.source_url,
                    }
                ]

    return result


def _redirect_with_error(conversation_id: str | None, message: str) -> RedirectResponse:
    target = f"/dashboard/chat/{conversation_id}" if conversation_id else "/dashboard/chat"
    return RedirectResponse(url=f"{target}?error={message}", status_code=303)


def _validate_uploads(documents: list[UploadFile]) -> str | None:
    """Gibt eine Fehlermeldung zurück (oder None), OHNE bereits etwas zu
    speichern/anzulegen - Validierung muss VOR jedem Datenbank-/Dateisystem-
    Zugriff abgeschlossen sein (gleiches Prinzip wie
    app/web/schriftsatz_router.py::_validate_uploads), damit ein
    abgelehnter Upload nicht trotzdem eine leere Unterhaltung anlegt."""
    for upload in documents:
        if not upload.filename:
            continue
        suffix_ok = any(upload.filename.lower().endswith(ext) for ext in _ALLOWED_UPLOAD_EXTENSIONS)
        if not suffix_ok:
            return (
                f"Dateityp von '{upload.filename}' wird nicht unterstützt "
                f"(erlaubt: {', '.join(sorted(_ALLOWED_UPLOAD_EXTENSIONS))})."
            )
    return None


@router.post("/send")
def send_message(
    request: Request,
    conversation_id: str = Form(""),
    matter_id: str = Form(""),
    content: str = Form(""),
    documents: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLAUDE_CALL)),
) -> RedirectResponse:
    """Eine Chat-Runde: legt bei Bedarf eine neue Unterhaltung an, hängt
    optional Dokumente an, speichert die Nutzernachricht und erzeugt die
    KI-Antwort - immer über `ChatService`/`DraftingService`, nie direkt."""
    content = content.strip()
    if not content and not any(doc.filename for doc in documents):
        return _redirect_with_error(conversation_id or None, "Bitte eine Nachricht eingeben.")

    upload_error = _validate_uploads(documents)
    if upload_error:
        return _redirect_with_error(conversation_id or None, upload_error)

    chat_service = _get_chat_service()

    if conversation_id:
        conversation = _require_own_conversation(db, conversation_id, current_user)
    else:
        # `matter_id` aus dem Formular (14.09.): stammt aus dem
        # "Mit dieser Akte im Chat arbeiten"-Einstieg der Aktendetailseite.
        # Ohne Angabe bleibt das Verhalten unveraendert (`None` ->
        # automatische Schnellakte). Die Akte wird wie ueberall sonst per
        # `get_or_404` geprueft, bevor sie verwendet wird.
        selected_matter = (
            get_or_404(db, Matter, matter_id, "Akte") if matter_id else None
        )
        conversation = chat_service.create_conversation(
            db,
            user=current_user,
            matter_id=selected_matter.id if selected_matter else None,
            title=content or "Neues Dokument",
            actor=current_user.email,
        )

    settings = get_settings()
    document_ids: list[str] = []
    for upload in documents:
        if not upload.filename:
            continue
        try:
            document = chat_service.attach_document(
                db,
                conversation=conversation,
                upload=upload,
                ocr_enabled=settings.ocr_enabled,
                ocr_languages=settings.ocr_languages,
                tesseract_cmd=settings.tesseract_cmd,
                actor=current_user.email,
            )
        except ValueError as exc:
            return _redirect_with_error(conversation.id, str(exc))
        if document is not None:
            document_ids.append(document.id)

    message_text = content or "Bitte das angehängte Dokument verarbeiten."
    # CHAT-02 (15.09.): Rueckgabewert jetzt erfasst statt verworfen - die ID
    # der gerade persistierten Nachricht wird an `send_message` gereicht,
    # damit sie `_build_history` von der Gespraechsverlauf-Auswahl
    # ausschliessen kann (sonst wuerde die aktuelle Frage doppelt im
    # Claude-Payload landen: einmal als History, einmal als aktuelle
    # Nachricht - siehe app/chat/service.py::ChatService._build_history).
    user_message = chat_service.record_user_message(
        db, conversation=conversation, content=message_text, document_ids=document_ids
    )

    try:
        drafting_service = get_drafting_service()
    except WritingProviderNotConfiguredError:
        drafting_service = None

    chat_service.send_message(
        db,
        conversation=conversation,
        content=message_text,
        drafting_service=drafting_service,
        actor=current_user.email,
        current_message_id=user_message.id,
    )

    return RedirectResponse(url=f"/dashboard/chat/{conversation.id}", status_code=303)


#: "Zusammenfassen"/"Antworten" auf einer Posteingang-Nachricht (16.09.,
#: UI/UX-Sweep - Referenz `04_posteingang_nachricht_detail.png` zeigt beide
#: als eigene Aktionen). KEINE neue KI-/Privacy-Architektur: beide starten
#: schlicht eine neue Chat-Unterhaltung mit einer vorformulierten
#: Nutzernachricht, durchlaufen also GENAU denselben, bereits vollstaendig
#: getesteten Weg wie jede andere Chat-Nachricht (Aktenisolation, Privacy
#: Gateway, Fail-Closed) - `ChatService.send_message` unterscheidet
#: Zusammenfassung (`_PURPOSE_CHAT`) und Antwortentwurf (`_PURPOSE_DRAFT`,
#: ueber `_looks_like_drafting_request` - der Text unten ist bewusst so
#: formuliert, dass er zuverlaessig erkannt wird) bereits selbst, siehe
#: app/chat/service.py.
#: "Antworten" erzeugt dadurch einen echten, im Editor pruef-/bearbeitbaren
#: Entwurf - KEINEN automatischen Versand (es existiert schlicht keine
#: Versandfunktion in der Chat-Pipeline, siehe CLAUDE.md: "Keine
#: automatische externe Kommunikation ... ohne explizite Freigabe").
#:
#: Braucht zwingend eine bereits zugeordnete Akte (Aktenisolation, wie
#: ueberall sonst in der Chat-Pipeline) - die UI zeigt die Buttons deshalb
#: nur fuer zugeordnete Nachrichten (siehe partials/message_detail.html);
#: dieser serverseitige Check ist die eigentliche Absicherung gegen einen
#: manipulierten Request auf eine nicht zugeordnete Nachricht.
_MESSAGE_ACTION_PROMPTS = {
    "summarize": (
        'Fasse diese eingehende Nachricht zusammen.\n\n'
        'Betreff: {subject}\nVon: {sender}\n\n{body}'
    ),
    "draft_reply": (
        'Erstelle eine Antwort an {sender} bezüglich "{subject}":\n\n{body}'
    ),
}


def _start_conversation_with_prompt(
    db: Session,
    *,
    matter_id: str,
    title: str,
    prompt: str,
    current_user: User,
    audit_event_type: str,
    audit_details: str,
    source_message_id: str | None = None,
) -> ChatConversation:
    """Gemeinsamer Kern fuer alle "Aktion aus einer bestehenden Seite
    heraus startet einen Chat"-Einstiegspunkte (16.09., zuerst fuer
    Posteingang-Nachrichten, jetzt auch fuer Aktendokumente) - GENAU
    derselbe, bereits vollstaendig getestete `ChatService`/
    `DraftingService`-Weg wie jede normale Chat-Nachricht, nur mit einer
    vorformulierten Nutzernachricht statt Tastatureingabe. Ein Aufrufer
    pro Quelle (Nachricht/Dokument) baut nur den passenden Prompt-Text und
    das Audit-Detail, den Rest teilen sie sich - vermeidet zwei fast
    identische Kopien derselben sicherheitsrelevanten Anlage-Logik.

    `source_message_id` (17.09., Overnight-Direktive §6/§7): optionale
    Posteingang-`Message`-ID, auf die diese Aktion antwortet - durchgereicht
    bis zu `Draft.message_id` (siehe ChatService.send_message), damit
    `draft_detail.html`s "Original links"-Panel die Ursprungsnachricht samt
    Anhaengen zeigen kann. `None` (Standard) = unveraendertes Verhalten."""
    chat_service = _get_chat_service()
    conversation = chat_service.create_conversation(
        db,
        user=current_user,
        matter_id=matter_id,
        title=title,
        actor=current_user.email,
    )
    db.add(
        AuditEvent(
            entity_type="ChatConversation",
            entity_id=conversation.id,
            event_type=audit_event_type,
            actor=current_user.email,
            details=audit_details,
        )
    )
    db.commit()

    user_message = chat_service.record_user_message(
        db, conversation=conversation, content=prompt
    )

    try:
        drafting_service = get_drafting_service()
    except WritingProviderNotConfiguredError:
        drafting_service = None

    chat_service.send_message(
        db,
        conversation=conversation,
        content=prompt,
        drafting_service=drafting_service,
        actor=current_user.email,
        current_message_id=user_message.id,
        source_message_id=source_message_id,
    )
    return conversation


@router.post("/from-message/{message_id}")
def start_conversation_from_message(
    request: Request,
    message_id: str,
    action: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLAUDE_CALL)),
) -> RedirectResponse:
    prompt_template = _MESSAGE_ACTION_PROMPTS.get(action)
    if prompt_template is None:
        raise HTTPException(status_code=400, detail="Unbekannte Aktion.")

    message = get_or_404(db, Message, message_id, "Nachricht")
    if message.matter_id is None:
        raise HTTPException(
            status_code=400,
            detail="Nachricht muss zuerst einer Akte zugeordnet werden.",
        )

    prompt = prompt_template.format(
        subject=message.subject or "(kein Betreff)",
        sender=message.sender or "unbekannt",
        body=message.body_text or "(kein Inhalt extrahiert)",
    )

    conversation = _start_conversation_with_prompt(
        db,
        matter_id=message.matter_id,
        title=message.subject or "Nachricht aus dem Posteingang",
        prompt=prompt,
        current_user=current_user,
        audit_event_type="chat_started_from_inbox_message",
        audit_details=f"Aktion '{action}' aus Posteingang-Nachricht {message.id} gestartet",
        source_message_id=message.id,
    )

    return RedirectResponse(url=f"/dashboard/chat/{conversation.id}", status_code=303)


#: KI-Aktionen auf der Akte-Dokumentansicht (16.09., UI/UX-Sweep - Referenz
#: `28_dokument_vorschau_export.png` zeigt "Dokument analysieren"/
#: "Zusammenfassung erstellen"/"Wichtige Daten extrahieren"/"Schriftsatz-
#: Entwurf erstellen" als Aktionen). Identisches Muster wie die
#: Posteingang-Aktionen oben - startet eine neue, matter-gebundene Chat-
#: Unterhaltung; der Prompt nennt den Dateinamen ausdruecklich, damit sich
#: die Antwort auf GENAU dieses Dokument bezieht (die lokale Kontext-
#: Vorbereitung selbst ist immer matter-, nicht dokumentweit gescoped,
#: siehe app/ai_providers/local_ai_provider.py::_build_sachverhalt -
#: bewusst NICHT veraendert, siehe dortige Aktenisolations-Begruendung).
#: "Aufgaben & Fristen vorschlagen" aus derselben Referenz bewusst NICHT
#: uebernommen - das gehoert zum bereits dokumentierten FALL-3-Fund
#: "Aufgaben & Fristen" (kein Prioritaets-/Status-Datenmodell, siehe
#: OPEN_ISSUES.md), keine neue, hier passend erscheinende Kleinigkeit.
_DOCUMENT_ACTION_PROMPTS = {
    "analyze": (
        'Bitte analysiere das Dokument "{filename}" aus dieser Akte und '
        'fasse die wichtigsten Punkte zusammen.'
    ),
    "summarize": 'Bitte fasse das Dokument "{filename}" in wenigen Sätzen zusammen.',
    "extract_data": (
        'Bitte extrahiere die wichtigsten Daten (z. B. Fristen, Beträge, '
        'Aktenzeichen, beteiligte Parteien) aus dem Dokument "{filename}".'
    ),
    "draft_reply": (
        'Bitte erstelle einen Schriftsatz-Entwurf auf Basis des Dokuments "{filename}".'
    ),
}


@router.post("/from-document/{document_id}")
def start_conversation_from_document(
    request: Request,
    document_id: str,
    action: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLAUDE_CALL)),
) -> RedirectResponse:
    prompt_template = _DOCUMENT_ACTION_PROMPTS.get(action)
    if prompt_template is None:
        raise HTTPException(status_code=400, detail="Unbekannte Aktion.")

    document = get_or_404(db, Document, document_id, "Dokument")
    if document.matter_id is None:
        raise HTTPException(
            status_code=400,
            detail="Dokument muss zuerst einer Akte zugeordnet werden.",
        )

    filename = document.original_filename or "Dokument"
    prompt = prompt_template.format(filename=filename)

    conversation = _start_conversation_with_prompt(
        db,
        matter_id=document.matter_id,
        title=filename,
        prompt=prompt,
        current_user=current_user,
        audit_event_type="chat_started_from_matter_document",
        audit_details=f"Aktion '{action}' aus Aktendokument {document.id} gestartet",
        # 17.09.: nur gesetzt, wenn dieses Dokument selbst ein Mail-Anhang
        # ist (document.message_id) - gibt dem "Original links"-Panel in
        # draft_detail.html denselben Ursprungskontext wie bei "Antworten"
        # direkt aus dem Posteingang. `None` fuer eigenstaendig hochgeladene
        # Dokumente ohne Nachrichtenbezug (unveraendertes Verhalten).
        source_message_id=document.message_id,
    )

    return RedirectResponse(url=f"/dashboard/chat/{conversation.id}", status_code=303)


def _sse_event(payload: dict) -> str:
    """Ein einzelnes Server-Sent-Event (siehe app_chat_stream.js fuer den
    Client). `ensure_ascii=False` ist unproblematisch (SSE ist UTF-8, keine
    Kopfzeilen-Kodierung wie bei HTTP-Headern betroffen)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_error_event(message: str) -> Iterator[str]:
    """EIN "done"-Ereignis fuer einen Fehler VOR jeder Nachrichtenerzeugung
    (leere Eingabe, ungueltiger Dateityp) - nichts wurde persistiert,
    `message_id=None` signalisiert das dem Client (kein Quellen-Abruf)."""
    yield _sse_event(
        {"kind": "done", "message_id": None, "content": message, "blocked": True, "sources": []}
    )


def _stream_chat_reply(
    chat_service: ChatService,
    db: Session,
    conversation: ChatConversation,
    message_text: str,
    drafting_service,
    actor: str,
    *,
    current_message_id: str | None = None,
) -> Iterator[str]:
    """Treibt `ChatService.send_message_stream` an und formt jedes
    Ereignis in ein SSE-Frame um - das abschliessende "done"-Ereignis
    traegt bereits die "Quellen & Verweise"-Daten (`_gather_message_sources`,
    identische Form wie im nicht-streamenden Seitenaufbau), damit der
    Client ohne Zusatzanfrage die vollstaendige Karte rendern kann."""
    # ERSTES Ereignis immer "start" (traegt die - ggf. neu angelegte -
    # conversation_id), DAMIT der Client sein verstecktes Formularfeld/die
    # URL aktualisieren kann, BEVOR die eigentliche Antwort eintrifft -
    # wichtig, wenn der Nutzer aus der Startansicht heraus die allererste
    # Nachricht einer neuen Unterhaltung sendet.
    yield _sse_event({"kind": "start", "conversation_id": conversation.id})

    for event in chat_service.send_message_stream(
        db,
        conversation=conversation,
        content=message_text,
        drafting_service=drafting_service,
        actor=actor,
        current_message_id=current_message_id,
    ):
        if event.kind == "delta":
            yield _sse_event({"kind": "delta", "text": event.text})
            continue
        if event.kind == "status":
            # P1 Performance-Feedback-Follow-up (17.09.): fester,
            # inhaltsfreier Fortschritts-Hinweis (siehe DraftingService.
            # _STEP_STATUS_LABELS) - rein informativ, kein "done"-Ersatz.
            yield _sse_event({"kind": "status", "status": event.status})
            continue
        message = event.message
        assert message is not None
        sources = _gather_message_sources(db, [message]).get(message.id, [])
        yield _sse_event(
            {
                "kind": "done",
                "message_id": message.id,
                "content": message.content,
                "blocked": message.blocked,
                "sources": sources,
            }
        )


@router.post("/send-stream")
def send_message_stream(
    request: Request,
    conversation_id: str = Form(""),
    matter_id: str = Form(""),
    content: str = Form(""),
    documents: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(permission=PERM_CLAUDE_CALL)),
) -> StreamingResponse:
    """Streaming-Variante von `send_message` (13.09., Streaming-
    Architekturentscheidung, siehe DECISIONS.md) - identische Validierung/
    Persistenz der Nutzernachricht wie `/send`, liefert die KI-Antwort aber
    als `text/event-stream` (echte inkrementelle Deltas fuer den bereits
    etablierten Fast Path, siehe ChatService.send_message_stream) statt
    eines vollstaendigen Seiten-Redirects.

    Bewusst EIN separater Endpunkt statt eines Content-Negotiation-Zweigs
    in `/send` - das bestehende, voll getestete klassische Formular bleibt
    UNVERAENDERT als Fallback erreichbar (z. B. falls JS deaktiviert ist,
    siehe chat.html/app_chat_stream.js: das `<form>` postet weiterhin
    regulaer an `/send`, das JS faengt den Submit nur ab, wenn es selbst
    fehlerfrei laedt)."""
    content = content.strip()
    if not content and not any(doc.filename for doc in documents):
        return StreamingResponse(
            _stream_error_event("Bitte eine Nachricht eingeben."), media_type="text/event-stream"
        )

    upload_error = _validate_uploads(documents)
    if upload_error:
        return StreamingResponse(_stream_error_event(upload_error), media_type="text/event-stream")

    chat_service = _get_chat_service()

    if conversation_id:
        conversation = _require_own_conversation(db, conversation_id, current_user)
    else:
        # `matter_id` aus dem Formular (14.09.): stammt aus dem
        # "Mit dieser Akte im Chat arbeiten"-Einstieg der Aktendetailseite.
        # Ohne Angabe bleibt das Verhalten unveraendert (`None` ->
        # automatische Schnellakte). Die Akte wird wie ueberall sonst per
        # `get_or_404` geprueft, bevor sie verwendet wird.
        selected_matter = (
            get_or_404(db, Matter, matter_id, "Akte") if matter_id else None
        )
        conversation = chat_service.create_conversation(
            db,
            user=current_user,
            matter_id=selected_matter.id if selected_matter else None,
            title=content or "Neues Dokument",
            actor=current_user.email,
        )

    settings = get_settings()
    document_ids: list[str] = []
    for upload in documents:
        if not upload.filename:
            continue
        try:
            document = chat_service.attach_document(
                db,
                conversation=conversation,
                upload=upload,
                ocr_enabled=settings.ocr_enabled,
                ocr_languages=settings.ocr_languages,
                tesseract_cmd=settings.tesseract_cmd,
                actor=current_user.email,
            )
        except ValueError as exc:
            return StreamingResponse(_stream_error_event(str(exc)), media_type="text/event-stream")
        if document is not None:
            document_ids.append(document.id)

    message_text = content or "Bitte das angehängte Dokument verarbeiten."
    # CHAT-02 (15.09.): siehe die identische Begruendung beim nicht-
    # streamenden Pfad oben (send_message) - Rueckgabewert erfasst statt
    # verworfen, damit die aktuelle Nachricht von der History ausgeschlossen
    # werden kann.
    user_message = chat_service.record_user_message(
        db, conversation=conversation, content=message_text, document_ids=document_ids
    )

    try:
        drafting_service = get_drafting_service()
    except WritingProviderNotConfiguredError:
        drafting_service = None

    return StreamingResponse(
        _stream_chat_reply(
            chat_service,
            db,
            conversation,
            message_text,
            drafting_service,
            current_user.email,
            current_message_id=user_message.id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/link-matter")
def link_matter(
    conversation_id: str,
    matter_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    """UI/UX-Überarbeitung, Phase 4 (13.09.): "Aktenbezug"-Karte im Chat -
    ordnet eine BESTEHENDE Unterhaltung einer ANDEREN, bereits
    existierenden Akte zu (mehrfach angefragt, siehe DECISIONS.md/
    OPEN_ISSUES.md - vorher gab es dafür KEINE Funktion, jede
    Konversation blieb für immer an ihre automatisch angelegte
    Schnellakte gebunden).

    ECHTER FUND (14.09., Sicherheits-Review im Rahmen der Posteingang-
    Untersuchung): nutzte bisher `Depends(require_login)` statt
    `Depends(require_role())` - dadurch fehlte die CSRF-Pruefung, die
    `require_role()` (siehe app/auth/permissions.py) fuer JEDE
    zustandsveraendernde Route erzwingt ("Login -> CSRF -> Berechtigung").
    `require_login` ist bewusst NUR fuer rein lesende Seiten gedacht
    (siehe dessen Docstring) - diese Route aendert aber echten Zustand
    (`ChatConversation.matter_id`). `require_role()` ohne Argumente
    verlangt weiterhin KEINE zusaetzliche Rolle/Berechtigung (identisches
    Muster wie app/web/lock_router.py::lock_now) - nur die fehlende
    CSRF-Pruefung wird ergaenzt, kein neues Berechtigungskonzept.

    WICHTIG (Aktenisolation, CLAUDE.md): ändert NUR die Zuordnung für
    KÜNFTIGE Nachrichten dieser Konversation (`ChatConversation.matter_id`
    steuert, welcher Aktenkontext - Dokumente/Fristen/Wissen - beim
    nächsten `DraftingService.create_draft`-Aufruf geladen wird, siehe
    `RuleBasedLocalAIProvider.prepare_draft_context`). BEREITS gesendete
    Nachrichten/erzeugte Drafts bleiben unverändert der alten Akte
    zugeordnet (`Draft.matter_id` wird hier nicht angefasst) - kein
    rückwirkendes Vermischen von Aktenkontext."""
    conversation = _require_own_conversation(db, conversation_id, current_user)
    target_matter = get_or_404(db, Matter, matter_id, "Akte")

    previous_matter_id = conversation.matter_id
    conversation.matter_id = target_matter.id
    db.add(
        AuditEvent(
            entity_type="ChatConversation",
            entity_id=conversation.id,
            event_type="chat_relinked_to_matter",
            actor=current_user.email,
            details=f"Unterhaltung von Akte {previous_matter_id} zu Akte {target_matter.id} umgehängt",
        )
    )
    db.commit()

    return RedirectResponse(url=f"/dashboard/chat/{conversation.id}", status_code=303)


@router.post("/{conversation_id}/delete")
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role()),
) -> RedirectResponse:
    """Löscht eine Unterhaltung (20.09., Owner-Direktive "WORKSTREAM A —
    CHATS LÖSCHBAR"). Bewusst HARD-DELETE (anders als Document, siehe
    app/documents/lifecycle.py fuer die Begruendung des dortigen Soft-
    Delete) - eine Konversation ist eine private Arbeitsflaeche
    (`_require_own_conversation`-Docstring), kein aufbewahrungspflichtiges
    Mandantendokument. `ChatConversation.messages` UND deren
    `attached_documents`-Verknuepfungen (NICHT die referenzierten
    `Document`-Zeilen selbst) sind bereits `cascade="all, delete-orphan"`
    modelliert (app/models/chat_conversation.py/chat_message.py) - werden
    beim `db.delete()` automatisch mitentfernt. Ein aus dieser Unterhaltung
    erzeugter `Draft` (Schriftsatz) bleibt UNBERUEHRT (kein Cascade auf
    `ChatMessage.draft_id`, andere Richtung der Fremdschluessel-Beziehung) -
    das eigentliche Arbeitsergebnis geht beim Loeschen des Chats NICHT
    verloren. Dieselbe Eigentuemer-/IDOR-Pruefung wie jede andere
    Konversations-Aktion (`_require_own_conversation`)."""
    conversation = _require_own_conversation(db, conversation_id, current_user)

    db.add(
        AuditEvent(
            entity_type="ChatConversation",
            entity_id=conversation.id,
            event_type="chat_conversation_deleted",
            actor=current_user.email,
            details=f"Unterhaltung gelöscht: {conversation.title}",
        )
    )
    db.delete(conversation)
    db.commit()

    return RedirectResponse(url="/dashboard/chat", status_code=303)
