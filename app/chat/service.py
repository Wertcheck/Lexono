"""ChatService – Persistenz + Orchestrierung fuer die Chat-Startseite.

Verantwortlich fuer:
1. Konversationen anlegen/auflisten (`create_conversation`,
   `list_conversations`) - jede Konversation ist IMMER an eine Akte
   gebunden (Aktenisolation), Auto-Anlage ueber
   `app/drafting/quick_matter.py::create_quick_matter` - identisches,
   bereits bewaehrtes Muster wie beim Schriftsatz-Generator, keine zweite
   Implementierung.
2. Dokumente an eine Konversation anhaengen (`attach_document`) - nutzt
   DENSELBEN sicheren Speicher-/Verarbeitungsweg wie
   `app/web/schriftsatz_router.py::_store_uploaded_document`
   (Pfad-Traversal-sichere Dateinamen-Behandlung, siehe dortiger
   Sicherheits-Fund) + die unveraenderte `DocumentProcessingService`
   (OCR/Textextraktion).
3. Eine Nachricht senden + KI-Antwort erzeugen (`send_message`) - ruft
   AUSSCHLIESSLICH `DraftingService.create_draft` auf (app/drafting/
   service.py) - denselben Weg, der bereits durch `ClaudePrivacyGateway`
   (Pseudonymisierung + Security-Check + Final Payload Gate) und alle
   bestehenden Tests abgesichert ist. KEIN direkter Cloud-Aufruf, KEIN
   zweiter Privacy-Pfad.

`content` einer Nutzernachricht wird 1:1 als `attorney_anmerkungen`
(siebtes Allowlist-Feld, siehe app/privacy/gateway_schema.py) an
`create_draft` uebergeben - durchlaeuft daher GENAU DENSELBEN
Pseudonymisierungs-/Security-Check-Durchlauf wie jede andere anwaltliche
Anmerkung im System, keine Sonderbehandlung.

4. Gespraechsverlauf mitgeben (CHAT-02, 15.09., `_build_history`) - die
   bis zu zehn juengsten User-/Assistant-Nachrichten DIESER Konversation
   (Owner-Entscheidung A/C/D, siehe .agentic/DECISIONS.md), begrenzt auf
   3.000/12.000 Zeichen (Owner-Entscheidung B), werden als achtes
   Allowlist-Feld (`anonymisierter_gespraechsverlauf`) an `create_draft`
   uebergeben - GENAU DERSELBE gemeinsame Pseudonymisierungs-Durchlauf,
   keine zweite Pseudonymisierung. Behebt den in der Chat-Intelligence-
   Forensik (15.09.) belegten Root Cause, dass das Modell fruehere Turns
   strukturell nie erreichte.
5. Aktenbestand-Fastpath (16.09., siehe `_find_most_recently_active_matter`) -
   eine reine Bestandsfrage ("Was ist die aktuellste Akte?") wird lokal aus
   der DB beantwortet, ohne Presidio-/Local-AI-/Claude-Aufruf: der uebrige
   Drafting-Pfad filtert (Aktenisolation) IMMER strikt auf genau eine
   Akte und kann eine aktenuebergreifende Bestandsfrage daher strukturell
   nie beantworten. Behebt den in der Chat-Intelligence-Forensik (14.09.)
   belegten Root Cause ("es existiert keine Abfrage ueber den
   Aktenbestand")."""

from __future__ import annotations

import mimetypes
import re
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.chat.title_generation import generate_conversation_title
from app.documents.service import DocumentProcessingService
from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME, create_quick_matter
from app.drafting.service import DraftingService
from app.ingestion.stability import compute_sha256
from app.models import (
    ChatConversation,
    ChatMessage,
    ChatMessageDocument,
    Client,
    Document,
    Law,
    LawSection,
    Matter,
    Message,
    User,
)
from app.observability.perf_trace import PerfTrace
from app.privacy.api_logger import friendly_block_message

#: ECHTER FUND (realer Abnahme-Test, 13.09.): der Chat rief
#: `drafting_service.create_draft` bisher IMMER mit `_PURPOSE_DRAFT`
#: ("formulate_draft") auf, unabhaengig vom tatsaechlichen Nutzerinhalt -
#: eine normale Frage wie "Was steht in § 558 BGB?" erzeugte dadurch einen
#: formellen Briefentwurf mit Betreff/Anrede statt einer normalen
#: inhaltlichen Antwort. Root Cause war, dass es fuer den Chat nur DIESEN
#: einen, fest auf Briefe ausgelegten Zweck gab - kein fehlerhaftes
#: Routing im eigentlichen Sinne, sondern schlicht keine Alternative.
#:
#: Kein Freitextfeld fuer den "purpose" (bleibt bei genau diesen zwei
#: festen Werten) - um versehentlich einen nicht erlaubten Zweck zu
#: erzeugen (siehe ALLOWED_PURPOSES, app/privacy/security_check.py).
_PURPOSE_DRAFT = "formulate_draft"
_PURPOSE_CHAT = "chat_response"

