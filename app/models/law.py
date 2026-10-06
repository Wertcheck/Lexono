"""Law – Gesetzeswerk (z. B. "BGB", "StGB") der digitalen Gesetzesbibliothek
(20.08., siehe app/laws/ und app/web/laws_router.py).

WICHTIG (Urheberrecht, § 5 UrhG): Gesetzestexte sind "amtliche Werke" und
GENIESSEN KEINEN URHEBERRECHTLICHEN SCHUTZ - die wörtliche Wiedergabe von
Paragraphentexten hier ist rechtlich unbedenklich. Das betrifft aber nur
die urheberrechtliche Zulässigkeit, NICHT die inhaltliche Vollständigkeit/
Aktualität: die Bibliothek startet mit einer bewusst KLEINEN, manuell
kuratierten Auswahl besonders bekannter, seit Jahrzehnten kaum veränderter
Kernvorschriften (siehe app/laws/fixtures/) - kein vollständiger, live
aktualisierter Gesetzestext (Grundregel "Unsicherheit explizit markieren",
siehe CLAUDE.md) - siehe die entsprechenden Hinweise in
templates/law_library.html.

Inhalte entstehen AUSSCHLIESSLICH über den kontrollierten Import
(`app/laws/service.py: import_law_fixture_data`), NIE automatisch durch
die KI generiert - gleiches Prinzip wie bei `Source`
(app/models/source.py: "Die KI darf keine Quelle erfinden").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Law(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "laws"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # Lokale Aktivierung (26.09., Owner-Direktive "KANZLEIWISSEN FINAL
    # PRODUCT IMPLEMENTATION" §4/§5/§19-§21): ein Gesetz KANN bereits lokal
    # importiert (Zeilen in `law_sections` vorhanden) aber vom Anwalt
    # bewusst deaktiviert sein - "installiert" und "aktiv" sind zwei
    # unterschiedliche Zustaende. Deaktivieren loescht NICHT die bereits
    # heruntergeladenen Paragraphen (kein erneuter Download noetig, um es
    # wieder zu aktivieren) - es blendet die Quelle nur aus den KI-
    # Funktionen aus (siehe app/chat/service.py::_find_law_section,
    # app/search/global_search_service.py::_search_law_sections, beide
    # jetzt mit `Law.is_active`-Filter). Default True, damit alle bereits
    # heute importierten 34 Gesetze nach der Migration unveraendert nutzbar
    # bleiben (keine stille Deaktivierung durch die Migration selbst).
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    # Echte, beim Import gemessene Downloadgroesse der offiziellen
    # "xml.zip" in Bytes (26.09.) - NIE geschaetzt/erfunden (Referenzbild
    # zeigt eine "Groesse"-Spalte, CLAUDE.md verbietet erfundene Werte).
    # `None` fuer Gesetze, die vor dieser Aenderung importiert wurden (die
    # Groesse wurde damals nicht aufgezeichnet) oder ueber die manuell
    # kuratierten JSON-Fixtures statt der offiziellen Quelle entstanden -
    # die UI zeigt in diesem Fall ehrlich "–" statt eines Platzhalterwerts.
    source_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Automatisierte Aktualisierung (03.10., Owner-Direktive "RELIABLE
    # LEGAL KNOWLEDGE UPDATES") ---
    # Echter HTTP-`ETag` der zuletzt importierten "xml.zip" von
    # "Gesetze im Internet" (real verifiziert: der Server liefert einen
    # stabilen, starken ETag + unterstuetzt bedingte GET-Anfragen - siehe
    # app/laws/gesetze_im_internet.py::fetch_source_etag). Dient als
    # Vergleichswert, um eine neue Fassung zu erkennen, OHNE jedes Mal die
    # komplette ZIP herunterladen zu muessen. `None` fuer Gesetze, die vor
    # dieser Erweiterung importiert wurden oder bei denen der Nachtrags-
    # Check nach einem erfolgreichen Import fehlschlug - ein fehlender Wert
    # fuehrt beim naechsten Check ehrlich zu "Aktualisierung verfuegbar"
    # (kein erfundenes "unveraendert").
    source_etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Zeitpunkt des letzten Pruefversuchs (erfolgreich ODER fehlgeschlagen) -
    # unterscheidet sich bewusst von `last_source_update_at` unten.
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    # Einer von: "unchanged" / "update_available" / "updated" / "failed" /
    # "unreachable" (siehe app/laws/install_service.py fuer die Konstanten
    # und die genaue Bedeutung jedes Zustands). `None` = noch nie geprueft.
    last_check_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Verstaendliche Fehlermeldung zum letzten Pruef-/Update-Versuch, falls
    # dieser nicht erfolgreich war - NIE Zugangsdaten/Secrets (gibt es bei
    # diesem oeffentlichen, unauthentifizierten Zugriff ohnehin nicht).
    last_check_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Zeitpunkt der letzten ERFOLGREICHEN inhaltlichen Aktualisierung aus
    # der Quelle (nicht jeder blosse Check) - unterscheidet sich von
    # `updated_at` (TimestampMixin), das durch JEDE Zeilenaenderung
    # (z. B. `is_active`-Toggle) veraendert wird.
    last_source_update_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Bewusst OHNE order_by hier: "§ 13" vs. "§ 2" sortiert als String
    # lexikografisch falsch (13 vor 2) - die numerisch korrekte Sortierung
    # uebernimmt app/laws/service.py: sort_sections_naturally beim
    # tatsaechlichen Abruf, nicht die Beziehung selbst.
    sections: Mapped[list["LawSection"]] = relationship(
        back_populates="law", cascade="all, delete-orphan"
    )
