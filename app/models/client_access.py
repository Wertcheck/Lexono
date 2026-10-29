"""ClientUserAccess - ausdrueckliche Mandantenzuweisung fuer Mitarbeiter (28.10.2026).

Admin und Anwalt arbeiten wie bisher kanzleiweit. Mitarbeiter erhalten in den Zusatzmodulen (Finanzen, Steuern) nur
Zugriff auf Mandanten, die ihnen hier ausdruecklich zugewiesen sind - ohne Zuweisung kein Zugriff (deny by default).
Zuweisen und Entziehen werden im Audit-Log protokolliert (app/modules/client_access.py).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ClientUserAccess(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_user_access"
    __table_args__ = (UniqueConstraint("client_id", "user_id", name="uq_client_user_access"),)

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