#: Bewusst regelbasiert (kein LLM-Aufruf, kein neues Intent-System) -
#: erkennt eine explizite Aufforderung, ein NEUES formelles Schreiben/
#: einen Schriftsatz zu ERSTELLEN. Jedes Muster verlangt ein Erstellungs-
#: Verb ("schreib(e)", "verfass(e)", "formulier(e)", "erstell(e)",
#: "entwerf(e)") in der Naehe (<=40 Zeichen) eines Formell-Dokument-
#: Substantivs ("schriftsatz", "antwortschreiben", "klage", "entwurf",
#: "einspruch", "widerspruch", "beschwerde") oder der Wendung "antwort
#: an"/"schreiben an" (= ein Schreiben AN jemanden richten, nicht ein
#: bestehendes "Schreiben" nur ANALYSIEREN/PRUEFEN).
#: Bewusst NICHT nur auf das Verb allein ("formuliere") geprueft - "Formuliere
#: diesen Absatz verstaendlicher" (Textueberarbeitung, siehe Testfaelle)
#: darf NICHT triggern, da kein Formell-Dokument-Substantiv folgt. Default
#: bei Uneindeutigkeit ist IMMER Chat, nie Drafting (Vorgabe: "Wenn nicht
#: eindeutig: NICHT Drafting").
#:
#: ECHTER FUND (P0 Performance+Routing-Root-Cause-Run, 13.09.): das
#: EXPLIZITE Auftragsbeispiel "Erstelle daraus einen Einspruch." (aus
#: genau diesem Auftrag als Pflicht-Testfall CASE C vorgegeben) wurde von
#: dieser Liste bisher NICHT erkannt - "Einspruch"/"Widerspruch"/
#: "Beschwerde" sind im deutschen Verwaltungs-/Steuerrecht ebenso formelle,
#: an eine Behoerde/Gegenseite gerichtete Schriftstuecke wie "Klage" (bereits
#: in der Liste) - real reproduziert: die Anfrage wurde faelschlich als
#: chat_response geroutet, Claude antwortete mit einer verwirrten
#: Zwischenform ("Ich kann noch keinen Einspruch erstellen...") statt eines
#: echten Entwurfs. Ergaenzt, keine neue Architektur.
_DRAFTING_TRIGGER_PATTERN = re.compile(
    r"\b(schreib\w*|verfass\w*|formulier\w*|erstell\w*|entwerf\w*)\b"
    r".{0,40}?"
    r"\b(schriftsatz\w*|antwortschreiben\w*|klage\w*|entwurf\w*|einspruch\w*|"
    r"widerspruch\w*|beschwerde\w*|antwort an|schreiben an)\b",
    re.IGNORECASE,
)


def _looks_like_drafting_request(content: str) -> bool:
    """Entscheidet CHAT_FIRST/DRAFTING_ONLY_WHEN_REQUESTED fuer EINE
    einzelne Nachricht - bewusst nur der AKTUELLE Nachrichtentext, keine
    Konversationshistorie/Session-State: eine vorherige Drafting-Anfrage
    darf eine unabhaengige Folgefrage ("Und warum?") nicht automatisch
    ebenfalls zu Drafting machen (siehe Testfaelle)."""
    return bool(_DRAFTING_TRIGGER_PATTERN.search(content))


#: Erkennt eine REINE Normfrage - Erkennungsverb (optional) + "§ NNN" ODER
#: "Art(ikel) NNN" (Artikel-basierte Gesetze wie das GG, ECHTER FUND
#: 13.09. beim Import des GG - dortige Normen haben KEIN "§"-Zeichen) +
#: Gesetzeskuerzel + NICHTS weiter (bis auf Satzzeichen). Bewusst als
#: GANZES-Nachricht-Anker (^...$), NICHT als blosses ".search()" irgendwo
#: im Text - eine Nachricht wie "Was steht in § 558 BGB, und wie wirkt
#: sich das auf meinen Mandanten Max Mustermann aus?" darf NIEMALS
#: treffen (enthaelt echten Fallbezug/Namen), nur eine Nachricht, die
#: AUSSCHLIESSLICH aus der Zitatfrage besteht. Siehe
#: app/chat/service.py::_answer_pure_norm_question fuer die Begruendung,
#: warum dieser enge Anker die Voraussetzung fuer den Claude-freien,
#: praesidio-freien Direktantwort-Pfad ist (P0 Performance/Legal-
#: Knowledge-Anbindung, 13.09.).
_NORM_QUESTION_PATTERN = re.compile(
    r"^(?:(?:was\s+steht\s+in|was\s+besagt|was\s+regelt|erkl(?:ä|ae)r\w*(?:\s+mir)?|"
    r"zeig(?:e)?\s+mir|nenne\s+mir\s+den\s+inhalt\s+von)\s+)?"
    r"(?:§\s*(\d+[a-z]?)|Art(?:ikel)?\.?\s*(\d+[a-z]?))\s+"
    r"(SGB\s*[IVXLCivxlc]{1,5}|SGB\s*\d{1,2}|[a-zA-ZÄÖÜäöüß]{2,10})"
    r"\s*[\.\?!]*$",
    re.IGNORECASE,
)

#: Mehrteilige Gesetzeskuerzel mit eingebettetem Leerzeichen (14.09.,
#: SGB-Import): die uebliche Zitierweise ist "§ 7 SGB II" bzw. "§ 7 SGB 2"
#: (MIT Leerzeichen zwischen "SGB" und der Buch-Nummer) - der generische
#: `[a-zA-ZÄÖÜäöüß]{2,10}`-Zweig oben deckt das NICHT ab (kein Leerzeichen,
#: keine Ziffern erlaubt). `_normalize_law_code` fasst beide Schreibweisen
#: auf denselben gespeicherten `law_code` zusammen ("SGBI".."SGBXII" - reine
#: Buchstaben, konsistent mit allen anderen Gesetzeskuerzeln, siehe
#: scripts/import_gesetze_im_internet.py::_CODE_OVERRIDES fuer die
#: Gegenseite beim Import).
_ARABIC_TO_ROMAN_SGB_BOOK = {
    "1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI",
    "7": "VII", "8": "VIII", "9": "IX", "10": "X", "11": "XI", "12": "XII",
}


def _normalize_law_code(raw_code: str) -> str:
    code = raw_code.strip().upper()
    if code.startswith("SGB"):
        book = code[3:].strip()
        if book.isdigit():
            book = _ARABIC_TO_ROMAN_SGB_BOOK.get(book, book)
        return f"SGB{book}"
    return code


def _looks_like_pure_norm_question(content: str) -> tuple[str, str] | None:
    """Gibt (Gesetzeskuerzel, Paragraphen-/Artikelnummer) zurueck, WENN die
    GESAMTE (getrimmte) Nachricht ausschliesslich aus einer einfachen
    Normzitat-Frage besteht - sonst None (dann laeuft die volle,
    unveraenderte Pipeline weiter, siehe send_message)."""
    match = _NORM_QUESTION_PATTERN.match(content.strip())
    if not match:
        return None
    number = match.group(1) or match.group(2)
    return _normalize_law_code(match.group(3)), number


