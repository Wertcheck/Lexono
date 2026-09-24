"""Deadline – (moegliche) Frist.

Wichtig (Konzept Prompt 10): Eine erkannte Frist darf nie automatisch als
endgueltig verbindlich gelten. Jede Deadline traegt daher Quelle
(`document_id`, `source_text`), eine Konfidenz und einen expliziten
Pruefstatus. Die eigentliche Erkennungslogik entsteht erst in Prompt 10 -
hier wird nur das Schema vorgesehen.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Deadline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deadlines"

    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id"), nullable=False, index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )
    # Quelle "Nachricht" (20.09., Posteingang-Fristenerkennung) - analog zu
    # `document_id`: eine erkannte Frist kann statt aus einem Dokument auch
    # direkt aus dem E-Mail-Text (`Message.body_text`) stammen, siehe
    # app/deadlines/service.py::analyze_message. Beide Quellenfelder bleiben
    # unabhaengig nullable - eine Deadline hat immer GENAU eine Quelle
    # (Dokument ODER Nachricht), nie beide gleichzeitig gesetzt.
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Erklaerender Text, WARUM diese Konfidenz/Erkennung so zustande kam
    # (z. B. "Datum ohne erkennbares Fristen-Schluesselwort in der Naehe -
    # koennte auch ein reines Referenzdatum sein") und der ausdrueckliche
    # Hinweis, dass die Frist NICHT als verbindlich bestaetigt gilt - siehe
    # app/deadlines/extractor.py::ExtractedDeadline.reasoning, von dort
    # unveraendert uebernommen (app/deadlines/service.py). Nullable, da
    # aeltere/anders erzeugte Datensaetze (z. B. Synthetic-Data-Fixtures)
    # keinen Reasoning-Text mitbringen.
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # unreviewed / confirmed / rejected - niemals automatisch "confirmed".
    review_status: Mapped[str] = mapped_column(
        String(32), default="unreviewed", nullable=False
    )

    matter: Mapped["Matter"] = relationship(back_populates="deadlines")
