"""Upload-Vorgang im Chat (§59): Idempotenz gegen Doppelklick, parallele Requests und Wiederholung nach verlorener
Antwort - derselbe Mechanismus wie app/models/schriftsatz_operation.py (§58), mit Chat-Bezug.

Jede Chat-Seite traegt eine serverseitig ausgestellte, signierte, nutzergebundene Upload-Vorgangs-ID. Nur ein Request
MIT Dateien verbraucht sie (§57: ein Upload ist ein eigener Vorgang ohne KI); eine spaetere, bewusst gesendete
Nachricht ohne Dateien ist davon unabhaengig. Der ERSTE Request legt die Zeile an (ID = Primaerschluessel, atomar);
jede Wiederholung DESSELBEN Uploads legt weder Unterhaltung, Dokumente noch Upload-Nachricht erneut an, sondern
liefert das gespeicherte Ergebnis. Nur IDs, Zustaende und ein HMAC-Fingerabdruck - keine Inhalte, keine Dateinamen.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ChatUploadOperation(TimestampMixin, Base):
    __tablename__ = "chat_upload_operations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # Vorgangs-ID (uuid4 hex, aus dem signierten Token)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # processing / done / failed (wie §58)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True
    )
    # JSON-Liste der in diesem Vorgang gespeicherten Dokument-IDs (fortlaufend - auch bei Teilfehlern nachvollziehbar)
    document_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
