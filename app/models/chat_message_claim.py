"""Prozessuebergreifende Verarbeitungssperre je Chat-Nutzernachricht (12.10.2026).

Ersetzt die frühere prozesslokale Sperre: Lexono verhindert per Windows-Mutex nur einen zweiten Start in DERSELBEN
Benutzersitzung (run.py, `_SINGLE_INSTANCE_MUTEX_NAME` ohne "Global\\"-Praefix). Ein zweites Benutzerkonto oder ein
Entwicklungsserver auf derselben Datenbank sind moeglich. Die Sperre ist deshalb eine Zeile mit der Nachrichten-ID als
PRIMAERSCHLUESSEL: genau ein Prozess kann sie anlegen (atomar durch die Datenbank). Sie wird nach Abschluss der
Verarbeitung (Antwort gespeichert oder Datenschutz-Rueckfrage ohne Cloud-Aufruf) wieder entfernt. Bleibt sie nach
einem Absturz liegen, gilt der Zustand als UNKLAR - es wird nichts automatisch erneut gesendet (fail-closed).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatMessageClaim(Base):
    __tablename__ = "chat_message_claims"

    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Nur zur Diagnose: Prozess-ID und Startzeitpunkt-Kennung des Halters, keine Inhalte.
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
