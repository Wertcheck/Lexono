"""Semantische Titel-Ableitung fuer neue Chat-Unterhaltungen (07.10.,
Owner-Direktive "CHAT-HISTORY-MANAGEMENT ERWEITERN" §1).

ECHTER FUND bei der Untersuchung der bisherigen Titelgenerierung
(app/chat/service.py::_derive_title, Vorzustand): reines
Whitespace-Normalisieren + Abschneiden bei 60 Zeichen - die ERSTEN WOERTER
der Nutzer-Nachricht wurden also woertlich zum Titel, unabhaengig davon,
ob sie das eigentliche Thema trafen (z. B. "Bitte nenne mir den
Paragraphen 558 BGB." wurde zu "Bitte nenne mir den Paragraphen 558
BGB." statt "§ 558 BGB").

Dieses Modul ersetzt NUR den Ableitungsschritt (reine Funktion, keine
Datenbank-/Request-Abhaengigkeit) durch eine Kette kleiner, lokaler
Mustererkennungen - KEIN LLM-Aufruf: der erzeugte Titel ist weiterhin ein
rein lokales, nie an die Cloud gesendetes Anzeige-Label (siehe
ChatConversation.title-Docstring), eine Cloud-Anfrage allein fuer die
Titelgebung waere ein unnoetiger Mehraufwand UND wuerde diese Eigenschaft
aufgeben. Reihenfolge (erster Treffer gewinnt, danach Truenkierungs-
Fallback):

1. Gesetzesverweis (§/Artikel + Nummer + Gesetzeskuerzel) - bewusst ALS
   ALLGEMEINES MUSTER erkannt (jede Zahl + jedes grossgeschriebene
   Kuerzel), NICHT als Sonderfall fuer § 558 BGB o.ae. hartcodiert.
2. "Unterschied zwischen X und Y" -> "X vs. Y"
3. Zusammenfassungs-Absicht ("... zusammenfassen"/"Zusammenfassung von
   ...") -> "<Thema> – Zusammenfassung"
4. Schreib-/Entwurfsabsicht (E-Mail/Brief/Schriftsatz/... + Grund ueber
   "wegen"/"bezüglich"/"zu"/"für") -> "<Dokumenttyp> – <Grund>"
5. Fallback: bereinigte (fuellwortbefreite), gekuerzte Eingabe - wie
   zuvor, nur ohne formelhafte Einleitungen wie "Bitte" / "Kannst du...".
"""

from __future__ import annotations

import re

#: Obergrenze fuer JEDEN erzeugten Titel (Direktive §1: "ca. 40-60 Zeichen
#: maximal") - großzügig bei 60 gezogen, angelehnt an die bisherige Grenze
#: in `_derive_title`, damit auch ein Fallback-Titel nie ausufert.
_MAX_TITLE_LENGTH = 60

_ELLIPSIS = "…"


def _cap(text: str, limit: int = _MAX_TITLE_LENGTH) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + _ELLIPSIS


#: Gesetzeskuerzel: grossgeschriebener Buchstabe, gefolgt von 1-9 weiteren
#: Buchstaben (deckt BGB/StGB/ZPO/StPO/HGB/GG/AO/InsO/VwGO/EStG/... ab,
#: OHNE eine feste Liste bekannter Kuerzel zu pflegen - jedes neue/seltene
#: Gesetz wird dadurch automatisch erkannt, nicht nur eine Handvoll
#: vorab eingetragener).
_LAW_ABBR = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]{1,12}"
_SECTION_NUM = r"\d+\s?[a-z]?"

_PARAGRAPH_RE = re.compile(
    rf"(?:§§?\s*|Paragraph(?:en)?\s+|Paragraf(?:en)?\s+)"
    rf"({_SECTION_NUM})"
    rf"(?:\s*(?:Abs\.|Absatz)\s*(\d+))?"
    rf"\s+({_LAW_ABBR})\b",
    re.UNICODE,
)

_ARTICLE_RE = re.compile(
    rf"(?:Art(?:ikel)?\.?\s+)"
    rf"({_SECTION_NUM})"
    rf"\s+({_LAW_ABBR})\b",
    re.UNICODE,
)


def _match_statute_citation(message: str) -> str | None:
    match = _PARAGRAPH_RE.search(message)
    if match:
        number = match.group(1).replace(" ", "")
        absatz = match.group(2)
        law = match.group(3)
        title = f"§ {number} {law}" if not absatz else f"§ {number} Abs. {absatz} {law}"
        return _cap(title)

    match = _ARTICLE_RE.search(message)
    if match:
        number = match.group(1).replace(" ", "")
        law = match.group(2)
        return _cap(f"Art. {number} {law}")

    return None


_COMPARISON_RE = re.compile(
    r"Unterschiede?\s+zwischen\s+(.+?)\s+und\s+(.+?)(?:[.?!]|$)",
    re.IGNORECASE | re.UNICODE,
)


def _match_comparison(message: str) -> str | None:
    match = _COMPARISON_RE.search(message)
    if not match:
        return None
    left = match.group(1).strip(" ,")
    right = match.group(2).strip(" ,")
    if not left or not right:
        return None
    return _cap(f"{left} vs. {right}")


