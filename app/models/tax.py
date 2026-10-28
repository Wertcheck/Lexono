"""Lexono Steuern - Datenmodell (28.10.2026, Konzept + Datenmodell, KEINE Oberflaeche, KEINE Uebermittlung).

Bewusst allgemein statt auf die Umsatzsteuer-Voranmeldung zugeschnitten:

- ``TaxFiling``: ein steuerlicher Vorgang eines Mandanten - Erklaerung, Anmeldung, Voranmeldung oder Nachricht
  (z. B. Einspruch, Fristverlaengerung) - je Steuerart (Katalog app/tax/catalog.py), Jahr und Zeitraum, optional mit
  Akte und Berichtigungsbezug (``correction_of_id``).
- ``TaxFilingPart``: Hauptvordruck, Anlagen und ergaenzende Formulare, mehrfach je Art moeglich (``instance_no``,
  z. B. mehrere Anlagen fuer verschiedene Objekte).
- ``TaxFieldValue``: Feldwerte je Teil als Schluessel/Wert mit Typ und Herkunft (manuell, Finanzbuchhaltung,
  Import). Die Feldschluessel sind bewusst freie Zeichenketten: die amtlichen Kennzahlen/Feldkennungen je Datenart
  und Jahrgang kommen erst mit der ERiC-Integration (siehe MODULARCHITEKTUR.md), es werden keine erfunden.
- ``TaxSubmission``: vorbereitete Struktur fuer Uebermittlungsversuche (Umgebung test/produktiv, Transferticket,
  Protokoll, Fehler). Wird derzeit NICHT beschrieben - es gibt keine ERiC-Anbindung.

Mandantentrennung: ``client_id`` auf dem Vorgang; Teile und Werte erreicht der Service nur ueber den Vorgang desselben
Mandanten. Statusverlauf und jede Wertaenderung stehen im Audit-Log (entity_type "TaxFiling").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

TAX_FILING_STATUSES = ("draft", "in_review", "approved", "submitted", "accepted", "rejected", "cancelled")
TAX_VALUE_TYPES = ("text", "amount_cents", "integer", "date", "boolean")
TAX_VALUE_SOURCES = ("manual", "fibu", "import")


class TaxFiling(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tax_filings"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    matter_id: Mapped[str | None] = mapped_column(ForeignKey("matters.id"), nullable=True, index=True)
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filing_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_kind: Mapped[str] = mapped_column(String(8), nullable=False)  # year / quarter / month / none
    period_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    form_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    correction_of_id: Mapped[str | None] = mapped_column(ForeignKey("tax_filings.id"), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)

    parts: Mapped[list["TaxFilingPart"]] = relationship(back_populates="filing", order_by="TaxFilingPart.created_at")


class TaxFilingPart(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tax_filing_parts"
    __table_args__ = (UniqueConstraint("filing_id", "part_code", "instance_no", name="uq_tax_filing_parts_instance"),)

    filing_id: Mapped[str] = mapped_column(ForeignKey("tax_filings.id"), nullable=False, index=True)
    part_code: Mapped[str] = mapped_column(String(64), nullable=False)
    instance_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)

    filing: Mapped[TaxFiling] = relationship(back_populates="parts")
    values: Mapped[list["TaxFieldValue"]] = relationship(back_populates="part", order_by="TaxFieldValue.field_key")


class TaxFieldValue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tax_field_values"
    __table_args__ = (UniqueConstraint("part_id", "field_key", name="uq_tax_field_values_key"),)

    part_id: Mapped[str] = mapped_column(ForeignKey("tax_filing_parts.id"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)

    part: Mapped[TaxFilingPart] = relationship(back_populates="values")


class TaxSubmission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tax_submissions"

    filing_id: Mapped[str] = mapped_column(ForeignKey("tax_filings.id"), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(12), nullable=False)  # test / production
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    transfer_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
