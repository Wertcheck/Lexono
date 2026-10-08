"""DraftingService – siehe __init__.py für die Gesamteinordnung.

Ablauf:
1. Aktenkontext lokal aufbereiten (`LocalAIProvider`).
2. Zugelassene Rechtsquellen recherchieren (`LegalResearchService`,
   Prompt 15) - liefert vollständige Belege, markiert unzureichend
   belegte Anfragen als offenen Prüfpunkt.
3. Freigegebenes Kanzleiwissen suchen (`DocumentSearchService.
   search_knowledge_base`), mit Rückverfolgung zu den tatsächlichen
   `KnowledgeItem`-Zeilen.
4. Über den Privacy Gateway (Schritt 1-3) pseudonymisieren, prüfen, ggf.
   blockieren.
5. Optional (§65, nur wenn ein `local_llm_provider` injiziert wurde):
   die bereits pseudonymisierte Payload zusätzlich lokal über Ollama
   vorverarbeiten (`LocalLLMProvider.process`, siehe app/ai_providers/
   local_llm_provider.py) - PFLICHT-Schritt, sobald aktiviert: schlägt er
   fehl (`LocalLLMUnavailableError`), wird die Anfrage kontrolliert
   blockiert und Claude NIEMALS aufgerufen (Datenschutz vor
   Verfügbarkeit). Ohne injizierten Provider (Standard - siehe
   app/web/service_factory.py, `settings.local_ai_enabled=False`)
   unverändertes Verhalten wie vor §65.

   RISIKOBASIERTE AUSNAHME (P0 Performance-Follow-up, 13.09., PRÄZISIERT
   15.09. als CHAT-04 - real evidenzbasiert entschieden, siehe
   DECISIONS.md fuer die volle Herleitung beider Schritte): Sinn dieses
   Schritts ist laut LEXONO_MASTER_PRODUCT.md §4, dass "the actual
   sensitive document/context reasoning" lokal bleibt, BEVOR irgendetwas
   an Claude geht. Fuer eine einfache Chat-Nachricht OHNE Aktendokument,
   bei der jede von Presidio erkannte Entitaet bereits eine der Akte
   strukturell bekannte Person ist (Mandant/Gegner/Anwalt/Gericht - siehe
   `RuleBasedLocalAIProvider._build_known_entities`), existiert kein
   "sensibler Dokument-/Aktenkontext", den dieser Schritt schuetzen
   koennte: der Sachverhalt ist ohne Dokument exakt `"Akte: {Titel}"`, und
   ein bereits bekannter Name darin ist keine neue, ungeschuetzte
   Information. ECHTER FUND (15.09.): die urspruengliche Bedingung
   "mappings leer" griff in der Praxis fast nie, weil ein realer
   Aktentitel fast immer den Mandantennamen enthaelt (z. B. "Muster, Anna
   offen 1") - real gemessen 10,8 s (warm) / 48,1 s (cold) allein fuer
   die dadurch erzwungene Vorabanalyse einer blossen Begruessung. Taucht
   dagegen IRGENDEINE Entitaet auf, die NICHT zu den bekannten Namen
   dieser Akte gehoert (neuer Name, Telefonnummer, IBAN - alles, was neu
   in die Chatnachricht getippt worden sein koennte), bleibt die volle
   Pipeline Pflicht. Presidio/Pseudonymisierung UND die DETERMINISTISCHE
   Platzhalter-Integritaetspruefung (Stufe 1, siehe
   response_validation.py) bleiben in JEDEM Fall PFLICHT und
   unveraendert - nur die LLM-gestuetzten Schritte (lokale Vorabanalyse +
   Stufe 2 der Antwortvalidierung) werden uebersprungen, NIE die
   Pseudonymisierung selbst. Sobald ein Aktendokument beteiligt ist ODER
   eine unbekannte Entitaet gefunden wird ODER es sich um einen
   expliziten Schreibauftrag handelt, bleibt die VOLLE Pipeline
   unveraendert PFLICHT - siehe `_should_skip_llm_privacy_layers`.
6. Bei Erfolg: `ClaudeWritingProvider` aufrufen, protokollieren
   (Schritt 5), lokal rekonstruieren, als `Draft` persistieren.
7. Unsicherheiten ergänzen (z. B. unbestätigte Fristen in der Akte).

KEINE Versand-Fähigkeit: dieser Service hat keine Methode, die eine
E-Mail verschickt oder einen Versand auslöst - das bleibt dem noch nicht
gebauten Postausgang (Prompt 25) und der anwaltlichen Freigabe
vorbehalten.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ai_providers.claude_writing_provider import ClaudeWritingProvider
from app.ai_providers.local_ai_provider import LocalAIProvider
from app.ai_providers.local_llm_provider import LocalLLMProvider, LocalLLMUnavailableError
from app.cost_control import CostControlService
from app.drafting.markdown_to_draft_html import render_ai_markdown_to_draft_html
from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME, create_quick_matter
from app.drafting.response_validation import validate_claude_response
from app.drafting.schema import DraftingResult, KnowledgeItemReference, SourceReference
from app.drafting.versioning import create_new_draft_version
from app.models import Deadline, Draft, DraftKnowledgeItemLink, DraftSourceLink, KnowledgeItem, Matter
from app.observability.perf_trace import PerfTrace
from app.privacy.api_logger import ApiCallLogger, categorize_block_reasons
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload, GatewayResult
from app.privacy.security_check import (
    check_response_placeholder_integrity,
    find_lenient_leak_exempt_placeholders,
)
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService

_LOCAL_LLM_ARGUMENTATIONSPUNKT_PREFIX = (
    "Lokale Vorabanalyse (automatisiert, Ollama, keine rechtliche Bewertung): "
)

# Muss mit app/chat/service.py::_PURPOSE_CHAT übereinstimmen (dort auch
# in ALLOWED_PURPOSES, security_check.py, allowlisted) - bewusst als
# String-Literal hier dupliziert statt importiert, um KEINE Abhängigkeit
# von app/chat auf app/drafting einzuführen (Schichtenrichtung bliebe
# sonst verkehrt: drafting ist die tiefere, generischere Schicht).
_CHAT_PURPOSE = "chat_response"

# ECHTER FUND, ROOT CAUSE FUER die purpose-gebundene Lockerung von
# `require_full_placeholder_coverage` weiter unten in
# `_finish_non_streaming_stream` (18.09., Owner-Direktive "CONTINUE
# AUTONOMOUS PRODUCT COMPLETION", Tiefen-E2E-Test Schriftsatz-Generator,
# live am echten Server reproduziert): `prepare_draft_context` (siehe
# `_prepare_and_gate` unten) baut Sachverhalt/Mappings fuer JEDEN Zweck
# IDENTISCH aus der GESAMTEN Akte (bis zu `_MAX_DOCUMENTS_IN_SACHVERHALT`
# Dokumenten). Ein fokussierter, korrekter Text zu EINEM konkreten
# Anliegen muss nicht JEDE in der Akte ueberhaupt vorkommende Person/
# jeden Ort/jedes Datum woertlich erwaehnen, nur weil es irgendwo in
# einem der Akte-Dokumente pseudonymisiert wurde.
#
# Live reproduziert bisher NUR fuer zwei Zwecke: `chat_response` (CHAT-01,
# 15.09.) und `formulate_draft` (18.09., sowohl chat-getriggert als auch
# der kanonische Schriftsatz-Generator-Weg). Die Lockerung gilt deshalb
# BEWUSST NUR fuer diese beiden Zwecke (`_RELAXED_COVERAGE_PURPOSES`
# unten) - fuer `improve_draft`/`correct_draft`/`optimize_style`/
# `improve_clarity`/`apply_house_style`/`transform_content_to_letter`/
# `review_draft` gibt es KEINE Live-Evidenz fuer denselben Fehlalarm, sie
# bleiben daher unveraendert bei voller Abdeckungspflicht (Vorgabe:
# "Keine Spekulation ueber weitere Purposes" - erst bei konkretem
# Live-Fund fuer einen dieser Zwecke waere eine Erweiterung gerechtfertigt).
#
# Die beiden TATSAECHLICH schuetzenden Pruefungen (Platzhalter-
# Manipulation/erfundene Tokens, Originalwert-Leck - siehe
# check_response_placeholder_integrity) bleiben davon UNBERUEHRT fuer
# JEDEN Zweck weiterhin zwingend aktiv - das hier ist ausschliesslich
# eine Vollstaendigkeits-/Qualitaetsheuristik, keine Sicherheitsschranke,
# und betrifft NICHT das separate, bewusst weiterhin strenge ausgehende
# Final Payload Gate (`check_payload_placeholder_integrity`).
_RELAXED_COVERAGE_PURPOSES = frozenset({_CHAT_PURPOSE, "formulate_draft"})

# P1-Fund (14.09., Performance-Benchmark Sec12-13): fuer JEDEN Aufruf mit
# Dokument-/Aktenkontext (Aktenanalyse, Schriftsatz) lief bisher 80-105+
# Sekunden lang KEIN sichtbares Feedback, weil `_finish_non_streaming` eine
# gewoehnliche, blockierende Funktion war - der bestehende risikobasierte
# Fast Path liefert echte Text-Deltas NUR fuer eine PII-/dokumentfreie
# Chat-Nachricht. Bewusst NICHT geloest durch echtes Text-Streaming des
# vollen Pfads (waere ein eigenstaendiges, groesseres Architekturthema -
# lokale Vorabanalyse UND Stufe-2-Validierung sind nicht trivial
# "streambar", ohne die Fail-Closed-Garantien zu gefaehrden), sondern durch
# genau die in OPEN_ISSUES.md selbst vorgeschlagene risikoaermere
# Zwischenloesung: Status-Ereignisse VOR jedem bereits bestehenden
# `trace.step(...)`-Block, aus einer festen, inhaltsfreien Vokabel-Liste -
# IDENTISCHES Prinzip wie `_BLOCK_CATEGORIES` (api_logger.py): niemals
# Sachverhalt/Text, nur ein bekannter Fortschritts-Code. Aendert NICHTS an
# der eigentlichen Kontrolllogik (siehe `_finish_non_streaming_stream` -
# reiner 1:1-Umbau von `return` auf `yield ... ; return`, jede
# Fail-Closed-Verzweigung bleibt exakt an derselben Stelle bestehen).
_STEP_STATUS_LABELS: dict[str, str] = {
    "local_ai_preanalysis": "Lokale Vorabanalyse läuft…",
    "local_ai_preanalysis_skipped": "Lokale Vorabanalyse läuft…",
    "claude": "Anfrage wird an Claude gesendet…",
    "validation": "Antwort wird lokal geprüft…",
    "reconstruction": "Antwort wird zusammengesetzt…",
}

#: ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
#: Pipeline", 07.10., per direktem Reproduktionsskript VOR dem Ausliefern
#: dieser Korrektur selbst gefunden): `create_quick_matter` (siehe dort)
#: legt fuer JEDE Akte ohne benannten Mandanten den gemeinsamen Sammel-
#: Mandanten "Ohne Mandantenzuordnung" an - `RuleBasedLocalAIProvider.
#: _build_known_entities` traegt dessen Namen (UND dessen automatisch
#: abgeleiteten "Nachnamen" "Mandantenzuordnung") IMMER in `known_entities
#: ["mandant"]` ein, sobald ein Dokument/eine Akte ueberhaupt existiert -
#: empirisch bestaetigt: `known_entities` ist fuer einen voellig
#: unverknuepften Chat (matter_id=None -> Quick-Matter-Autocreate) NIEMALS
#: wirklich leer, sondern IMMER mindestens
#: `{"mandant": ["Ohne Mandantenzuordnung", "Mandantenzuordnung"]}`. Ohne
#: diese Ausnahme waere die Bedingung `not known_entities` in
#: `_prepare_and_gate` (skip_general_knowledge_pseudonymization) in der Praxis
#: NIEMALS wahr gewesen - der gesamte Mechanismus haette fuer echte
#: Nutzer:innen nie ausgeloest. Die Pruefung ignoriert deshalb gezielt
#: GENAU diesen einen, strukturell bekannten Platzhalter-Namen (und seinen
#: abgeleiteten Nachnamen) - jeder ANDERE/zusaetzliche bekannte Name
#: (ein echter Mandant/Gegner/Anwalt/Gericht) gilt weiterhin uneingeschraenkt
#: als "es existiert Akte-/Mandantenkontext".
_PLACEHOLDER_ONLY_KNOWN_ENTITY_NAMES = frozenset(
    {
        PLACEHOLDER_CLIENT_NAME.strip().lower(),
        PLACEHOLDER_CLIENT_NAME.strip().split()[-1].lower(),
    }
)


def _has_only_placeholder_known_entities(known_entities: dict[str, list[str]] | None) -> bool:
    """`True`, wenn `known_entities` entweder leer ist ODER ausschliesslich
    den Sammel-Mandanten-Platzhalternamen enthaelt (siehe Konstante oben) -
    in BEIDEN Faellen existiert strukturell KEIN echter Akte-/Mandanten-
    kontext fuer diese Anfrage."""
    if not known_entities:
        return True
    for names in known_entities.values():
        for name in names:
            if name and name.strip().lower() not in _PLACEHOLDER_ONLY_KNOWN_ENTITY_NAMES:
                return False
    return True


def _should_skip_llm_privacy_layers(
    *,
    purpose: str,
    has_document_context: bool,
    mappings: list,
    known_entities: dict[str, list[str]] | None = None,
) -> bool:
    """P0 Performance-Follow-up (13.09.), PRÄZISIERT 15.09. (CHAT-04):
    entscheidet, ob die LLM-gestuetzten §65-Schritte (lokale Vorabanalyse +
    Stufe 2 der Antwortvalidierung) fuer DIESE Anfrage tatsaechlich
    sensiblen Dokument-/Aktenkontext schuetzen wuerden - siehe
    Moduldocstring Schritt 5 fuer die volle Herleitung aus
    LEXONO_MASTER_PRODUCT.md §4.

    Bewusst KONSERVATIV (alle Bedingungen muessen zutreffen, sonst bleibt
    die volle Pipeline Pflicht):
    - `purpose == "chat_response"`: kein expliziter Schreibauftrag (siehe
      app/chat/service.py::_looks_like_drafting_request) - ein
      Schriftsatz/Entwurf bleibt IMMER auf der vollen Pipeline.
    - `not has_document_context`: kein Aktendokument ist in den
      Sachverhalt eingeflossen (RuleBasedLocalAIProvider) - verlaesst
      sich NICHT allein auf Presidios Entitaetserkennung, die ein
      Dokument enthalten koennte, dessen Sensibilitaet Presidio nicht
      als benanntes PII erkennt.
    - JEDE gefundene Entitaet ist eine BEKANNTE, der Akte bereits
      strukturell zugeordnete Entitaet (Mandant/Gegner/Anwalt/Gericht,
      siehe `RuleBasedLocalAIProvider._build_known_entities`) - PRÄZISIERUNG
      der urspruenglichen Bedingung `not mappings`.

    ECHTER FUND (Chat-Intelligence-Forensik, 15.09.): `not mappings` schlug
    in der Praxis fast IMMER fehl, weil der Sachverhalt ohne Dokument
    exakt `"Akte: {matter.title}"` ist (siehe
    RuleBasedLocalAIProvider._build_sachverhalt) und ein realer Aktentitel
    ("Muster, Anna offen 1") fast immer den Mandantennamen enthaelt -
    Presidio pseudonymisiert ihn also praktisch immer, auch wenn die
    eigentliche Chatnachricht ("Hallo") nichts Sensibles enthaelt. Real
    gemessen: 10,8 s (warm) / 48,1 s (cold) allein fuer die dadurch
    erzwungene lokale Vorabanalyse einer Begruessung.

    WARUM DIE PRÄZISIERUNG SICHER IST (nicht nur schneller): der Mandanten-/
    Gegner-/Anwalts-/Gerichtsname ist bereits STRUKTURELL Teil der Akte -
    er stammt aus `Client.name`/`Party.name`, nicht aus neu getipptem
    Freitext, und wird ohnehin bereits zuverlaessig pseudonymisiert
    (`known_entities` existiert eigens dafuer, siehe der "Frau Müller"-Fix
    in `_build_known_entities`). Die ursprünglich befuerchtete Gefahr laut
    Moduldocstring ("Dokumentinhalt, den Presidio nicht als PII erkennt")
    bleibt durch `not has_document_context` VOLLSTAENDIG unveraendert
    abgedeckt - das betraf nie den Aktentitel. Taucht dagegen IRGENDEINE
    Entitaet auf, die NICHT zu den bekannten Namen dieser Akte gehoert (ein
    neuer Name, eine Telefonnummer, eine IBAN - alles, was der Anwalt neu
    in die Chatnachricht getippt haben koennte), bleibt die volle Pipeline
    Pflicht - GENAU der Fall, den dieser Schritt tatsaechlich schuetzen
    soll. Der Abgleich ist bewusst EXAKT (kein Fuzzy-Match): `known_entities`
    wird von `detect_known_entities` (app/privacy/detectors.py) per exaktem
    Teilstring-Pattern gesucht, der resultierende `original_value` ist
    deshalb IMMER exakt einer der bekannten Namen selbst - ein
    unpraeziser/verpasster Treffer faellt folglich IMMER auf die
    langsamere, volle Pipeline zurueck, nie auf den schnellen Pfad
    (sicherer Fehlerfall in beide Richtungen).

    `known_entities=None` (Aufrufer liefert es nicht) verhaelt sich bei
    nicht-leeren `mappings` bewusst konservativ (kein Skip) - ohne dieses
    Wissen laesst sich Sicherheit nicht nachweisen.

    Presidio/Pseudonymisierung und die deterministische Platzhalter-
    Integritaetspruefung (Stufe 1) sind von dieser Funktion NICHT
    betroffen - sie bleiben immer Pflicht, siehe Aufrufer."""
    if purpose != _CHAT_PURPOSE or has_document_context:
        return False
    if not mappings:
        return True
    if not known_entities:
        return False
    known_names = {
        name.strip().lower()
        for names in known_entities.values()
        for name in names
        if name and name.strip()
    }
    return all((m.original_value or "").strip().lower() in known_names for m in mappings)


@dataclass
class _PreparedRequest:
    """Interner Zwischenzustand nach dem gemeinsamen Vorbereitungs-/Gate-
    Teil von `create_draft`/`create_draft_stream` (13.09., Streaming-
    Architekturentscheidung - siehe DECISIONS.md) - EINMAL berechnet
    (Aktenauflösung, Recherche, Privacy Gateway, Kostenkontrolle), von
    BEIDEN Methoden weiterverwendet, damit `create_draft_stream` für den
    nicht-streaming-faehigen Fall NICHT denselben teuren Presidio-/
    Recherche-Durchlauf ein zweites Mal ausführen muss."""

    matter_id: str
    payload: ClaudeRequestPayload
    gateway_result: GatewayResult
    skip_llm_privacy_layers: bool
    source_list: list[SourceReference]
    knowledge_items_used: list[KnowledgeItemReference]
    open_review_points: list[str]
    message_id: str | None = None
    chat_triggered: bool = False
    lenient_leak_exempt_placeholders: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DraftStreamEvent:
    """Ein Ereignis aus `DraftingService.create_draft_stream` - eines von
    DREI Arten: ein inkrementelles Text-Delta (`kind="delta"`, bereits
    lokal rekonstruiert, NIE ein Platzhalter-Mapping), ein
    Fortschritts-Hinweis (`kind="status"`, siehe `_STEP_STATUS_LABELS` -
    IMMER eine feste, inhaltsfreie Vokabel, niemals Sachverhalt/Text -
    KEIN Ersatz fuer "result", rein informativ, beliebig viele pro Aufruf
    moeglich) oder das abschliessende Gesamtergebnis (`kind="result"`,
    identische Form wie der Rückgabewert von `create_draft` - GENAU EIN
    "result"-Ereignis pro Aufruf, immer als letztes)."""

    kind: str
    text: str = ""
    status: str = ""
    result: DraftingResult | None = None


class DraftingService:
    def __init__(
        self,
        local_ai: LocalAIProvider,
        research_service: LegalResearchService,
        search_service: DocumentSearchService,
        gateway: ClaudePrivacyGateway,
        writing_provider: ClaudeWritingProvider,
        *,
        api_logger: ApiCallLogger | None = None,
        cost_control: CostControlService | None = None,
        model_name: str = "unknown",
        local_llm_provider: LocalLLMProvider | None = None,
    ) -> None:
        self.local_ai = local_ai
        self.research_service = research_service
        self.search_service = search_service
        self.gateway = gateway
        self.writing_provider = writing_provider
        self.api_logger = api_logger if api_logger is not None else ApiCallLogger()
        self.cost_control = cost_control if cost_control is not None else CostControlService()
        self.model_name = model_name
        # None (Standard) = kein lokaler KI-Zwischenschritt, unveraendertes
        # Verhalten wie vor §65 - siehe Moduldocstring Schritt 5.
        self.local_llm_provider = local_llm_provider

    def create_draft(
        self,
        matter_id: str | None,
        purpose: str,
        db: Session,
        *,
        stil: str | None = None,
        vorlage: str | None = None,
        attorney_anmerkungen: str | None = None,
        previous_draft: Draft | None = None,
        actor: str = "system",
        new_matter_title: str | None = None,
        new_client_name: str | None = None,
        trace: PerfTrace | None = None,
        gespraechsverlauf: list[str] | None = None,
        message_id: str | None = None,
        chat_triggered: bool = False,
    ) -> DraftingResult:
        """Erstellt eine neue Draft-Version.

        `previous_draft=None` (Standardfall): erste Version einer neuen
        Entwurfslinie (v1).

        `previous_draft=<Draft>`: Neugenerierung als FOLGEVERSION - z. B.
        angestoßen durch `AttorneyInstructionService.apply_instruction`
        (siehe app/attorney_instructions/service.py). Erzeugt IMMER eine
        NEUE `Draft`-Zeile über `create_new_draft_version`
        (app/drafting/versioning.py) - die Vorgänger-Zeile wird an keiner
        Stelle verändert.

        `attorney_anmerkungen`: unpseudonymisierter Freitext einer
        anwaltlichen Anmerkung (siebtes Allowlist-Feld, siehe
        gateway_schema.py) - durchläuft hier denselben Privacy-Gateway-
        Durchlauf wie Sachverhalt/Quellen/Vorlage, GENAU EINMAL, bevor
        irgendetwas Claude erreicht.

        `trace` (Performance-Root-Cause-Run, 13.09.): optionale
        `PerfTrace`-Instanz (app/observability/perf_trace.py) - misst nur
        die bereits bestehenden Schritte (retrieval/privacy_gateway/
        local_ai_preanalysis/claude/validation/reconstruction), erfindet
        keine neuen. `None` (Standard, alle bestehenden Aufrufer)
        erzeugt intern automatisch eine neue - unverändertes Verhalten.

        `matter_id=None` (Schriftsatz-Generator, 20.08.): statt einen Fehler
        zu werfen, wird automatisch eine neue Akte (mit einem ebenfalls neu
        angelegten Mandanten) angelegt, DAMIT dieser Entwurf überhaupt
        gespeichert werden kann - `Draft.matter_id` ist NICHT nullable
        (Aktenisolation, siehe CLAUDE.md). Alles NACH diesem Block (Privacy-Gateway,
        Kostenkontrolle, Audit-Logging des eigentlichen Schreibauftrags)
        bleibt UNVERÄNDERT - der Auto-Create-Zweig liefert nur eine echte
        `matter_id`, bevor die bestehende Logik beginnt. Die Anlage selbst
        wird als eigenes `AuditEvent` festgehalten (nachvollziehbar, siehe
        CLAUDE.md-Grundregel), unabhängig vom Erfolg/Misserfolg der
        anschließenden Entwurfserstellung.

        `gespraechsverlauf` (CHAT-02, 15.09.): optionale, bereits als
        "Rolle: Text"-Zeilen formatierte Liste vorheriger Chat-Turns (siehe
        app/chat/service.py::ChatService._build_history für Aufbau/Budget-
        Logik) - durchläuft hier GENAU DENSELBEN Privacy-Gateway-Durchlauf
        wie jedes andere Feld, bevor irgendetwas Claude erreicht.
        `None`/leere Liste (Standard, alle bisherigen Aufrufer -
        Schriftsatz-Generator, anwaltliche Anweisungen) = unverändertes
        Verhalten wie vor CHAT-02.

        `message_id` (17.09., Overnight-Direktive §6/§7 "Dokumente/Workflows
        verbinden"): optionale ID der `Message`, auf die dieser Entwurf
        antwortet (z. B. "Antworten" auf eine Posteingang-Nachricht, siehe
        app/web/chat_router.py::start_conversation_from_message) - wird NUR
        unveraendert an `Draft.message_id` durchgereicht (`create_new_draft_
        version` unterstuetzt dieses Feld bereits laenger, siehe app/
        drafting/versioning.py, war bisher aber von KEINEM Aufrufer
        tatsaechlich verdrahtet). Speist den bereits bestehenden "Original
        links / Entwurf rechts"-Split in `draft_detail.html`, der ohne
        dieses Feld strukturell nie eine Original-Nachricht anzeigen konnte.
        `None` (Standard, alle bisherigen Aufrufer) = unveraendertes
        Verhalten (kein Original-Bezug, wie bisher immer).

        `chat_triggered` (18.09., Flow-Audit "Posteingang -> Antworten",
        live am echten Server reproduziert - siehe OPEN_ISSUES.md fuer die
        volle Herleitung): `True` NUR wenn `ChatService.send_message`/
        `-_stream` der Aufrufer ist (Chat-Freitext, "Zusammenfassen"/
        "Antworten" auf eine Nachricht, "Dokument analysieren"/
        "Schriftsatz-Entwurf erstellen" auf ein Dokument) - NIE von
        `schriftsatz_router.py` (Schriftsatz-Generator, der den vollen
        Akte-Kontext tatsaechlich ausschoepfen soll). Steuert zusammen mit
        `message_id` (s.o.), ob `_finish_non_streaming_stream` die volle
        Platzhalter-Abdeckung verlangt - ein Chat-getriggerter Entwurf ist
        strukturell immer eine Antwort auf EINE Nachricht/EIN Dokument/EINE
        Chat-Anfrage, nie ein eigenstaendiger, das gesamte Aktenwissen
        ausschoepfender Schriftsatz."""
        trace = trace or PerfTrace()
        prepared = self._prepare_and_gate(
            matter_id,
            purpose,
            db,
            stil=stil,
            vorlage=vorlage,
            attorney_anmerkungen=attorney_anmerkungen,
            previous_draft=previous_draft,
            actor=actor,
            new_matter_title=new_matter_title,
            new_client_name=new_client_name,
            trace=trace,
            gespraechsverlauf=gespraechsverlauf,
            message_id=message_id,
            chat_triggered=chat_triggered,
        )
        if isinstance(prepared, DraftingResult):
            return prepared
        return self._finish_non_streaming(
            prepared, purpose, db, previous_draft=previous_draft, actor=actor, trace=trace
        )

    def create_draft_stream(
        self,
        matter_id: str | None,
        purpose: str,
        db: Session,
        *,
        stil: str | None = None,
        vorlage: str | None = None,
        attorney_anmerkungen: str | None = None,
        previous_draft: Draft | None = None,
        actor: str = "system",
        new_matter_title: str | None = None,
        new_client_name: str | None = None,
        trace: PerfTrace | None = None,
        gespraechsverlauf: list[str] | None = None,
        message_id: str | None = None,
        chat_triggered: bool = False,
    ) -> Generator[DraftStreamEvent, None, None]:
        """Streaming-Variante von `create_draft` (13.09., Streaming-
        Architekturentscheidung - siehe DECISIONS.md fuer die volle
        Herleitung). Liefert ECHTE inkrementelle Text-Deltas (TTFR-Gewinn)
        NUR fuer den bereits etablierten risikobasierten Fast Path
        (`_should_skip_llm_privacy_layers` = True, d. h. `purpose=
        "chat_response"`, kein Aktendokument, UND jede von Presidio in der
        GESAMTEN Payload gefundene Entitaet ist bereits eine der Akte
        bekannte Person - siehe CHAT-04, 15.09.: `gateway_result.mappings`
        ist in diesem Fall NICHT mehr zwingend leer, jeder darin
        enthaltene Treffer ist aber ein bereits bekannter Name, kein neu
        hinzugekommener).

        JEDE Anfrage, die die volle Pipeline braucht (Dokumentkontext,
        erkanntes PII, oder ein expliziter Schreibauftrag), wird
        stattdessen UNVERAENDERT ueber `_finish_non_streaming` (= derselbe
        Code wie `create_draft`) verarbeitet und als EIN einziges
        "delta" + "result"-Ereignis ausgeliefert - kein TTFR-Gewinn fuer
        diesen Fall, aber auch KEINE Abkuerzung/Aenderung der bestehenden
        Validierungs-/Fail-Closed-Garantien (deterministische UND
        semantische Antwortvalidierung bleiben fuer diesen Fall exakt wie
        vor der Streaming-Einfuehrung Pflicht).

        Sicherheitsmodell fuer den ECHTEN Streaming-Pfad (mappings
        garantiert leer): das Platzhalter-Mapping verlaesst den Server nie
        (es ist ohnehin leer), die Rekonstruktion bleibt vollstaendig
        serverseitig (hier ein reiner No-Op, siehe reconstruct_response),
        und die deterministische Platzhalter-Integritaetspruefung (Stufe 1,
        siehe app/privacy/security_check.py::check_response_placeholder_integrity)
        laeuft NACH JEDEM Delta auf dem bisher akkumulierten Text - findet
        sie ein unerwartetes platzhalterfoermiges Token (z. B. weil Claude
        entgegen der Systemanweisung eines erfindet), wird SOFORT
        abgebrochen (kein weiteres Delta, Nachricht wird als blockiert
        persistiert) - strenger/reaktionsschneller als die bisherige
        Pruefung erst am Gesamttext, nicht schwaecher. Stufe 2 (semantische
        Pruefung) ist fuer diesen Fall bereits seit dem P0
        Performance-Follow-up (13.09.) uebersprungen (siehe
        `_should_skip_llm_privacy_layers`), unveraendert durch Streaming."""
        trace = trace or PerfTrace()
        prepared = self._prepare_and_gate(
            matter_id,
            purpose,
            db,
            stil=stil,
            vorlage=vorlage,
            attorney_anmerkungen=attorney_anmerkungen,
            previous_draft=previous_draft,
            actor=actor,
            new_matter_title=new_matter_title,
            new_client_name=new_client_name,
            trace=trace,
            gespraechsverlauf=gespraechsverlauf,
            message_id=message_id,
            chat_triggered=chat_triggered,
        )
        if isinstance(prepared, DraftingResult):
            yield DraftStreamEvent(kind="result", result=prepared)
            return

        # ECHTER FUND (08.10., Real-User-E2E im installierten Build a6ba839):
        # der echte Streaming-Pfad setzt GARANTIERT LEERE Mappings voraus
        # (siehe `_stream_from_writing_provider`: Stufe-1-Pruefung nach jedem
        # Delta gegen `gateway_result.mappings`, keine Abdeckungs-Lockerung).
        # Seit der Skip-Entscheidung auf lokal-stammende Mappings (a6ba839)
        # kann `skip_llm_privacy_layers` auch bei NICHT-leeren Mappings
        # (Betraege/Zahlen aus der KI-Historie, z. B. "[TELEFON_01]") True
        # sein - dann fehlte der Platzhalter im akkumulierten Teiltext, der
        # Stream wurde nach dem ersten Delta als "unerwarteter Platzhalter"
        # abgebrochen. Streaming daher NUR bei tatsaechlich leeren Mappings;
        # sonst der bestehende, voll mapping-faehige nicht-streamende Pfad
        # (deterministische Stufe 1 mit Chat-Abdeckungs-Lockerung, lokale
        # Rekonstruktion) - weiterhin OHNE Local AI.
        streaming_eligible = (
            prepared.skip_llm_privacy_layers
            and not prepared.gateway_result.mappings
            and hasattr(self.writing_provider, "write_stream")
        )
        if not streaming_eligible:
            # P1 Performance-Feedback-Follow-up (17.09.): `_finish_non_
            # streaming_stream` liefert jetzt selbst "status"-Zwischen-
            # ereignisse (siehe dortiger Docstring/`_STEP_STATUS_LABELS`) -
            # einfach durchreichen. Das abschliessende "delta"+"result"-Paar
            # bleibt UNVERAENDERT identisch zum bisherigen Verhalten (vor
            # diesem Umbau kam nur genau dieses eine Paar, ohne jeden
            # Zwischenschritt).
            for event in self._finish_non_streaming_stream(
                prepared, purpose, db, previous_draft=previous_draft, actor=actor, trace=trace
            ):
                if event.kind != "result":
                    yield event
                    continue
                result = event.result
                assert result is not None
                if result.success and result.draft_text:
                    yield DraftStreamEvent(kind="delta", text=result.draft_text)
                yield DraftStreamEvent(kind="result", result=result)
            return

        yield from self._stream_from_writing_provider(
            prepared, purpose, db, previous_draft=previous_draft, actor=actor, trace=trace
        )

    def _prepare_and_gate(
        self,
        matter_id: str | None,
        purpose: str,
        db: Session,
        *,
        stil: str | None,
        vorlage: str | None,
        attorney_anmerkungen: str | None,
        previous_draft: Draft | None,
        actor: str,
        new_matter_title: str | None,
        new_client_name: str | None,
        trace: PerfTrace,
        gespraechsverlauf: list[str] | None = None,
        message_id: str | None = None,
        chat_triggered: bool = False,
    ) -> DraftingResult | _PreparedRequest:
        """Gemeinsamer Vorbereitungs-/Gate-Teil von `create_draft`/
        `create_draft_stream` (Aktenauflösung, Recherche, Privacy Gateway,
        Kostenkontrolle) - EINMAL ausgefuehrt, von BEIDEN Methoden
        weiterverwendet (siehe `_PreparedRequest`-Docstring). Ein
        zurueckgegebenes `DraftingResult` bedeutet: bereits an dieser
        Stelle blockiert (Datenschutz-Gate oder Budget) - der Aufrufer
        gibt es unveraendert zurueck bzw. als einziges Stream-Ereignis
        weiter."""
        if not matter_id:
            matter = create_quick_matter(
                db, title=new_matter_title, client_name=new_client_name, actor=actor
            )
            matter_id = matter.id
        else:
            matter = db.query(Matter).filter_by(id=matter_id).first()
            if matter is None:
                raise ValueError(f"Matter {matter_id} nicht gefunden")
        if previous_draft is not None and previous_draft.matter_id != matter_id:
            raise ValueError(
                "previous_draft gehört nicht zur angegebenen Akte - "
                "Versionsketten dürfen Aktengrenzen nicht überschreiten"
            )

        preparation = self.local_ai.prepare_draft_context(matter_id, db)

        with trace.step("retrieval"):
            source_list, quellen_texts, open_review_points = self._gather_legal_sources(
                matter, db, actor=actor
            )
            knowledge_items_used, knowledge_texts = self._gather_knowledge_items(matter, db)

        # ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
        # Pipeline", 07.10.): Presidios generische ORGANIZATION-Erkennung
        # pseudonymisiert unterschiedslos auch oeffentlich bekannte
        # Organisationen aus allgemeinen Wissensfragen ("World Health
        # Organization") - Claude bekommt dann nur einen Platzhalter und
        # kann die Frage nicht mehr sinnvoll beantworten (live reproduziert,
        # siehe app/privacy/pseudonymizer.py::pseudonymize Docstring zu
        # `skip_categories`). Diese Pseudonymisierung NUR dann ueberspringen,
        # wenn VOR dem Gateway-Aufruf bereits ALLE drei Bedingungen
        # zutreffen: kein expliziter Schreibauftrag (purpose ==
        # "chat_response", exakt wie bei `_should_skip_llm_privacy_layers`
        # unten), kein Aktendokument im Sachverhalt UND keine bekannten
        # Aktenbeteiligten ausser dem strukturellen Sammel-Mandanten-
        # Platzhalter (siehe `_has_only_placeholder_known_entities` oben -
        # OHNE diese Praezisierung waere `known_entities` fuer JEDEN Chat
        # NIEMALS leer gewesen, siehe dortiger ECHTER FUND, und dieser
        # Mechanismus haette real nie ausgeloest). Sobald IRGENDeine
        # dieser Bedingungen nicht zutrifft (Akte/Dokument/ECHTER Mandant
        # im Spiel), bleibt das Verhalten 100% unveraendert (volle
        # Pseudonymisierung wie bisher). RESTRISIKO, bewusst nicht
        # eliminiert: tippt ein Anwalt den Namen eines brandneuen, noch
        # NICHT mit einer Akte verknuepften Mandanten in einen voellig
        # unverknuepften allgemeinen Chat, greift dieser Mechanismus nicht
        # schuetzend - `known_entities`/Akte-Verknuepfung bleibt der
        # massgebliche, zuverlaessige Schutzpfad; das ist der erwartete,
        # vorgesehene Arbeitsablauf (Akte zuerst anlegen/verknuepfen), kein
        # unentdeckter Bug.
        skip_general_knowledge_pseudonymization = (
            purpose == _CHAT_PURPOSE
            and not preparation.has_document_context
            and _has_only_placeholder_known_entities(preparation.known_entities)
        )

        with trace.step("privacy_gateway"):
            gateway_result = self.gateway.prepare_request(
                purpose=purpose,
                sachverhalt=preparation.sachverhalt,
                argumentationspunkte=preparation.argumentationspunkte,
                quellenverweise=quellen_texts + knowledge_texts,
                stil=stil,
                vorlage=vorlage,
                anwaltliche_anmerkungen=attorney_anmerkungen,
                known_entities=preparation.known_entities,
                gespraechsverlauf=gespraechsverlauf,
                skip_general_knowledge_pseudonymization=skip_general_knowledge_pseudonymization,
            )

        if not gateway_result.allowed:
            self.api_logger.log_blocked(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                reasons=gateway_result.reasons,
            )
            return DraftingResult(
                success=False,
                blocked_reasons=gateway_result.reasons,
                open_review_points=open_review_points,
            )

        # Kostenkontrolle (Prompt 33) - NACH der Datenschutzprüfung (die
        # ist immer zuerst relevant), aber VOR dem tatsächlichen,
        # kostenpflichtigen Aufruf. Ohne konfiguriertes Budget
        # (settings.monthly_budget_usd=None, Standard) wird nie blockiert.
        budget_check = self.cost_control.check_before_call(db)
        if not budget_check.allowed:
            self.api_logger.log_blocked(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                reasons=[budget_check.reason or "Kostenlimit erreicht"],
            )
            return DraftingResult(
                success=False,
                blocked_reasons=[budget_check.reason or "Kostenlimit erreicht"],
                open_review_points=open_review_points,
            )

        # P0 Performance-Follow-up (13.09.): siehe Moduldocstring Schritt 5
        # und `_should_skip_llm_privacy_layers` fuer die volle Begruendung -
        # Presidio/Pseudonymisierung (oben, gateway.prepare_request) UND die
        # deterministische Platzhalter-Integritaetspruefung (Stufe 1, siehe
        # validate_claude_response weiter unten) bleiben davon UNBERUEHRT.
        # ECHTER FUND (08.10., Real-User-E2E im installierten Build): in einer
        # Unterhaltung wurden General-Chat-Folgefragen ohne jedes PII
        # ("wieviele Klempnerbetriebe ...", "was kannst du") ~60 s lang
        # verarbeitet (Vorabanalyse 8-10 s + lokale Validierung 35-40 s)
        # statt ~12 s - die Mappings, die den Skip verhinderten, stammten
        # AUSSCHLIESSLICH aus frueheren Claude-Antworten der Historie (Euro-
        # Betraege der Mietpreis-Antwort als "betrag", ein NER-Fehlalarm
        # "Miet-Check" als "person"; nachgewiesen per Gateway-Reproduktion
        # an der echten Unterhaltung). Ein Wert, der nirgends im lokal-
        # stammenden Text (Anwalt-Eingabe, Akte/Dokument; siehe
        # GatewayResult.locally_sourced_text) vorkommt, kann KEINE neue,
        # vom Anwalt eingebrachte sensible Entitaet sein - genau die
        # sollen die LLM-Schichten schuetzen. Konservativ: taucht ein Wert
        # auch nur als Teilstring im lokalen Text auf, bleibt er
        # relevant (im Zweifel volle Pipeline). Die Pseudonymisierung
        # selbst (Pre-Cloud) ist davon unberuehrt - diese Mappings werden
        # weiterhin ersetzt.
        locally_sourced_mappings = [
            m
            for m in gateway_result.mappings
            if (m.original_value or "") in gateway_result.locally_sourced_text
        ]
        skip_llm_privacy_layers = _should_skip_llm_privacy_layers(
            purpose=purpose,
            has_document_context=preparation.has_document_context,
            mappings=locally_sourced_mappings,
            known_entities=preparation.known_entities,
        )

        # ECHTER FUND (07.10., urspruenglich; PRAEZISIERT 07.10. durch
        # Owner-Direktive "Architektur-Audit Privacy-/Chat-Pipeline" -
        # siehe app/privacy/security_check.py::find_lenient_leak_exempt_
        # placeholders fuer die volle Herleitung): NUR lokal-stammender
        # Text darf als Nachweis dienen, dass ein Wert NIE von Claude
        # erfunden wurde. Frueher wurde das hier lokal aus nur
        # `attorney_anmerkungen` + "Anwalt: "-Historienzeilen
        # rekonstruiert - deckte `sachverhalt`/`vorlage`/`quellenverweise`
        # (dokumentbasierten Kontext) NICHT ab. Jetzt EINE einzige,
        # vollstaendige Quelle: `gateway_result.locally_sourced_text`
        # (app/privacy/gateway.py::GatewayResult, aus denselben RAW-
        # Eingaben wie der Gateway-Aufruf oben gebaut) - schliesst diese
        # Luecke, wichtig seit "person" ebenfalls diese Lockerung nutzen
        # darf (siehe dortiger Kommentar).
        lenient_leak_exempt_placeholders = frozenset(
            find_lenient_leak_exempt_placeholders(
                gateway_result.mappings,
                lawyer_authored_text=gateway_result.locally_sourced_text,
            )
        )

        return _PreparedRequest(
            matter_id=matter_id,
            payload=gateway_result.payload,
            gateway_result=gateway_result,
            skip_llm_privacy_layers=skip_llm_privacy_layers,
            lenient_leak_exempt_placeholders=lenient_leak_exempt_placeholders,
            source_list=source_list,
            knowledge_items_used=knowledge_items_used,
            open_review_points=open_review_points,
            message_id=message_id,
            chat_triggered=chat_triggered,
        )

    def _finish_non_streaming(
        self,
        prepared: _PreparedRequest,
        purpose: str,
        db: Session,
        *,
        previous_draft: Draft | None,
        actor: str,
        trace: PerfTrace,
    ) -> DraftingResult:
        """Duenner, synchroner Wrapper um `_finish_non_streaming_stream` -
        fuer `create_draft` (nicht-streamende Aufrufer: Schriftsatz-
        Generator, anwaltliche Anweisungen, Tests), die weiterhin ein
        einfaches `DraftingResult` statt eines Generators erwarten.
        Verwirft die "status"-Zwischenereignisse, behaelt nur das
        abschliessende "result" - IDENTISCHES Endergebnis wie vor der
        Umstellung auf den Generator (P1 Performance-Feedback-Follow-up,
        17.09.), siehe dortigen Docstring fuer die volle Begruendung."""
        for event in self._finish_non_streaming_stream(
            prepared, purpose, db, previous_draft=previous_draft, actor=actor, trace=trace
        ):
            if event.kind == "result":
                assert event.result is not None
                return event.result
        raise AssertionError(  # pragma: no cover - Invariante, kein echter Fehlerfall
            "_finish_non_streaming_stream endete ohne 'result'-Ereignis"
        )

    def _finish_non_streaming_stream(
        self,
        prepared: _PreparedRequest,
        purpose: str,
        db: Session,
        *,
        previous_draft: Draft | None,
        actor: str,
        trace: PerfTrace,
    ) -> Generator[DraftStreamEvent, None, None]:
        """Der volle, verhaltensgleiche zweite Teil von `create_draft`
        (lokale Vorabanalyse, Claude-Aufruf, Antwortvalidierung,
        Rekonstruktion, Persistenz) - ausgelagert, damit `create_draft_stream`
        denselben Code fuer den nicht-streaming-faehigen Fall wiederverwenden
        kann, OHNE `_prepare_and_gate` ein zweites Mal auszufuehren (kein
        doppelter Presidio-/Recherche-Durchlauf).

        P1 Performance-Feedback-Follow-up (17.09., siehe OPEN_ISSUES.md/
        `_STEP_STATUS_LABELS`): seit diesem Umbau ein GENERATOR statt einer
        gewoehnlichen Funktion - liefert VOR jedem bereits bestehenden
        `trace.step(...)`-Block ein `kind="status"`-Ereignis, damit
        `create_draft_stream` fuer JEDEN Aufruf (nicht nur den
        risikobasierten Fast Path) sichtbaren Fortschritt ausliefern kann,
        statt 80-105+ Sekunden lang gar nichts zu senden. Reiner 1:1-Umbau
        der bisherigen `return DraftingResult(...)`-Anweisungen auf
        `yield DraftStreamEvent(kind="result", result=DraftingResult(...));
        return` - jede Fail-Closed-Verzweigung, jede Bedingung, jede
        Reihenfolge bleibt exakt unveraendert an derselben Stelle bestehen,
        siehe `_finish_non_streaming` fuer die synchrone Gegenprobe (nutzt
        denselben Code, verwirft nur die "status"-Ereignisse)."""
        matter_id = prepared.matter_id
        payload = prepared.payload
        gateway_result = prepared.gateway_result
        skip_llm_privacy_layers = prepared.skip_llm_privacy_layers
        open_review_points = prepared.open_review_points

        # §65: PFLICHT-Zwischenschritt, sobald ein local_llm_provider
        # injiziert wurde (siehe app/web/service_factory.py) - schlaegt er
        # fehl, wird NIEMALS stattdessen direkt Claude aufgerufen
        # (Datenschutz vor Verfuegbarkeit, siehe Moduldocstring Schritt 5).
        if self.local_llm_provider is not None:
            yield DraftStreamEvent(
                kind="status", status=_STEP_STATUS_LABELS["local_ai_preanalysis"]
            )
        if self.local_llm_provider is not None and skip_llm_privacy_layers:
            with trace.step("local_ai_preanalysis_skipped"):
                pass
        elif self.local_llm_provider is not None:
            try:
                with trace.step("local_ai_preanalysis"):
                    local_result = self.local_llm_provider.process(payload)
            except LocalLLMUnavailableError:
                self.api_logger.log_error(
                    db,
                    workflow_id=matter_id,
                    model=self.model_name,
                    purpose=purpose,
                    payload=payload,
                    error_status="local_ai_unavailable",
                )
                yield DraftStreamEvent(
                    kind="result",
                    result=DraftingResult(
                        success=False,
                        blocked_reasons=[
                            "Lokale KI (Ollama) nicht erreichbar - Anfrage wurde nicht "
                            "an Claude gesendet."
                        ],
                        open_review_points=open_review_points,
                    ),
                )
                return

            # ECHTER FUND (26.09., Owner-Direktive "DOCUMENT WORKSPACE /
            # SCHRIFTSATZ PRODUCT-COMPLETION" §17, im Pflicht-E2E-Test real
            # reproduziert, nahezu deterministisch bei "Dokument
            # analysieren"/"Schriftsatz-Entwurf erstellen"): die lokale
            # Vorabanalyse (Ollama) selbst erfand einen nie zugewiesenen
            # Platzhalter ("[AKTENZEICHEN_01]", OBWOHL der ihr uebergebene
            # Sachverhalt keinerlei Aktenzeichen-Angabe enthielt), TROTZ der
            # bereits bestehenden expliziten Anweisung dagegen (siehe
            # `_LOCAL_LLM_SYSTEM_PROMPT`). Dieser erfundene Platzhalter
            # landete bisher UNGEPRUEFT in `anonymisierte_argumentations
            # punkte` und damit im an Claude gesendeten Payload - Claude
            # kopierte ihn danach (korrekt gemaess SEINER eigenen Anweisung,
            # "verwende Platzhalter, die im Sachverhalt vorkommen") in die
            # Antwort, wo ihn `check_response_placeholder_integrity` am Ende
            # der Kette zwar zuverlaessig als "unerwartet" erkannte und
            # blockierte (fail-closed korrekt) - aber ERST NACH einem
            # bereits verbrauchten, kostenpflichtigen Claude-Aufruf, mit
            # einer fuer den Anwalt irrefuehrenden Fehlermeldung (klang nach
            # einem Claude-Problem, war aber ein lokaler KI-Fund).
            #
            # Fix: dieselbe bereits bestehende, bewaehrte deterministische
            # Pruefung jetzt ZUSAETZLICH direkt auf die lokale Zusammen-
            # fassung angewendet, BEVOR sie ueberhaupt in den Claude-Payload
            # uebernommen wird - faengt den Fund frueher ab (kein
            # unnoetiger Claude-Aufruf, siehe §15 Performance) UND
            # praeziser (die Fehlermeldung kann jetzt ehrlich auf die
            # lokale KI verweisen). `require_full_coverage=False`, weil eine
            # Zusammenfassung nicht JEDEN Mapping-Platzhalter erwaehnen muss
            # (anders als der finale Brieftext) - nur neu erfundene/
            # veraenderte Tokens oder ein geleakter Originalwert sind hier
            # ein Fund. KEIN automatischer Retry (respektiert die bestehende
            # "kontrollierter Abbruch, niemals automatische Neuformulierung"-
            # Architekturentscheidung unveraendert) - derselbe fail-closed
            # Abbruch wie beim finalen Claude-Antwort-Check, nur frueher.
            local_summary_issues = check_response_placeholder_integrity(
                local_result.text, gateway_result.mappings, require_full_coverage=False
            )
            if local_summary_issues:
                self.api_logger.log_blocked(
                    db,
                    workflow_id=matter_id,
                    model=self.model_name,
                    purpose=purpose,
                    reasons=local_summary_issues,
                )
                yield DraftStreamEvent(
                    kind="result",
                    result=DraftingResult(
                        success=False,
                        blocked_reasons=[
                            "Die lokale KI-Vorabanalyse hat Auffälligkeiten "
                            "festgestellt - Entwurf wurde nicht übernommen, "
                            "bevor eine Cloud-Anfrage gestellt wurde.",
                            *local_summary_issues,
                        ],
                        open_review_points=open_review_points,
                    ),
                )
                return

            payload = payload.model_copy(
                update={
                    "anonymisierte_argumentationspunkte": [
                        *payload.anonymisierte_argumentationspunkte,
                        f"{_LOCAL_LLM_ARGUMENTATIONSPUNKT_PREFIX}{local_result.text}",
                    ]
                }
            )

        yield DraftStreamEvent(kind="status", status=_STEP_STATUS_LABELS["claude"])
        try:
            with trace.step("claude"):
                writing_result = self.writing_provider.write(payload)
        except Exception:
            self.api_logger.log_error(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                payload=payload,
            )
            yield DraftStreamEvent(
                kind="result",
                result=DraftingResult(
                    success=False,
                    blocked_reasons=["Interner Fehler bei der Textproduktion"],
                    open_review_points=open_review_points,
                ),
            )
            return

        self.api_logger.log_success(
            db,
            workflow_id=matter_id,
            model=self.model_name,
            purpose=purpose,
            payload=payload,
            token_count=writing_result.token_count,
            input_tokens=writing_result.input_tokens,
            output_tokens=writing_result.output_tokens,
        )

        # ECHTER FUND (19.09., live am echten Server reproduziert, ZWEIMAL
        # deterministisch mit identischer Akte/identischem Auftrag): der
        # Schriftsatz-Generator lieferte bei einer grossen/vollen Akte einen
        # Claude-Aufruf, der GENAU `max_tokens` (2000) Output-Tokens
        # verbrauchte, aber `writing_result.text` war dabei LEER
        # (`len(text) == 0`) - vermutlich verbraucht das Modell das gesamte
        # Token-Budget, ohne einen sichtbaren finalen Text zu produzieren.
        # VOR der Lockerung von `require_full_placeholder_coverage` (siehe
        # `_RELAXED_COVERAGE_PURPOSES` oben) wurde dieser Fall als
        # Nebeneffekt zuverlaessig erkannt: eine leere Antwort "enthaelt"
        # trivialerweise keinen einzigen erwarteten Platzhalter und wurde
        # daher ueber `check_placeholders_present` blockiert (mit der
        # irrefuehrenden Meldung "Interner Konsistenzfehler bei der
        # Pseudonymisierung", aber immerhin BLOCKIERT - kein leerer Entwurf
        # als "Erfolg"). Die Lockerung entfernt diesen zufaelligen Schutz -
        # ohne eigenstaendigen Ersatz wuerde ein leerer/abgeschnittener
        # Entwurf jetzt still als `success=True` durchgereicht
        # (`draft-content-box` blieb in der UI leer) - genau der von §4
        # "REAL OBJECTS - NO FAKE UI" verbotene Fall ("Eine Erfolgsmeldung
        # ohne die zugrunde liegende Operation ist NICHT vollstaendig").
        # Deshalb hier ein EIGENSTAENDIGER, purpose-unabhaengiger
        # Mindestinhalt-Check - bewusst VOR dem `local_llm_provider`-Block
        # unten (der nur laeuft, wenn lokale KI ueberhaupt konfiguriert
        # ist) und unabhaengig von `require_full_placeholder_coverage`,
        # damit dieser Fall auch bei deaktivierter lokaler KI und fuer JEDEN
        # Zweck sicher erkannt wird - keine Platzhalter-/PII-Pruefung,
        # sondern eine reine Plausibilitaetspruefung ("kam ueberhaupt Text
        # zurueck").
        if not writing_result.text or not writing_result.text.strip():
            self.api_logger.log_error(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                payload=payload,
                error_status="empty_writing_response",
            )
            yield DraftStreamEvent(
                kind="result",
                result=DraftingResult(
                    success=False,
                    blocked_reasons=[
                        "Die KI hat keinen verwertbaren Text zurückgegeben "
                        "(leere Antwort, möglicherweise durch das "
                        "Token-Limit abgeschnitten) - Entwurf wurde nicht "
                        "übernommen. Bitte erneut versuchen, ggf. mit "
                        "kürzeren Anmerkungen/weniger Dokumenten."
                    ],
                    open_review_points=open_review_points,
                ),
            )
            return

        # Lokale Datenschutz-/Qualitaetspruefung der (noch pseudonymisierten)
        # Claude-Antwort, VOR jeder Rekonstruktion - siehe
        # app/drafting/response_validation.py. Nur aktiv, wenn lokale KI
        # ueberhaupt konfiguriert ist (derselbe Schalter wie der bestehende
        # Vorabanalyse-Schritt oben, kein neuer Toggle). Zweistufig,
        # deterministisch VOR semantisch - ein LLM-Urteil kann eine
        # fehlgeschlagene deterministische Pruefung nie ueberstimmen (siehe
        # Moduldocstring dort). Bei jedem Fehlschlag: kontrollierter Abbruch,
        # NIEMALS automatische Neuformulierung/Reparatur.
        if self.local_llm_provider is not None:
            yield DraftStreamEvent(kind="status", status=_STEP_STATUS_LABELS["validation"])
            try:
                with trace.step("validation"):
                    validation = validate_claude_response(
                        writing_result.text,
                        gateway_result.mappings,
                        payload.anonymisierter_sachverhalt,
                        self.local_llm_provider,
                        skip_semantic_check=skip_llm_privacy_layers,
                        # Entwicklungsgeschichte dieser einen Zeile (CHAT-01
                        # 15.09. -> zwei weitere Eskalationen 18.09., volle
                        # Root-Cause-Begruendung bei `_RELAXED_COVERAGE_PURPOSES`
                        # oben): "jeder Mapping-Platzhalter muss im Text
                        # vorkommen" wurde ZUERST fuer chat_response
                        # deaktiviert (blockierte normale Chat-Begruessungen),
                        # DANN fuer chat-getriggerte formulate_draft-Aufrufe
                        # (blockierte "Antworten" auf eine Posteingang-
                        # Nachricht), DANN live auch fuer den kanonischen
                        # Schriftsatz-Generator-Weg reproduziert (KEIN
                        # message_id/chat_triggered - der bis dahin letzte
                        # Fall, der die Forderung noch erfuellen sollte).
                        # Ursache in allen drei Faellen identisch:
                        # `prepare_draft_context` baut Sachverhalt/Mappings
                        # aus der GESAMTEN Akte. BEWUSST NUR fuer
                        # `_RELAXED_COVERAGE_PURPOSES` deaktiviert (bisher
                        # live reproduziert), NICHT fuer alle Zwecke - siehe
                        # dortiger Kommentar fuer die Abgrenzung gegen
                        # Spekulation ueber unbewiesene Zwecke. Die beiden
                        # TATSAECHLICH schuetzenden Pruefungen (Platzhalter-
                        # Manipulation/erfundene Tokens, Originalwert-Leck)
                        # bleiben davon unberuehrt fuer JEDEN Zweck weiterhin
                        # zwingend aktiv - siehe
                        # check_response_placeholder_integrity. Betrifft NICHT
                        # das separate, bewusst weiterhin strenge ausgehende
                        # Final Payload Gate (check_payload_placeholder_integrity).
                        # `lenient_leak_exempt_placeholders` (07.10., siehe
                        # app/privacy/security_check.py::
                        # find_lenient_leak_exempt_placeholders) ist die
                        # EINZIGE, eng begrenzte Ausnahme von "Originalwert-
                        # Leck bleibt fuer JEDEN Zweck zwingend aktiv" -
                        # betrifft nur NER-basierte ort/organisation-Werte,
                        # die nachweislich nie vom Anwalt selbst getippt
                        # wurden.
                        require_full_placeholder_coverage=(
                            purpose not in _RELAXED_COVERAGE_PURPOSES
                        ),
                        lenient_leak_exempt_placeholders=prepared.lenient_leak_exempt_placeholders,
                    )
            except LocalLLMUnavailableError:
                self.api_logger.log_error(
                    db,
                    workflow_id=matter_id,
                    model=self.model_name,
                    purpose=purpose,
                    payload=payload,
                    error_status="local_ai_unavailable",
                )
                yield DraftStreamEvent(
                    kind="result",
                    result=DraftingResult(
                        success=False,
                        blocked_reasons=[
                            "Lokale Prüfung der Antwort (Ollama) nicht erreichbar - "
                            "Entwurf wurde nicht übernommen."
                        ],
                        open_review_points=open_review_points,
                    ),
                )
                return
        else:
            # ECHTER FUND (06.10., Owner-Direktive "Schriftsatz-Workflow,
            # Pseudonymisierung, lokale KI und DIN-A4-Dokumentdarstellung",
            # /local-ai-causality-test): die deterministische Platzhalter-
            # Integritaetspruefung (Stufe 1 in `validate_claude_response`,
            # reines Python ueber `check_response_placeholder_integrity` -
            # KEIN LLM-Aufruf, siehe dortigen Fruehzeitig-Return bei
            # `skip_semantic_check=True`, BEVOR `local_llm_provider`
            # ueberhaupt angefasst wird) lief bisher NUR, wenn lokale KI
            # ueberhaupt konfiguriert war (derselbe Schalter wie die rein
            # OPTIONALE semantische Qualitaetspruefung/Stufe 2). Bei
            # deaktivierter lokaler KI (Standardkonfiguration,
            # `settings.local_ai_enabled=False`) konnte dadurch ein von
            # Claude erfundener oder vertauschter Platzhalter unbemerkt bis
            # ins finale Dokument durchrutschen (live reproduziert, siehe
            # tests/test_drafting_service.py::
            # test_unmapped_placeholder_in_claude_response_is_blocked_even_without_local_ai).
            # `self.local_llm_provider` ist hier `None` und wird wegen
            # `skip_semantic_check=True` NIE tatsaechlich aufgerufen (Regel
            # "kein LLM fuer eine deterministische Pruefung, wenn nicht
            # erforderlich") - reine Wiederverwendung derselben, bereits
            # bestehenden Funktion, keine zweite Pruefimplementierung.
            with trace.step("validation"):
                validation = validate_claude_response(
                    writing_result.text,
                    gateway_result.mappings,
                    payload.anonymisierter_sachverhalt,
                    self.local_llm_provider,
                    skip_semantic_check=True,
                    require_full_placeholder_coverage=(
                        purpose not in _RELAXED_COVERAGE_PURPOSES
                    ),
                    lenient_leak_exempt_placeholders=prepared.lenient_leak_exempt_placeholders,
                )

        if not validation.passed:
            # ECHTER FUND (UI-Live-Validierung "Zusammenfassen"-Aktion,
            # 17.09.): bis hierhin landete JEDER Stufe-1/Stufe-2-Befund
            # unter demselben festen `error_status=
            # "response_validation_failed"` im Audit-Log - der
            # sicherheitskritischste Fall (Original-PII-Leck in der
            # Antwort) war im Log nicht von einer harmlosen
            # Formulierungs-Inkonsistenz zu unterscheiden.
            #
            # ERWEITERUNG (05.10., Owner-Direktive "P1-BUGFIX", siehe
            # app/drafting/response_validation.py::ResponseValidationResult
            # fuer die volle Herleitung): `validation.stage ==
            # "semantic"` bedeutet, der Fund kommt NICHT aus der
            # deterministischen Platzhalter-Pruefung (der tatsaechlichen
            # Datenschutz-Durchsetzung), sondern aus der rein
            # qualitaetsbezogenen, nachweislich unzuverlaessigen
            # lokalen LLM-Pruefung (Stufe 2) - bekommt deshalb eine
            # EIGENE, ehrliche Kategorie/Meldung statt der
            # Freitext-Mustererkennung (`categorize_block_reasons`),
            # die auf vom lokalen Modell frei erfundenen Formulierungen
            # unzuverlaessig ist (meist "unknown_block_reason", faelschlich
            # als "Datenschutzgruende" dargestellt). Stufe 1
            # (`validation.stage == "deterministic"`) bleibt UNVERAENDERT
            # ueber die bestehende Kategorisierung gemeldet - diese
            # Aenderung betrifft ausschliesslich die MELDUNG, nicht die
            # Fail-Closed-Entscheidung selbst (beide Stufen blockieren
            # weiterhin bei einem Fund).
            if validation.stage == "semantic":
                error_status = "local_quality_check_uncertain"
                user_message = (
                    "Die lokale Qualitätsprüfung konnte die Antwort nicht "
                    "eindeutig bestätigen - kein Datenschutzvorfall. "
                    "Entwurf wurde sicherheitshalber nicht übernommen."
                )
            else:
                error_status = (
                    categorize_block_reasons(validation.issues)
                    or "response_validation_failed"
                )
                user_message = (
                    "Lokale Prüfung der Antwort hat Auffälligkeiten festgestellt - "
                    "Entwurf wurde nicht übernommen."
                )
            self.api_logger.log_error(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                payload=payload,
                error_status=error_status,
            )
            yield DraftStreamEvent(
                kind="result",
                result=DraftingResult(
                    success=False,
                    blocked_reasons=[user_message, *validation.issues],
                    open_review_points=open_review_points,
                ),
            )
            return

        yield DraftStreamEvent(kind="status", status=_STEP_STATUS_LABELS["reconstruction"])
        with trace.step("reconstruction"):
            reconstructed_text = self.gateway.reconstruct_response(
                writing_result.text, gateway_result.mappings
            )

        draft = self._persist_draft(
            matter_id,
            reconstructed_text,
            purpose,
            db,
            actor=actor,
            previous_draft=previous_draft,
            message_id=prepared.message_id,
        )
        self._persist_reference_links(
            draft, prepared.source_list, prepared.knowledge_items_used, db
        )
        uncertainties = self._gather_uncertainties(matter_id, db)

        yield DraftStreamEvent(
            kind="result",
            result=DraftingResult(
                success=True,
                draft_id=draft.id,
                draft_text=reconstructed_text,
                source_list=prepared.source_list,
                knowledge_items_used=prepared.knowledge_items_used,
                open_review_points=open_review_points,
                uncertainties=uncertainties,
            ),
        )

    def _stream_from_writing_provider(
        self,
        prepared: _PreparedRequest,
        purpose: str,
        db: Session,
        *,
        previous_draft: Draft | None,
        actor: str,
        trace: PerfTrace,
    ) -> Generator[DraftStreamEvent, None, None]:
        """Der ECHTE Streaming-Pfad (nur erreicht, wenn `create_draft_stream`
        bereits `prepared.skip_llm_privacy_layers` UND eine `write_stream`-
        Faehigkeit des Providers festgestellt hat - `gateway_result.mappings`
        ist an dieser Stelle daher GARANTIERT leer, siehe Docstring von
        `create_draft_stream`)."""
        matter_id = prepared.matter_id
        payload = prepared.payload
        gateway_result = prepared.gateway_result
        open_review_points = prepared.open_review_points

        if self.local_llm_provider is not None:
            with trace.step("local_ai_preanalysis_skipped"):
                pass

        accumulated: list[str] = []
        anomaly_reasons: list[str] | None = None
        writing_result = None

        text_stream = self.writing_provider.write_stream(payload)
        try:
            with trace.step("claude"):
                while True:
                    try:
                        delta = next(text_stream)
                    except StopIteration as stop:
                        writing_result = stop.value
                        break
                    accumulated.append(delta)
                    # Deterministische Stufe-1-Pruefung (siehe Docstring
                    # von create_draft_stream) - laeuft NACH JEDEM Delta auf
                    # dem bisher akkumulierten Text, nicht erst am Ende.
                    deterministic_issues = check_response_placeholder_integrity(
                        "".join(accumulated), gateway_result.mappings
                    )
                    if deterministic_issues:
                        anomaly_reasons = deterministic_issues
                        text_stream.close()
                        break
                    yield DraftStreamEvent(kind="delta", text=delta)
        except Exception:
            self.api_logger.log_error(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                payload=payload,
            )
            yield DraftStreamEvent(
                kind="result",
                result=DraftingResult(
                    success=False,
                    blocked_reasons=["Interner Fehler bei der Textproduktion"],
                    open_review_points=open_review_points,
                ),
            )
            return

        self.api_logger.log_success(
            db,
            workflow_id=matter_id,
            model=self.model_name,
            purpose=purpose,
            payload=payload,
            token_count=writing_result.token_count if writing_result else None,
            input_tokens=writing_result.input_tokens if writing_result else None,
            output_tokens=writing_result.output_tokens if writing_result else None,
        )

        # Analoger Mindestinhalt-Check wie im nicht-streamenden Pfad oben
        # (19.09., gleiche Root Cause): hier ist `gateway_result.mappings`
        # IMMER leer (siehe Kommentar bei `reconstruct_response` unten) -
        # `check_response_placeholder_integrity` findet bei leerem
        # `accumulated`-Text also STRUKTURELL nie einen Fund (weder
        # Platzhalter-Manipulation noch Originalwert-Leck moeglich ohne
        # Mappings), unabhaengig vom Zweck. Ohne diesen expliziten Check
        # wuerde ein leerer/abgeschnittener Stream ebenso still als
        # `success=True` durchgereicht.
        if not "".join(accumulated).strip():
            self.api_logger.log_error(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                payload=payload,
                error_status="empty_writing_response",
            )
            yield DraftStreamEvent(
                kind="result",
                result=DraftingResult(
                    success=False,
                    blocked_reasons=[
                        "Die KI hat keinen verwertbaren Text zurückgegeben "
                        "(leere Antwort, möglicherweise durch das "
                        "Token-Limit abgeschnitten) - Entwurf wurde nicht "
                        "übernommen."
                    ],
                    open_review_points=open_review_points,
                ),
            )
            return

        if anomaly_reasons is not None:
            self.api_logger.log_error(
                db,
                workflow_id=matter_id,
                model=self.model_name,
                purpose=purpose,
                payload=payload,
                error_status="response_validation_failed",
            )
            yield DraftStreamEvent(
                kind="result",
                result=DraftingResult(
                    success=False,
                    blocked_reasons=[
                        "Lokale Prüfung der Antwort hat Auffälligkeiten festgestellt - "
                        "Entwurf wurde nicht übernommen.",
                        *anomaly_reasons,
                    ],
                    open_review_points=open_review_points,
                ),
            )
            return

        full_text = "".join(accumulated)
        with trace.step("reconstruction"):
            # No-Op (gateway_result.mappings ist hier immer leer) - aus
            # Konsistenzgruenden trotzdem ueber denselben Aufruf wie der
            # nicht-streamende Pfad, kein Sonderfall.
            reconstructed_text = self.gateway.reconstruct_response(
                full_text, gateway_result.mappings
            )

        draft = self._persist_draft(
            matter_id,
            reconstructed_text,
            purpose,
            db,
            actor=actor,
            previous_draft=previous_draft,
            message_id=prepared.message_id,
        )
        self._persist_reference_links(
            draft, prepared.source_list, prepared.knowledge_items_used, db
        )
        uncertainties = self._gather_uncertainties(matter_id, db)

        yield DraftStreamEvent(
            kind="result",
            result=DraftingResult(
                success=True,
                draft_id=draft.id,
                draft_text=reconstructed_text,
                source_list=prepared.source_list,
                knowledge_items_used=prepared.knowledge_items_used,
                open_review_points=open_review_points,
                uncertainties=uncertainties,
            ),
        )

    def _gather_legal_sources(
        self, matter: Matter, db: Session, *, actor: str
    ) -> tuple[list[SourceReference], list[str], list[str]]:
        research_results = self.research_service.research_for_matter(
            matter, db, actor=actor
        )

        source_list: list[SourceReference] = []
        quellen_texts: list[str] = []
        open_review_points: list[str] = []
        seen_source_ids: set[str] = set()

        for result in research_results:
            if not result.sufficiently_supported:
                open_review_points.append(
                    f"Nicht ausreichend belegt: '{result.query}' – manuelle "
                    "Rechtsrecherche erforderlich."
                )
            for finding in result.findings:
                quellen_texts.append(
                    f"{finding.title} ({finding.reference or 'ohne Fundstelle'}): "
                    f"{finding.snippet}"
                )
                if finding.source_id not in seen_source_ids:
                    seen_source_ids.add(finding.source_id)
                    source_list.append(
                        SourceReference(
                            source_id=finding.source_id,
                            title=finding.title,
                            reference=finding.reference,
                            url=finding.url,
                        )
                    )

        return source_list, quellen_texts, open_review_points

    def _gather_knowledge_items(
        self, matter: Matter, db: Session
    ) -> tuple[list[KnowledgeItemReference], list[str]]:
        query = matter.practice_area or matter.title
        results = self.search_service.search_knowledge_base(query, db)

        knowledge_items_used: list[KnowledgeItemReference] = []
        knowledge_texts: list[str] = []
        for result in results:
            item = db.query(KnowledgeItem).filter_by(id=result.entity_id).first()
            if item is None:
                continue  # verwaister Embedding-Eintrag - ueberspringen
            knowledge_items_used.append(
                KnowledgeItemReference(knowledge_item_id=item.id, title=item.title)
            )
            knowledge_texts.append(f"{item.title}: {result.snippet}")

        return knowledge_items_used, knowledge_texts

    def _persist_draft(
        self,
        matter_id: str,
        content: str,
        purpose: str,
        db: Session,
        *,
        actor: str,
        previous_draft: Draft | None = None,
        message_id: str | None = None,
    ) -> Draft:
        """Delegiert an `create_new_draft_version` (app/drafting/versioning.py) -
        siehe dort für die Begründung, warum das Anlegen neuer Draft-Zeilen
        an EINER zentralen Stelle gebündelt ist. `event_type` unterscheidet
        die allererste Version ("draft_created", unverändertes Verhalten)
        von einer Folgeversion durch Neugenerierung ("draft_version_created").

        `message_id` (17.09.): einfach durchgereicht an `create_new_draft_
        version`, das dieses Feld bereits unterstuetzt (inkl. automatischer
        Vererbung an Folgeversionen, falls hier nicht explizit gesetzt) -
        siehe DraftingService.create_draft für die volle Begründung.

        Markdown->HTML (05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY
        PASS" Phase D, live im Dokument-Analyse->Schriftsatz-Workflow
        gefundener Bug): `content` ist IMMER Claudes rohe, durchgaengig
        Markdown-formatierte Antwort (siehe app/drafting/
        markdown_to_draft_html.py für die volle Herleitung) - wird hier zu
        Editor-darstellbarem HTML gewandelt, `content_format` wird dabei
        IMMER explizit auf "html" gesetzt (nicht dem Default/einer evtl.
        geerbten "text"-Vorgaengerversion ueberlassen), damit Inhalt und
        Format niemals auseinanderlaufen.

        `status="chat_reference"` bei `purpose==_CHAT_PURPOSE` (05.10.,
        Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS", §11/§12 - live
        reproduziert: JEDE Chat-Nachricht, auch eine reine Analyse-/
        Zusammenfassungs-/Rueckfrage ohne jeden Schriftsatz-Intent, erzeugte
        bisher eine ganz normale "draft"-Zeile samt "Vollstaendigen Editor
        oeffnen"-Link und Eintrag in jeder Entwuerfe-Liste - obwohl
        `_looks_like_drafting_request` (app/chat/service.py) bereits
        zuverlaessig zwischen echtem Schriftsatz-Wunsch und normaler Frage
        unterscheidet. Root Cause war NICHT fehlende Intent-Erkennung
        (die existierte bereits), sondern dass ihr Ergebnis (`purpose`)
        nirgends die Draft-PERSISTENZ beeinflusste, nur den Prompt-Stil.
        Diese Draft-Zeile bleibt bewusst bestehen (keine Architekturaenderung,
        volle Wiederverwendung): sie ist weiterhin der einzige Ort, an dem
        `DraftSourceLink`/`DraftKnowledgeItemLink` (Quellen & Verweise im
        Chat, siehe chat_router.py::_gather_message_sources) haengen -
        wird aber per neuem Status "chat_reference" NIE als echter
        Schriftsatz gelistet (siehe Filter in drafts_router.py/
        matters_router.py) und chat.html zeigt dafuer KEINEN Editor-Link
        (siehe dortige Bedingung). Bei Folgeversionen (`previous_draft`
        gesetzt) wird nie "chat_reference" erzwungen - in der Praxis ruft
        der Chat `create_draft`/`-_stream` ohnehin nie mit `previous_draft`
        auf (jede Chat-Antwort ist strukturell eigenstaendig, siehe
        app/chat/service.py), ein echter Schriftsatz-Workflow (Regenerierung/
        Instruktion) bleibt dadurch vollstaendig unberuehrt."""
        event_type = "draft_created" if previous_draft is None else "draft_version_created"
        details = (
            f"Entwurf erstellt (Zweck: {purpose})"
            if previous_draft is None
            else f"Neue Version durch Neugenerierung (Zweck: {purpose})"
        )
        status = "chat_reference" if (purpose == _CHAT_PURPOSE and previous_draft is None) else "draft"
        return create_new_draft_version(
            db,
            matter_id=matter_id,
            content=render_ai_markdown_to_draft_html(content),
            previous_draft=previous_draft,
            message_id=message_id,
            actor=actor,
            event_type=event_type,
            details=details,
            content_format="html",
            status=status,
        )

    def _persist_reference_links(
        self,
        draft: Draft,
        source_list: list[SourceReference],
        knowledge_items_used: list[KnowledgeItemReference],
        db: Session,
    ) -> None:
        """Hält fest, welche Quellen/Wissenselemente TATSÄCHLICH für genau
        DIESE Version verwendet wurden (Prompt 24) - siehe Moduldocstring
        in app/models/draft_reference_links.py für die Begründung, warum
        das persistiert statt nur transient zurückgegeben wird."""
        for source_ref in source_list:
            db.add(DraftSourceLink(draft_id=draft.id, source_id=source_ref.source_id))
        for knowledge_ref in knowledge_items_used:
            db.add(
                DraftKnowledgeItemLink(
                    draft_id=draft.id, knowledge_item_id=knowledge_ref.knowledge_item_id
                )
            )
        if source_list or knowledge_items_used:
            db.commit()

    def _gather_uncertainties(self, matter_id: str, db: Session) -> list[str]:
        uncertainties: list[str] = []
        unreviewed_deadlines = (
            db.query(Deadline)
            .filter(Deadline.matter_id == matter_id, Deadline.review_status == "unreviewed")
            .all()
        )
        if unreviewed_deadlines:
            uncertainties.append(
                f"{len(unreviewed_deadlines)} unbestätigte Frist(en) in dieser Akte - "
                "vor Freigabe prüfen."
            )
        return uncertainties
