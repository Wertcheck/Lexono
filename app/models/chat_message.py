"""ChatMessage – eine einzelne Nachricht (Nutzer ODER KI-Antwort) innerhalb
einer ChatConversation.

WICHTIG (Datenschutz): `content` einer ASSISTANT-Nachricht ist entweder
- der bereits lokal REKONSTRUIERTE Klartext einer Claude-Antwort (nach
  `ClaudePrivacyGateway.reconstruct_response`, siehe app/chat/service.py)
  - identische Sensibilitaet/Speicherort-Logik wie `Draft.content`, oder
- eine feste, inhaltsfreie Meldung aus `friendly_block_message()`
  (app/privacy/api_logger.py) bei einer blockierten/fehlgeschlagenen
  Anfrage - NIE die rohen Blockierungsgruende (koennten PII enthalten,
  siehe dortige Begruendung).

Es gibt HIER keinen Codepfad, der unpseudonymisierten Text an die Cloud
sendet oder rohe Blockierungsgruende speichert - beide Garantien kommen
strukturell aus der Wiederverwendung von `DraftingService.create_draft`
(app/chat/service.py), nicht aus einer neuen, hier eingefuehrten Pruefung."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

VALID_CHAT_MESSAGE_ROLES = frozenset({"user", "assistant"})


class ChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("chat_conversations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # True bei einer blockierten/fehlgeschlagenen Anfrage (Privacy-Gate,
    # Kostenlimit, kein API-Key, technischer Fehler) - UI zeigt solche
    # Nachrichten sichtbar anders an (siehe chat.html), damit der Anwalt nie
    # eine Blockierungsmeldung mit einer echten KI-Antwort verwechselt.
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Verweist auf die tatsaechlich erzeugte Draft-Zeile (nur bei
    # role="assistant" UND blocked=False) - reine Nachvollziehbarkeits-
    # verknuepfung zur bereits bestehenden Entwurfsansicht, kein Ersatz fuer
    # dortige Freigabe-/Audit-Mechanik.
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id"), nullable=True)

    conversation: Mapped["ChatConversation"] = relationship(back_populates="messages")
    draft: Mapped["Draft | None"] = relationship()
    attached_documents: Mapped[list["ChatMessageDocument"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class ChatMessageDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Verknuepfung zwischen einer Nutzernachricht und den in GENAU diesem
    Zug hochgeladenen Dokumenten - gleiches Prinzip wie DraftSourceLink/
    DraftKnowledgeItemLink (app/models/draft_reference_links.py): haelt
    fest, was zum Zeitpunkt dieser Nachricht tatsaechlich angehaengt war,
    unabhaengig davon, was spaeter mit dem Document-Datensatz passiert."""

    __tablename__ = "chat_message_documents"

    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )

    message: Mapped["ChatMessage"] = relationship(back_populates="attached_documents")
    document: Mapped["Document"] = relationship()
