"""LocalAIProvider – Protocol + regelbasierte Implementierung.

`RuleBasedLocalAIProvider` erfindet KEINE neue Analyselogik - sie bündelt
ausschließlich bereits bestehende, getestete Bausteine (Document.
extracted_text aus Prompt 06, Deadline aus Prompt 10,
DocumentSearchService.search_knowledge_base aus Prompt 11/12) zu EINEM
Ergebnis, das direkt als Eingabe für `ClaudePrivacyGateway.prepare_request`
(Schritt 3) dient - siehe Pipeline-Diagramm in der Architekturvorgabe:
"Local AI -> ... -> Draft Preparation -> Privacy Gateway".

WICHTIG: Jede Datenbankabfrage ist strikt nach `matter_id` gefiltert -
exakt dasselbe Isolationsmuster wie in `search_within_matter` (Prompt 11)
und `PromptContextBuilder` (Prompt 16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Deadline, Document, Matter, Party
from app.search.service import DocumentSearchService

#: ECHTER FUND (07.10., Owner-Direktive "INSTALLER + GIT + CLOUD-E2E-CHAT-
#: QUALITY", per echtem Cloud-E2E-Test mit dem real konfigurierten
#: ANTHROPIC_API_KEY reproduziert - siehe dortigen Abschlussbericht fuer die
#: volle Herleitung): eine zweite Chat-Nachricht in DERSELBEN automatisch
#: angelegten "Schnellentwurf"-Akte (siehe app/drafting/quick_matter.py::
#: create_quick_matter, matterlose allgemeine Chat-Frage) wurde faelschlich
#: als Datenschutzverstoss blockiert ("original_value_leaked"). Root Cause:
#: `_build_sachverhalt` unten speiste bisher IMMER den woertlichen
#: Akten-Titel ("Akte: Schnellentwurf 2026-10-07") in Presidio ein - bei
#: einer ECHTEN Akte ist das richtig (der Titel kann reale Mandantendaten
#: enthalten), aber der AUTOMATISCH GENERIERTE Platzhaltertitel ist reiner
#: Systemtext, NIE echte Mandantendaten. Presidios deutsches NER-Modell
#: erkannte "Schnellentwurf" faelschlich als DATUM-Entitaet, dieser Wert
#: wurde pseudonymisiert - als Claude in einer FOLGEFRAGE erneut korrekt
#: ueber dieselbe Rechtsnorm antwortete, enthielt die Antwort denselben
#: (voellig unverdaechtigen) Text erneut im Klartext, was die
#: Leck-Pruefung (`check_response_placeholder_integrity`) als "Original-
#: wert wieder aufgetaucht" wertete und die Antwort blockierte - obwohl
#: nie echte Mandantendaten im Spiel waren. Betrifft AUSSCHLIESSLICH Akten
#: mit dem gemeinsamen Sammel-Mandanten `PLACEHOLDER_CLIENT_NAME` (siehe
#: app/drafting/quick_matter.py::_resolve_placeholder_client) - eine ECHTE
#: Akte mit echtem Mandanten ist davon nicht betroffen, ihr Titel wird
#: weiterhin unveraendert (und damit korrekt geschuetzt) uebergeben."""
_PLACEHOLDER_MATTER_SACHVERHALT = "Akte: (kein spezifischer Fall zugeordnet)"

#: Oeffentlicher Name derselben Konstante (08.10.): app/ai_providers/
#: claude_writing_provider.py erkennt daran einen Chat OHNE jeden Akten-/
#: Mandanten-/Dokumentkontext und stellt ihn dem Modell als "allgemeine
#: Frage" dar statt als leeren "Sachverhalt" (siehe dortigen ECHTER FUND).
NO_CASE_CONTEXT_SACHVERHALT = _PLACEHOLDER_MATTER_SACHVERHALT