def _find_law_section(db: Session, *, law_code: str, section_number: str) -> LawSection | None:
    """Sucht die lokal bereits vorhandene, zitierfaehige Einzelnorm - EXAKTE
    Uebereinstimmung des Paragraphen-/Artikelzeichens reicht nicht (der
    Nutzertext hat kein "§ "/"Art "-Praefix, `LawSection.section_number`
    aber schon, z. B. "§ 558" oder "Art 12a"), daher der Vergleich ueber
    die reine Nummer. Liefert None, wenn das Gesetz/die Norm lokal (noch)
    nicht importiert ist - der Aufrufer (send_message) faellt dann auf die
    volle, unveraenderte Pipeline zurueck, KEIN Fehler/Abbruch.

    `Law.is_active`-Filter (26.09., Owner-Direktive "KANZLEIWISSEN FINAL
    PRODUCT IMPLEMENTATION" §20): ein vom Anwalt in Kanzleiwissen
    DEAKTIVIERTES Gesetz darf hier nicht mehr gefunden werden, obwohl die
    Paragraphen technisch noch in der DB liegen (Deaktivieren loescht
    keine Daten, siehe app/laws/service.py::toggle_law_active) - dieselbe
    "faellt transparent auf die volle Pipeline zurueck"-Behandlung wie ein
    noch nie importiertes Gesetz, kein Fehler/Sonderfall."""
    candidates = (
        db.query(LawSection)
        .join(Law, Law.code == LawSection.law_code)
        .filter(LawSection.law_code == law_code, Law.is_active.is_(True))
        .all()
    )
    for candidate in candidates:
        match = _ENBEZ_SECTION_NUMBER_RE.search(candidate.section_number)
        if not match:
            continue
        number = match.group(1) or match.group(2)
        if number.lower() == section_number.lower():
            return candidate
    return None


_ENBEZ_SECTION_NUMBER_RE = re.compile(r"§\s*(\w+)|Art(?:ikel)?\.?\s*(\w+)", re.IGNORECASE)


def _format_norm_answer(section: LawSection) -> str:
    """Baut eine ehrliche Direktantwort aus dem lokal vorliegenden amtlichen
    Normtext - KEIN Claude-Aufruf, KEINE Interpretation/Zusammenfassung durch
    ein Modell, nur der amtliche Wortlaut + ein kurzer, wahrheitsgemaesser
    Hinweis, woher er stammt (siehe CLAUDE.md: "Niemals Rechtsquellen ...
    erfinden" - hier gibt es nichts zu erfinden, da 1:1 der importierte
    amtliche Text wiedergegeben wird)."""
    heading = f"{section.section_number} {section.law_code}"
    if section.title:
        heading = f"{heading} – {section.title}"
    return (
        f"{heading}\n\n{section.text_content}\n\n"
        f"Quelle: {section.source_name}"
        + (f", Stand: {section.last_updated}" if section.last_updated else "")
        + "."
    )


# --- Aktenbestand-Fastpath (16.09., Diagnose vom 14.09. umgesetzt) --------
# ECHTER FUND (14.09., Chat-Intelligence-Forensik, siehe OPEN_ISSUES.md
# "P1 - Chat kennt den AKTENBESTAND nicht"): eine Frage wie "Was ist die
# aktuellste Akte?" konnte nie beantwortet werden, weil der gesamte
# Drafting-Pfad IMMER strikt auf genau eine `matter_id` filtert
# (Aktenisolation) - ein Bestandsueberblick ueber MEHRERE Akten hat dort
# strukturell keinen Platz. Analog zum bereits bestehenden Norm-Fast-Path
# oben: eine eng begrenzte, ausschliesslich aus einer reinen
# Bestandsfrage bestehende Nachricht wird HIER lokal aus der DB
# beantwortet - kein Presidio-Lauf, kein Local-AI-Aufruf, kein
# Claude-Aufruf, da keine Akteninhalte verarbeitet werden, nur Metadaten
# (Titel/Aktenzeichen/Mandant/letzte Aktivitaet) der ohnehin fuer den
# angemeldeten Nutzer sichtbaren Akten-Uebersicht (dieselbe Sichtbarkeit
# wie `/dashboard/matters`, keine neue Datenfreigabe). KEINE Verletzung
# der Aktenisolation: die Antwort nennt ausschliesslich Metadaten GENAU
# EINER Akte (der zuletzt bewegten), nie Inhalte mehrerer Akten
# gleichzeitig.
_RECENT_MATTER_QUESTION_PATTERN = re.compile(
    r"^(?:"
    r"(?:was\s+ist|was\s+war|zeig(?:e)?\s+mir)\s+(?:die\s+|meine\s+)?"
    r"(?:aktuellste|neueste|letzte)\s+akte"
    r"|"
    r"welche\s+akte\s+ist\s+(?:die\s+)?(?:aktuellste|neueste|letzte)"
    r")\s*[\.\?!]?\s*$",
    re.IGNORECASE,
)


def _looks_like_recent_matter_question(content: str) -> bool:
    """Wie `_looks_like_pure_norm_question`: nur bei einer Nachricht, die
    AUSSCHLIESSLICH aus der Bestandsfrage besteht (Ganzes-Nachricht-Anker)
    - eine Nachricht mit echtem Fallbezug daneben ("...und betrifft das
    auch meinen Mandanten X?") darf NIEMALS treffen und faellt auf die
    volle Pipeline zurueck."""
    return bool(_RECENT_MATTER_QUESTION_PATTERN.match(content.strip()))


