"""Client – Mandant.

Oberste Isolationsebene: jede Matter (Akte) gehoert zu genau einem Client.
Kontext-/Wissensabruf darf nie ueber die Grenze eines Client hinweg
vermischen (siehe ARCHITECTURE.md §7, wird vollstaendig erst in Prompt 11
und 41 technisch durchgesetzt).

Erweiterung um CRM-Felder (Mandantendatenbank, 20.08. - siehe
app/web/clients_router.py): `practice_area`/`responsible_user_id` werden
bewusst auf DIESER Ebene (nicht nur auf `Matter.practice_area`) gefuehrt,
weil das "Mandant anlegen"-Formular sie als Stammdaten des Mandanten
selbst erfasst (ein Mandant kann mehrere Akten mit UNTERSCHIEDLICHEN
Rechtsgebieten haben - dieses Feld ist die allgemeine CRM-Einordnung, kein
Duplikat/Zwang zur Uebereinstimmung mit einzelnen Akten).

`status` ("active"/"archived") existiert, WEIL `matters` unten weiterhin
mit `cascade="all, delete-orphan"` definiert ist - ein echtes Loeschen
eines Mandanten mit bestehenden Akten wuerde deren gesamte Fallhistorie
(Nachrichten, Dokumente, Entwuerfe, Fristen) unwiderruflich mitloeschen,
was mit gesetzlichen Aufbewahrungspflichten fuer Anwaltsakten kollidieren
kann (ausdrueckliche Vorgabe des Anwalts). `ClientService.delete_client`
(app/clients/service.py) erzwingt deshalb: Hard-Delete NUR ohne
verknuepfte Akten, sonst ausschliesslich Archivierung (Status-Wechsel,
keine Kaskade) - siehe dortige Begruendung.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

VALID_CLIENT_STATUSES = ("active", "archived")


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_number: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Allgemeine CRM-Einordnung des Mandanten (siehe Moduldocstring) -
    # bewusst freier String statt DB-Enum, analog zu `Matter.practice_area`.
    practice_area: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Mandanten-Typ: Privatperson/Unternehmen (03.10., Owner-Direktive
    # "REFERENZGETREUE MANDANTENUEBERSICHT", Referenzabgleich
    # `29_mandanten_uebersicht.png` §"Kategorie"-Spalte) - ECHTER FUND beim
    # Bildabgleich: die Referenz zeigt dort "Privatperson"/"Unternehmen",
    # eine fundamental andere Unterscheidung als `practice_area`
    # (Rechtsgebiet/Fachgebiet) - es gab bisher KEIN Feld dafuer. Bewusst
    # freier String statt DB-Enum (gleiches Muster wie `practice_area`/
    # `status`), siehe app/clients/service.py::CLIENT_TYPE_SUGGESTIONS fuer
    # die vorgeschlagenen Werte.
    client_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Ort (03.10., gleicher Fund): die Referenz zeigt eine eigene "Ort"-
    # Spalte - es gab bisher kein Adressfeld auf `Client` (nur `FirmProfile`
    # hatte bereits `city`, siehe dortiges Modell). Bewusst NUR die Stadt,
    # keine vollstaendige Adresse (die Referenz zeigt ausschliesslich den
    # Ortsnamen, keine Strasse/PLZ) - kein erfundenes Feld ueber den
    # tatsaechlichen Referenzbedarf hinaus.
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # "active" (Standard) / "archived" - siehe Moduldocstring zur
    # Loeschlogik. Bewusst freier String statt DB-Enum, gleiches Muster wie
    # `Matter.status`.
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    responsible_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )

    matters: Mapped[list["Matter"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    responsible_user: Mapped["User | None"] = relationship()
    notes: Mapped[list["Note"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