#: ECHTER FUND, MIT REALEN PRODUKTIONSDATEN GEMESSEN (05.10., Owner-
#: Direktive "P1-BUGFIX: Schriftsatz unvollständig..."): die vorherige
#: Grenze von 500 Zeichen (selbst nach dem Fix der vorherigen Runde, die
#: urspruenglich NUR den `build_snippet`-Fallback-Fehler auf faktisch 160
#: Zeichen behob) reicht fuer reale Dokumente NICHT aus - an einer echten
#: Produktions-Akte reproduziert ("Lexono_Testdokument_Schreiben_
#: erstellen.pdf", 2638 Zeichen): die eigentliche Aufgabenstellung
#: ("Bitte analysiere das Dokument und erstelle einen sachlichen
#: Entwurf...") stand GANZ AM ENDE des Dokuments, weit hinter Zeichen
#: 500 - der Sachverhalt brach bereits nach dem ersten Absatz ab, bevor
#: Claude die eigentliche Anweisung ueberhaupt sah. Verteilung ueber 100
#: reale, bereits extrahierte Dokumente in der Produktions-DB gemessen
#: (nicht geraten, siehe CLAUDE.md "keine pauschale Erhoehung von Limits
#: ohne Messung"): Median 257 Zeichen, aber P75/P90/P95 bei 2607 Zeichen,
#: Maximum 4594, KEIN einziges Dokument ueber 5000 Zeichen. 5000 Zeichen
#: erfasst damit praktisch jedes real beobachtete Dokument vollstaendig,
#: ohne eine willkuerlich grosse, ungemessene Zahl zu waehlen.
_MAX_DOCUMENT_EXCERPT_CHARS = 5000
# Sicherheitsergänzung (Prompt 28): ohne Obergrenze könnte eine Akte mit
# sehr vielen (z. B. absichtlich zugeschickten) kleinen Anhängen den
# Sachverhalt und damit die Kosten/Tokenzahl jeder Claude-Anfrage
# unbegrenzt aufblähen. Begrenzung auf die neuesten N Dokumente -
# konsistent mit der bereits bestehenden Pro-Dokument-Zeichenbegrenzung.
# Worst Case bei der neuen Grenze: 30 Dokumente x 5000 Zeichen = 150.000
# Zeichen (~37.500 Tokens) - weiterhin deutlich innerhalb des
# Kontextfensters des Modells, auch wenn dieser Extremfall (30
# gleichzeitig volle Dokumente an einer Akte) in den gemessenen
# Produktionsdaten nicht vorkommt.
_MAX_DOCUMENTS_IN_SACHVERHALT = 30

# Grobe, tolerante Rollen-Zuordnung fuer Party.role (Freitext, Prompt 04).
_OPPONENT_ROLE_KEYWORDS = ("gegner", "gegenseite", "beklagte", "beklagter")
_COURT_ROLE_KEYWORDS = ("gericht", "finanzamt", "behörde", "behoerde")
_LAWYER_ROLE_KEYWORDS = ("anwalt", "anwältin", "rechtsanwalt", "prozessbevollmächtigt")


def _document_excerpt(extracted_text: str) -> str:
    """Baut den tatsaechlich in den Sachverhalt eingehenden Dokument-
    Ausschnitt - bis zu `_MAX_DOCUMENT_EXCERPT_CHARS` Zeichen, mit
    Zeilenumbruch->Doppel-Leerzeichen-Normalisierung (identisches Prinzip
    wie app/search/utils.py::build_snippet, siehe dortiger Docstring fuer
    die Begruendung: Absatzgrenzen muessen fuer
    `_find_possible_unrecognized_names` erkennbar bleiben).

    ECHTER FUND, SYNTHETISCH REPRODUZIERT (05.10., Owner-Direktive
    "Vollstaendiger UX- und Workflow-Audit"): diese Stelle rief bisher
    `build_snippet(text[:500], "")` auf - mit LEERER Suchanfrage faellt
    `build_snippet` aber auf seinen fuer SUCHTREFFER-Vorschauen gedachten
    Fallback zurueck (`_SNIPPET_FALLBACK_LENGTH = 160`), NICHT auf die
    hier eigentlich gewollten 500 Zeichen - jedes Dokument im Sachverhalt
    wurde dadurch faktisch auf die ERSTEN 160 ZEICHEN verkuerzt (oft nur
    Briefkopf/Anrede, VOR jedem inhaltlichen Absatz), und zwar per blindem
    Zeichen-Slice OHNE Wort-/Satzgrenze - live reproduziert: ein
    Bescheiddatum wurde exakt mitten im Jahr abgeschnitten ("01.09.20"
    statt "01.09.2026"), waehrend die unabhaengige Fristenerkennung
    (`_build_argumentationspunkte`) dasselbe Datum bereits vollstaendig
    lieferte - Claude erhielt dadurch zwei widerspruechliche Datums-
    angaben fuer denselben Sachverhalt und markierte dies (korrekt!) als
    klaerungsbeduerftig, der eigentliche Dokumentinhalt (Betrag,
    Begruendung) tauchte im generierten Entwurf ueberhaupt nicht auf, weil
    er erst nach Zeichen 160 im Originaldokument stand. `build_snippet`
    selbst bleibt UNVERAENDERT (wird an anderer Stelle korrekt fuer echte
    Suchtreffer-Vorschauen mit einer echten Suchanfrage verwendet, siehe
    app/search/service.py/app/promptlayer/builder.py) - dies ist eine
    eigenstaendige, lokale Hilfsfunktion statt einer Wiederverwendung
    einer fuer einen anderen Zweck bestimmten Funktion."""
    normalized = extracted_text.replace("\n", "  ").strip()
    truncated = normalized[:_MAX_DOCUMENT_EXCERPT_CHARS]
    suffix = "…" if len(normalized) > _MAX_DOCUMENT_EXCERPT_CHARS else ""
    return f"{truncated}{suffix}"


