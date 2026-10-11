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
_STRUCTURAL_ROLES = frozenset({"feldwert", "tabellenzelle", "nach_rollenwort"})
_SENTENCE_ROLES = frozenset({"subjekt", "vor_finitem_verb", "feldwert", "tabellenzelle"})
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


def _roles(doc, text: str, token) -> set[str]:
    roles: set[str] = set()
    head = token.head
    # Subjekt eines nicht-infiniten Verbs - auch bei Verbzweitstellung ("Dazu erklaert Fuerst, ...").
    if token.dep_ == "sb" and head.pos_ in ("VERB", "AUX") and "Inf" not in head.morph.get("VerbForm"):
        roles.add("subjekt")
    following = doc[token.i + 1] if token.i + 1 < len(doc) else None
    if following is not None and following.pos_ in ("VERB", "AUX") and "Fin" in following.morph.get("VerbForm"):
        roles.add("vor_finitem_verb")
    if token.dep_ == "nk" and head.pos_ == "ADP" and head.lower_ in _ROLE_PREPOSITIONS:
        roles.add("nach_praeposition")
    end = token.idx + len(token.text)
    if _LABEL_BEFORE.search(text[: token.idx]) and pn._is_bare_cell_value(text, end):
        roles.add("feldwert")
    if pn._is_bare_cell_value(text, end) and _CELL_BEFORE.search(text[: token.idx]):
        roles.add("tabellenzelle")
    previous = doc[token.i - 1] if token.i else None
    if previous is not None and previous.lower_ in _ROLE_NOUNS:
        roles.add("nach_rollenwort")
    return roles


def _is_bare(doc, token) -> bool:
    if any(c.i < token.i and c.pos_ in _MODIFIER_POS for c in token.children):
        return False
    previous = doc[token.i - 1] if token.i else None
    return not (previous is not None and previous.pos_ in _MODIFIER_POS)


def _context(source: str, term: str) -> tuple[str, str]:
    match = re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(term) + r"(?![\wÄÖÜäöüß])", source)
    if match is None:
        return "", ""
    before = source[max(0, match.start() - _CONTEXT_CHARS) : match.start()]
    after = source[match.end() : match.end() + _CONTEXT_CHARS]
    before = before.split("\n")[-1]
    after = after.split("\n")[0]
    return before.lstrip(), after.rstrip()


def find_review_candidates(
    scan_text: str,
    *,
    context_source: str,
    decisions: PrivacyDecisions | None = None,
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
        roles = _roles(doc, neutral, token)
        if not roles:
            continue
        if "Plur" in token.morph.get("Number") and not roles & _STRUCTURAL_ROLES:
            continue
        # Anweisungs-Imperativ am Satzanfang ("Ueberarbeite den Entwurf") ist keine Namensrolle.
        if pn._SENTENCE_START_PREFIX.search(neutral[: token.idx]) and not roles & _SENTENCE_ROLES:
            continue
        score = pn._name_probe_score(word)
        if score < REVIEW_MIN_PROBE_SCORE:
            continue
        before, after = _context(context_source, word)
        found[word] = ReviewCandidate(
            term=word, context_before=before, context_after=after, roles=tuple(sorted(roles)), probe_score=score
        )
        if len(found) >= _MAX_CANDIDATES:
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
