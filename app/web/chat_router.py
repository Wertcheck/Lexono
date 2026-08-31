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

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import (
    PERM_CLAUDE_CALL,
    PermissionDeniedError,
    require_login,
    require_role,
)
from app.chat.service import ChatService
from app.config import get_settings
from app.db.session import get_db
from app.documents.extraction import SUPPORTED_TEXT_EXTENSIONS
from app.models import ChatConversation, User
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
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> HTMLResponse:
    """Neue Startseite nach dem Login: zeigt die zuletzt aktive
    Unterhaltung, oder einen ruhigen Leerzustand, wenn noch keine
    existiert (siehe chat.html)."""
    chat_service = _get_chat_service()
    conversations = chat_service.list_conversations(db, user=current_user)
    active_conversation = conversations[0] if conversations else None

    return _render_chat_page(request, db, current_user, conversations, active_conversation, error)


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


def _render_chat_page(
    request: Request,
    db: Session,
    current_user: User,
    conversations: list[ChatConversation],
    active_conversation: ChatConversation | None,
    error: str | None = None,
) -> HTMLResponse:
    settings = get_settings()
    context = {
        "request": request,
        "active_nav": "Chat",
        "current_user": current_user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "conversations": conversations,
        "active_conversation": active_conversation,
        "messages": active_conversation.messages if active_conversation else [],
        "allowed_upload_extensions": sorted(_ALLOWED_UPLOAD_EXTENSIONS),
        "provider_configured": settings.anthropic_api_key is not None,
        "error": error,
    }
    return templates.TemplateResponse(request, "chat.html", context)


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
        conversation = chat_service.create_conversation(
            db,
            user=current_user,
            matter_id=None,
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
    chat_service.record_user_message(
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
    )

    return RedirectResponse(url=f"/dashboard/chat/{conversation.id}", status_code=303)
