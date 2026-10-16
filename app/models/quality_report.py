"""Qualitaetsmeldungen und synthetische Regressionsfaelle (ARCHITECTURE.md §75, FUTURE_ROADMAP.md P4).

`QualityReport` haelt eine Meldung aus der Kanzlei zu GENAU einer Akte (optional einem Dokument der Akte) fest:
uebersehene personenbezogene Daten, uebermaessige Pseudonymisierung, verfaelschte rechtlich relevante Inhalte,
nicht erkannte Fristen, sonstige Datenschutzbedenken.

Datenminimierung:
- `excerpt` (die betroffene Fundstelle, hoechstens 300 Zeichen) ist das einzige Feld, das gezielt Dokumentinhalt
  aufnimmt. Er bleibt lokal in der Kanzlei-Datenbank (gleiches Schutzniveau wie das Dokument selbst), wird nie
  protokolliert, nie exportiert und beim Abschliessen/Verwerfen der Meldung geleert.
- `diagnosis_json` enthaelt ausschliesslich Kategorien, Ja/Nein-Werte, Zaehler und Stufennamen - nie Text aus Dokument,
  Fundstelle oder Pseudonymisierung (per Test abgesichert).
- Akte endgueltig geloescht -> Meldungen werden mitgeloescht (ON DELETE CASCADE); Dokument geloescht -> Verweis wird
  geleert.

`RegressionCase` ist ein von Hand geschriebener, SYNTHETISCHER Testfall zu einer bestaetigten Ursache. Er uebernimmt nie
automatisch Text aus der Meldung (Leck-Pruefung gegen Fundstelle und alle bekannten Mandanten-/Beteiligtennamen,
app/quality_reports/regression.py) und wirkt nie automatisch auf produktive Regeln: freigegebene Faelle werden exportiert und
von Entwicklern bewusst in tests/regression_cases/ uebernommen, wo sie gegen die echte Pipeline laufen."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

CATEGORY_MISSED_PERSONAL_DATA = "missed_personal_data"
CATEGORY_OVER_PSEUDONYMIZATION = "over_pseudonymization"
CATEGORY_ALTERED_LEGAL_CONTENT = "altered_legal_content"
CATEGORY_MISSED_DEADLINE = "missed_deadline"
CATEGORY_PRIVACY_CONCERN = "privacy_concern"
QUALITY_CATEGORIES = {
    CATEGORY_MISSED_PERSONAL_DATA: "Personenbezogene Daten übersehen",
    CATEGORY_OVER_PSEUDONYMIZATION: "Zu viel pseudonymisiert",
    CATEGORY_ALTERED_LEGAL_CONTENT: "Rechtlich relevanter Inhalt verfälscht",
    CATEGORY_MISSED_DEADLINE: "Frist nicht erkannt",
    CATEGORY_PRIVACY_CONCERN: "Mögliches Datenschutzproblem",
}

#: Verarbeitungsstufen, auf die eine Diagnose eine Ursache eingrenzen kann.
DIAGNOSIS_STAGES = {
    "ocr": "Texterkennung (OCR)",
    "extraction": "Textextraktion/Aufbereitung",
    "presidio": "Namenserkennung (Presidio/NER)",
    "pseudonymization": "Pseudonymisierung",
    "validation": "Prüfung/Validierung vor der Cloud",
    "routing": "Ablauf/Zuordnung (Routing)",
    "deadline_rules": "Fristenerkennung (Regeln)",
    "unknown": "Nicht eingegrenzt",
}

STATUS_NEW = "neu"
STATUS_DIAGNOSED = "diagnostiziert"
STATUS_CONFIRMED = "ursache_bestaetigt"
STATUS_DISMISSED = "verworfen"
STATUS_RESOLVED = "behoben"
REPORT_STATUSES = (STATUS_NEW, STATUS_DIAGNOSED, STATUS_CONFIRMED, STATUS_DISMISSED, STATUS_RESOLVED)
CLOSED_STATUSES = (STATUS_DISMISSED, STATUS_RESOLVED)

CASE_DRAFT = "entwurf"
CASE_APPROVED = "freigegeben"
CASE_REJECTED = "verworfen"

MAX_EXCERPT_LENGTH = 300


class QualityReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "quality_reports"

    matter_id: Mapped[str] = mapped_column(ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_NEW)
    diagnosis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspected_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confirmed_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    fix_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RegressionCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "regression_cases"

    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("quality_reports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    synthetic_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=CASE_DRAFT)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    test_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
