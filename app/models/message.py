"""Message – eingehende oder ausgehende Nachricht (i. d. R. E-Mail).

`matter_id` ist bewusst nullable: eine neu eingegangene Nachricht durchlaeuft
den Workflow-Zustand NEEDS_MATTER_MATCH, bevor sie einer Akte zugeordnet ist
(siehe ARCHITECTURE.md §6). Erst nach Zuordnung wird `matter_id` gesetzt.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    matter_id: Mapped[str | None] = mapped_column(
        ForeignKey("matters.id"), nullable=True, index=True
    )
    # Message-ID-Header o. ae., zur Deduplizierung/Nachvollziehbarkeit.
    external_message_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # inbound/outbound
    sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lesestatus im Posteingang (Migration schritt3_038): None = ungelesen. Gesetzt beim ausdruecklichen Oeffnen der
    # Nachricht oder per "Als gelesen markieren" (app/web/router.py); ausgehende Nachrichten gelten nie als ungelesen.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_unread(self) -> bool:
        return self.direction == "inbound" and self.read_at is None

    matter: Mapped["Matter | None"] = relationship(back_populates="messages")
    documents: Mapped[list["Document"]] = relationship(back_populates="message")