@dataclass
class DraftPreparationResult:
    sachverhalt: str
    argumentationspunkte: list[str] = field(default_factory=list)
    quellenverweise: list[str] = field(default_factory=list)
    known_entities: dict[str, list[str]] = field(default_factory=dict)
    # P0 Performance-Follow-up (13.09.): objektives, bereits aus der
    # ohnehin ausgefuehrten Dokumentenabfrage abgeleitetes Signal - OB
    # tatsaechlich Aktendokumente in den Sachverhalt eingeflossen sind.
    # Wird von DraftingService genutzt, um zu entscheiden, ob die
    # verpflichtenden lokalen KI-Schritte (§65) fuer eine Anfrage
    # tatsaechlich sensiblen Dokument-/Aktenkontext verarbeiten - siehe
    # dortigen Kommentar fuer die volle Begruendung.
    has_document_context: bool = False


class LocalAIProvider(Protocol):
    def prepare_draft_context(
        self, matter_id: str, db: Session
    ) -> DraftPreparationResult: ...


class RuleBasedLocalAIProvider:
    def __init__(self, search_service: DocumentSearchService | None = None) -> None:
        self.search_service = search_service

    def prepare_draft_context(
        self, matter_id: str, db: Session
    ) -> DraftPreparationResult:
        if not matter_id:
            raise ValueError(
                "matter_id ist erforderlich - Kontextvorbereitung ohne "
                "Aktenbezug ist nicht erlaubt"
            )

        matter = db.query(Matter).filter_by(id=matter_id).first()
        if matter is None:
            raise ValueError(f"Matter {matter_id} nicht gefunden")

        sachverhalt, has_document_context = self._build_sachverhalt(matter_id, matter, db)
        argumentationspunkte = self._build_argumentationspunkte(matter_id, db)
        quellenverweise = self._build_quellenverweise(matter, db)
        known_entities = self._build_known_entities(matter_id, matter, db)

        return DraftPreparationResult(
            sachverhalt=sachverhalt,
            argumentationspunkte=argumentationspunkte,
            quellenverweise=quellenverweise,
            known_entities=known_entities,
            has_document_context=has_document_context,
        )

    def _build_sachverhalt(
        self, matter_id: str, matter: Matter, db: Session
    ) -> tuple[str, bool]:
        # Platzhalter-Akte (siehe Modul-Kommentar oben zu
        # `_PLACEHOLDER_MATTER_SACHVERHALT`): der automatisch generierte
        # Titel ("Schnellentwurf <Datum>") ist reiner Systemtext, keine
        # echten Mandantendaten - NICHT unveraendert durch Presidio
        # schicken. Eine ECHTE Akte (jeder andere Mandant) bleibt
        # unveraendert: ihr Titel kann reale Daten enthalten und muss
        # weiterhin wie bisher geschuetzt werden.
        # Lokaler Import (nicht auf Modulebene): app.drafting.quick_matter
        # haengt transitiv ueber app/drafting/__init__.py von
        # app.drafting.service ab, welches seinerseits dieses Modul
        # importiert - ein Import auf Modulebene erzeugt daher einen
        # Zirkelimport (live reproduziert: "cannot import name
        # 'LocalAIProvider' from partially initialized module ...").
        from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME

        if matter.client and matter.client.name == PLACEHOLDER_CLIENT_NAME:
            parts = [_PLACEHOLDER_MATTER_SACHVERHALT]
        else:
            parts = [f"Akte: {matter.title}"]
        documents = (
            db.query(Document)
            .filter(Document.matter_id == matter_id)
            .filter(Document.extracted_text.isnot(None))
            .filter(Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
            .limit(_MAX_DOCUMENTS_IN_SACHVERHALT)
            .all()
        )
        for document in documents:
            excerpt = _document_excerpt(document.extracted_text)
            type_label = document.classified_type or "unklassifiziert"
            parts.append(f"[{type_label}] {excerpt}")
        return "\n".join(parts), bool(documents)

    def _build_argumentationspunkte(self, matter_id: str, db: Session) -> list[str]:
        deadlines = db.query(Deadline).filter(Deadline.matter_id == matter_id).all()
        return [
            f"Mögliche Frist ({deadline.review_status}): {deadline.source_text}"
            for deadline in deadlines
        ]

    def _build_quellenverweise(self, matter: Matter, db: Session) -> list[str]:
        if self.search_service is None:
            return []
        query = matter.practice_area or matter.title
        results = self.search_service.search_knowledge_base(query, db)
        return [result.snippet for result in results]

    def _build_known_entities(
        self, matter_id: str, matter: Matter, db: Session
    ) -> dict[str, list[str]]:
        known: dict[str, list[str]] = {"mandant": [], "gegner": [], "anwalt": [], "gericht": []}
        if matter.client and matter.client.name:
            known["mandant"].append(matter.client.name)

        parties = db.query(Party).filter(Party.matter_id == matter_id).all()
        for party in parties:
            role = (party.role or "").lower()
            if any(keyword in role for keyword in _OPPONENT_ROLE_KEYWORDS):
                known["gegner"].append(party.name)
            elif any(keyword in role for keyword in _COURT_ROLE_KEYWORDS):
                known["gericht"].append(party.name)
            elif any(keyword in role for keyword in _LAWYER_ROLE_KEYWORDS):
                known["anwalt"].append(party.name)
            else:
                known.setdefault("beteiligter", []).append(party.name)

        # ECHTER FUND (14.09., Overnight-Direktive §8, realer Regressionsfall
        # "Frau Müller"): bisher wurde ausschliesslich der VOLLSTAENDIGE Name
        # ("Anna Müller") als bekannte Entitaet indiziert. `detect_known_
        # entities` (app/privacy/detectors.py) sucht aber nur EXAKT nach
        # diesem vollstaendigen String - ein im echten Kanzleitext sehr
        # haeufiger blosser Nachname-Verweis ohne Vornamen ("Frau Müller",
        # "die Mandantin Müller", oder auch komplett ohne Anrede/Titel
        # einfach "Müller") wurde dadurch NICHT erkannt, obwohl der Nachname
        # Teil einer bekannten, der Akte zugeordneten Person ist. Ergaenzung:
        # zusaetzlich zum vollen Namen wird auch das LETZTE Wort (typischer
        # Nachname bei "Vorname Nachname"-Schema) separat als bekannte
        # Entitaet derselben Kategorie indiziert - deckt damit auch den
        # Fall OHNE jede Anrede ab (anders als die Ergaenzung in
        # app/privacy/security_check.py::_find_possible_unrecognized_names,
        # die ein Anrede-/Rollenwort direkt vor dem Nachnamen voraussetzt).
        # Bewusst NUR bei mehrteiligen Namen (ein einzelnes Wort als Name
        # waere bereits identisch zum vollen Namen, keine Ergaenzung noetig).
        for category, names in list(known.items()):
            surnames = []
            for name in names:
                parts = name.strip().split()
                # ECHTER FUND (14.09., beim Haerten dieser Ergaenzung): eine
                # Mindestlaenge ist zwingend - ohne sie wuerde z. B. ein
                # (in echten Namen zwar unueblicher, aber testweise/real
                # theoretisch moeglicher) einzelner Buchstabe als "Nachname"
                # per Substring-Regex (siehe detect_known_entities) JEDES
                # Vorkommen dieses Buchstabens IRGENDWO im Text treffen und
                # damit die komplette Pseudonymisierung/Wiederaufteilung
                # unbrauchbar machen (real reproduziert:
                # test_context_never_contains_data_from_other_matter mit
                # Mandant "Mandant A" -> "A" als Nachname haette jedes "a"
                # im Fliesstext getroffen). Echte deutsche Nachnamen sind
                # praktisch nie kuerzer als 3 Zeichen.
                if len(parts) >= 2 and len(parts[-1]) >= 3 and parts[-1] not in names:
                    surnames.append(parts[-1])
            known[category] = names + [s for s in surnames if s not in names]

        return {category: names for category, names in known.items() if names}
