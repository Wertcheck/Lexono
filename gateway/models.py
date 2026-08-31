"""Datenmodell des Lexono-Gateway - bewusst EINE Tabelle.

Kein `app.models.base.Base` (eigener Deployment-Kontext, siehe
gateway/__init__.py) und bewusst KEINE Alembic-Historie: für eine
Pilotphase mit einem einzelnen, sehr kleinen Schema genügt
`Base.metadata.create_all()` beim Start (siehe gateway/db.py) - eine volle
Migrationskette ist Overhead, der erst bei tatsächlichen künftigen
Schema-Änderungen im Produktivbetrieb gerechtfertigt wäre.

`Tenant` speichert AUSSCHLIESSLICH Authentifizierungs-/Limit-Metadaten
einer Kanzlei-Installation - keine Mandantendaten, keine Dokumentinhalte,
keine Chat-Historie. `secret_hash` ist ein Argon2id-Hash (nie das
Klartext-Secret), siehe gateway/security.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Menschenlesbarer Name NUR fuer die Gateway-Administration (z. B.
    # "Kanzlei Mustermann") - erscheint niemals in Logs oder Antworten an
    # den Client selbst.
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Oeffentlicher Teil der Credential (Bearer "<client_id>:<secret>") -
    # dient als indizierter DB-Lookup-Schluessel, ist kein Geheimnis fuer
    # sich allein.
    client_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=lambda: str(uuid.uuid4())
    )
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
