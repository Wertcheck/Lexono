"""Unsichere Personen-Kandidaten und Datenschutz-Entscheidungen (Human in the Loop, 12.10.2026).

Siehe PRIVACY_REVIEW_ARCHITECTURE.md. Die automatische Erkennung (presidio_ner/detectors) entscheidet weiterhin allein,
was SICHER personenbezogen ist. Dieses Modul findet im bereits pseudonymisierten Text die verbleibenden Woerter, bei
denen ein Personenname plausibel, aber nicht belegt ist ("Richter", seltene Namen in Tabellen) - die dritte Stufe
"unsicher". Der Gateway maskiert sie vorsichtshalber (`review_mode="mask"`) oder fragt den Anwalt (`"ask"`).

Kandidat = grossgeschriebenes, alphabetisches Wort (>= 3 Buchstaben, keine Abkuerzung), das NICHT bereits ersetzt
wurde, ohne Artikel/Adjektiv/Zahl/Pronomen davor, in einer Namensrolle (Subjekt bzw. direkt vor finitem Verb, nach
"von/durch/mit/an/bei/fuer", Feldwert hinter "Label:", Tabellenzelle, nach einem Rollenwort wie "Zeuge"), und das
NER-Modell etikettiert es in mindestens einem von fuenf neutralen Rahmensaetzen als Person (Modell-Probing). Gemessen
(synthetisch): erfasst 21 von 34 Restlecks; 4 Rueckfragen in 18 Dokumenten, 0 bei 17 Anwaltsanweisungen.

Entscheidungen (`PrivacyDecisions`): "personal" -> der Begriff wird als bekannte Entitaet pseudonymisiert;
"not_personal" -> NUR die Rueckfrage zu genau diesem Begriff entfaellt. Eine Entscheidung kann keine automatische
Erkennung, keinen Restrisiko-Scan und keinen Leak-Check aufheben.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.privacy import presidio_ner as pn
from app.privacy.detectors import DetectedSpan

REVIEW_MIN_PROBE_SCORE = 1
_CONTEXT_CHARS = 60
_MAX_CANDIDATES = 8
_LABEL_BEFORE = re.compile(r"[A-ZÄÖÜ][\wÄÖÜäöüß]*:[ \t]*$")
_CELL_BEFORE = re.compile(r"(?:\|[ \t]*|[ \t]{2,})$")
_ROLE_PREPOSITIONS = frozenset({"von", "durch", "mit", "an", "bei", "für"})
_ROLE_NOUNS = frozenset(
    {
        "zeuge", "zeugin", "gutachter", "gutachterin", "sachverständiger", "sachverständige", "herr", "frau", "kollege",
        "kollegin", "mandant", "mandantin", "rechtsanwalt", "rechtsanwältin", "geschäftsführer", "geschäftsführerin",
        "bearbeiter", "bearbeiterin", "ansprechpartner", "ansprechpartnerin", "vermieter", "vermieterin", "mieter",
        "mieterin",
    }
)
_MODIFIER_POS = ("DET", "ADJ", "NUM", "PRON")
_STRUCTURAL_ROLES = frozenset(
    {"feldwert", "tabellenzelle", "nach_rollenwort", "vor_rollenwort", "aufzaehlung_mit_person", "ueberschrift"}
)
_SENTENCE_ROLES = frozenset(
    {"subjekt", "vor_finitem_verb", "feldwert", "tabellenzelle", "vor_rollenwort", "aufzaehlung_mit_person"}
)
# Restlecks (12.10.2026, reproduziert im Benchmark): Aufzaehlung neben einem bereits ersetzten PERSONEN-Platzhalter
# ("[PERSON_01], Richter sowie Herr [PERSON_01]") - Glieder einer Aufzaehlung teilen ihren Typ.
_PERSON_PLACEHOLDER = r"\[(?:PERSON|MANDANT|GEGNER|ANWALT)_\d+\]"
# Verbindungen: Komma/Konjunktion oder ein Listenpunkt ("- Fuerst" unter "- Herr [PERSON_03]" - Befund realistisches PDF).
_ENUM_LINK = r"\s*(?:,|und|sowie|oder|bzw\.|[-–•*·])\s*"
_TITLE = r"(?:(?:Herrn?|Frau|Dr\.|Prof\.)\s+)*"
_ENUM_AFTER_PERSON = re.compile(_PERSON_PLACEHOLDER + _ENUM_LINK + _TITLE + r"$")
_ENUM_BEFORE_PERSON = re.compile(_ENUM_LINK + _TITLE + _PERSON_PLACEHOLDER)
_LINE_BOUNDARY = re.compile(r"\n|[ \t]{2,}")
_ROLE_NOUN_AFTER = re.compile(r",\s*(?:" + "|".join(sorted(_ROLE_NOUNS, key=len, reverse=True)) + r")\b", re.IGNORECASE)
MAX_TERM_LENGTH = 80
# Vom System selbst erzeugte Kopfzeilen (app/ai_providers/local_ai_provider.py: "Akte: <Titel>") sind keine
# Namensrolle im Sinne einer Rueckfrage - der Aktentitel ist lokale Stammdaten-Information; Mandant/Beteiligte darin
# deckt known_entities ab. Ohne diese Ausnahme fragte JEDE Nachricht einer Akte nach ihrem eigenen Titel.
_SYSTEM_LABEL_LINE = re.compile(r"(?:^|\n)[ \t.]*Akte:[^\n]*$")


@dataclass(frozen=True)
class ReviewCandidate:
    """Ein unsicherer Begriff. `context` ist ein kurzer Ausschnitt aus dem LOKALEN Originaltext (nur fuer die lokale
    Rueckfrage-Anzeige, nie Teil einer Cloud-Payload, nie in Logs/Statistiken)."""

    term: str
    context_before: str
    context_after: str
    roles: tuple[str, ...]
    probe_score: int


@dataclass(frozen=True)
class PrivacyDecisions:
    """Bereits getroffene Entscheidungen fuer den aktuellen Kontext (Unterhaltung/Akte)."""

    personal: frozenset[str] = field(default_factory=frozenset)
    not_personal: frozenset[str] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        return not self.personal and not self.not_personal


def _roles(doc, text: str, token, raw: str | None = None) -> set[str]:
    roles: set[str] = set()
    head = token.head
    # Satzrollen gelten nur innerhalb einer Zeile: ueber eine harte Zeilengrenze (Umbruch oder >= 2 Leerzeichen aus der
    # Dokumentaufbereitung) haengt der Parser Tabellen-/Listenzellen an Verben fremder Saetze ("Position  Menge  Betrag
    # Estrich ... Der Streitwert betraegt" -> "Menge", "Estrich" als Subjekt; Befund realistisches PDF, 12.10.2026).
    lines_source = raw if raw is not None else text

    def same_line(other) -> bool:
        lo, hi = sorted((other.idx, token.idx))
        return _LINE_BOUNDARY.search(lines_source, lo, hi) is None

    # Subjekt eines nicht-infiniten Verbs - auch bei Verbzweitstellung ("Dazu erklaert Fuerst, ...").
    if (
        token.dep_ == "sb"
        and head.pos_ in ("VERB", "AUX")
        and "Inf" not in head.morph.get("VerbForm")
        and same_line(head)
    ):
        roles.add("subjekt")
    following = doc[token.i + 1] if token.i + 1 < len(doc) else None
    if following is not None and following.pos_ in ("VERB", "AUX") and "Fin" in following.morph.get("VerbForm"):
        roles.add("vor_finitem_verb")
    # Praeposition + Zeilenumbruch + Name ist der gewoehnliche PDF-/OCR-Umbruch ("von" / "Richter vom ...") - hier gilt die
    # Zeilengrenzen-Einschraenkung bewusst NICHT (Befund Scan-Variante, 12.10.2026).
    if token.dep_ == "nk" and head.pos_ == "ADP" and head.lower_ in _ROLE_PREPOSITIONS:
        roles.add("nach_praeposition")
    # Agens an der Oberflaeche, falls der Parser scheitert (installierter Build, 12.10.2026: "durch  Lange geprueft" -
    # "Lange" als Adjektiv zu "geprueft" geparst): unmittelbar nach "von/durch", gefolgt von einem kleingeschriebenen Wort.
    previous_word = next((t for t in reversed(doc[: token.i]) if not t.is_space), None)
    following_word = next((t for t in doc[token.i + 1 :] if not t.is_space), None)
    if (
        previous_word is not None
        and previous_word.lower_ in ("von", "durch")
        and following_word is not None
        and following_word.text[:1].islower()
        and following_word.is_alpha
        and following_word.pos_ not in ("ADP", "DET", "PRON", "CCONJ", "SCONJ", "PART")
        and following_word.lower_.endswith(("t", "en"))  # Partizip ("geprueft", "uebergeben"), nicht "am"/"zum"
    ):
        roles.add("nach_praeposition")
    end = token.idx + len(token.text)
    if _LABEL_BEFORE.search(text[: token.idx]) and pn._is_bare_cell_value(text, end):
        roles.add("feldwert")
    if pn._is_bare_cell_value(text, end) and _CELL_BEFORE.search(text[: token.idx]) and _row_mentions_a_person(raw or text, token.idx, end):
        roles.add("tabellenzelle")
    previous = next((t for t in reversed(doc[: token.i]) if not t.is_space), None)
    if previous is not None and previous.lower_ in _ROLE_NOUNS:
        roles.add("nach_rollenwort")
    # "Stuckenschneider, Zeuge" (Kopfzeile eines Protokolls): Apposition VOR einem Rollenwort.
    if _ROLE_NOUN_AFTER.match(text, end):
        roles.add("vor_rollenwort")
    # Aufzaehlungsglied neben einem ersetzten Personen-Platzhalter (Rohtext, positionsgleich zur Analysefassung).
    if raw is not None and (_ENUM_AFTER_PERSON.search(raw[: token.idx]) or _ENUM_BEFORE_PERSON.match(raw, end)):
        roles.add("aufzaehlung_mit_person")
    # Listenzeile ohne Aufzaehlungszeichen (DOCX-Liste: "Herr [PERSON_03]" / "Fuerst" / "Dr. [PERSON_05]"): das Wort steht
    # allein auf seiner Zeile, und eine Nachbarzeile enthaelt einen ersetzten Personen-Platzhalter.
    if raw is not None and _alone_on_line_next_to_person(raw, token.idx, end):
        roles.add("aufzaehlung_mit_person")
    # Kurze Ueberschriftszeile "Zeugenaussage Richter": letztes Wort einer Zeile aus hoechstens vier Woertern ohne Verb
    # und ohne Satzzeichen am Ende, direkt nach einem Substantiv.
    # Zeilengrenzen aus dem ROHtext: die Analysefassung ersetzt "Richter\nDer" als vermeintlich weichen PDF-Umbruch durch
    # ein Leerzeichen - die Ueberschrift verschmolz sonst mit dem naechsten Satz (12.10.2026, "Zeugenaussage Richter").
    # Zeilengrenze = Umbruch ODER mindestens zwei Leerzeichen: die Dokumentaufbereitung (local_ai_provider.
    # _normalize_document_text) ersetzt Zeilenumbrueche durch zwei Leerzeichen (Befund im installierten Build).
    lines_source = raw if raw is not None else text
    starts = [m.end() for m in _LINE_BOUNDARY.finditer(lines_source, 0, token.idx)]
    line_start = starts[-1] if starts else 0
    following_boundary = _LINE_BOUNDARY.search(lines_source, end)
    line_end = following_boundary.start() if following_boundary else -1
    line = pn._neutralize_internal_tokens(lines_source)[line_start : len(lines_source) if line_end < 0 else line_end].strip()
    if (
        line.endswith(token.text)
        and 2 <= len(line.split()) <= 4
        and previous is not None
        and previous.pos_ in ("NOUN", "PROPN")
        and previous.idx >= line_start
        and not any(t.pos_ in ("VERB", "AUX") for t in doc if line_start <= t.idx < end)
        and (line_end < 0 or line_end >= end)
    ):
        roles.add("ueberschrift")
    return roles


_ROW_BOUNDARY = re.compile(r"\n|(?<![|\s])[ \t]{2,}(?![ \t|])")
_TABLE_ROW = re.compile(r"[^|\n]*(?:\|[^|\n]*)+")


def _alone_on_line_next_to_person(raw: str, start: int, end: int) -> bool:
    boundaries = [(m.start(), m.end()) for m in _LINE_BOUNDARY.finditer(raw)]
    before = [b for b in boundaries if b[1] <= start]
    after = [b for b in boundaries if b[0] >= end]
    if not before or not after:
        return False
    line = raw[before[-1][1] : after[0][0]].strip(" \t-–•*·")
    if line != raw[start:end]:
        return False
    previous_line = raw[before[-2][1] if len(before) > 1 else 0 : before[-1][0]]
    next_line = raw[after[0][1] : after[1][0] if len(after) > 1 else len(raw)]
    return bool(re.search(_PERSON_PLACEHOLDER, previous_line) or re.search(_PERSON_PLACEHOLDER, next_line))


def _row_mentions_a_person(text: str, start: int, end: int) -> bool:
    """Tabellenzelle nur dann namensverdaechtig, wenn dieselbe Tabellenzeile (durch "|" verbunden) ein Rollenwort
    ("Zeuge"), "Name" oder einen ersetzten Personen-Platzhalter enthaelt - sonst ist es z. B. eine Rechnungsposition
    ("Position | Menge | Preis", Befund 12.10.2026)."""
    # Zeilengrenze: Umbruch oder >= 2 Leerzeichen, die NICHT an einem "|" stehen ("Kiel  Position | Menge" trennt zwei
    # Tabellen der Dokumentaufbereitung; "Zeuge  |  Richter" bleibt eine Zeile).
    boundaries = [m.end() for m in _ROW_BOUNDARY.finditer(text, 0, start)]
    row_start = boundaries[-1] if boundaries else 0
    following = _ROW_BOUNDARY.search(text, end)
    row_end = following.start() if following else len(text)
    for match in _TABLE_ROW.finditer(text, row_start, row_end):
        if match.start() <= start and end <= match.end():
            row = text[match.start() : start] + " " + text[end : match.end()]
            words = {w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß]+", row)}
            return bool(words & (_ROLE_NOUNS | {"name", "person", "beteiligter", "beteiligte"})) or bool(
                re.search(_PERSON_PLACEHOLDER, row)
            )
    return False


def _agrees_with_singular_verb(doc, token) -> bool:
    """Formgleiche Singular/Plural-Woerter ("Richter", "Fischer") taggt das Modell oft als Plural. Ist das Wort Subjekt
    eines finiten Verbs im SINGULAR ("Richter hat angerufen"), ist der Plural-Tag widerlegt (12.10.2026)."""
    head = token.head
    verb = head if token.dep_ == "sb" and head.pos_ in ("VERB", "AUX") else None
    if verb is None and token.i + 1 < len(doc) and doc[token.i + 1].pos_ in ("VERB", "AUX"):
        verb = doc[token.i + 1]
    return verb is not None and "Fin" in verb.morph.get("VerbForm") and "Sing" in verb.morph.get("Number")


def _is_modifier(token) -> bool:
    # Nur echte Woerter/Zahlen zaehlen: der Tagger etikettiert z. B. das Tabellenzeichen "|" als ADJ.
    return token.pos_ in _MODIFIER_POS and (token.is_alpha or token.like_num)


def _is_bare(doc, token) -> bool:
    if any(c.i < token.i and _is_modifier(c) for c in token.children):
        return False
    previous = next((t for t in reversed(doc[: token.i]) if not t.is_space), None)
    return not (previous is not None and _is_modifier(previous))


def _context(source: str, term: str) -> tuple[str, str]:
    match = re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(term) + r"(?![\wÄÖÜäöüß])", source)
    if match is None:
        return "", ""
    start = max(0, match.start() - _CONTEXT_CHARS)
    end = match.end() + _CONTEXT_CHARS
    before = source[start : match.start()].split("\n\n")[-1]
    after = source[match.end() : end].split("\n\n")[0]
    # Nur ganze Woerter zeigen (kein "...sterbau GmbH"); einfache Zeilenumbrueche (PDF) als Leerzeichen.
    if start > 0 and len(before) == match.start() - start and " " in before.strip():
        before = before[before.find(" ") + 1 :]
    if end < len(source) and len(after) == end - match.end() and " " in after.strip():
        after = after[: after.rfind(" ")]
    lead = " " if before[-1:].isspace() else ""
    trail = " " if after[:1].isspace() else ""
    return " ".join(before.split()) + lead, trail + " ".join(after.split())


def find_review_candidates(
    scan_text: str,
    *,
    context_source: str,
    decisions: PrivacyDecisions | None = None,
    max_candidates: int | None = _MAX_CANDIDATES,
) -> list[ReviewCandidate]:
    """Unsichere Kandidaten im PSEUDONYMISIERTEN `scan_text` (ohne fruehere KI-Antworten).

    `context_source` ist der lokale Rohtext derselben Anfrage (nur fuer den Kontextausschnitt). Bereits als
    "not_personal" entschiedene Begriffe werden uebersprungen; "personal" ist zu diesem Zeitpunkt bereits ersetzt.
    Wirft Ausnahmen weiter - der Gateway behandelt einen Fehler hier fail-closed."""
    if not scan_text or not scan_text.strip():
        return []
    decisions = decisions or PrivacyDecisions()
    neutral = pn._neutralize_internal_tokens(scan_text)
    doc = pn._analysis_doc(scan_text)
    found: dict[str, ReviewCandidate] = {}
    for token in doc:
        word = token.text
        if len(word) < 3 or len(word) > MAX_TERM_LENGTH or not word.isalpha() or not word[0].isupper() or word.isupper():
            continue
        if word in found or word in decisions.not_personal or word in decisions.personal:
            continue
        if word.lower() in pn._NEVER_ENTITY_WORDS or word in pn._PROTOCOL_LABELS:
            continue
        if not _is_bare(doc, token):
            continue
        if _SYSTEM_LABEL_LINE.search(neutral[: token.idx]):
            continue
        if neutral[token.idx + len(word) : token.idx + len(word) + 1] == ":":
            continue  # Feldbezeichnung ("Akte:", "Name:", "Betreff:"), kein Feldwert
        roles = _roles(doc, neutral, token, raw=scan_text)
        if not roles:
            continue
        if (
            "Plur" in token.morph.get("Number")
            and not roles & _STRUCTURAL_ROLES
            and not _agrees_with_singular_verb(doc, token)
            and not (
                "nach_praeposition" in roles
                and token.head.lower_ in ("von", "durch")
                and token.lemma_.lower() == token.text.lower()
            )
        ):
            # Echter Plural ("Zahlungen", Lemma "Zahlung") bleibt ausgeschlossen; formgleiche Woerter als Agens nach
            # "von"/"durch" ("durch Lange geprueft", Lemma == Wortform) sind mehrdeutig und werden weiter am
            # Probing-Score gemessen. Andere Praepositionen ("bei Maengeln") bleiben ausgeschlossen.
            continue
        # Anweisungs-Imperativ am Satzanfang ("Ueberarbeite den Entwurf") ist keine Namensrolle.
        if pn._SENTENCE_START_PREFIX.search(neutral[: token.idx]) and not roles & _SENTENCE_ROLES:
            continue
        score = pn._name_probe_score(word)
        if score < REVIEW_MIN_PROBE_SCORE:
            continue
        # Fachwort-Fehlalarme (12.10.2026, 600er-Korpus): jeder synthetische Testname wird im staerksten Namensrahmen
        # "Herr X hat unterschrieben" als Person gelesen; Fachwoerter wie "Bescheid", "Flachdach", "Zugluft" nicht.
        # Gemessen: Fachwort-Faelle 100 -> 87 von 600, Klartext-Namen unveraendert 0,4 %.
        if not pn._name_probe_frames(word)[0]:
            continue
        before, after = _context(context_source, word)
        found[word] = ReviewCandidate(
            term=word, context_before=before, context_after=after, roles=tuple(sorted(roles)), probe_score=score
        )
        if max_candidates is not None and len(found) >= max_candidates:
            break
    return list(found.values())


def detect_decision_terms(text: str, terms: set[str] | frozenset[str]) -> list[DetectedSpan]:
    """Fundstellen der als personenbezogen entschiedenen Begriffe (exakte Schreibung, ganze Woerter - "Richter" trifft
    nicht "Schiedsrichter"). Kategorie "person"."""
    spans: list[DetectedSpan] = []
    for term in sorted(terms, key=len, reverse=True):
        if not term or not term.strip():
            continue
        pattern = re.compile(r"(?<![\wÄÖÜäöüß])" + re.escape(term) + r"(?![\wÄÖÜäöüß])")
        for match in pattern.finditer(text):
            if any(s.start < match.end() and match.start() < s.end for s in spans):
                continue
            spans.append(DetectedSpan(category="person", start=match.start(), end=match.end(), value=match.group()))
    return spans
