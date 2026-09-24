"""Note – freie Anwaltsnotiz zu einer Akte ODER einem Mandanten.

ECHTER FUND (19.09., UI/UX-Referenzabgleich mehrerer Akte-/Mandant-Detail-
Referenzbilder, z. B. "32_aufgaben_und_fristen_detail.png"/
"30_mandant_detail.png": beide zeigen konsequent einen eigenen "Notizen"-
Tab): bisher gab es projektweit KEINE Moeglichkeit, eine freie, nicht-KI-
generierte Notiz an einer Akte oder einem Mandanten zu hinterlegen - nur
strukturierte Objekte (Dokumente/Nachrichten/Aufgaben/Fristen/Entwuerfe).
Bewusst minimal gehalten (Text + Autor + Zeitstempel, kein Bearbeiten/
Formatieren) - siehe app/web/note_actions_router.py fuer die Begruendung,
warum nur Erstellen/Anzeigen (kein Bearbeiten/Loeschen) der erste Schnitt
ist.

`matter_id`/`client_id` (19.09., Erweiterung um den zweiten Referenzfund):
GENAU EINES der beiden ist gesetzt, nie beide - bewusst als zwei
eigenstaendige nullable FKs statt eines polymorphen "entity_type"/
"entity_id"-Paars, um dem im gesamten Projekt etablierten Muster direkter
Fremdschluessel zu folgen (Party.matter_id, Deadline.matter_id, ...) statt
eine neue, abweichende Architektur einzufuehren. Die Invariante wird auf
Router-Ebene durchgesetzt (app/web/note_actions_router.py), nicht per
DB-Constraint - SQLite CHECK-Constraints ueber mehrere Spalten sind im
Projekt bisher nirgends verwendet."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Note(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notes"

    matter_id: Mapped[str | None] = mapped_column(
        ForeignKey("matters.id"), nullable=True, index=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id"), nullable=True, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Freier String wie ueberall sonst im Projekt (z. B. AuditEvent.actor,
    # Draft-Erstellung) - keine FK auf User, damit eine Notiz auch nach
    # Entfernen des urspruenglichen Benutzerkontos lesbar/zuordenbar bleibt.
    author: Mapped[str] = mapped_column(String(255), nullable=False)

    matter: Mapped["Matter | None"] = relationship(back_populates="notes")
    client: Mapped["Client | None"] = relationship(back_populates="notes")
