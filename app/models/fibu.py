"""Finanzbuchhaltung (MVP, 28.10.2026) – mandantenbezogene Buchungen, Konten, Belege, Aufgaben.

Isolation: JEDE Tabelle traegt eine Pflicht-`client_id` (Mandant = oberste Isolationsebene, siehe app/models/client.py).
Querverweise (Konto/Gegenkonto/Beleg einer Buchung) werden im Service zusaetzlich gegen dieselbe `client_id`
geprueft (app/fibu/service.py) - ein Fremdschluessel allein verhindert keine Mandantenvermischung.

Bewusst eigene Tabellen statt `Document`/`Task`: Belege sind keine Akten-Dokumente (keine Datenschutzpruefung/KI-
Pipeline, kein Chat-Kontext), Aufgaben haengen hier am Mandanten statt an einer Akte. Bestehende Tabellen bleiben
unveraendert.

Betraege: ganze Cent (`BigInteger`), keine Gleitkommazahlen. Netto/Steuer werden beim Speichern aus Brutto und
Steuersatz berechnet und mitgespeichert, damit eine Buchung spaeter nachvollziehbar bleibt.

KEINE Aussage zu GoBD-Konformitaet, Kontenrahmen oder steuerlicher Korrektheit - siehe Moduldocstring
app/fibu/service.py.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

FIBU_ACCOUNT_KINDS = ("revenue", "expense", "bank", "receivable", "payable", "other")
FIBU_BOOKING_DIRECTIONS = ("income", "expense")
FIBU_BOOKING_STATUSES = ("open", "posted", "cancelled")
FIBU_TAX_RATES = (0, 7, 19)


class FibuClientProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Steuerliche Stammdaten eines Mandanten fuer die Buchhaltung (die Mandantentabelle selbst bleibt unveraendert)."""

    __tablename__ = "fibu_client_profiles"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, unique=True, index=True)
    legal_form: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tax_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class FibuAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fibu_accounts"
    __table_args__ = (UniqueConstraint("client_id", "number", name="uq_fibu_accounts_client_number"),)

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    number: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FibuReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fibu_receipts"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # "stored" = gespeichert, noch keiner Buchung zugeordnet; die Zuordnung ergibt sich aus FibuBooking.receipt_id
    processing_status: Mapped[str] = mapped_column(String(16), default="stored", nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)


class FibuBooking(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fibu_bookings"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    booking_date: Mapped[date] = mapped_column(Date, nullable=False)
    receipt_number: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    gross_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("fibu_accounts.id"), nullable=False)
    contra_account_id: Mapped[str] = mapped_column(ForeignKey("fibu_accounts.id"), nullable=False)
    receipt_id: Mapped[str | None] = mapped_column(ForeignKey("fibu_receipts.id"), nullable=True, index=True)
    # open = erfasst, noch aenderbar; posted = gebucht (nicht mehr aenderbar, nur Storno); cancelled = storniert
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)

    account: Mapped[FibuAccount] = relationship(foreign_keys=[account_id])
    contra_account: Mapped[FibuAccount] = relationship(foreign_keys=[contra_account_id])
    receipt: Mapped[FibuReceipt | None] = relationship()


class FibuTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fibu_tasks"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="open", nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
