"""Matter – Akte/Mandat.

Zentrale Isolationseinheit fuer Aktenkontext: Retrieval, Wissensabruf und
KI-Kontext duerfen nie ueber die Grenze einer Matter hinweg vermischen
(Grundregel, siehe CLAUDE.md). Alle aktenbezogenen Entitaeten (Party,
Message, Document, Task, Deadline, Draft, WorkflowRun) referenzieren
`matter_id`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Matter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "matters"

    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    reference_number: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    practice_area: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Bewusst freier String statt DB-Enum: der Status einer Akte selbst
    # (offen/geschlossen) ist unabhaengig vom Workflow-Status einzelner
    # Vorgaenge (siehe WorkflowRun / ARCHITECTURE.md §6).
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    # SOFT-DELETE (03.10., Owner-Direktive "AKTENUEBERSICHT FINALISIEREN UND
    # PRODUKTIONSREIF VERIFIZIEREN" §6) - identisches Prinzip wie
    # `Document.deleted_at` (app/documents/lifecycle.py): eine Akte ist eine
    # potenziell aufbewahrungspflichtige Mandantenunterlage, ein echtes
    # DB-Delete waere hier aus Compliance-Gruenden nicht vertretbar UND
    # wuerde (anders als bei Document) verwaiste Zeilen in Modellen
    # hinterlassen, die KEINE Cascade-Relationship zu Matter haben
    # (ChatConversation/GeneratedDocument/AttorneyInstruction/OutboxEntry).
    # NULL = aktiv/sichtbar, gesetzt = aus Anwendersicht geloescht - der
    # Datensatz UND alle abhaengigen Zeilen bleiben vollstaendig erhalten.
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)

    client: Mapped["Client"] = relationship(back_populates="matters")
    parties: Mapped[list["Party"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    deadlines: Mapped[list["Deadline"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    drafts: Mapped[list["Draft"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    notes: Mapped[list["Note"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