def _find_most_recently_active_matter(db: Session) -> tuple[Matter, datetime] | None:
    """Liefert die Akte mit der juengsten tatsaechlichen Aktivitaet
    (juengste E-Mail/Nachricht ODER juengstes Dokument in der Akte, sonst
    - nur falls eine Akte noch KEIN einziges Dokument/keine Nachricht hat -
    ihr eigenes `created_at` als Rueckfallwert).

    ECHTER FUND (16.09., beim Schreiben des Regressionstests fuer diese
    Funktion): der erste Entwurf nahm `Matter.updated_at` bewusst mit in
    den `max()`-Vergleich auf ("Statusaenderung zaehlt auch als
    Aktivitaet") - das widersprach der eigenen Diagnose vom 14.09.
    ("`updated_at` bildet KEINE Aktivitaet ab und waere als Antwort
    irrefuehrend", siehe OPEN_ISSUES.md) und fuehrte real zu einem
    falschen Ergebnis im Test: JEDE frisch anlegte `Matter`-Zeile bekommt
    `updated_at` = tatsaechliche Wanduhrzeit beim INSERT, unabhaengig
    davon, wie alt ihre (nachtraeglich mit einem aelteren Datum
    versehenen) Dokumente/Nachrichten sind - dadurch gewann in einem
    3-Akten-Testfall zuverlaessig die Akte, die im Test einfach ALS
    LETZTE angelegt wurde, statt die mit der tatsaechlich juengsten
    Nachricht. `updated_at` fliesst deshalb NICHT mehr ein; nur `created_at`
    bleibt als Rueckfallwert fuer Akten OHNE jedes Dokument/jede Nachricht.

    Schliesst Akten des Sammel-Platzhalter-Mandanten ("Ohne
    Mandantenzuordnung", siehe app/drafting/quick_matter.py) bewusst AUS:
    das sind Schnellentwuerfe ohne echten Aktenbezug (u. a. entsteht bei
    JEDER Konversation ohne gewaehlte Akte automatisch eine neue - siehe
    ChatService.create_conversation), keine "echte" Akte im Sinne der
    Frage. Ohne diesen Ausschluss waere die Antwort auf "was ist die
    aktuellste Akte?" andernfalls trivial/nutzlos: fast immer exakt die
    Akte, die durch das Stellen der Frage selbst gerade erst angelegt
    wurde."""
    matters = (
        db.query(Matter)
        .join(Client, Matter.client_id == Client.id)
        .filter(Client.name != PLACEHOLDER_CLIENT_NAME)
        .all()
    )
    if not matters:
        return None

    message_activity = dict(
        db.query(Message.matter_id, func.max(Message.created_at))
        .filter(Message.matter_id.isnot(None))
        .group_by(Message.matter_id)
        .all()
    )
    document_activity = dict(
        db.query(Document.matter_id, func.max(Document.created_at))
        .filter(Document.matter_id.isnot(None))
        .group_by(Document.matter_id)
        .all()
    )

    def _activity(matter: Matter) -> datetime:
        candidates = [
            message_activity.get(matter.id),
            document_activity.get(matter.id),
        ]
        real_candidates = [c for c in candidates if c is not None]
        if real_candidates:
            return max(real_candidates)
        return matter.created_at

    best = max(matters, key=_activity)
    return best, _activity(best)


def _format_recent_matter_answer(matter: Matter, *, last_activity: datetime) -> str:
    """Ehrliche Direktantwort aus lokalen Metadaten - KEIN Claude-Aufruf,
    KEINE Interpretation, nur Fakten aus der DB (analog `_format_norm_
    answer`). Nennt bewusst nur die EINE gefundene Akte, keine weiteren
    Kandidaten/Alternativen (Aktenisolation: keine Vermischung von
    Metadaten mehrerer Akten in einer Antwort)."""
    reference = f" (Az. {matter.reference_number})" if matter.reference_number else ""
    return (
        f'Die zuletzt bewegte Akte ist "{matter.title}"{reference}, '
        f"Mandant: {matter.client.name}. "
        f"Letzte Aktivität: {last_activity.strftime('%d.%m.%Y')}.\n\n"
        '"Zuletzt bewegt" bedeutet die jüngste Nachricht oder das jüngste '
        "Dokument in dieser Akte (nicht zwingend das Anlagedatum)."
    )


@dataclass(frozen=True)
class ChatStreamEvent:
    """Ein Ereignis aus `ChatService.send_message_stream` (13.09.,
    Streaming-Architekturentscheidung) - `kind="delta"` traegt ein
    inkrementelles Text-Stueck (bereits vollstaendig lokal rekonstruiert,
    NIE ein Platzhalter-Mapping, siehe DraftingService.create_draft_stream),
    `kind="status"` (P1 Performance-Feedback-Follow-up, 17.09., siehe
    DraftingService._STEP_STATUS_LABELS) traegt einen festen,
    inhaltsfreien Fortschritts-Hinweis - rein informativ, beliebig viele
    pro Aufruf, KEIN Ersatz fuer "done" - `kind="done"` traegt die bereits
    persistierte, endgueltige `ChatMessage` (GENAU EIN "done"-Ereignis pro
    Aufruf, immer als letztes) - der Router rendert daraus die "Quellen &
    Verweise"-Karte identisch zum nicht-streamenden Pfad."""

    kind: str
    text: str = ""
    status: str = ""
    message: ChatMessage | None = None


_PROVIDER_UNAVAILABLE_MESSAGE = (
    "Die Cloud-KI ist nicht konfiguriert (kein Anthropic-API-Schlüssel hinterlegt). "
    "Bitte eine Administratorin oder einen Administrator kontaktieren, bevor Sie den "
    "Chat für KI-Antworten nutzen können."
)

_UNEXPECTED_ERROR_MESSAGE = (
    "Bei der Verarbeitung ist ein unerwarteter technischer Fehler aufgetreten. "
    "Die Anfrage wurde nicht an die Cloud-KI gesendet. Bitte erneut versuchen."
)

#: Gleiches Limit wie der Schriftsatz-Generator (app/web/schriftsatz_router.py)
#: - Groessenpruefung erfolgt HIER (nicht vorab im Router), weil erst hier
#: tatsaechlich gelesen wird (`UploadFile.size` ist bei manchen Clients
#: nicht zuverlaessig vorab gesetzt).
_MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB pro Datei

