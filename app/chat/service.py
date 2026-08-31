"""ChatService – Persistenz + Orchestrierung fuer die Chat-Startseite.

Verantwortlich fuer:
1. Konversationen anlegen/auflisten (`create_conversation`,
   `list_conversations`) - jede Konversation ist IMMER an eine Akte
   gebunden (Aktenisolation), Auto-Anlage ueber
   `app/drafting/quick_matter.py::create_quick_matter` - identisches,
   bereits bewaehrtes Muster wie beim Schriftsatz-Generator, keine zweite
   Implementierung.
2. Dokumente an eine Konversation anhaengen (`attach_document`) - nutzt
   DENSELBEN sicheren Speicher-/Verarbeitungsweg wie
   `app/web/schriftsatz_router.py::_store_uploaded_document`
   (Pfad-Traversal-sichere Dateinamen-Behandlung, siehe dortiger
   Sicherheits-Fund) + die unveraenderte `DocumentProcessingService`
   (OCR/Textextraktion).
3. Eine Nachricht senden + KI-Antwort erzeugen (`send_message`) - ruft
   AUSSCHLIESSLICH `DraftingService.create_draft` auf (app/drafting/
   service.py) - denselben Weg, der bereits durch `ClaudePrivacyGateway`
   (Pseudonymisierung + Security-Check + Final Payload Gate) und alle
   bestehenden Tests abgesichert ist. KEIN direkter Cloud-Aufruf, KEIN
   zweiter Privacy-Pfad.

`content` einer Nutzernachricht wird 1:1 als `attorney_anmerkungen`
(siebtes Allowlist-Feld, siehe app/privacy/gateway_schema.py) an
`create_draft` uebergeben - durchlaeuft daher GENAU DENSELBEN
Pseudonymisierungs-/Security-Check-Durchlauf wie jede andere anwaltliche
Anmerkung im System, keine Sonderbehandlung."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.documents.service import DocumentProcessingService
from app.drafting.quick_matter import create_quick_matter
from app.drafting.service import DraftingService
from app.ingestion.stability import compute_sha256
from app.models import ChatConversation, ChatMessage, ChatMessageDocument, Document, User
from app.privacy.api_logger import friendly_block_message

#: Fest verdrahteter Schreibauftrag, exakt wie im Schriftsatz-Generator
#: (app/web/schriftsatz_router.py) - kein Freitextfeld fuer den "purpose",
#: um versehentlich einen nicht erlaubten Zweck zu erzeugen (siehe
#: ALLOWED_PURPOSES, app/privacy/security_check.py).
_PURPOSE = "formulate_draft"

_PROVIDER_UNAVAILABLE_MESSAGE = (
    "Die Cloud-KI ist nicht konfiguriert (kein Anthropic-API-Schlüssel hinterlegt). "
    "Bitte eine Administratorin oder einen Administrator kontaktieren, bevor Sie den "
    "Chat für KI-Antworten nutzen können."
)

_UNEXPECTED_ERROR_MESSAGE = (
    "Bei der Verarbeitung ist ein unerwarteter technischer Fehler aufgetreten. "
    "Die Anfrage wurde nicht an die Cloud-KI gesendet. Bitte erneut versuchen."
)

#: Gleiches Limit wie der Schriftsatz-Generator (app/web/schriftsatz_router.py)
#: - Groessenpruefung erfolgt HIER (nicht vorab im Router), weil erst hier
#: tatsaechlich gelesen wird (`UploadFile.size` ist bei manchen Clients
#: nicht zuverlaessig vorab gesetzt).
_MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB pro Datei


def _derive_title(first_message: str) -> str:
    """Kurzer, rein lokaler Anzeige-Titel aus der ersten Nutzernachricht -
    nie an die Cloud gesendet, nur fuer die Konversationsliste."""
    normalized = " ".join(first_message.split())
    if len(normalized) <= 60:
        return normalized or "Neue Unterhaltung"
    return normalized[:57] + "…"


class ChatService:
    def __init__(self, storage_dir: Path | str) -> None:
        self.storage_dir = Path(storage_dir)

    # --- Konversationen -------------------------------------------------

    def create_conversation(
        self,
        db: Session,
        *,
        user: User,
        matter_id: str | None,
        new_matter_title: str | None = None,
        new_client_name: str | None = None,
        title: str,
        actor: str,
    ) -> ChatConversation:
        if not matter_id:
            matter = create_quick_matter(
                db, title=new_matter_title, client_name=new_client_name, actor=actor
            )
            matter_id = matter.id
        conversation = ChatConversation(
            matter_id=matter_id, user_id=user.id, title=_derive_title(title)
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    def list_conversations(self, db: Session, *, user: User) -> list[ChatConversation]:
        return (
            db.query(ChatConversation)
            .filter(ChatConversation.user_id == user.id)
            .order_by(ChatConversation.updated_at.desc())
            .all()
        )

    def get_conversation(self, db: Session, conversation_id: str) -> ChatConversation | None:
        return db.query(ChatConversation).filter_by(id=conversation_id).first()

    # --- Dokumente --------------------------------------------------------

    def attach_document(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        upload: UploadFile,
        ocr_enabled: bool,
        ocr_languages: str,
        tesseract_cmd: str | None,
        actor: str,
    ) -> Document | None:
        if not upload.filename:
            return None

        content = upload.file.read()
        if len(content) > _MAX_UPLOAD_SIZE_BYTES:
            raise ValueError(
                f"Datei '{upload.filename}' überschreitet die maximale Größe von "
                f"{_MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
            )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # SICHERHEITSKRITISCH (Path Traversal, siehe Pilot Readiness Review
        # + app/web/schriftsatz_router.py::_store_uploaded_document fuer den
        # real gefundenen und behobenen Fund): NIEMALS `upload.filename`
        # ungefiltert in den Zielpfad einbauen - `.name` behaelt nur den
        # letzten Pfadbestandteil.
        safe_filename = Path(upload.filename).name or "unbenannt"
        destination_path = self.storage_dir / f"{uuid.uuid4()}_{safe_filename}"
        destination_path.write_bytes(content)

        mime_type, _ = mimetypes.guess_type(upload.filename)
        document = Document(
            matter_id=conversation.matter_id,
            file_path=str(destination_path),
            original_filename=upload.filename,
            content_hash=compute_sha256(destination_path),
            mime_type=mime_type,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        processor = DocumentProcessingService(
            ocr_enabled=ocr_enabled, ocr_languages=ocr_languages, tesseract_cmd=tesseract_cmd
        )
        processor.process_document(document, db, actor=actor)
        return document

    # --- Nachrichten --------------------------------------------------------

    def _create_message(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        role: str,
        content: str,
        blocked: bool = False,
        draft_id: str | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            conversation_id=conversation.id,
            role=role,
            content=content,
            blocked=blocked,
            draft_id=draft_id,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    def get_attached_document(
        self, db: Session, *, conversation: ChatConversation, document_id: str
    ) -> Document | None:
        """Laedt ein Dokument NUR, wenn es tatsaechlich an eine Nachricht
        DIESER Konversation angehaengt ist (Dokument-Workspace, Masterprompt
        V2, Task #62) - verhindert, dass ueber eine erratene/manipulierte
        Dokument-ID ein Dokument aus einer fremden Konversation/Akte
        abgerufen werden kann (Aktenisolation, CLAUDE.md "Aktenkontext
        strikt isolieren"). Der Aufrufer (chat_router.py) hat bereits
        vorher per `_require_own_conversation` sichergestellt, dass die
        Konversation selbst dem angemeldeten Nutzer gehoert."""
        return (
            db.query(Document)
            .join(ChatMessageDocument, ChatMessageDocument.document_id == Document.id)
            .join(ChatMessage, ChatMessage.id == ChatMessageDocument.message_id)
            .filter(
                ChatMessage.conversation_id == conversation.id,
                Document.id == document_id,
            )
            .first()
        )

    def record_user_message(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        content: str,
        document_ids: list[str] | None = None,
    ) -> ChatMessage:
        message = self._create_message(db, conversation=conversation, role="user", content=content)
        for document_id in document_ids or []:
            db.add(ChatMessageDocument(message_id=message.id, document_id=document_id))
        if document_ids:
            db.commit()
        return message

    def send_message(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        content: str,
        drafting_service: DraftingService | None,
        actor: str,
    ) -> ChatMessage:
        """Erzeugt die KI-Antwort auf die zuletzt gespeicherte Nutzer-
        nachricht. `drafting_service=None` bedeutet: kein API-Schluessel
        konfiguriert (vom Router bereits per `ProviderNotConfiguredError`
        beim Bauen des Service erkannt, siehe app/web/chat_router.py) -
        wird hier als eigener, klar erkennbarer Chat-Zustand behandelt,
        NICHT als Ausnahme."""
        if drafting_service is None:
            return self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=_PROVIDER_UNAVAILABLE_MESSAGE,
                blocked=True,
            )

        try:
            result = drafting_service.create_draft(
                conversation.matter_id,
                _PURPOSE,
                db,
                attorney_anmerkungen=content,
                actor=actor,
            )
        except Exception:  # noqa: BLE001 - siehe Moduldocstring: Chat-Fehlerzustand
            # statt einer unbehandelten Ausnahme. Fail-closed bleibt
            # gewahrt: wirft die Gateway-Pseudonymisierung selbst eine
            # Ausnahme (z. B. Presidio/spaCy-Fehler), wurde bereits vorher
            # KEIN Payload gebaut und KEIN Cloud-Aufruf versucht - siehe
            # ClaudePrivacyGateway.prepare_request, dort bewusst ohne
            # Fallback-Pfad, der Pseudonymisierung uebersprungen haette.
            return self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=_UNEXPECTED_ERROR_MESSAGE,
                blocked=True,
            )

        if result.success:
            return self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=result.draft_text or "",
                draft_id=result.draft_id,
            )

        return self._create_message(
            db,
            conversation=conversation,
            role="assistant",
            content=friendly_block_message(result.blocked_reasons),
            blocked=True,
        )
