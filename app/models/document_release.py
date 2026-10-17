"""Dokumentfreigabe vor KI-Verarbeitung (§62): Pruefergebnis je Dokumentstand und manuelle Freigabe.

`DocumentPrivacyCheck` ist das zwischengespeicherte Ergebnis der ZENTRALEN Datenschutzpruefung
(`ClaudePrivacyGateway.prepare_request`, lokal, ohne Cloud) fuer GENAU einen Dokumentstand: Textstand-Fingerabdruck
(`text_version`) und Kontext-Fingerabdruck (gespeicherte Namensentscheidungen + bekannte Beteiligte der Akte). Aendert
sich eines davon, gilt das Ergebnis nicht mehr - es wird neu geprueft. Gespeichert werden nur Ergebnis und Anzahl, keine
Inhalte.

`DocumentRelease` ist eine manuelle Freigabe durch Anwalt/Admin, gebunden an den freigegebenen Textstand: aendert sich der
Dokumenttext, ist sie veraltet (wirkungslos), eine erneute Freigabe ist noetig.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

CHECK_PASSED = "passed"  # zentrale Pruefung erfolgreich, keine Auffaelligkeit
CHECK_UNCERTAIN = "uncertain"  # erfolgreich nur nach Entscheidung (unsichere Begriffe, Rueckfrage)
CHECK_FAILED = "failed"  # zentrale Pruefung blockiert (fail-closed)


class DocumentPrivacyCheck(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_privacy_checks"
    __table_args__ = (UniqueConstraint("document_id", "text_version", "context_key", name="uq_document_privacy_check"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    text_version: Mapped[str] = mapped_column(String(32), nullable=False)
    context_key: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DocumentRelease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_releases"
    __table_args__ = (UniqueConstraint("document_id", "text_version", name="uq_document_release_version"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    text_version: Mapped[str] = mapped_column(String(32), nullable=False)
    released_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    mode_at_release: Mapped[str] = mapped_column(String(16), nullable=False)
