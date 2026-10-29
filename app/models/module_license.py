"""ModuleLicense - importierte, signierte Lizenzdatei fuer kostenpflichtige Module (28.10.2026).

Gespeichert wird der unveraenderte Lizenztext; die Pruefung (Signatur, Laufzeit, Module) passiert bei jeder Abfrage
neu in app/modules/licensing.py - die Datenbank ist also KEINE Vertrauensquelle: wer hier etwas einschleust, ohne den
privaten Herstellerschluessel zu besitzen, schaltet nichts frei. Ersetzte/entfernte Lizenzen bleiben als inaktive
Zeilen erhalten (nachvollziehbar), die Aktionen stehen zusaetzlich im Audit-Log.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ModuleLicense(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "module_licenses"

    license_text: Mapped[str] = mapped_column(Text, nullable=False)
    license_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    imported_by: Mapped[str] = mapped_column(String(255), nullable=False)
