"""Letterhead – ZUSAETZLICHE Briefkoepfe einer Kanzlei (neben dem Kanzlei-Profil).

Eine Kanzlei kann mit mehreren Briefkoepfen arbeiten (z. B. "Kanzlei allgemein" und "Kanzlei
Immobilienrecht"). Der bisherige Briefkopf - die Briefkopffelder des `FirmProfile`
(Name/Anschrift/Kontakt/Logo/Unterzeichner) - bleibt UNVERAENDERT der erste Briefkopf
("firm", Name in `FirmProfile.letterhead_name`); jede Zeile dieser Tabelle ist ein weiterer
Briefkopf mit exakt denselben Feldern. Es gibt keine zweite Konfigurationsquelle fuer den
Briefkopf des Kanzlei-Profils und keine Begrenzung auf zwei Briefkoepfe. Welcher Briefkopf
Standard ist, steht in `FirmProfile.default_letterhead_id` (None = der Profil-Briefkopf);
welcher Briefkopf zu einem Schriftsatz gehoert, in `Draft.letterhead_ref`. Siehe
app/firm_profile/letterheads.py."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Letterhead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "letterheads"

    #: Anzeigename, z. B. "Kanzlei Immobilienrecht" (eindeutig, ohne Beachtung der Grossschreibung).
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    firm_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    legal_form: Mapped[str | None] = mapped_column(String(255), nullable=True)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_addition: Mapped[str | None] = mapped_column(String(255), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signatory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    logo_original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    signature_original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by_actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