# --- CHAT-02 (15.09.): Gespraechsverlauf-Budget -----------------------------
# Owner-Entscheidung (finale Freigabe, 15.09.), NICHT selbst gewaehlt - siehe
# .agentic/DECISIONS.md fuer die vollstaendige Herleitung/den Entscheidungs-
# prozess (CHAT-INT-DIAG-Forensik -> vier offene Produktfragen -> Owner-
# Entscheidung). Prioritaet bei der Auswahl (Owner-Vorgabe, woertlich):
# 1. aktuelle Conversation, 2. juengste Messages, 3. Character Budget.
_MAX_HISTORY_MESSAGES = 10
_MAX_HISTORY_CHARS_PER_MESSAGE = 3_000
_MAX_HISTORY_CHARS_TOTAL = 12_000

#: Formatierung je History-Eintrag ("Rolle: Text") - identisches Muster wie
#: `_LOCAL_LLM_ARGUMENTATIONSPUNKT_PREFIX` in app/drafting/service.py.
#:
#: ECHTER FUND (15.09., beim Testen dieses Codes): der naheliegende erste
#: Versuch nutzte hier "Lexono" (der Produktname) als Label fuer die
#: eigene vorherige Antwort - Presidios deutsches NER-Modell erkennt
#: "Lexono" als PERSON-Entitaet (kapitalisiertes, namensartiges Wort) und
#: haette es pseudonymisiert (z. B. "[PERSON_01]: ..." statt
#: "Lexono: ...") - kein Datenschutzproblem (Ueberpseudonymisierung ist
#: sicher, nicht gefaehrlich), aber es haette die Rollenkennzeichnung
#: unbrauchbar gemacht. "Assistent" ist ein normales deutsches
#: Rollen-/Berufswort (wie "Anwalt") und wird von Presidio NICHT als
#: Person erkannt - real mit `.agentic/memos/chatdiag_harness/` verwandten
#: Ad-hoc-Tests bestaetigt.
_HISTORY_ROLE_LABELS = {"user": "Anwalt", "assistant": "Assistent"}


#: ECHTER FUND behoben (07.10., Owner-Direktive "CHAT-HISTORY-MANAGEMENT
#: ERWEITERN" §1): dieser Name bezeichnete bisher eine reine
#: Whitespace-Normalisierung + 60-Zeichen-Abschneidung - die ersten Woerter
#: der Nachricht wurden also woertlich zum Titel, unabhaengig vom Thema.
#: Jetzt ein duenner Alias auf die neue, semantische Titel-Ableitung
#: (app/chat/title_generation.py) - eigener Name hier beibehalten, damit
#: der einzige Aufrufer unten unveraendert bleibt.
_derive_title = generate_conversation_title


