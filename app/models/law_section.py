"""LawSection – einzelner Paragraph/Artikel eines Gesetzeswerks (20.08.).

`law_code` referenziert bewusst `Law.code` (nicht `Law.id`) als Fremd-
schlüssel - der vom Auftrag explizit vorgegebene natürliche Schlüssel
("z. B. code: 'BGB'"), konsistent mit den Import-Fixtures
(app/laws/fixtures/*.json), die Paragraphen ebenfalls über den Gesetzes-
code statt einer UUID referenzieren.

`last_updated` ist ein bewusst MANUELL gepflegtes Fachfeld (Datum, zu dem
der jeweilige Fixture-Eintrag zuletzt inhaltlich geprüft/aktualisiert
wurde) - zu unterscheiden von `updated_at` (TimestampMixin, rein
technischer Zeitpunkt der letzten DB-Zeilenänderung). Siehe
app/models/law.py-Moduldocstring zur urheberrechtlichen Einordnung
(§ 5 UrhG) und zur bewussten Unvollständigkeit der Startdaten.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LawSection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "law_sections"
    __table_args__ = (
        UniqueConstraint("law_code", "section_number", name="uq_law_sections_law_code_section_number"),
    )

    law_code: Mapped[str] = mapped_column(ForeignKey("laws.code"), nullable=False, index=True)
    section_number: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    last_updated: Mapped[date] = mapped_column(Date, nullable=False)

    # Erweiterung (13.09., Anbindung an "Gesetze im Internet"): zusaetzlich
    # zu den bereits vorhandenen manuell kuratierten Fixture-Zeilen
    # (source_name="Kuratierte Auswahl", source_url=NULL, doknr=NULL)
    # koennen Zeilen jetzt auch aus dem offiziellen XML-Download
    # importiert werden (app/laws/gesetze_im_internet.py). `doknr` ist
    # der amtliche eindeutige Dokumentschluessel aus der Quelle (fuer
    # stabile Re-Importe/Updates), `source_url` der echte, aus `doknr`/
    # Gesetzes-Slug abgeleitete Deep-Link zur Einzelnorm (NIEMALS
    # erfunden - siehe app/laws/gesetze_im_internet.py fuer die
    # Herleitung). Beide bewusst nullable, um bestehende kuratierte
    # Zeilen ohne Datenverlust/Zwangsmigration weiterzufuehren.
    source_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Kuratierte Auswahl", server_default="Kuratierte Auswahl"
    )
    doknr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    law: Mapped["Law"] = relationship(back_populates="sections")
