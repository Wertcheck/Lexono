"""GlobalSearchService – Universal Command Bar (Strg+K/⌘K, 20.08.).

Zwei strikt getrennte Kategorien, siehe app/search/__init__.py-
Moduldocstring ("WICHTIGSTE REGEL") und app/models/source.py:

- "Lokal" (Mandant/Akte/Dokument/E-Mail): AUSSCHLIESSLICH einfache, lokale
  SQL-Abfragen gegen die eigene SQLite-Datenbank (Name/Aktenzeichen/
  Dateiname/Absender+Betreff) - kein Embedding-Modell, kein KI-Aufruf,
  keine Netzwerkverbindung. Bewusst KEINE Volltext-/semantische Suche über
  `Document.extracted_text`: `DocumentSearchService.search_within_matter`
  verlangt zwingend eine `matter_id` (Aktenisolation, siehe dortiges
  Moduldocstring) - eine aktenübergreifende Command-Bar würde genau diese
  Regel verletzen. Die Dokumenten-Kategorie hier durchsucht deshalb NUR
  den Dateinamen (Metadaten, kein Akteninhalt) über alle Akten hinweg,
  was diese Isolation nicht berührt. Die E-Mail-Kategorie (`_search_
  messages`, 25.09.) folgt derselben Metadaten-Grenze und durchsucht nur
  Absender/Betreff, nicht `Message.body_text`.
- "Extern" (Rechtsquellen, `Source`-Modell): Gesetze/Rechtsprechung/
  Kommentare - laut app/models/source.py-Moduldocstring bewusst eine
  "eigene Schicht, strikt getrennt von Mandanten-/Aktendaten", nie durch
  die KI erfunden (`SourceService.import_source`, manuelle Erfassung).
  WICHTIG (Ehrlichkeitsgebot, siehe CLAUDE.md "Unsicherheit explizit
  markieren, nicht verschweigen"): auch diese Suche läuft technisch
  vollständig LOKAL (dasselbe `fastembed`-Modell wie überall sonst im
  System, siehe app/search/embeddings.py - "keine Mandantendaten
  verlassen dafür die Kanzlei-Umgebung"). Es gibt in diesem Projekt
  aktuell KEINE Anbindung, die eine Suchanfrage tatsächlich an einen
  Cloud-Dienst schickt - eine "echte" externe KI-Rechtsrecherche (Cloud-
  Anthropic-Aufruf mit frei formulierter Rechtsfrage) würde dem
  ausdrücklichen Grundsatz "Niemals Rechtsquellen, Fundstellen oder
  Zitate erfinden" widersprechen (siehe LegalResearchService: "es wurde
  KEINE Fundstelle erfunden" - genau deshalb ruft auch die bestehende
  Rechercheschicht dafür bewusst KEIN LLM auf). "Extern" bezeichnet daher
  die INHALTLICHE Herkunft (allgemeines Recht statt Mandantendaten), NICHT
  einen tatsächlichen Netzwerkaufruf - das UI macht diese Unterscheidung
  über den Badge-Titeltext (siehe `badge_title` unten) explizit, statt sie
  zu verschweigen.

Dritte Kategorie seit der digitalen Gesetzesbibliothek (20.08., siehe
app/laws/): "Extern/Gesetz" (`LawSection`-Modell) - eigenes, spezifischeres
Badge als das allgemeine "Extern" der `Source`-Treffer, weil hier
konkreter Gesetzeswortlaut (amtliches Werk, § 5 UrhG) statt einer
allgemeinen Quellenangabe gefunden wird. Reine SQL-Suche (kein
Embedding-Modell noetig, siehe app/laws/service.py: get_sections) -
robuster/schneller als die `Source`-Suche und deshalb NICHT im
try/except unten (kein Embedding-Ausfallrisiko).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Client, Document, Law, LawSection, Matter, Message, Source
from app.search.service import DocumentSearchService

logger = logging.getLogger(__name__)

MIN_QUERY_LENGTH = 2

_LOCAL_BADGE_TITLE = (
    "Mandantenbezogene Daten - Suche läuft ausschließlich lokal auf diesem "
    "Rechner (SQL-Abfrage, kein KI-/Netzwerkaufruf)."
)
_EXTERNAL_BADGE_TITLE = (
    "Allgemeine Rechtsquelle (Gesetz/Rechtsprechung), keine Mandantendaten - "
    "technisch läuft auch diese Suche lokal (siehe app/search/embeddings.py), "
    "es wird keine Anfrage an einen Cloud-Dienst gesendet."
)
_LAW_BADGE_TITLE = (
    "Gesetzestext (amtliches Werk, § 5 UrhG) - kuratierte lokale Fixture-Daten, "
    "keine Garantie für Vollständigkeit/Aktualität. Suche läuft lokal (SQL-"
    "Abfrage), keine Cloud-Anfrage."
)


@dataclass(frozen=True)
class GlobalSearchResult:
    entity_type: str  # "Client" | "Matter" | "Document" | "Source" | "LawSection"
    title: str
    subtitle: str
    url: str
    badge_label: str  # "Lokal" | "Extern" | "Extern/Gesetz"
    badge_title: str


class GlobalSearchService:
    def __init__(self, document_search_service: DocumentSearchService) -> None:
        self._document_search_service = document_search_service

    def search(
        self, query: str, db: Session, *, limit_per_category: int = 5
    ) -> list[GlobalSearchResult]:
        query = (query or "").strip()
        if len(query) < MIN_QUERY_LENGTH:
            return []

        # "Lokal" (reine SQL-Abfragen) MUSS unabhaengig von der
        # "Extern"-Kategorie funktionieren: die Rechtsquellen-Suche haengt
        # am Embedding-Modell (app/search/embeddings.py), das z. B. beim
        # allerersten Start ohne Internetzugang oder bei einem defekten
        # ONNX-Runtime-Setup fehlschlagen kann (siehe FastEmbedProvider-
        # Docstring). Ein Mandanten-/Aktennamen-Treffer darf NIE an einem
        # Embedding-Problem scheitern, das mit ihm gar nichts zu tun hat -
        # deshalb laufen beide Kategorien in getrennten try/except-Bloecken,
        # nicht in einer gemeinsamen Anweisungskette.
        results: list[GlobalSearchResult] = []
        results.extend(self._search_clients(query, db, limit_per_category))
        results.extend(self._search_matters(query, db, limit_per_category))
        results.extend(self._search_documents(query, db, limit_per_category))
        results.extend(self._search_messages(query, db, limit_per_category))
        results.extend(self._search_law_sections(query, db, limit_per_category))
        try:
            results.extend(self._search_sources(query, db, limit_per_category))
        except Exception:  # noqa: BLE001 - siehe Kommentar oben
            logger.warning(
                "Rechtsquellen-Suche (Command Bar) fehlgeschlagen, "
                "liefere nur lokale Treffer weiter",
                exc_info=True,
            )
        return results

    # --- "Lokal": Mandanten/Akten/Dokumente (reine SQL-Abfragen) ---------

    def _search_clients(self, query: str, db: Session, limit: int) -> list[GlobalSearchResult]:
        like = f"%{query}%"
        rows = (
            db.query(Client)
            .filter(or_(Client.name.ilike(like), Client.client_number.ilike(like)))
            .order_by(Client.name.asc())
            .limit(limit)
            .all()
        )
        return [
            GlobalSearchResult(
                entity_type="Client",
                title=client.name,
                subtitle=(
                    f"Mandant · {client.client_number or 'ohne Nummer'}"
                    + (" · archiviert" if client.status == "archived" else "")
                ),
                url=f"/dashboard/clients/{client.id}",
                badge_label="Lokal",
                badge_title=_LOCAL_BADGE_TITLE,
            )
            for client in rows
        ]

    def _search_matters(self, query: str, db: Session, limit: int) -> list[GlobalSearchResult]:
        like = f"%{query}%"
        rows = (
            db.query(Matter)
            .filter(
                or_(Matter.title.ilike(like), Matter.reference_number.ilike(like)),
                Matter.deleted_at.is_(None),
            )
            .order_by(Matter.updated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            GlobalSearchResult(
                entity_type="Matter",
                title=matter.title,
                subtitle=(
                    f"Akte · {matter.reference_number or 'ohne Aktenzeichen'}"
                    + (f" · {matter.practice_area}" if matter.practice_area else "")
                ),
                # ECHTER FUND (17.09., Owner-Direktive §5): dieser Kommentar
                # war veraltet - seit der UI/UX-Ueberarbeitung (13.09., siehe
                # app/web/placeholder_router.py) ist "/matters" KEIN
                # Platzhalter mehr, `matters_router.py` liefert eine echte
                # Aktendetailseite (Tabs, Dokumente, Aufgaben & Fristen,
                # Kommunikation). Der Suchtreffer zeigte bislang trotzdem
                # noch auf die Mandanten-Detailseite - jetzt direkt auf die
                # tatsaechliche, spezifischere Zielseite.
                url=f"/dashboard/matters/{matter.id}",
                badge_label="Lokal",
                badge_title=_LOCAL_BADGE_TITLE,
            )
            for matter in rows
        ]

    def _search_documents(self, query: str, db: Session, limit: int) -> list[GlobalSearchResult]:
        """Durchsucht bewusst NUR `original_filename` (Metadaten), NICHT
        `extracted_text` (Akteninhalt) - siehe Moduldocstring zur
        Aktenisolation. Ein JOIN auf Matter liefert `matter.title` in
        derselben Abfrage (keine zusätzliche Query pro Treffer)."""
        like = f"%{query}%"
        rows = (
            db.query(Document, Matter.title)
            .join(Matter, Document.matter_id == Matter.id)
            .filter(
                Document.original_filename.ilike(like),
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            GlobalSearchResult(
                entity_type="Document",
                title=document.original_filename or document.id,
                subtitle=f"Dokument · Akte „{matter_title}“",
                # ECHTER FUND (17.09., Owner-Direktive §5 "ein Link fuehrt
                # zu keinem echten Ziel"): zeigte bisher auf die Mandanten-
                # Uebersicht statt auf das Dokument selbst - der Anwalt
                # haette es dort manuell erneut suchen muessen. Die
                # Aktendokument-Seite (Vorschau/KI-Aktionen/Download/
                # Erkannte Fristen) existiert seit heute, jetzt genutzt.
                url=f"/dashboard/matters/{document.matter_id}/document/{document.id}",
                badge_label="Lokal",
                badge_title=_LOCAL_BADGE_TITLE,
            )
            for document, matter_title in rows
        ]

    def _search_messages(self, query: str, db: Session, limit: int) -> list[GlobalSearchResult]:
        """Posteingang-Nachrichten (25.09., Owner-Direktive "POSTEINGANG /
        STRICT REFERENCE IMPLEMENTATION" §6 - die Referenz zeigt oben im
        globalen Suchfeld "In E-Mails, Mandanten, Akten oder Inhalten
        suchen ...", die bisherige Command Bar durchsuchte E-Mails jedoch
        gar nicht). Durchsucht bewusst nur Absender/Betreff (Metadaten),
        nicht `body_text` - dieselbe Metadaten-statt-Volltext-Grenze wie
        bei `_search_documents` oben, aus demselben Grund: eine
        aktenuebergreifende Volltextsuche ueber Nachrichteninhalte waere
        keine reine Metadatensuche mehr. Absender/Betreff sind dagegen
        genau das, was die Posteingang-eigene Suche (`Message.sender`/
        `Message.subject`, app/web/router.py::_load_messages) ohnehin
        bereits als durchsuchbar behandelt - keine neue, weitergehende
        Datenkategorie."""
        like = f"%{query}%"
        rows = (
            db.query(Message)
            .filter(or_(Message.sender.ilike(like), Message.subject.ilike(like)))
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            GlobalSearchResult(
                entity_type="Message",
                title=message.subject or "(kein Betreff)",
                subtitle=f"E-Mail · {message.sender or 'unbekannter Absender'}",
                url=f"/dashboard/inbox/{message.id}",
                badge_label="Lokal",
                badge_title=_LOCAL_BADGE_TITLE,
            )
            for message in rows
        ]

    # --- "Extern/Gesetz": Gesetzesbibliothek (LawSection) -----------------

    def _search_law_sections(self, query: str, db: Session, limit: int) -> list[GlobalSearchResult]:
        """Reine SQL-Suche ueber alle Gesetzeswerke hinweg - siehe
        app/laws/service.py: get_sections fuer dieselbe Filterlogik,
        beschraenkt auf EIN Gesetzeswerk (hier bewusst uebergreifend, da
        die Command Bar keine Gesetzesauswahl kennt).

        `Law.is_active`-Filter (26.09., Owner-Direktive "KANZLEIWISSEN
        FINAL PRODUCT IMPLEMENTATION" §19/§20): ein in Kanzleiwissen
        deaktiviertes Gesetz gilt als "für die lokale Nutzung nicht
        aktiviert" - konsistent mit derselben Sperre im Chat-Fast-Path
        (app/chat/service.py::_find_law_section) darf es auch hier nicht
        auftauchen, obwohl die Paragraphen technisch noch vorhanden sind."""
        like = f"%{query}%"
        rows = (
            db.query(LawSection)
            .join(Law, Law.code == LawSection.law_code)
            .filter(
                Law.is_active.is_(True),
                or_(
                    LawSection.section_number.ilike(like),
                    LawSection.title.ilike(like),
                    LawSection.text_content.ilike(like),
                ),
            )
            .order_by(LawSection.law_code.asc(), LawSection.section_number.asc())
            .limit(limit)
            .all()
        )
        return [
            GlobalSearchResult(
                entity_type="LawSection",
                title=f"{section.section_number} {section.title}",
                subtitle=f"Gesetz · {section.law_code}",
                url=f"/dashboard/laws/{section.law_code}/{section.id}",
                badge_label="Extern/Gesetz",
                badge_title=_LAW_BADGE_TITLE,
            )
            for section in rows
        ]

    # --- "Extern": Rechtsquellen (Gesetze/Rechtsprechung) -----------------

    def _search_sources(self, query: str, db: Session, limit: int) -> list[GlobalSearchResult]:
        search_results = self._document_search_service.search_sources(query, db, limit=limit)
        results: list[GlobalSearchResult] = []
        for result in search_results:
            source = db.query(Source).filter_by(id=result.entity_id).first()
            if source is None:
                continue
            results.append(
                GlobalSearchResult(
                    entity_type="Source",
                    title=source.title,
                    subtitle=f"Rechtsquelle · {source.source_type}"
                    + (f" · {source.reference}" if source.reference else ""),
                    # ECHTER FUND (17.09., Owner-Direktive §5): "/dashboard/
                    # sources" ist weiterhin die "in Vorbereitung"-
                    # Platzhalterseite (siehe placeholder_router.py) - aber
                    # seit 14.09. existiert unter "/dashboard/knowledge"
                    # (Kanzleiwissen) eine ECHTE, tatsaechlich befuellte
                    # Rechtsquellen-Tabelle (Titel/Fundstelle/Typ/Freigabe,
                    # siehe knowledge_router.py). Kein Anker pro einzelner
                    # Quelle moeglich (keine Zeilen-IDs im Template), aber
                    # die Zielseite zeigt die gesuchte Quelle tatsaechlich
                    # an, statt eines toten "in Vorbereitung"-Hinweises.
                    url="/dashboard/knowledge",
                    badge_label="Extern",
                    badge_title=_EXTERNAL_BADGE_TITLE,
                )
            )
        return results