_SUMMARIZE_VERB_RE = re.compile(
    r"fass(?:e|en|t)?\s+(?:den\s+|die\s+|das\s+)?(.+?)\s+zusammen",
    re.IGNORECASE | re.UNICODE,
)
#: Einzelnes Wort-Token statt `.+?` bis zum Satzende (06.10.-Nachkorrektur,
#: live gefunden): "Zusammenfassung des Mietvertrags ERSTELLEN?" hat KEIN
#: zweites Satzzeichen vor dem "?" am Ende - ein nicht-gieriges `.+?` bis
#: zum naechsten [.?!] haette dadurch "Mietvertrags erstellen" statt nur
#: "Mietvertrags" eingefangen. Deutsche zusammengesetzte Substantive sind
#: ohnehin fast immer EIN Wort (anders als im Englischen) - ein einzelnes
#: Token ist daher die richtige, nicht nur die bequemere Grenze.
_SUMMARIZE_NOUN_RE = re.compile(
    r"Zusammenfassung\s+(?:des|der|von)\s+([\wäöüÄÖÜß\-]+)",
    re.IGNORECASE | re.UNICODE,
)


def _match_summarize_intent(message: str) -> str | None:
    match = _SUMMARIZE_VERB_RE.search(message) or _SUMMARIZE_NOUN_RE.search(message)
    if not match:
        return None
    topic = match.group(1).strip(" ,")
    if not topic:
        return None
    return _cap(f"{topic} – Zusammenfassung")


#: Dokumenttyp-Stichwoerter fuer die Schreib-/Entwurfsabsicht - bewusst
#: eine kleine, offensichtliche Menge gaengiger Kanzlei-Dokumenttypen
#: (keine Gesetzes-/Paragraphenliste wie oben, hier geht es um die
#: ERZEUGUNGSABSICHT, nicht um einen erkennbaren Rechtsbegriff).
_DOC_TYPE_LABELS: dict[str, str] = {
    "e-mail": "E-Mail",
    "email": "E-Mail",
    "schriftsatz": "Schriftsatz",
    "klageschrift": "Klageschrift",
    "klage": "Klage",
    "mahnung": "Mahnung",
    "kündigung": "Kündigung",
    "widerspruch": "Widerspruch",
    "antwort": "Antwort",
    "brief": "Brief",
    "entwurf": "Entwurf",
}
_DOC_TYPE_RE = re.compile(
    "|".join(re.escape(kw) for kw in _DOC_TYPE_LABELS), re.IGNORECASE
)
_REASON_RE = re.compile(
    r"(?:wegen|bezüglich|bzgl\.|betreffend|zu|für)\s+"
    r"(?:der|die|das|des|den|dem)?\s*(.+?)(?:[.?!]|$)",
    re.IGNORECASE | re.UNICODE,
)


def _match_drafting_intent(message: str) -> str | None:
    doc_match = _DOC_TYPE_RE.search(message)
    if not doc_match:
        return None
    doc_type = _DOC_TYPE_LABELS[doc_match.group(0).lower()]

    reason_match = _REASON_RE.search(message[doc_match.end() :])
    if not reason_match:
        return None
    reason = reason_match.group(1).strip(" ,")
    if not reason:
        return None
    return _cap(f"{doc_type} – {reason}")


#: Formelhafte Einleitungen, die im Fallback-Titel keinen inhaltlichen
#: Mehrwert haben ("Bitte erkläre mir..." -> "erkläre mir..." ist als
#: Titel genauso wenig hilfreich, aber wenigstens kein totes Fuellwort
#: ganz am Anfang). Nur EINMAL am Satzanfang entfernt, keine tiefere
#: Umformulierung - bleibt bewusst ein Kuerzungs-Fallback, kein eigener
#: Versuch semantischer Benennung.
_FILLER_PREFIX_RE = re.compile(
    r"^(?:"
    r"Bitte\s+|"
    r"Kannst du (?:mir\s+)?|"
    r"Könntest du (?:mir\s+)?|"
    r"Würdest du (?:mir\s+)?|"
    r"Ich (?:möchte|würde gerne|hätte gerne)\s+(?:wissen[,:]?\s+)?|"
    r"Ich habe eine Frage[,:]?\s+"
    r")",
    re.IGNORECASE | re.UNICODE,
)


def _fallback_title(message: str) -> str:
    normalized = " ".join(message.split())
    if not normalized:
        return "Neue Unterhaltung"
    stripped = _FILLER_PREFIX_RE.sub("", normalized, count=1).strip()
    cleaned = stripped or normalized
    # Erster Buchstabe grossgeschrieben, falls das Entfernen der
    # Einleitung mit einem Kleinbuchstaben beginnen liess (z. B. "Bitte
    # erkläre..." -> "erkläre..." -> "Erkläre...").
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return _cap(cleaned)


def generate_conversation_title(first_message: str) -> str:
    """Kurzer, semantisch sinnvoller, rein lokaler Anzeige-Titel fuer eine
    neue Chat-Unterhaltung - niemals an die Cloud gesendet, nur fuer die
    Konversationsliste (siehe Moduldocstring fuer die volle
    Erkennungsreihenfolge)."""
    if not first_message or not first_message.strip():
        return "Neue Unterhaltung"

    for matcher in (
        _match_statute_citation,
        _match_comparison,
        _match_summarize_intent,
        _match_drafting_intent,
    ):
        result = matcher(first_message)
        if result:
            return result

    return _fallback_title(first_message)
