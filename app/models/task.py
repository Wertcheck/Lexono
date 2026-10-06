"""Task – Aufgabe innerhalb einer Akte."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # open / done - bewusst einfach gehalten.
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    # Prioritaet (03.10., Owner-Direktive "AUFGABEN & FRISTEN", Referenzabgleich
    # `18_akte_dokumente_detail.png`): die Referenz zeigt Hoch/Mittel/Niedrig
    # als eigene Spalte + Filter + visuelle Kennzeichnung - es gab bisher KEIN
    # Feld dafuer (der oben stehende, jetzt ueberholte Kommentar sah das
    # ausdruecklich erst "bei Bedarf" vor). Bewusst freier String statt
    # DB-Enum, nullable (bestehende Aufgaben bekommen KEINEN geratenen Wert,
    # siehe app/tasks/service.py::PRIORITY_SUGGESTIONS), gleiches Muster wie
    # `Client.client_type`.
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)

    matter: Mapped["Matter"] = relationship(back_populates="tasks")
