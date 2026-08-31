"""ChatConversation – ein Chat-Verlauf der neuen Chat-Startseite.

Die Chat-Oberflaeche ist bewusst KEINE eigenstaendige KI-/Cloud-Architektur
(siehe app/chat/service.py) - dieses Modell haelt nur die Verlaufsstruktur
(welche Nachrichten gehoeren zusammen, welcher Akte ist der Chat
zugeordnet) fest. Jede Konversation ist IMMER genau einer Akte zugeordnet
(Aktenisolation, CLAUDE.md-Grundregel) - fehlt bei Chat-Beginn eine
Aktenauswahl, wird wie beim bestehenden Schriftsatz-Generator automatisch
eine neue Akte angelegt (app/drafting/quick_matter.py::create_quick_matter,
identisches, bereits bewaehrtes Muster - keine zweite Implementierung)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_conversations"

    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    # Kurzer, automatisch aus der ersten Nutzernachricht abgeleiteter Titel
    # (siehe app/chat/service.py) - rein lokale UI-Beschriftung, nie an die
    # Cloud gesendet.
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    matter: Mapped["Matter"] = relationship()
    user: Mapped["User"] = relationship()
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )
