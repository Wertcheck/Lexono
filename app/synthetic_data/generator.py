"""SyntheticDataGenerator – erzeugt realistische, vollständig fiktive
Testfälle (Prompt 29).

Zweck: Demo-/Entwicklungsdaten für das Dashboard OHNE echte
Mandantendaten (Konzept-Annahme A3, siehe app/synthetic_data/scenarios.py
für die Grundregel) UND die Datengrundlage für den in Prompt 30
geforderten Qualitäts-Benchmark ("≥20 synthetische Fälle") - dieser
Generator liefert die Fälle, Prompt 30 baut die Bewertungslogik darauf
auf. Bewusst hier NICHT vermischt (eigene Zuständigkeit).

Deterministisch bei gesetztem `seed` - wichtig für einen reproduzierbaren
Benchmark (Prompt 30): derselbe Seed erzeugt exakt dieselben Fälle, ein
Qualitätsvergleich zwischen zwei Codeständen bleibt dadurch fair.

Erzeugt AUSSCHLIESSLICH lokale Datenbankzeilen - ruft an KEINER Stelle
die Claude API auf (kein Kostenrisiko, kein Netzwerkzugriff nötig).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.classification.classifier import PlaceholderDocumentClassifier
from app.models import (
    AuditEvent,
    Client,
    Deadline,
    Document,
    Draft,
    KnowledgeItem,
    Matter,
    Message,
    OutboxEntry,
    Party,
    ProcessingError,
    Source,
    Task,
)
from app.synthetic_data.scenarios import SCENARIOS, CaseScenario

# Deutsche Standard-Platzhalternamen (das Äquivalent zu "John Doe") -
# bewusst KEINE echten Personen, auch keine entfernt realen Personen
# nachempfundenen Namen. Reine Vor-/Nachname-Kombinatorik erzeugt genug
# Varianz für >20 Fälle, ohne eine Namensliste "voller" Personen zu
# brauchen.
_VORNAMEN = (
    "Max", "Erika", "Thomas", "Sabine", "Michael", "Petra", "Andreas", "Julia",
    "Stefan", "Claudia", "Martin", "Nicole", "Frank", "Sandra", "Christian", "Anna",
)
_NACHNAMEN = (
    "Mustermann", "Musterfrau", "Beispiel", "Schmidt", "Meier", "Fischer",
    "Weber", "Wagner", "Becker", "Hoffmann", "Schulz", "Neumann",
)
_FIRMENNAMEN_MUSTER = (
    "Musterbau GmbH", "Beispiel Consulting AG", "Handwerk Schmidt & Söhne",
    "Testhandel Weber KG", "Musterhandwerk Fischer GmbH", "Beispiel Logistik GmbH",
    # Ergaenzt 24.09. (Owner-Direktive "ROADMAP-ALIGNED PRODUCT COMPLETION"
    # §10: Mandantschaft soll erkennbar unterschiedliche Rechtsformen
    # umfassen, nicht nur GmbH/AG/KG) - Einzelunternehmen (e.K.) und
    # Personengesellschaften (GbR/OHG/UG) waren bisher in der Namenspoolung
    # nicht vertreten, obwohl `_RECHTSFORM_SUFFIXE` sie bereits kennt.
    "Schreinerei Hoffmann e.K.", "Architekturbüro Neumann & Schulz GbR",
    "Handelshaus Becker OHG", "Webdesign Fischer UG",
)

#: Aktenzeichen-Kürzel je Rechtsgebiet.
#:
#: ECHTER FUND (15.09.): das Kürzel wurde per `random.choice` GEWÜRFELT und
#: hatte mit dem Fall nichts zu tun - eine Umsatzsteuer-Nachschau bekam
#: "BP", eine Betriebsprüfung "ESt" oder "Sonst", ein Kündigungswiderspruch
#: "USt". In einer Steuerkanzlei kodiert genau dieses Kürzel das
#: Sachgebiet; würfelt man es, ist der gesamte Demo-Datenbestand in sich
#: widersprüchlich und als Arbeits-/Testgrundlage wertlos.
#:
#: Rechtsgebiete ohne eigenes Steuer-Kürzel laufen bewusst unter "Sonst" -
#: das ist die kanzleiübliche Sammelkategorie, kein Platzhalter.
_AKTENZEICHEN_SUFFIX_JE_RECHTSGEBIET = {
    "Einkommensteuer": "ESt",
    "Betriebsprüfung": "BP",
    "Umsatzsteuer": "USt",
    # Ergaenzt 20.09. (Workstream B "vollstaendige Faelle", siehe
    # generate_complex_case) - beide Rechtsgebiete liegen innerhalb der
    # dokumentierten Zielgruppe "Steuer-/Wirtschaftskanzleien"
    # (PROJECT_STATE.md), anders als z. B. Familien-/Straf-/reines
    # Verkehrsrecht (bewusst NICHT ergaenzt, siehe DECISIONS.md).
    "Gesellschaftsrecht": "GesR",
    "Erbschaftsteuer": "ErbSt",
}
_AKTENZEICHEN_SUFFIX_SONSTIGE = "Sonst"

#: Angehängte Rechtsformen, die in der kanzleiüblichen Kurzbezeichnung
#: einer Akte entfallen (siehe `_short_name`).
_RECHTSFORM_SUFFIXE = (
    "GmbH & Co. KG", "gGmbH", "GmbH", "mbH", "AG", "SE", "KG", "OHG",
    "GbR", "UG", "e.K.", "e.V.",
)

# Eindeutiges, im Datenbestand SICHTBARES Kennzeichen fuer synthetische
# Demo-/Testdaten (14.09., Nachtrag zum Overnight-Auftrag §9: Demo-Daten
# muessen "klar als Test-/Demo-Daten erkennbar", idempotent und resetbar
# sein). Bewusst ein Praefix auf `Client.client_number` statt einer neuen
# Datenbankspalte:
#   - keine Migration noetig (das Feld existiert bereits und ist genau
#     dafuer gedacht: die kanzleiseitige Mandantennummer),
#   - in der Oberflaeche SOFORT sichtbar ("DEMO-0001" in der
#     Mandantenliste) - eine unsichtbare Flag-Spalte koennte eine Kanzlei
#     versehentlich fuer echte Daten halten,
#   - liefert gleichzeitig ein exaktes, sicheres Praedikat fuer den Reset
#     (siehe `reset_demo_data`), das echte Mandanten strukturell nicht
#     treffen kann.
DEMO_CLIENT_NUMBER_PREFIX = "DEMO-"

# RFC-2606-Testdomain: `.invalid` ist per Standard technisch nicht
# aufloesbar - eine so adressierte Nachricht kann keine echte Kanzleipost
# sein. Eine Konstante statt mehrerer Literale, damit Erzeugung
# (`_email_for`) und Aufraeumen (`reset_demo_data`) nicht auseinanderlaufen.
_SYNTHETIC_EMAIL_DOMAIN = "example-testdomain.invalid"

_ORTE = (
    "Berlin", "München", "Hamburg", "Köln", "Frankfurt",
    "Stuttgart", "Düsseldorf", "Leipzig", "Hannover", "Nürnberg",
)


def reset_demo_data(db: Session) -> dict[str, int]:
    """Entfernt ALLE synthetischen Demo-Daten wieder - und ausschliesslich
    diese (14.09., Nachtrag §9: "resetbar", "Wiederholte Testlaeufe duerfen
    nicht unkontrolliert Datensaetze anhaeufen").

    Sicherheitsanker ist AUSSCHLIESSLICH das Praefix auf
    `Client.client_number` (siehe DEMO_CLIENT_NUMBER_PREFIX): geloescht wird
    nur, was an einem so markierten Mandanten haengt. Ein echter Mandant
    ohne dieses Praefix kann strukturell nicht getroffen werden - deshalb
    bewusst KEIN "alles loeschen"-Pfad und kein Loeschen anhand von
    Namensmustern (ein echter Mandant koennte "Mustermann" heissen).

    Loescht bewusst in Abhaengigkeitsreihenfolge von innen nach aussen statt
    sich auf ORM-Kaskaden zu verlassen - die Kaskadenkonfiguration ist pro
    Beziehung unterschiedlich und ein stiller FK-Fehler waere hier
    schwer zu bemerken.

    Gibt die Anzahl geloeschter Datensaetze je Typ zurueck (fuer eine
    ehrliche, pruefbare Ausgabe im CLI-Skript statt eines blossen "fertig").
    """
    demo_clients = (
        db.query(Client)
        .filter(Client.client_number.like(f"{DEMO_CLIENT_NUMBER_PREFIX}%"))
        .all()
    )
    client_ids = [c.id for c in demo_clients]
    if not client_ids:
        return {
            "clients": 0, "matters": 0, "messages": 0,
            "documents": 0, "deadlines": 0, "tasks": 0, "audit_events": 0,
            "parties": 0,
        }

    matter_ids = [
        m.id for m in db.query(Matter).filter(Matter.client_id.in_(client_ids)).all()
    ]

    counts = {"clients": len(client_ids), "matters": len(matter_ids)}
    if matter_ids:
        # Postausgang VOR den Entwuerfen loeschen (OutboxEntry.draft_id ist
        # ein Fremdschluessel auf Draft), Entwuerfe vor der Akte. Ohne
        # diesen Block blieben die seit 15.09. erzeugten Demo-Entwuerfe und
        # Postausgangs-Eintraege als Waisen zurueck und wuerden die
        # Entwurfs-/Postausgangsliste bei jedem Seeden weiter anfuellen -
        # genau der Waisen-Fehler, der bei den Demo-Nachrichten schon
        # einmal auftrat (siehe Kommentar weiter unten).
        counts["outbox_entries"] = (
            db.query(OutboxEntry)
            .filter(OutboxEntry.matter_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        # Die Versionskette zeigt per `previous_version_id` auf einen
        # anderen Draft DERSELBEN Menge. Ein Bulk-Delete hat keine
        # garantierte Reihenfolge, koennte also die Vorversion vor der
        # Folgeversion loeschen. Die Selbstreferenz deshalb zuerst loesen -
        # unabhaengig davon, ob SQLite die Fremdschluessel gerade erzwingt
        # (PRAGMA foreign_keys ist nicht ueberall gesetzt; sich darauf zu
        # verlassen waere genau die Art stiller Annahme, die hier schon
        # einmal zu Waisen gefuehrt hat).
        db.query(Draft).filter(Draft.matter_id.in_(matter_ids)).update(
            {Draft.previous_version_id: None}, synchronize_session=False
        )
        counts["drafts"] = (
            db.query(Draft)
            .filter(Draft.matter_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        counts["deadlines"] = (
            db.query(Deadline)
            .filter(Deadline.matter_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        counts["tasks"] = (
            db.query(Task)
            .filter(Task.matter_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        # ECHTER FUND (20.09., beim realen GUI-Durchgang durch die
        # installierte Anwendung entdeckt, siehe OPEN_ISSUES.md fuer die
        # volle Reproduktion): `ProcessingError` (Fehler-&-Wiederholungen-
        # Uebersicht, app/errors/) referenziert ein `Document` nur per
        # freiem `entity_id`-String, OHNE Fremdschluessel/Cascade - ein
        # Bulk-Loeschen von Dokumenten OHNE diese Zeile hinterliess
        # dauerhaft verwaiste Fehler-Eintraege, die auf ein nicht mehr
        # existierendes Dokument zeigen und in der UI fuer immer als
        # "offen" erscheinen (real in der Produktions-DB gefunden: zwei
        # solche Waisen aus fruehren Demo-Daten-Resets, eine davon durch
        # einen Retry-Versuch sogar dauerhaft in "retrying" haengengeblieben
        # - siehe der dazugehoerige Fix in app/errors/service.py). Die
        # Dokument-IDs muessen VOR dem Loeschen erfasst werden - ein Bulk-
        # `Query.delete()` liefert nur die Trefferanzahl, keine Zeilen.
        document_ids = [
            d.id for d in db.query(Document.id).filter(Document.matter_id.in_(matter_ids)).all()
        ]
        if document_ids:
            db.query(ProcessingError).filter(
                ProcessingError.entity_type == "Document",
                ProcessingError.entity_id.in_(document_ids),
            ).delete(synchronize_session=False)
        counts["documents"] = (
            db.query(Document)
            .filter(Document.matter_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        counts["messages"] = (
            db.query(Message)
            .filter(Message.matter_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        # ECHTER FUND (17.09., beim Bauen von app/web/parties_router.py
        # selbst entdeckt, VOR dem Fertigmelden behoben statt nur
        # dokumentiert): `Party` ist erst seit heute ueberhaupt anlegbar
        # (siehe dortiges Moduldocstring) - dieser Bulk-Reset kannte das
        # Modell noch nicht. `Matter.parties` hat zwar `cascade="all,
        # delete-orphan"`, aber `Query.delete()` ist ein reines Bulk-SQL-
        # DELETE OHNE ORM-Objekte im Session-Identity-Map - SQLAlchemy-
        # Cascades greifen dabei NICHT (dokumentiertes Verhalten, kein
        # Sonderfall dieses Modells). Ohne diese Zeile wuerde jede Demo-
        # Akte mit mindestens einer erfassten Partei beim naechsten
        # `--reset` genau die Art verwaister Zeile hinterlassen, die heute
        # bereits einmal real in der Produktions-DB gefunden wurde (siehe
        # OPEN_ISSUES.md, DOCX-Export-Absturz-Fund) - hier praeventiv
        # vermieden, nicht erst nachtraeglich repariert.
        counts["parties"] = (
            db.query(Party)
            .filter(Party.matter_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        counts["audit_events"] = (
            db.query(AuditEvent)
            .filter(AuditEvent.entity_id.in_(matter_ids))
            .delete(synchronize_session=False)
        )
        db.query(Matter).filter(Matter.id.in_(matter_ids)).delete(
            synchronize_session=False
        )
    else:
        counts.update(
            {
                "deadlines": 0,
                "tasks": 0,
                "documents": 0,
                "messages": 0,
                "audit_events": 0,
                "parties": 0,
            }
        )

    # Noch NICHT zugeordnete Demo-Nachrichten (matter_id IS NULL, siehe
    # generate_case(unassigned=True)) haengen an keiner Akte und wuerden von
    # der Loeschung oben nicht erfasst - sie blieben als Waisen zurueck und
    # wuerden den Posteingang bei jedem erneuten Seeden weiter anfuellen,
    # genau entgegen der Idempotenz-Zusage. Sicherer Anker dafuer ist die
    # RFC-2606-Testdomain im Absender: `.invalid` ist per Standard technisch
    # nicht aufloesbar und kann deshalb keine echte Kanzleipost sein.
    orphan_messages = (
        db.query(Message)
        .filter(
            Message.matter_id.is_(None),
            Message.sender.like(f"%@{_SYNTHETIC_EMAIL_DOMAIN}%"),
        )
        .all()
    )
    if orphan_messages:
        orphan_ids = [m.id for m in orphan_messages]
        counts["documents"] += (
            db.query(Document)
            .filter(Document.message_id.in_(orphan_ids))
            .delete(synchronize_session=False)
        )
        counts["messages"] += (
            db.query(Message)
            .filter(Message.id.in_(orphan_ids))
            .delete(synchronize_session=False)
        )

    db.query(Client).filter(Client.id.in_(client_ids)).delete(
        synchronize_session=False
    )
    db.commit()
    return counts


@dataclass
class SyntheticCase:
    """Bündelt alle für einen Fall erzeugten Datensätze - erleichtert
    Tests/Auswertungen (Prompt 30), die auf mehrere Teile gleichzeitig
    zugreifen wollen."""

    client: Client
    matter: Matter
    message: Message
    document: Document
    deadline: Deadline | None
    scenario_key: str
    #: Entwurfsversionen dieser Akte, aelteste zuerst (leer, wenn der Fall
    #: noch keinen Antwortentwurf hat - siehe `_maybe_create_draft_chain`).
    drafts: list[Draft] = field(default_factory=list)
    #: Postausgang-Eintrag, falls der letzte Entwurf freigegeben wurde.
    outbox_entry: OutboxEntry | None = None


@dataclass
class SyntheticComplexCase:
    """Ergebnis von `generate_complex_case` - buendelt einen vollstaendigen
    Fall mit MEHREREN verbundenen Dokumenten (20.09., Workstream B), anders
    als `SyntheticCase` (genau ein Dokument)."""

    client: Client
    matter: Matter
    documents: list[Document]
    deadline: Deadline | None
    draft: Draft
    scenario_key: str


class SyntheticDataGenerator:
    def __init__(
        self, seed: int | None = None, document_storage_dir: Path | str | None = None
    ) -> None:
        # `seed=None` => nicht-deterministisch (für Demo-Zwecke gedacht,
        # jedes Mal neue Fälle). Ein gesetzter Seed => reproduzierbar
        # (für den Benchmark aus Prompt 30 wichtig).
        self._random = random.Random(seed)
        # `document_storage_dir` (14.09., Nachtrag §6/§8 "Keine
        # Test-Illusion"): bisher trug jedes erzeugte `Document` nur einen
        # FIKTIVEN Pfad ("/data/synthetic/...") ohne Datei dahinter - reale
        # Dokument-Workflows (Extraktion, OCR-Status, Dokumentanalyse,
        # Vorschau) waren auf Demo-Daten damit gar nicht ausfuehrbar, obwohl
        # `extracted_text` bereits gefuellt war. Ist ein Verzeichnis
        # angegeben, wird pro Dokument eine ECHTE, mit der bestehenden
        # Extraktion lesbare PDF-Datei geschrieben.
        # Bewusst OPTIONAL (Default `None` = bisheriges Verhalten): Unit-
        # Tests gegen eine In-Memory-DB sollen keine Dateien anlegen
        # muessen, und die bestehenden Aufrufer bleiben unveraendert.
        self._document_storage_dir = (
            Path(document_storage_dir) if document_storage_dir else None
        )
        # Laufender Fallindex - steuert DETERMINISTISCH, welche Faelle einen
        # Antwortentwurf bekommen (siehe `_DRAFT_PLAN`). Bewusst ein Zaehler
        # statt `self._random`: der Entwurfszustand einer Demo-Kanzlei soll
        # bei gleichem Seed identisch sein, aber auch bei `seed=None` eine
        # sinnvolle Mischung ergeben statt zufaelliger Haeufungen.
        self._case_index = 0
        # Derselbe Klassifikator, den auch der produktive
        # DocumentProcessingService verwendet - Demo-Daten sollen exakt das
        # zeigen, was die echte Pipeline liefern wuerde (siehe generate_case).
        self._classifier = PlaceholderDocumentClassifier()

    def _pick_person_name(self) -> str:
        return f"{self._random.choice(_VORNAMEN)} {self._random.choice(_NACHNAMEN)}"

    def _pick_client_name(self) -> str:
        # Mischung aus Privatpersonen und fiktiven Firmen - realistisch
        # für eine Steuerkanzlei mit gemischter Mandantschaft.
        if self._random.random() < 0.5:
            return self._pick_person_name()
        return self._random.choice(_FIRMENNAMEN_MUSTER)

    def _short_name(self, full_name: str) -> str:
        """Kurzbezeichnung des Mandanten für den Aktentitel.

        ECHTER FUND (15.09., beim Nachmessen des Generators): vorher
        `full_name.split()[0].lower()`. Daraus entstanden Aktentitel wie
        "Einspruch Steuerbescheid 2024 – musterbau", "Betriebsprüfung 2022
        – julia", "Vertragsprüfung – claudia". Kleingeschriebene
        Vornamens-/Firmenfragmente sehen in einer Aktenliste nicht nach
        Kanzlei aus, sondern nach kaputten Testdaten - und genau diese
        Liste ist die Demo-Oberfläche für die Pilotkanzlei.

        Kanzleiübliche Kurzbezeichnung:
        - Firma: Name ohne angehängte Rechtsform ("Musterbau GmbH" →
          "Musterbau", "Testhandel Weber KG" → "Testhandel Weber").
        - Privatperson: Nachname ("Julia Schmidt" → "Schmidt") - danach
          wird eine Akte in der Kanzlei tatsächlich benannt, nicht nach
          dem Vornamen.
        """
        name = full_name.strip()
        # Längste Rechtsform zuerst, damit "GmbH & Co. KG" nicht als "KG"
        # greift und ein "& Co." stehen lässt.
        for suffix in sorted(_RECHTSFORM_SUFFIXE, key=len, reverse=True):
            if name.endswith(" " + suffix):
                return name[: -(len(suffix) + 1)].strip()
        # Firma OHNE angehängte Rechtsform (z. B. "Handwerk Schmidt &
        # Söhne") - vollständig übernehmen. Die Nachnamen-Regel unten würde
        # daraus sonst "Söhne" machen; der erste Anlauf dieses Fixes hat
        # genau das produziert. Erkennung bewusst exakt (bekannte
        # Firmenliste) mit "&" als zusätzlichem, eindeutigem Firmenindiz,
        # statt zu raten.
        if name in _FIRMENNAMEN_MUSTER or "&" in name:
            return name
        parts = name.split()
        return parts[-1] if len(parts) > 1 else name

    def _filename_slug(self, short_name: str) -> str:
        """Dateisystemtauglicher Namensteil für Dokumentdateinamen.

        SELBST EINGEBAUTE REGRESSION, hier behoben (15.09.): der Fix an
        `_short_name` (lesbare Aktentitel) schlug ungewollt auch auf
        `document_filename_template` durch, das denselben Platzhalter
        benutzte. Reale Ausgabe danach:
        "mahnung_Handwerk Schmidt & Söhne.pdf",
        "vertragsentwurf_Beispiel Logistik.pdf" - Leerzeichen, "&" und
        Grossbuchstaben im Dateinamen. Aufgefallen beim Nachsehen der
        tatsaechlich erzeugten Dokumente, nicht durch einen Test.

        Aktentitel und Dateiname haben verschiedene Anforderungen: der
        Titel soll lesbar sein, der Dateiname unauffaellig. Deshalb ein
        eigener Platzhalter statt eines Kompromisses, der beides halb
        richtig macht."""
        umlaute = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
        text = short_name.lower()
        for umlaut, ersatz in umlaute.items():
            text = text.replace(umlaut, ersatz)
        text = "".join(ch if ch.isalnum() else "_" for ch in text)
        while "__" in text:
            text = text.replace("__", "_")
        return text.strip("_")

    def _email_for(self, name: str) -> str:
        slug = name.lower().replace(" ", ".").replace("&", "und")
        slug = "".join(ch for ch in slug if ch.isalnum() or ch == ".")
        return f"{slug}@{_SYNTHETIC_EMAIL_DOMAIN}"

    def _phone_number(self) -> str:
        """Bewusst aus dem fuer fiktive Nummern reservierten deutschen
        Rufnummernbereich (030 23125 xx, siehe BNetzA-Reservierung fuer
        Film/Funk) - kann keine reale Person erreichen."""
        return f"+49 30 23125 {self._random.randint(10, 99)}"

    def _write_document_file(self, matter_id: str, filename: str, text: str) -> str:
        """Schreibt den Dokumenttext als ECHTE, extrahierbare Datei -
        sofern ein `document_storage_dir` konfiguriert ist (siehe
        __init__). Ohne Verzeichnis bleibt es beim bisherigen, rein
        fiktiven Pfad.

        Nutzt bewusst `pymupdf`/`python-docx` - dieselben Bibliotheken, mit
        denen die Anwendung diese Formate auch wieder LIEST
        (app/documents/extraction.py); die erzeugte Datei ist damit
        nachweislich durch die echte Extraktionslogik verarbeitbar und
        nicht nur "eine Datei".

        Formatwahl anhand der ANGEFORDERTEN Dateiendung (20.09., Workstream
        B "Dokument-Varianz" - vorher wurde IMMER eine PDF geschrieben,
        selbst wenn `filename` auf ".docx" endete, was zu einem irreführenden
        ".docx.pdf"-Dateinamen und einem falschen Format-Badge geführt hätte
        - echter, selbst gefundener Fund beim Entwurf dieser Erweiterung,
        siehe `icons.file_type_badge`-Warnung zur Endungstreue an anderer
        Stelle im Projekt)."""
        if self._document_storage_dir is None:
            return f"/data/synthetic/{matter_id}/{filename}"

        target_dir = self._document_storage_dir / matter_id
        target_dir.mkdir(parents=True, exist_ok=True)

        from app.export.pdf_text import sanitize_for_base14_font

        if filename.lower().endswith(".docx"):
            from docx import Document as DocxDocument

            target = target_dir / filename
            docx_document = DocxDocument()
            for paragraph in text.split("\n"):
                docx_document.add_paragraph(paragraph)
            docx_document.save(str(target))
            return str(target)

        import pymupdf

        pdf_name = filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"
        target = target_dir / pdf_name
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 545, 792),
            sanitize_for_base14_font(text),
            fontsize=11,
            fontname="helv",
        )
        doc.save(str(target))
        doc.close()
        return str(target)

    def _next_demo_client_number(self, db: Session) -> str:
        """Fortlaufende, kollisionsfreie Demo-Mandantennummer. `client_number`
        traegt eine UNIQUE-Constraint - bei wiederholtem Seeden gegen dieselbe
        Datenbank muss die naechste freie Nummer gefunden werden (gleiches
        Muster wie `_generate_unique_reference_number`)."""
        existing = (
            db.query(Client)
            .filter(Client.client_number.like(f"{DEMO_CLIENT_NUMBER_PREFIX}%"))
            .count()
        )
        for offset in range(existing + 1, existing + 500):
            candidate = f"{DEMO_CLIENT_NUMBER_PREFIX}{offset:04d}"
            taken = (
                db.query(Client).filter_by(client_number=candidate).first() is not None
            )
            if not taken:
                return candidate
        raise RuntimeError(
            "Keine freie Demo-Mandantennummer gefunden - bitte Demo-Daten "
            "zuruecksetzen (siehe reset_demo_data)."
        )

    def generate_case(
        self,
        db: Session,
        *,
        scenario_key: str | None = None,
        unassigned: bool = False,
        include_draft: bool = False,
    ) -> SyntheticCase:
        """Erzeugt EINEN vollständigen, in sich konsistenten Fall
        (Mandant + Akte + eingehende Nachricht + Dokument + ggf. Frist)
        und committet ihn.

        `include_draft=False` per Default (15.09.): ein Antwortentwurf wird
        NUR erzeugt, wenn der Aufrufer es ausdrücklich anfordert.
        ECHTER FUND: `test_full_case_journey_from_synthetic_data_to_sent_outbox`
        ruft `generate_case()` zweimal direkt auf, um zwei UNABHAENGIGE
        Faelle fuer einen Aktenisolations-Test aufzubauen ("der zweite Fall
        bleibt waehrend der GESAMTEN Reise vollstaendig unberuehrt") - mit
        `include_draft` standardmaessig aktiv haette der zweite Aufruf
        (Index 1) einen automatisch erzeugten Entwurf bekommen und genau
        diese Isolations-Zusicherung gebrochen. Ein direkter
        `generate_case()`-Aufruf ist ein bewusst MINIMALER, vom Aufrufer
        kontrollierter Einzelfall - die realistische Durchmischung mit
        Entwuerfen/Postausgang ist eine Eigenschaft von `generate_many()`
        (dort `include_draft=True`), nicht der Einzelfall-API.

        `unassigned=True` (14.09.): die Nachricht und ihr Dokument kommen
        NOCH OHNE Aktenzuordnung herein - der reale Ausgangszustand des
        Gold-Workflows ("E-Mail vom Finanzamt trifft ein, Lexono soll
        Mandant und Akte erkennen"). ECHTER FUND beim UI-Durchgang: der
        Posteingang meldete "0 ohne Aktenzuordnung", weil JEDE erzeugte
        Nachricht bereits fest einer Akte zugeordnet war - der wichtigste
        Zustand des Posteingangs (und die gesamte Zuordnungs-Oberflaeche
        samt Vorschlagskarte) war dadurch mit Demo-Daten gar nicht
        darstellbar oder testbar.

        Die passende Akte wird trotzdem angelegt: sie ist die RICHTIGE
        Antwort, die Lexono finden soll - dadurch ist ueberpruefbar, ob die
        Zuordnung den korrekten Vorschlag macht, statt nur "irgendeinen"."""
        scenario = self._pick_scenario(scenario_key)
        case_index = self._case_index
        self._case_index += 1

        mandant_name = self._pick_client_name()
        mandant_kurz = self._short_name(mandant_name)
        jahr = self._random.randint(2022, 2025)
        betrag = self._random.randint(500, 25000)
        bescheid_datum = date.today() - timedelta(days=self._random.randint(1, 10))

        format_kwargs = {
            "mandant": mandant_name,
            "mandant_kurz": mandant_kurz,
            # Getrennt vom lesbaren Kurznamen, siehe `_filename_slug`.
            "mandant_dateiname": self._filename_slug(mandant_kurz),
            "jahr": jahr,
            "jahr_von": jahr - 2,
            "betrag": f"{betrag:,}".replace(",", "."),
            "bescheid_datum": bescheid_datum.strftime("%d.%m.%Y"),
            "pruefungsbeginn": (
                date.today() + timedelta(days=self._random.randint(10, 40))
            ).strftime("%d.%m.%Y"),
        }

        # Mandantenstammdaten vollstaendig statt nur `name` (14.09., echter
        # Fund beim UI/UX-Abgleich gegen assets/ux-ui/29_mandanten_uebersicht.png):
        # die Referenz zeigt Mandantennummer, Kontakt und Rechtsgebiet - der
        # Generator hat bisher NUR den Namen gesetzt, wodurch die
        # Mandantenliste in jeder Demo-/Testumgebung durchgehend "–" anzeigte
        # und der Referenzzustand gar nicht herstellbar war.
        client = Client(
            name=mandant_name,
            client_number=self._next_demo_client_number(db),
            contact_email=self._email_for(mandant_name),
            contact_phone=self._phone_number(),
            practice_area=scenario.practice_area,
            status="active",
        )
        db.add(client)
        db.flush()

        matter = Matter(
            client_id=client.id,
            title=scenario.matter_title_template.format(**format_kwargs),
            practice_area=scenario.practice_area,
            reference_number=self._generate_unique_reference_number(
                jahr, db, practice_area=scenario.practice_area
            ),
        )
        db.add(matter)
        db.flush()

        received_at = datetime.now(timezone.utc) - timedelta(
            days=self._random.randint(0, 5), hours=self._random.randint(0, 23)
        )
        message = Message(
            # Bei `unassigned` bewusst ohne Akte - siehe Docstring.
            matter_id=None if unassigned else matter.id,
            direction="inbound",
            # Realistischer Absender MIT Anzeigename ("Sabine Schmidt
            # <sabine.schmidt@...>") statt blosser Adresse (14.09.): echte
            # Kanzleipost traegt praktisch immer einen Anzeigenamen, und die
            # Aktenzuordnung wertet ihn als eigenes Signal aus
            # (app/matching/matcher.py). Mit blosser Adresse war die
            # Demo-Datenbasis in genau diesem Punkt unrealistisch und
            # konnte den Namensabgleich gar nicht ueben.
            sender=f"{mandant_name} <{self._email_for(mandant_name)}>",
            subject=scenario.email_subject_template.format(**format_kwargs),
            body_text=scenario.email_body_template.format(**format_kwargs),
            created_at=received_at,
        )
        db.add(message)
        db.flush()

        filename = scenario.document_filename_template.format(**format_kwargs)
        document_text = scenario.document_extracted_text_template.format(**format_kwargs)
        file_path = self._write_document_file(matter.id, filename, document_text)
        # Klassifikation durch den ECHTEN Klassifikator statt hart gesetzter
        # Szenario-Werte (14.09., Nachtrag §8 "Keine Test-Illusion").
        # ECHTER FUND: die Szenarien setzten `classified_type` fest UND eine
        # erfundene Konfidenz zwischen 0.6 und 0.95 - der produktive
        # `PlaceholderDocumentClassifier` kann aber konstruktionsbedingt
        # NIE ueber 0.4 kommen (bewusste Sicherheitsgrenze, siehe
        # app/classification/classifier.py). Demo-Daten zeigten damit
        # Konfidenzen, die im echten Betrieb unmoeglich sind - und haetten
        # eine automatische Aktenzuordnung als funktionierend erscheinen
        # lassen, die real strukturell gar nicht greifen kann. Ausserdem
        # waren die festen Werte inhaltlich ueberholt (eine
        # Pruefungsanordnung war als "Gerichtliches Schreiben" gesetzt).
        classification = self._classifier.classify(document_text, filename=filename)
        document = Document(
            matter_id=None if unassigned else matter.id,
            message_id=message.id,
            original_filename=filename,
            file_path=file_path,
            extracted_text=document_text,
            classified_type=classification.document_type,
            classification_confidence=classification.confidence,
        )
        db.add(document)
        db.flush()

        deadline: Deadline | None = None
        if (
            not unassigned
            and scenario.has_deadline
            and scenario.deadline_days_from_now is not None
        ):
            source_text = (
                scenario.deadline_source_text_template.format(**format_kwargs)
                if scenario.deadline_source_text_template
                else None
            )
            deadline = Deadline(
                matter_id=matter.id,
                document_id=document.id,
                source_text=source_text,
                due_date=date.today() + timedelta(days=scenario.deadline_days_from_now),
                confidence=round(self._random.uniform(0.5, 0.9), 2),
            )
            db.add(deadline)
            db.flush()

        drafts: list[Draft] = []
        outbox_entry: OutboxEntry | None = None
        if include_draft:
            drafts, outbox_entry = self._maybe_create_draft_chain(
                db,
                matter=matter,
                message=message,
                scenario=scenario,
                format_kwargs=format_kwargs,
                unassigned=unassigned,
                case_index=case_index,
            )

        db.add(
            AuditEvent(
                entity_type="Matter",
                entity_id=matter.id,
                event_type="synthetic_case_generated",
                actor="system",
                details=f"Synthetischer Testfall erzeugt (Szenario: {scenario.key})",
            )
        )
        db.commit()
        db.refresh(client)
        db.refresh(matter)
        db.refresh(message)
        db.refresh(document)
        if deadline is not None:
            db.refresh(deadline)

        return SyntheticCase(
            client=client,
            matter=matter,
            message=message,
            document=document,
            deadline=deadline,
            scenario_key=scenario.key,
            drafts=drafts,
            outbox_entry=outbox_entry,
        )

    def generate_complex_case(self, db: Session) -> "SyntheticComplexCase":
        """Erzeugt EINEN vollstaendigen, MEHRSEITIGEN synthetischen Fall
        mit einer echten, chronologisch verbundenen Dokumentenkette
        (20.09., Owner-Direktive "WORKSTREAM B — SYNTHETISCHE KANZLEI-
        WELT" §10: "NICHT einfach viele Mandanten mit jeweils einem
        Dokument anlegen. Stattdessen vollstaendige synthetische Faelle").

        Bewusst EIN fest ausgearbeiteter Gesellschaftsrecht-Fall
        ("Gesellschafterstreit - Anteilsuebertragung") statt eines
        generischen Vorlagen-Systems fuer beliebige Ketten - Direktive
        Punkt 26 verlangt ausdruecklich "weniger, aber vollstaendig
        verbundene Faelle" statt einer Datenflut, und ein einzelner, echt
        durchdachter Fall mit sechs inhaltlich zusammenhaengenden
        Stationen demonstriert den vollen Dokumentlebenszyklus besser als
        viele oberflaechliche Varianten. Rechtsgebiet "Gesellschaftsrecht"
        bewusst gewaehlt, weil es innerhalb der dokumentierten Zielgruppe
        "Steuer-/Wirtschaftskanzleien" liegt (PROJECT_STATE.md,
        "Produktidentitaet") - anders als z. B. Familien-/Straf-/reines
        Verkehrsrecht, die NICHT ergaenzt wurden (siehe DECISIONS.md).

        Kette (chronologisch, jeweils mit echter, extrahierbarer Datei -
        PDF UND einmal echtes DOCX, siehe `_write_document_file`):
        1. Gesellschaftsvertrag (Referenzdokument, PDF)
        2. Eingehende E-Mail des Mandanten (schildert den Konflikt)
        3. Schreiben der Gegenseite (Anwalt des Mitgesellschafters,
           Forderung MIT Frist, PDF)
        4. Handelsregisterauszug (Nachweis, PDF)
        5. Eigene Aktenanalyse (internes Arbeitsdokument, ECHTES DOCX -
           testet die in dieser Runde neu ergaenzte DOCX-Schreibfaehigkeit
           von `_write_document_file`)
        6. Antwortentwurf (Draft, feste Vorlage wie bei `generate_case`,
           KEIN KI-Aufruf)."""
        case_index = self._case_index
        self._case_index += 1

        mandant_name = self._pick_client_name()
        mandant_kurz = self._short_name(mandant_name)
        mandant_dateiname = self._filename_slug(mandant_kurz)
        gegner_name = self._pick_person_name()
        jahr = date.today().year
        today = date.today()

        client = Client(
            name=mandant_name,
            client_number=self._next_demo_client_number(db),
            contact_email=self._email_for(mandant_name),
            contact_phone=self._phone_number(),
            practice_area="Gesellschaftsrecht",
            status="active",
        )
        db.add(client)
        db.flush()

        matter = Matter(
            client_id=client.id,
            title=f"Gesellschafterstreit Anteilsübertragung – {mandant_kurz}",
            practice_area="Gesellschaftsrecht",
            reference_number=self._generate_unique_reference_number(
                jahr, db, practice_area="Gesellschaftsrecht"
            ),
        )
        db.add(matter)
        db.flush()

        documents: list[Document] = []
        messages: list[Message] = []

        def _add_inbound(
            *, days_ago: int, subject: str, body: str, filename: str, text: str,
            classified_type_hint: str | None = None,
        ) -> tuple[Message, Document]:
            created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
            msg = Message(
                matter_id=matter.id,
                direction="inbound",
                sender=f"{mandant_name} <{self._email_for(mandant_name)}>",
                subject=subject,
                body_text=body,
                created_at=created_at,
            )
            db.add(msg)
            db.flush()
            file_path = self._write_document_file(matter.id, filename, text)
            classification = self._classifier.classify(text, filename=filename)
            doc = Document(
                matter_id=matter.id,
                message_id=msg.id,
                original_filename=filename,
                file_path=file_path,
                extracted_text=text,
                classified_type=classification.document_type,
                classification_confidence=classification.confidence,
                created_at=created_at,
            )
            db.add(doc)
            db.flush()
            messages.append(msg)
            documents.append(doc)
            return msg, doc

        # 1. Gesellschaftsvertrag (Referenzdokument, bereits bei
        # Mandatsbeginn bekannt).
        _add_inbound(
            days_ago=60,
            subject="Gesellschaftsvertrag zur Aktenanlage",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nanbei der Gesellschaftsvertrag "
                f"unserer {mandant_kurz} GmbH zu Ihrer Akte. Bitte legen Sie eine "
                f"Akte fuer allgemeine gesellschaftsrechtliche Fragen an.\n\n"
                f"Mit freundlichen Gruessen\n{mandant_name}"
            ),
            filename=f"gesellschaftsvertrag_{mandant_dateiname}.pdf",
            text=(
                f"Gesellschaftsvertrag der {mandant_kurz} GmbH.\n"
                f"§ 5 Geschaeftsanteile: Die Geschaeftsanteile koennen nur mit "
                f"Zustimmung der Gesellschafterversammlung uebertragen werden.\n"
                f"§ 12 Vorkaufsrecht: Den uebrigen Gesellschaftern steht bei einer "
                f"geplanten Anteilsuebertragung ein Vorkaufsrecht zu."
            ),
        )

        # 2. Eingehende E-Mail: schildert den eigentlichen Konflikt.
        _add_inbound(
            days_ago=45,
            subject="Streit um geplante Anteilsübertragung",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nich moechte meinen "
                f"Geschaeftsanteil an einen externen Investor veraeussern. Mein "
                f"Mitgesellschafter, Herr {gegner_name}, widerspricht unter "
                f"Berufung auf das Vorkaufsrecht aus § 12 des Gesellschaftsvertrags. "
                f"Bitte pruefen Sie, ob dieser Widerspruch berechtigt ist.\n\n"
                f"Mit freundlichen Gruessen\n{mandant_name}"
            ),
            filename=f"schilderung_konflikt_{mandant_dateiname}.pdf",
            text=(
                f"Zusammenfassung des Sachverhalts durch den Mandanten: geplante "
                f"Uebertragung des Geschaeftsanteils an einen externen Investor. "
                f"Mitgesellschafter {gegner_name} widerspricht unter Berufung auf "
                f"das vertragliche Vorkaufsrecht."
            ),
        )

        # 3. Schreiben der Gegenseite - MIT Frist.
        _, gegner_doc = _add_inbound(
            days_ago=40,
            subject="Weiterleitung: Schreiben des gegnerischen Anwalts",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nanbei das Schreiben des "
                f"Anwalts von Herrn {gegner_name}. Er fordert eine Stellungnahme "
                f"innerhalb von 14 Tagen.\n\nMit freundlichen Gruessen\n{mandant_name}"
            ),
            filename=f"schreiben_gegenseite_{mandant_dateiname}.pdf",
            text=(
                f"Namens und in Vollmacht unseres Mandanten, Herrn {gegner_name}, "
                f"machen wir das vertragliche Vorkaufsrecht aus § 12 des "
                f"Gesellschaftsvertrags geltend und widersprechen der geplanten "
                f"Anteilsuebertragung.\n"
                f"Wir fordern eine Stellungnahme innerhalb von 14 Tagen ab Zugang "
                f"dieses Schreibens."
            ),
        )
        deadline = Deadline(
            matter_id=matter.id,
            document_id=gegner_doc.id,
            source_text="Stellungnahme innerhalb von 14 Tagen ab Zugang dieses Schreibens.",
            due_date=today + timedelta(days=14),
            confidence=0.85,
        )
        db.add(deadline)
        db.flush()

        # 4. Handelsregisterauszug (Nachweis).
        _add_inbound(
            days_ago=35,
            subject="Aktueller Handelsregisterauszug",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nwie besprochen anbei der "
                f"aktuelle Handelsregisterauszug der {mandant_kurz} GmbH.\n\n"
                f"Mit freundlichen Gruessen\n{mandant_name}"
            ),
            filename=f"handelsregisterauszug_{mandant_dateiname}.pdf",
            text=(
                f"Handelsregisterauszug {mandant_kurz} GmbH.\nGeschaeftsfuehrer: "
                f"{mandant_name}.\nGesellschafter: {mandant_name}, {gegner_name}.\n"
                f"Stammkapital: 25.000 EUR."
            ),
        )

        # 5. Eigene Aktenanalyse - ECHTES DOCX, direkt der Akte zugeordnet
        # (kein eingehendes Schreiben, sondern internes Arbeitsdokument -
        # dieselbe Modellierung wie ein direkter Akte-Upload, siehe
        # app/web/document_actions_router.py::upload_documents).
        analyse_text = (
            f"Interne Aktenanalyse - Gesellschafterstreit {mandant_kurz} GmbH\n\n"
            f"Das im Gesellschaftsvertrag (§ 12) verankerte Vorkaufsrecht duerfte "
            f"einer Anteilsuebertragung an einen externen Investor entgegenstehen, "
            f"sofern die Gesellschafterversammlung dem nicht ausdruecklich "
            f"zustimmt.\n\n"
            f"Empfehlung: Zunaechst eine Gesellschafterversammlung einberufen und "
            f"eine Zustimmung bzw. einen Verzicht auf das Vorkaufsrecht anstreben, "
            f"bevor die Uebertragung weiterverfolgt wird."
        )
        analyse_filename = f"aktenanalyse_{mandant_dateiname}.docx"
        analyse_file_path = self._write_document_file(
            matter.id, analyse_filename, analyse_text
        )
        analyse_classification = self._classifier.classify(
            analyse_text, filename=analyse_filename
        )
        analyse_doc = Document(
            matter_id=matter.id,
            original_filename=analyse_filename,
            file_path=analyse_file_path,
            extracted_text=analyse_text,
            classified_type=analyse_classification.document_type,
            classification_confidence=analyse_classification.confidence,
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        db.add(analyse_doc)
        db.flush()
        documents.append(analyse_doc)

        # 6. Antwortentwurf - feste Vorlage, KEIN KI-Aufruf (identisches
        # Prinzip wie _maybe_create_draft_chain).
        draft_content = (
            f"Sehr geehrte Damen und Herren,\n\n"
            f"namens und in Vollmacht unserer Mandantschaft, der {mandant_kurz} "
            f"GmbH, nehmen wir Stellung zu Ihrem Schreiben betreffend das "
            f"geltend gemachte Vorkaufsrecht.\n\n"
            f"Wir werden unserer Mandantschaft empfehlen, zunaechst eine "
            f"Gesellschafterversammlung einzuberufen, um die Frage einer "
            f"Zustimmung zur geplanten Anteilsuebertragung zu klaeren. Wir "
            f"bitten insoweit um eine Fristverlaengerung von zwei Wochen.\n\n"
            f"Mit freundlichen Gruessen"
        )
        draft = Draft(
            matter_id=matter.id,
            message_id=messages[-1].id,
            content=draft_content,
            version=1,
            status="draft",
        )
        db.add(draft)
        db.flush()

        db.add(
            AuditEvent(
                entity_type="Matter",
                entity_id=matter.id,
                event_type="synthetic_complex_case_generated",
                actor="system",
                details=(
                    f"Vollstaendiger synthetischer Fall erzeugt: "
                    f"Gesellschafterstreit ({len(documents)} Dokumente)"
                ),
            )
        )
        db.commit()
        db.refresh(client)
        db.refresh(matter)
        db.refresh(draft)

        return SyntheticComplexCase(
            client=client,
            matter=matter,
            documents=documents,
            deadline=deadline,
            draft=draft,
            scenario_key="gesellschafterstreit_anteilsuebertragung",
        )

    def generate_complex_case_erbschaftsteuer(self, db: Session) -> "SyntheticComplexCase":
        """Zweiter vollstaendiger, mehrseitiger synthetischer Fall (24.09.,
        Owner-Direktive "ROADMAP-ALIGNED PRODUCT COMPLETION" §10) -
        Rechtsgebiet "Erbschaftsteuer", das bereits am 20.09. als
        zusaetzlich zulaessige Zielgruppen-Erweiterung dokumentiert und
        sogar schon im Aktenzeichen-Kuerzel (`_AKTENZEICHEN_SUFFIX_JE_
        RECHTSGEBIET["Erbschaftsteuer"] = "ErbSt"`) vorbereitet, aber nie
        tatsaechlich als Fall umgesetzt wurde - eine bereits getroffene
        Scope-Entscheidung, keine neue.

        Bewusst als EIGENSTAENDIGE Methode statt einer Parametrisierung von
        `generate_complex_case` (identisches Argument wie dort: ein
        generisches Vorlagen-System wurde von der 20.09.-Direktive
        ausdruecklich abgelehnt, "weniger, aber vollstaendig verbundene
        Faelle" statt Vorlagen-Abstraktion) - vermeidet ausserdem jedes
        Regressionsrisiko fuer den bereits live verifizierten
        Gesellschafterstreit-Fall.

        Kette (chronologisch, jeweils mit echter, extrahierbarer Datei):
        1. Nachlassverzeichnis (Referenzdokument, PDF) - Mandant reicht es
           direkt nach dem Erbfall ein.
        2. Eingehende E-Mail: Mandant meldet Zweifel am spaeter
           festgesetzten Grundbesitzwert.
        3. Erbschaftsteuerbescheid des Finanzamts MIT Einspruchsfrist
           (PDF) - erzeugt die Frist.
        4. Verkehrswertgutachten der Nachlassimmobilie (Nachweis, PDF) -
           belegt einen niedrigeren Wert als im Bescheid angesetzt.
        5. Eigene Aktenanalyse (internes Arbeitsdokument, echtes DOCX).
        6. Antwortentwurf (Einspruch gegen den Bescheid, Draft)."""
        case_index = self._case_index
        self._case_index += 1

        mandant_name = self._pick_person_name()
        mandant_kurz = self._short_name(mandant_name)
        mandant_dateiname = self._filename_slug(mandant_kurz)
        # Erblasser: eigener Name, darf nicht mit dem Mandanten kollidieren.
        erblasser_name = self._pick_person_name()
        while erblasser_name == mandant_name:
            erblasser_name = self._pick_person_name()
        jahr = date.today().year
        today = date.today()
        nachlasswert = 620_000 + (case_index % 5) * 35_000
        bescheid_wert_immobilie = 410_000 + (case_index % 4) * 20_000
        gutachten_wert_immobilie = bescheid_wert_immobilie - 65_000
        festgesetzte_steuer = 38_500 + (case_index % 6) * 1_250

        client = Client(
            name=mandant_name,
            client_number=self._next_demo_client_number(db),
            contact_email=self._email_for(mandant_name),
            contact_phone=self._phone_number(),
            practice_area="Erbschaftsteuer",
            status="active",
        )
        db.add(client)
        db.flush()

        matter = Matter(
            client_id=client.id,
            title=(
                f"Erbschaftsteuer Nachlass {self._short_name(erblasser_name)} – "
                f"{mandant_kurz}"
            ),
            practice_area="Erbschaftsteuer",
            reference_number=self._generate_unique_reference_number(
                jahr, db, practice_area="Erbschaftsteuer"
            ),
        )
        db.add(matter)
        db.flush()

        documents: list[Document] = []
        messages: list[Message] = []

        def _add_inbound(
            *, days_ago: int, subject: str, body: str, filename: str, text: str,
        ) -> tuple[Message, Document]:
            created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
            msg = Message(
                matter_id=matter.id,
                direction="inbound",
                sender=f"{mandant_name} <{self._email_for(mandant_name)}>",
                subject=subject,
                body_text=body,
                created_at=created_at,
            )
            db.add(msg)
            db.flush()
            file_path = self._write_document_file(matter.id, filename, text)
            classification = self._classifier.classify(text, filename=filename)
            doc = Document(
                matter_id=matter.id,
                message_id=msg.id,
                original_filename=filename,
                file_path=file_path,
                extracted_text=text,
                classified_type=classification.document_type,
                classification_confidence=classification.confidence,
                created_at=created_at,
            )
            db.add(doc)
            db.flush()
            messages.append(msg)
            documents.append(doc)
            return msg, doc

        # 1. Nachlassverzeichnis (Referenzdokument).
        _add_inbound(
            days_ago=50,
            subject="Nachlassverzeichnis nach dem Tod meiner/meines Angehörigen",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nnach dem Tod von "
                f"{erblasser_name} übersende ich Ihnen anbei das erstellte "
                f"Nachlassverzeichnis. Ich möchte Sie bitten, die erbschaft"
                f"steuerlichen Auswirkungen für mich zu prüfen.\n\n"
                f"Mit freundlichen Grüßen\n{mandant_name}"
            ),
            filename=f"nachlassverzeichnis_{mandant_dateiname}.pdf",
            text=(
                f"Nachlassverzeichnis nach {erblasser_name}, verstorben am "
                f"{(today - timedelta(days=55)).strftime('%d.%m.%Y')}.\n"
                f"Nachlassimmobilie (Einfamilienhaus): {bescheid_wert_immobilie} EUR "
                f"(vorläufige Schätzung).\n"
                f"Bankguthaben: 85.000 EUR.\nHausrat: 12.000 EUR.\n"
                f"Nachlassverbindlichkeiten (Bestattungskosten u. a.): 9.500 EUR.\n"
                f"Vorläufiger Nachlasswert: {nachlasswert} EUR."
            ),
        )

        # 2. Eingehende E-Mail: Zweifel am spaeter festgesetzten Wert.
        _add_inbound(
            days_ago=38,
            subject="Zweifel an der Wertermittlung der Nachlassimmobilie",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nich habe den Eindruck, dass "
                f"der Wert der Immobilie im Nachlassverzeichnis zu hoch "
                f"angesetzt wurde - der tatsächliche Zustand des Hauses lässt "
                f"einen niedrigeren Verkehrswert vermuten. Können Sie das vor "
                f"der endgültigen steuerlichen Bewertung prüfen lassen?\n\n"
                f"Mit freundlichen Grüßen\n{mandant_name}"
            ),
            filename=f"rueckfrage_bewertung_{mandant_dateiname}.pdf",
            text=(
                f"Zusammenfassung durch den Mandanten: Zweifel an der "
                f"Wertermittlung der Nachlassimmobilie, Bitte um Prüfung vor "
                f"Abschluss des Besteuerungsverfahrens."
            ),
        )

        # 3. Erbschaftsteuerbescheid - MIT Frist.
        _, bescheid_doc = _add_inbound(
            days_ago=30,
            subject="Erbschaftsteuerbescheid erhalten",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nanbei der Erbschaftsteuer"
                f"bescheid des Finanzamts. Bitte prüfen Sie, ob der angesetzte "
                f"Immobilienwert korrekt ist.\n\nMit freundlichen Grüßen\n"
                f"{mandant_name}"
            ),
            filename=f"erbschaftsteuerbescheid_{mandant_dateiname}.pdf",
            text=(
                f"Erbschaftsteuerbescheid.\n"
                f"Erblasser: {erblasser_name}. Erwerber: {mandant_name}.\n"
                f"Steuerklasse I, persönlicher Freibetrag: 400.000 EUR.\n"
                f"Wert des Erwerbs (Grundbesitzwert Immobilie: "
                f"{bescheid_wert_immobilie} EUR zzgl. sonstiges Vermögen): "
                f"{nachlasswert} EUR.\n"
                f"Steuerpflichtiger Erwerb nach Freibetrag: "
                f"{nachlasswert - 400_000} EUR.\n"
                f"Festgesetzte Erbschaftsteuer: {festgesetzte_steuer} EUR.\n"
                f"Rechtsbehelfsbelehrung: Einspruch ist innerhalb eines Monats "
                f"nach Bekanntgabe dieses Bescheids einzulegen."
            ),
        )
        deadline = Deadline(
            matter_id=matter.id,
            document_id=bescheid_doc.id,
            source_text=(
                "Einspruch ist innerhalb eines Monats nach Bekanntgabe dieses "
                "Bescheids einzulegen."
            ),
            due_date=today + timedelta(days=28),
            confidence=0.85,
        )
        db.add(deadline)
        db.flush()

        # 4. Verkehrswertgutachten - Nachweis eines niedrigeren Werts.
        _add_inbound(
            days_ago=20,
            subject="Verkehrswertgutachten der Nachlassimmobilie",
            body=(
                f"Sehr geehrte Damen und Herren,\n\nwie besprochen anbei das "
                f"von einem öffentlich bestellten Sachverständigen erstellte "
                f"Verkehrswertgutachten der Nachlassimmobilie.\n\n"
                f"Mit freundlichen Grüßen\n{mandant_name}"
            ),
            filename=f"verkehrswertgutachten_{mandant_dateiname}.pdf",
            text=(
                f"Verkehrswertgutachten der Nachlassimmobilie (Einfamilienhaus).\n"
                f"Ermittelter Verkehrswert zum Bewertungsstichtag: "
                f"{gutachten_wert_immobilie} EUR.\n"
                f"Begründung: erhöhter Sanierungsstau (Dach, Heizungsanlage), "
                f"im pauschalen Bescheidwert nicht berücksichtigt."
            ),
        )

        # 5. Eigene Aktenanalyse - echtes DOCX.
        analyse_text = (
            f"Interne Aktenanalyse - Erbschaftsteuer Nachlass "
            f"{self._short_name(erblasser_name)} ({mandant_kurz})\n\n"
            f"Der im Erbschaftsteuerbescheid angesetzte Grundbesitzwert der "
            f"Nachlassimmobilie ({bescheid_wert_immobilie} EUR) liegt "
            f"{bescheid_wert_immobilie - gutachten_wert_immobilie} EUR über "
            f"dem durch das eingeholte Verkehrswertgutachten belegten Wert "
            f"({gutachten_wert_immobilie} EUR).\n\n"
            f"Nach § 198 BewG kann ein niedrigerer gemeiner Wert durch ein "
            f"Gutachten nachgewiesen werden. Empfehlung: fristgerecht "
            f"Einspruch einlegen und das Gutachten als Nachweis beifügen."
        )
        analyse_filename = f"aktenanalyse_{mandant_dateiname}.docx"
        analyse_file_path = self._write_document_file(
            matter.id, analyse_filename, analyse_text
        )
        analyse_classification = self._classifier.classify(
            analyse_text, filename=analyse_filename
        )
        analyse_doc = Document(
            matter_id=matter.id,
            original_filename=analyse_filename,
            file_path=analyse_file_path,
            extracted_text=analyse_text,
            classified_type=analyse_classification.document_type,
            classification_confidence=analyse_classification.confidence,
            created_at=datetime.now(timezone.utc) - timedelta(days=15),
        )
        db.add(analyse_doc)
        db.flush()
        documents.append(analyse_doc)

        # 6. Antwortentwurf - Einspruch, feste Vorlage, KEIN KI-Aufruf.
        draft_content = (
            f"Sehr geehrte Damen und Herren,\n\n"
            f"namens und in Vollmacht unseres Mandanten, {mandant_name}, "
            f"legen wir gegen den Erbschaftsteuerbescheid betreffend den "
            f"Nachlass von {erblasser_name}\n\n"
            f"                    E i n s p r u c h\n\n"
            f"ein.\n\n"
            f"Begründung:\n"
            f"Der im Bescheid angesetzte Grundbesitzwert der Nachlass"
            f"immobilie von {bescheid_wert_immobilie} EUR ist überhöht. "
            f"Ausweislich des beigefügten Verkehrswertgutachtens beträgt der "
            f"tatsächliche gemeine Wert lediglich {gutachten_wert_immobilie} "
            f"EUR (§ 198 BewG). Wir beantragen, den Bescheid entsprechend zu "
            f"ändern und die Erbschaftsteuer neu festzusetzen.\n\n"
            f"Mit freundlichen Grüßen"
        )
        draft = Draft(
            matter_id=matter.id,
            message_id=messages[-1].id,
            content=draft_content,
            version=1,
            status="draft",
        )
        db.add(draft)
        db.flush()

        db.add(
            AuditEvent(
                entity_type="Matter",
                entity_id=matter.id,
                event_type="synthetic_complex_case_generated",
                actor="system",
                details=(
                    f"Vollstaendiger synthetischer Fall erzeugt: "
                    f"Erbschaftsteuer-Einspruch ({len(documents)} Dokumente)"
                ),
            )
        )
        db.commit()
        db.refresh(client)
        db.refresh(matter)
        db.refresh(draft)

        return SyntheticComplexCase(
            client=client,
            matter=matter,
            documents=documents,
            deadline=deadline,
            draft=draft,
            scenario_key="erbschaftsteuer_einspruch",
        )

    #: Wie viele der zugeordneten Faelle einen Antwortentwurf tragen und in
    #: welchem Freigabezustand. Bewusst deterministisch ueber den Index
    #: (nicht zufaellig), damit derselbe Seed denselben Datenbestand liefert
    #: und die Demo-Kanzlei reproduzierbar bleibt.
    #:
    #: Die Verteilung bildet den realen Arbeitsstand einer Kanzlei ab: die
    #: meisten Akten haben noch keinen Entwurf, einige einen offenen, einer
    #: liegt beim Anwalt zur Pruefung, einer ist freigegeben und wartet im
    #: Postausgang, einer wurde bereits (ausserhalb des Systems) versendet.
    #:
    #: WICHTIG - Kollision mit der Zuordnungslogik (eigener Fehler im ersten
    #: Anlauf, beim Nachzaehlen der erzeugten Daten aufgefallen):
    #: `generate_many` macht jeden dritten Fall zu noch NICHT zugeordneter
    #: Post (`i % 3 == 2`). Diese Faelle bekommen bewusst keinen Entwurf -
    #: das sind modulo 6 genau die Indizes 2 und 5. Der erste Entwurf dieses
    #: Plans belegte ausgerechnet 2 und 5 mit "approved_pending" und
    #: "draft"; "approved_pending" konnte dadurch NIE entstehen, und der
    #: Postausgang enthielt ausschliesslich bereits versendete Eintraege -
    #: ausgerechnet der Zustand "wartet auf Versand", auf den es fuer die
    #: anwaltliche Freigabe ankommt, fehlte vollstaendig.
    #: Deshalb hier nur Indizes aus {0, 1, 3, 4}.
    _DRAFT_PLAN: tuple[tuple[int, str], ...] = (
        (0, "approved_sent"),
        (1, "draft"),
        (3, "approved_pending"),
        (4, "legal_review"),
    )

    def _maybe_create_draft_chain(
        self,
        db: Session,
        *,
        matter: Matter,
        message: Message,
        scenario: CaseScenario,
        format_kwargs: dict[str, object],
        unassigned: bool,
        case_index: int,
    ) -> tuple[list[Draft], OutboxEntry | None]:
        """Legt fuer einen Teil der Faelle einen Antwortentwurf an - bei
        einem davon zusaetzlich eine zweite Version und einen
        Postausgang-Eintrag.

        WARUM (15.09.): der Generator erzeugte Mandanten, Akten, Dokumente
        und Fristen, aber KEINE Entwuerfe. Real gemessen: in der
        installierten Instanz hingen ALLE 41 Entwuerfe an
        "Schnellentwurf"-Akten aus dem Chat, KEINER an einer echten Akte,
        und der Postausgang war vollstaendig leer. Die Gold-Workflow-
        Stationen am Ende (Entwurf -> anwaltliche Freigabe -> Postausgang)
        liessen sich mit Demo-Daten also weder vorfuehren noch ehrlich
        End-to-End pruefen.

        Ein noch NICHT zugeordneter Fall (`unassigned`) bekommt bewusst
        keinen Entwurf: die Post ist dort noch gar nicht triagiert - ein
        fertiger Antwortentwurf zu einer Nachricht ohne Akte waere ein
        Zustand, den der echte Workflow nie erzeugt.

        KEIN KI-Aufruf (feste Szenario-Vorlage, siehe
        `CaseScenario.draft_body_template`) - deterministisch und
        kostenfrei, wie der uebrige Generator."""
        if unassigned or not scenario.draft_body_template:
            return [], None

        plan = dict(self._DRAFT_PLAN).get(case_index % 6)
        if plan is None:
            return [], None

        content = scenario.draft_body_template.format(**format_kwargs)
        first = Draft(
            matter_id=matter.id,
            message_id=message.id,
            content=content,
            version=1,
            status="draft",
        )
        db.add(first)
        db.flush()
        drafts = [first]

        if plan == "draft":
            return drafts, None

        # Zweite Version: so sieht eine Akte aus, in der der Anwalt bereits
        # einmal nachgeschaerft hat. Die Versionskette (previous_version_id)
        # ist das, was die Entwurfsansicht als Zeitleiste zeigt.
        second = Draft(
            matter_id=matter.id,
            message_id=message.id,
            content=content + "\n\nErgaenzung nach anwaltlicher Durchsicht.",
            version=2,
            status="legal_review" if plan == "legal_review" else "approved",
            previous_version_id=first.id,
        )
        db.add(second)
        db.flush()
        drafts.append(second)

        if plan == "legal_review":
            return drafts, None

        entry = OutboxEntry(
            matter_id=matter.id,
            draft_id=second.id,
            status="sent" if plan == "approved_sent" else "pending",
        )
        if plan == "approved_sent":
            entry.sent_at = datetime.now(timezone.utc) - timedelta(
                days=self._random.randint(1, 4)
            )
            entry.sent_by = f"anwalt@{_SYNTHETIC_EMAIL_DOMAIN}"
        db.add(entry)
        db.flush()
        return drafts, entry

    def generate_many(self, db: Session, count: int) -> list[SyntheticCase]:
        """Erzeugt `count` Fälle, zyklisch über alle Szenarien verteilt
        (nicht rein zufällig) - garantiert, dass bei count >= Anzahl
        Szenarien JEDES Szenario mindestens einmal vorkommt (wichtig für
        einen aussagekräftigen Benchmark in Prompt 30)."""
        cases: list[SyntheticCase] = []
        for i in range(count):
            scenario_key = SCENARIOS[i % len(SCENARIOS)].key
            # Jeder dritte Fall kommt als NOCH NICHT zugeordnete Nachricht
            # herein (deterministisch, nicht zufaellig - der Datenbestand
            # bleibt reproduzierbar). So enthaelt die Demo-Kanzlei beide
            # realen Zustaende: bereits abgelegte Vorgaenge UND frische
            # Post, die noch triagiert werden muss (Gold-Workflow-Start).
            cases.append(
                self.generate_case(
                    db,
                    scenario_key=scenario_key,
                    unassigned=(i % 3 == 2),
                    # generate_many() ist der demobestand-fuellende Pfad -
                    # nur hier soll die realistische Durchmischung mit
                    # Entwuerfen/Postausgang entstehen (siehe
                    # generate_case()-Docstring fuer den Grund, warum das
                    # NICHT der Default der Einzelfall-API ist).
                    include_draft=True,
                )
            )
        return cases

    def _pick_scenario(self, scenario_key: str | None) -> CaseScenario:
        if scenario_key is None:
            return self._random.choice(SCENARIOS)
        for scenario in SCENARIOS:
            if scenario.key == scenario_key:
                return scenario
        raise ValueError(
            f"Unbekanntes Szenario '{scenario_key}' - verfügbar: "
            f"{[s.key for s in SCENARIOS]}"
        )

    def _generate_unique_reference_number(
        self, jahr: int, db: Session, *, practice_area: str
    ) -> str:
        """`Matter.reference_number` trägt eine UNIQUE-Constraint - bei
        wiederholter Generatornutzung gegen dieselbe (Demo-)Datenbank
        könnte der Zufallsraum gelegentlich kollidieren. Prüft daher
        aktiv gegen die DB und generiert bei einem Treffer neu, statt
        einen harten IntegrityError zu riskieren.

        Das Sachgebiets-Kürzel folgt dem Rechtsgebiet des Falls und wird
        NICHT mehr gewürfelt (siehe
        `_AKTENZEICHEN_SUFFIX_JE_RECHTSGEBIET`). Nur die Laufnummer ist
        zufällig - sie ist der Teil, der in einer echten Kanzlei auch
        keine Bedeutung trägt."""
        suffix = _AKTENZEICHEN_SUFFIX_JE_RECHTSGEBIET.get(
            practice_area, _AKTENZEICHEN_SUFFIX_SONSTIGE
        )
        for _ in range(50):
            laufnummer = self._random.randint(1, 999)
            candidate = f"{jahr}/{laufnummer:04d}-{suffix}"
            exists = (
                db.query(Matter).filter_by(reference_number=candidate).first()
                is not None
            )
            if not exists:
                return candidate
        # Praktisch nie erreicht (Zufallsraum >> realistische Fallzahlen),
        # aber ein garantiert eindeutiger Fallback statt einer Endlosschleife.
        # Das Sachgebiets-Kürzel bleibt auch hier korrekt - ein eindeutiges,
        # aber fachlich falsches Aktenzeichen waere kein Fortschritt.
        return f"{jahr}/{self._random.randint(1000, 999999)}-{suffix}"

    def generate_shared_knowledge_base(
        self, db: Session
    ) -> tuple[list[Source], list[KnowledgeItem]]:
        """Erzeugt eine kleine, freigegebene Rechtsquellen-/Kanzlei-
        Wissensbasis, die für ALLE Fälle gemeinsam genutzt wird
        (realistisch: eine Kanzlei-Wissensbasis ist nicht pro Akte
        getrennt). Nutzt bewusst ECHTE, öffentlich bekannte
        Gesetzesnummern (§ 355 AO usw.) - das sind allgemein bekannte
        Rechtsnormen, keine Mandantendaten, und machen die Fälle
        realistisch nutzbar mit der bestehenden Recherche-/Zitierlogik.

        IDEMPOTENT seit 14.09. (Nachtrag §9): ein wiederholter Aufruf legt
        NICHTS doppelt an, sondern liefert die bereits vorhandenen Eintraege
        zurueck. Vorher warnte das CLI-Skript ausdruecklich davor, diese
        Funktion mehrfach aufzurufen ("fuehrt bei mehrfachem Aufruf zu
        doppelten Eintraegen") - genau die Art von Datenmuell, die der
        Nachtrag ausschliesst."""
        existing_sources = (
            db.query(Source)
            .filter(Source.reference.in_(["§ 355 AO", "§ 196 AO", "§ 4 KSchG"]))
            .all()
        )
        existing_items = (
            db.query(KnowledgeItem)
            .filter(KnowledgeItem.title.like("Standard-Textbaustein:%"))
            .all()
        )
        if existing_sources or existing_items:
            return existing_sources, existing_items

        sources = [
            Source(
                title="Einspruch gegen Steuerbescheide – Frist",
                source_type="Gesetz",
                reference="§ 355 AO",
                approval_level="freigegeben",
                notes="Einspruchsfrist: ein Monat nach Bekanntgabe des Verwaltungsakts.",
            ),
            Source(
                title="Prüfungsanordnung – Voraussetzungen",
                source_type="Gesetz",
                reference="§ 196 AO",
                approval_level="freigegeben",
                notes="Regelt die formellen Voraussetzungen einer Betriebsprüfung.",
            ),
            Source(
                title="Kündigungsschutzklage – Klagefrist",
                source_type="Gesetz",
                reference="§ 4 KSchG",
                approval_level="freigegeben",
                notes="Klage muss innerhalb von drei Wochen nach Zugang erhoben werden.",
            ),
        ]
        db.add_all(sources)

        knowledge_items = [
            KnowledgeItem(
                title="Standard-Textbaustein: Einspruchseinlegung",
                content=(
                    "Namens und im Auftrag unseres Mandanten legen wir hiermit form- "
                    "und fristgerecht Einspruch gegen den Bescheid vom [DATUM] ein."
                ),
                category="Textbaustein",
                practice_area="Einkommensteuer",
                approval_status="approved",
            ),
            KnowledgeItem(
                title="Standard-Textbaustein: Fristverlängerung beantragen",
                content=(
                    "Wir bitten um Verlängerung der Frist zur Stellungnahme um vier "
                    "Wochen, da uns die vollständigen Unterlagen noch nicht vorliegen."
                ),
                category="Textbaustein",
                practice_area=None,
                approval_status="approved",
            ),
        ]
        db.add_all(knowledge_items)
        db.commit()
        for source in sources:
            db.refresh(source)
        for item in knowledge_items:
            db.refresh(item)
        return sources, knowledge_items

    def generate_shared_document_templates(self, db: Session) -> list["DocumentTemplate"]:
        """Erzeugt eine kleine, gemeinsam genutzte Bibliothek an
        Kanzlei-Mustertexten fuer den deterministischen (KI-freien)
        Dokumentengenerator (app/document_generator/).

        ECHTER FUND (15.09., beim UI-Sweep gegen die echte Instanz): sowohl
        `document_templates` als auch `generated_documents` waren in der
        installierten Datenbank VOLLSTAENDIG LEER (0 Zeilen). Die Seite
        selbst rendert ehrlich (kein Crash, klare Beschreibung), aber ohne
        mindestens eine Vorlage laesst sich das Feature weder vorfuehren
        noch End-to-End testen - derselbe Fehlerklasse wie der zuvor
        behobene Entwurf-/Postausgang-Gap.

        Nutzt AUSSCHLIESSLICH die einfachen, aus Matter/Client/FirmProfile
        auflösbaren Platzhalter (SUPPORTED_PLACEHOLDERS, siehe
        app/document_generator/placeholders.py) - bewusst KEINE
        `[Paragraf:GESETZ:§...]`-Platzhalter, damit diese Vorlagen
        unabhaengig davon funktionieren, welche Gesetze gerade importiert
        sind.

        IDEMPOTENT nach demselben Muster wie `generate_shared_knowledge_base`:
        ein wiederholter Aufruf legt nichts doppelt an."""
        from app.models import DocumentTemplate

        existing = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.name.like("Mustertext:%"))
            .all()
        )
        if existing:
            return existing

        templates = [
            DocumentTemplate(
                name="Mustertext: Fristverlängerung beantragen",
                category="Fristsachen",
                description=(
                    "Kurzes Schreiben zur Beantragung einer Fristverlängerung - "
                    "füllt Mandant, Aktenzeichen und Datum automatisch aus der Akte."
                ),
                content=(
                    "Sehr geehrte Damen und Herren,\n\n"
                    "in der Angelegenheit [Mandantenname], Aktenzeichen [Aktenzeichen], "
                    "bitten wir um Verlängerung der laufenden Frist um zwei Wochen, da "
                    "uns die vollständigen Unterlagen noch nicht vorliegen.\n\n"
                    "Mit freundlichen Grüßen\n[Kanzleiname]\n[Bearbeiter]\n[Datum]"
                ),
                version=1,
                created_by_actor="system",
            ),
            DocumentTemplate(
                name="Mustertext: Mandatsbestätigung",
                category="Allgemein",
                description=(
                    "Bestätigt die Übernahme eines Mandats gegenüber einer Behörde "
                    "oder einem Verfahrensbeteiligten."
                ),
                content=(
                    "Sehr geehrte Damen und Herren,\n\n"
                    "wir zeigen an, dass wir [Mandantenname] in der Angelegenheit "
                    "[Aktentitel] (Aktenzeichen [Aktenzeichen], Rechtsgebiet "
                    "[Rechtsgebiet]) rechtlich vertreten.\n\n"
                    "Mit freundlichen Grüßen\n[Kanzleiname]\n[Bearbeiter]\n[Datum]"
                ),
                version=1,
                created_by_actor="system",
            ),
        ]
        db.add_all(templates)
        db.commit()
        for template in templates:
            db.refresh(template)
        return templates
