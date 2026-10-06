"""FirmPracticeArea – ein fachlicher Schwerpunkt der Kanzlei (03.10.,
Owner-Direktive "KANZLEIFACHPROFIL UND JURISTISCHE WISSENSSTEUERUNG").

WARUM eine eigene Tabelle statt einer CSV-/JSON-Spalte auf `FirmProfile`:
mehrere Schwerpunkte sollen einzeln hinzugefuegt/entfernt werden koennen,
OHNE Duplikate zu riskieren und ohne fragiles String-Parsing bei jeder
Aenderung - ein `UniqueConstraint` auf (firm_profile_id, practice_area)
verhindert doppelte Zuordnungen auf DB-Ebene, exakt das von der Direktive
verlangte Kriterium "Vermeidung doppelter Zuordnungen". Gleiches
Kind-Zeilen-pro-Wert-Muster wie `Party`/`Note` (siehe dortige Module),
keine neue Architektur.

`FirmProfile` ist ein bewusstes Singleton (siehe dortiger Moduldocstring:
GENAU EINE Zeile fuer die gesamte Installation) - Lexono hat laut
Datenmodell KEIN Mehr-Kanzlei-Konzept (`User` hat kein `firm_id`, siehe
app/models/user.py). Das Kanzleifachprofil ist deshalb zweifelsfrei
installationsweit, nicht benutzer- oder aktenbezogen - dieselbe
Zuordnungsfrage, die `FirmProfile` selbst bereits beantwortet hat, es
entsteht KEINE neue Mehr-Kanzlei-Entscheidung.

`practice_area` speichert den EXAKTEN String-Wert aus
`app.clients.service.PRACTICE_AREA_SUGGESTIONS` - der einzigen im Projekt
vorhandenen Rechtsgebiets-"Taxonomie" (bewusst nur eine flache Liste ohne
Untergebiete/IDs, siehe dortigen Kommentar: "bewusst KEINE DB-Enum/harte
Validierung" fuer Mandanten-/Akten-Freitextfelder). Fuer das
Kanzleifachprofil gilt eine STRENGERE Regel als dort: nur Werte aus dieser
Liste sind zulaessig (siehe app/firm_profile/practice_areas.py), weil das
Fachprofil ein bewusst kuratiertes, kleines Konzept ist (Direktive §2:
"Kanzleifachprofil"/"Aktenkontext" duerfen nicht vermischt werden) -
anders als die bewusst freien Mandanten-/Akten-Felder.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FirmPracticeArea(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "firm_practice_areas"
    __table_args__ = (
        UniqueConstraint(
            "firm_profile_id", "practice_area", name="uq_firm_practice_areas_profile_area"
        ),
    )

    firm_profile_id: Mapped[str] = mapped_column(
        ForeignKey("firm_profiles.id"), nullable=False, index=True
    )
    practice_area: Mapped[str] = mapped_column(String(128), nullable=False)

    firm_profile: Mapped["FirmProfile"] = relationship(back_populates="practice_areas")