class ChatService:
    def __init__(self, storage_dir: Path | str) -> None:
        self.storage_dir = Path(storage_dir)

    # --- Konversationen -------------------------------------------------

    def create_conversation(
        self,
        db: Session,
        *,
        user: User,
        matter_id: str | None,
        new_matter_title: str | None = None,
        new_client_name: str | None = None,
        title: str,
        actor: str,
    ) -> ChatConversation:
        if not matter_id:
            matter = create_quick_matter(
                db, title=new_matter_title, client_name=new_client_name, actor=actor
            )
            matter_id = matter.id
        conversation = ChatConversation(
            matter_id=matter_id, user_id=user.id, title=_derive_title(title)
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    def list_conversations(self, db: Session, *, user: User) -> list[ChatConversation]:
        return (
            db.query(ChatConversation)
            .filter(ChatConversation.user_id == user.id)
            .order_by(ChatConversation.updated_at.desc())
            .all()
        )

    def get_conversation(self, db: Session, conversation_id: str) -> ChatConversation | None:
        return db.query(ChatConversation).filter_by(id=conversation_id).first()

    # --- Dokumente --------------------------------------------------------

    def attach_document(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        upload: UploadFile,
        ocr_enabled: bool,
        ocr_languages: str,
        tesseract_cmd: str | None,
        actor: str,
    ) -> Document | None:
        if not upload.filename:
            return None

        content = upload.file.read()
        if len(content) > _MAX_UPLOAD_SIZE_BYTES:
            raise ValueError(
                f"Datei '{upload.filename}' überschreitet die maximale Größe von "
                f"{_MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
            )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # SICHERHEITSKRITISCH (Path Traversal, siehe Pilot Readiness Review
        # + app/web/schriftsatz_router.py::_store_uploaded_document fuer den
        # real gefundenen und behobenen Fund): NIEMALS `upload.filename`
        # ungefiltert in den Zielpfad einbauen - `.name` behaelt nur den
        # letzten Pfadbestandteil.
        safe_filename = Path(upload.filename).name or "unbenannt"
        destination_path = self.storage_dir / f"{uuid.uuid4()}_{safe_filename}"
        destination_path.write_bytes(content)

        mime_type, _ = mimetypes.guess_type(upload.filename)
        document = Document(
            matter_id=conversation.matter_id,
            file_path=str(destination_path),
            original_filename=upload.filename,
            content_hash=compute_sha256(destination_path),
            mime_type=mime_type,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        processor = DocumentProcessingService(
            ocr_enabled=ocr_enabled, ocr_languages=ocr_languages, tesseract_cmd=tesseract_cmd
        )
        processor.process_document(document, db, actor=actor)
        return document

    # --- Nachrichten --------------------------------------------------------

    def _create_message(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        role: str,
        content: str,
        blocked: bool = False,
        draft_id: str | None = None,
        law_section_id: str | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            conversation_id=conversation.id,
            role=role,
            content=content,
            blocked=blocked,
            draft_id=draft_id,
            law_section_id=law_section_id,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    def get_attached_document(
        self, db: Session, *, conversation: ChatConversation, document_id: str
    ) -> Document | None:
        """Laedt ein Dokument NUR, wenn es tatsaechlich an eine Nachricht
        DIESER Konversation angehaengt ist (Dokument-Workspace, Masterprompt
        V2, Task #62) - verhindert, dass ueber eine erratene/manipulierte
        Dokument-ID ein Dokument aus einer fremden Konversation/Akte
        abgerufen werden kann (Aktenisolation, CLAUDE.md "Aktenkontext
        strikt isolieren"). Der Aufrufer (chat_router.py) hat bereits
        vorher per `_require_own_conversation` sichergestellt, dass die
        Konversation selbst dem angemeldeten Nutzer gehoert."""
        return (
            db.query(Document)
            .join(ChatMessageDocument, ChatMessageDocument.document_id == Document.id)
            .join(ChatMessage, ChatMessage.id == ChatMessageDocument.message_id)
            .filter(
                ChatMessage.conversation_id == conversation.id,
                Document.id == document_id,
                Document.deleted_at.is_(None),
            )
            .first()
        )

    def record_user_message(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        content: str,
        document_ids: list[str] | None = None,
    ) -> ChatMessage:
        message = self._create_message(db, conversation=conversation, role="user", content=content)
        for document_id in document_ids or []:
            db.add(ChatMessageDocument(message_id=message.id, document_id=document_id))
        if document_ids:
            db.commit()
        return message

    def _build_history(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        exclude_message_id: str | None,
    ) -> list[str]:
        """Baut den zu uebertragenden Gespraechsverlauf (CHAT-02, achtes
        Allowlist-Feld, siehe app/privacy/gateway_schema.py) - GEMEINSAM
        von `send_message`/`send_message_stream` genutzt (Owner-Vorgabe
        §5: "Vermeide zwei unabhaengige Implementierungen derselben
        History-Logik").

        VERIFIZIERT AM CODE (nicht angenommen, Owner-Vorgabe "CURRENT
        MESSAGE"): der Router persistiert die aktuelle Nutzernachricht
        bereits VOR dem Aufruf von `send_message`/`send_message_stream`
        (`record_user_message`, siehe app/web/chat_router.py) - ohne
        `exclude_message_id` waere die gerade gestellte Frage selbst
        bereits Teil der "juengsten Nachrichten dieser Conversation" und
        wuerde damit DOPPELT im Payload landen (einmal als History, einmal
        als aktuelle Nachricht). `exclude_message_id` ist deshalb keine
        Bequemlichkeit, sondern die einzige zuverlaessige Art, das zu
        verhindern - ein Zeitstempel-Vergleich waere bei sehr schnell
        aufeinanderfolgenden Anfragen nicht sicher eindeutig.

        Auswahl-/Kuerzungslogik (Owner-Entscheidungen A/B/C/D, 15.09.,
        Prioritaet laut Owner-Vorgabe: 1. aktuelle Conversation,
        2. juengste Messages, 3. Character Budget):
        1. NUR diese `conversation` (Session Scope D) - Query ist bereits
           auf `conversation_id` gefiltert, dieselbe Isolationsgrenze wie
           ueberall sonst im Produkt (Aktenisolation).
        2. NUR `role in (user, assistant)` (Message Roles C) UND nicht
           blockierte Nachrichten - eine blockierte Nachricht traegt als
           `content` eine inhaltsfreie Fehlermeldung
           (`friendly_block_message`), kein echter Gespraechsbeitrag.
        3. Die `_MAX_HISTORY_MESSAGES` juengsten (Turn Budget A) werden per
           SQL `ORDER BY created_at DESC LIMIT N` ermittelt - Rekonstruktion
           weiter zurueckliegender Nachrichten ist strukturell ausgeschlossen.
        4. Jede Nachricht wird auf `_MAX_HISTORY_CHARS_PER_MESSAGE` Zeichen
           gekappt (deterministisch: die ERSTEN N Zeichen, gleiches Muster
           wie `_MAX_DOCUMENT_EXCERPT_CHARS` in
           app/ai_providers/local_ai_provider.py) - eine einzelne sehr
           lange Nachricht kann dadurch nicht das gesamte Gesamtbudget
           verbrauchen (Owner-Vorgabe B, woertlich).
        5. Ab der (bereits nach Aktualitaet sortierten) Liste wird von
           NEU nach ALT aufsummiert, bis das naechste Element das
           `_MAX_HISTORY_CHARS_TOTAL`-Gesamtbudget ueberschreiten wuerde -
           dann STOPPT die Auswahl (aeltere Nachrichten werden NICHT
           uebersprungen zugunsten kleinerer spaeterer). Das Ergebnis ist
           damit immer ein LUECKENLOSER, zusammenhaengender juengster
           Ausschnitt der Conversation - nie eine "loechrige" Auswahl -
           und erfuellt Owner-Prioritaet 2 vor 3 (juengste Messages vor
           Character Budget: die Auswahl bevorzugt IMMER Aktualitaet,
           das Budget entscheidet nur, WIE VIELE der juengsten Nachrichten
           noch hineinpassen).
        6. Das Ergebnis wird in chronologische Reihenfolge (alt -> neu)
           zurueckgedreht - so liest sich der Verlauf fuer Claude wie ein
           echtes Gespraech.

        Rueckgabe: bereits als "Rolle: Text"-Zeilen formatierte Strings
        (siehe `_HISTORY_ROLE_LABELS`) - NOCH NICHT pseudonymisiert, das
        uebernimmt GENAU DER BESTEHENDE, gemeinsame Pseudonymisierungslauf
        in `ClaudePrivacyGateway.prepare_request` (app/privacy/gateway.py),
        identisch zu jedem anderen Feld. Das gilt ausdruecklich auch fuer
        Assistant-Eintraege: `ChatMessage.content` einer Assistant-Zeile
        ist bereits lokal REKONSTRUIERTER Klartext (siehe
        app/models/chat_message.py) - er durchlaeuft hier folglich ERNEUT
        eine vollstaendige Pseudonymisierung, es gibt keinen "bereits
        sicher"-Fast-Path an Presidio vorbei."""
        query = (
            db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conversation.id)
            .filter(ChatMessage.role.in_(("user", "assistant")))
            .filter(ChatMessage.blocked.is_(False))
        )
        if exclude_message_id is not None:
            query = query.filter(ChatMessage.id != exclude_message_id)

        newest_first = (
            query.order_by(ChatMessage.created_at.desc())
            .limit(_MAX_HISTORY_MESSAGES)
            .all()
        )

        # ECHTER FUND (15.09., beim Testen dieses Codes): das Gesamtbudget
        # muss die tatsaechlich UEBERTRAGENE Zeichenlaenge zaehlen (inkl.
        # "Rolle: "-Praefix), nicht nur die Laenge des reinen
        # Nachrichteninhalts - sonst kann die Summe der tatsaechlich
        # gesendeten Zeilen das Owner-Budget (12.000 Zeichen) trotz
        # bestandener Pruefung ueberschreiten (real gemessen: 4 Nachrichten
        # a genau 3.000 Zeichen Inhalt + "Anwalt: "-Praefix ergaben 12.032
        # tatsaechlich gesendete Zeichen, 32 ueber dem Budget). Deshalb wird
        # hier die FERTIG FORMATIERTE Zeile gebildet und GENAU DIESE Laenge
        # fuer die Budgetpruefung verwendet.
        selected_entries: list[str] = []
        total_chars = 0
        for message in newest_first:
            entry = (
                f"{_HISTORY_ROLE_LABELS[message.role]}: "
                f"{message.content[:_MAX_HISTORY_CHARS_PER_MESSAGE]}"
            )
            if total_chars + len(entry) > _MAX_HISTORY_CHARS_TOTAL:
                break
            selected_entries.append(entry)
            total_chars += len(entry)

        selected_entries.reverse()  # chronologisch (alt -> neu) fuer die Uebertragung
        return selected_entries

    def send_message(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        content: str,
        drafting_service: DraftingService | None,
        actor: str,
        current_message_id: str | None = None,
        source_message_id: str | None = None,
    ) -> ChatMessage:
        """Erzeugt die KI-Antwort auf die zuletzt gespeicherte Nutzer-
        nachricht. `drafting_service=None` bedeutet: kein API-Schluessel
        konfiguriert (vom Router bereits per `ProviderNotConfiguredError`
        beim Bauen des Service erkannt, siehe app/web/chat_router.py) -
        wird hier als eigener, klar erkennbarer Chat-Zustand behandelt,
        NICHT als Ausnahme.

        `source_message_id` (17.09., Overnight-Direktive §6/§7 - NICHT zu
        verwechseln mit `current_message_id` unten, das ist eine
        `ChatMessage`-ID, hier geht es um eine POSTEINGANG-`Message`-ID):
        optionale ID der Posteingang-Nachricht, auf die diese Chat-Antwort
        antwortet (siehe app/web/chat_router.py::
        start_conversation_from_message) - durchgereicht an `DraftingService.
        create_draft(message_id=...)`, damit `draft_detail.html`s
        "Original links"-Panel die tatsaechliche Ursprungsnachricht +
        Anhaenge zeigen kann. `None` (Standard, alle bisherigen Aufrufer) =
        unveraendertes Verhalten.

        `current_message_id` (CHAT-02, 15.09.): die ID der gerade per
        `record_user_message` persistierten aktuellen Nutzernachricht -
        wird von `_build_history` ausgeschlossen, damit die aktuelle
        Nachricht nicht doppelt (einmal als History, einmal als aktuelle
        Nachricht) im Claude-Payload landet. `None` (Standard) = keine
        Ausschluss-ID bekannt - fuer bestehende Aufrufer/Tests, die sich
        nicht fuer History interessieren, unveraendert nutzbar.

        VOR allem anderen (auch vor der `drafting_service is None`-Pruefung,
        siehe Modulkommentar oben zu `_looks_like_pure_norm_question`): bei
        einer REINEN Normzitat-Frage, deren Paragraph bereits lokal
        importiert ist (siehe app/laws/gesetze_im_internet.py), wird DIREKT
        aus der lokalen Gesetzesbibliothek geantwortet - kein Presidio-Lauf,
        kein Local-AI-Aufruf, kein Claude-Aufruf noetig, da der amtliche Text
        bereits vollstaendig lokal vorliegt (P0 Performance + Anbindung der
        Gesetzesbibliothek, 13.09.). Findet sich lokal KEIN passender
        Paragraph (Gesetz noch nicht importiert), faellt dies transparent auf
        die volle, unveraenderte Pipeline zurueck - funktioniert daher auch
        ohne konfigurierten API-Schluessel.

        Ebenso VOR allem anderen: eine reine Bestandsfrage ("Was ist die
        aktuellste Akte?") wird direkt aus der DB beantwortet, siehe
        Modulkommentar zu `_find_most_recently_active_matter`. Gibt es
        (nach Ausschluss der Schnellentwurf-Sammelakten) keine einzige
        echte Akte, faellt dies transparent auf die volle Pipeline zurueck."""
        norm_question = _looks_like_pure_norm_question(content)
        if norm_question is not None:
            law_code, section_number = norm_question
            section = _find_law_section(db, law_code=law_code, section_number=section_number)
            if section is not None:
                return self._create_message(
                    db,
                    conversation=conversation,
                    role="assistant",
                    content=_format_norm_answer(section),
                    law_section_id=section.id,
                )

        if _looks_like_recent_matter_question(content):
            found = _find_most_recently_active_matter(db)
            if found is not None:
                matter, last_activity = found
                return self._create_message(
                    db,
                    conversation=conversation,
                    role="assistant",
                    content=_format_recent_matter_answer(matter, last_activity=last_activity),
                )

        if drafting_service is None:
            return self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=_PROVIDER_UNAVAILABLE_MESSAGE,
                blocked=True,
            )

        trace = PerfTrace()
        with trace.step("routing"):
            purpose = _PURPOSE_DRAFT if _looks_like_drafting_request(content) else _PURPOSE_CHAT
        gespraechsverlauf = self._build_history(
            db, conversation=conversation, exclude_message_id=current_message_id
        )
        try:
            result = drafting_service.create_draft(
                conversation.matter_id,
                purpose,
                db,
                attorney_anmerkungen=content,
                actor=actor,
                trace=trace,
                gespraechsverlauf=gespraechsverlauf,
                message_id=source_message_id,
                chat_triggered=True,
            )
        except Exception:  # noqa: BLE001 - siehe Moduldocstring: Chat-Fehlerzustand
            # statt einer unbehandelten Ausnahme. Fail-closed bleibt
            # gewahrt: wirft die Gateway-Pseudonymisierung selbst eine
            # Ausnahme (z. B. Presidio/spaCy-Fehler), wurde bereits vorher
            # KEIN Payload gebaut und KEIN Cloud-Aufruf versucht - siehe
            # ClaudePrivacyGateway.prepare_request, dort bewusst ohne
            # Fallback-Pfad, der Pseudonymisierung uebersprungen haette.
            return self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=_UNEXPECTED_ERROR_MESSAGE,
                blocked=True,
            )

        if result.success:
            return self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=result.draft_text or "",
                draft_id=result.draft_id,
            )

        return self._create_message(
            db,
            conversation=conversation,
            role="assistant",
            content=friendly_block_message(result.blocked_reasons),
            blocked=True,
        )

    def send_message_stream(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        content: str,
        drafting_service: DraftingService | None,
        actor: str,
        current_message_id: str | None = None,
        source_message_id: str | None = None,
    ) -> Generator[ChatStreamEvent, None, None]:
        """Streaming-Variante von `send_message` (13.09., Streaming-
        Architekturentscheidung, siehe DECISIONS.md). Liefert echte
        inkrementelle Text-Deltas nur fuer den bereits etablierten
        risikobasierten Fast Path (siehe DraftingService.create_draft_stream)
        - jede andere Nachricht liefert genau EIN "delta" mit dem
        vollstaendigen Text, danach "done" - IDENTISCHES Endergebnis
        (dieselbe `ChatMessage`, dieselbe Persistenz-Logik) wie
        `send_message`, nur die Auslieferung ist inkrementell. Die
        `ChatMessage` wird GENAU EINMAL persistiert, erst nachdem der
        vollstaendige Text vorliegt (kein Teil-Speichern bei Abbruch/
        Verbindungsabbruch) - identische Semantik wie `send_message`.

        `current_message_id` (CHAT-02, 15.09.): identische Bedeutung/
        Begruendung wie bei `send_message` - siehe dortigen Docstring.
        History wird ueber DIESELBE `_build_history`-Methode gebaut (Owner-
        Vorgabe §5: keine zweite, unabhaengige History-Implementierung)."""
        norm_question = _looks_like_pure_norm_question(content)
        if norm_question is not None:
            law_code, section_number = norm_question
            section = _find_law_section(db, law_code=law_code, section_number=section_number)
            if section is not None:
                answer = _format_norm_answer(section)
                yield ChatStreamEvent(kind="delta", text=answer)
                message = self._create_message(
                    db,
                    conversation=conversation,
                    role="assistant",
                    content=answer,
                    law_section_id=section.id,
                )
                yield ChatStreamEvent(kind="done", message=message)
                return

        if _looks_like_recent_matter_question(content):
            found = _find_most_recently_active_matter(db)
            if found is not None:
                matter, last_activity = found
                answer = _format_recent_matter_answer(matter, last_activity=last_activity)
                yield ChatStreamEvent(kind="delta", text=answer)
                message = self._create_message(
                    db,
                    conversation=conversation,
                    role="assistant",
                    content=answer,
                )
                yield ChatStreamEvent(kind="done", message=message)
                return

        if drafting_service is None:
            yield ChatStreamEvent(kind="delta", text=_PROVIDER_UNAVAILABLE_MESSAGE)
            message = self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=_PROVIDER_UNAVAILABLE_MESSAGE,
                blocked=True,
            )
            yield ChatStreamEvent(kind="done", message=message)
            return

        trace = PerfTrace()
        with trace.step("routing"):
            purpose = _PURPOSE_DRAFT if _looks_like_drafting_request(content) else _PURPOSE_CHAT
        gespraechsverlauf = self._build_history(
            db, conversation=conversation, exclude_message_id=current_message_id
        )

        final_result = None
        try:
            for event in drafting_service.create_draft_stream(
                conversation.matter_id,
                purpose,
                db,
                attorney_anmerkungen=content,
                actor=actor,
                trace=trace,
                gespraechsverlauf=gespraechsverlauf,
                message_id=source_message_id,
                chat_triggered=True,
            ):
                if event.kind == "delta":
                    yield ChatStreamEvent(kind="delta", text=event.text)
                elif event.kind == "status":
                    # P1 Performance-Feedback-Follow-up (17.09.): vorher
                    # wurde jedes Nicht-"delta"-Ereignis stillschweigend als
                    # Endergebnis behandelt - `create_draft_stream` liefert
                    # jetzt zusaetzlich "status"-Zwischenereignisse (siehe
                    # DraftingService._STEP_STATUS_LABELS), die hier 1:1
                    # durchgereicht werden, statt sie zu verwerfen.
                    yield ChatStreamEvent(kind="status", status=event.status)
                else:
                    final_result = event.result
        except Exception:  # noqa: BLE001 - siehe send_message fuer die Begruendung
            message = self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=_UNEXPECTED_ERROR_MESSAGE,
                blocked=True,
            )
            yield ChatStreamEvent(kind="delta", text=_UNEXPECTED_ERROR_MESSAGE)
            yield ChatStreamEvent(kind="done", message=message)
            return

        if final_result is None:
            # Strukturell nie erwartet (create_draft_stream liefert IMMER
            # genau ein "result"-Ereignis) - Fail-Closed statt stiller
            # Fortsetzung mit unvollstaendigem Zustand.
            message = self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=_UNEXPECTED_ERROR_MESSAGE,
                blocked=True,
            )
            yield ChatStreamEvent(kind="done", message=message)
            return

        if final_result.success:
            message = self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=final_result.draft_text or "",
                draft_id=final_result.draft_id,
            )
        else:
            message = self._create_message(
                db,
                conversation=conversation,
                role="assistant",
                content=friendly_block_message(final_result.blocked_reasons),
                blocked=True,
            )
        yield ChatStreamEvent(kind="done", message=message)
