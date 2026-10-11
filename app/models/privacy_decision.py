"""Datenschutz-Entscheidungen des Anwalts (Human in the Loop) und datensparsame Lern-Statistik (12.10.2026).

Siehe PRIVACY_REVIEW_ARCHITECTURE.md (M5/M6).

`PrivacyDecision` gehoert IMMER zu genau einer Akte (Aktenisolation) und optional zu einer Chat-Unterhaltung.
Der Begriff steht im Klartext, weil er fuer kuenftige Anfragen DERSELBEN Akte exakt wiedererkannt werden muss - er ist
Aktendatum wie ein Beteiligtenname und wird mit der Unterhaltung/Akte geloescht.

`PrivacyFeedbackStat` ist die einzige aktenuebergreifende Struktur und enthaelt KEINEN Klartext: nur einen HMAC des
Begriffs (lokaler Schluessel), die Entscheidung, Zaehler und grobe Merkmale (Rollen, Probing-Score). Sie dient
ausschliesslich der Auswertung wiederkehrender Fehler (scripts/privacy_feedback_report.py) - sie aendert nie
automatisch Regeln und loest kein Modelltraining aus.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

DECISION_PERSONAL = "personal"
DECISION_NOT_PERSONAL = "not_personal"
DECISION_UNSURE = "unsure"
VALID_PRIVACY_DECISIONS = (DECISION_PERSONAL, DECISION_NOT_PERSONAL, DECISION_UNSURE)


class PrivacyDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "privacy_decisions"
    __table_args__ = (UniqueConstraint("matter_id", "conversation_id", "term", name="uq_privacy_decision_scope_term"),)

    matter_id: Mapped[str] = mapped_column(ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    term: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PrivacyFeedbackStat(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "privacy_feedback_stats"
    __table_args__ = (UniqueConstraint("term_hmac", "decision", name="uq_privacy_feedback_term_decision"),)

    term_hmac: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Grobe, nicht identifizierende Merkmale der letzten Rueckfrage, z. B. {"roles": ["feldwert"], "probe": 2}
    features_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
