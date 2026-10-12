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
from app.privacy.detectors import _ALL_REGEX_DETECTORS, DetectedSpan

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
    if (
        following is not None
        and following.pos_ in ("VERB", "AUX")
        and "Fin" in following.morph.get("VerbForm")
        and not _is_article_contraction_object(doc, token)
    ):
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


# Praeposition mit verschmolzenem bestimmtem Artikel (13.10.2026, Befund installierter Build: "Die Abdichtung im Keller
# ist mangelhaft" -> Rueckfrage "Keller"). Die Rolle "vor_finitem_verb" griff rein ueber die Nachbarschaft "Keller ist",
# obwohl der Parser "Keller" als Kern der Praepositionalgruppe "im Keller" erkennt (Subjekt ist "Abdichtung"). Auf diese
# Formen folgt ein Nachname praktisch nie ("im Keller", "am Fenster", "ins Haus"). Bewusst NICHT enthalten: "beim", "vom",
# "zum", "zur" - umgangssprachlich auch vor Namen ("beim Huber", "vom Keller"). Nur diese eine Rolle entfaellt; Subjekt,
# Rollenwort, "von"/"durch"-Agens, Feldwert, Tabelle usw. bleiben unveraendert.
_ARTICLE_CONTRACTIONS = frozenset({"im", "ins", "am", "ans", "aufs", "ums", "übers", "überm", "unterm", "hinterm", "vorm"})


def _is_article_contraction_object(doc, token) -> bool:
    previous = next((t for t in reversed(doc[: token.i]) if not t.is_space), None)
    return (
        previous is not None
        and previous.lower_ in _ARTICLE_CONTRACTIONS
        and token.dep_ == "nk"
        and token.head.i == previous.i
    )


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
        if pn._is_identifier_label(word):
            continue  # Kennnummer-Bezeichnung neben maskierter Nummer ("Schadennummer [KENNUNG_01]", §22) - kein Name
        if pn.is_derivational_compound(word):
            continue  # Ableitungs-Kompositum ("Raeumungsfrist") - kein Name, siehe presidio_ner.is_derivational_compound
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
    for match in _PLATE_SHAPE.finditer(neutral):
        # Zeilenumbruch (PDF) im Begriff als Leerzeichen: Entscheidungen sind einzeilig (decisions._validate);
        # detect_decision_terms trifft den Umbruch im Text trotzdem (_decision_term_pattern).
        term = re.sub(r"[ \t]*\r?\n[ \t]*", " ", match.group())
        # Bewusst NICHT durch max_candidates begrenzt: sonst ginge ein Kennzeichen hinter vielen Namenskandidaten
        # ungefragt im Klartext hinaus (die Form ist selten - gemessen 8 Treffer in 916 Texten, alle echte Kennzeichen).
        if term in found or term in decisions.not_personal or term in decisions.personal:
            continue
        if match.group("district") in _PLATE_SHAPE_NOT_A_DISTRICT or _is_citation_not_plate(neutral, match):
            continue
        if match.group("sep") == " " and not _plausible_spaced_plate(neutral, match):
            continue
        before, after = _context(context_source, match.group())
        found[term] = ReviewCandidate(
            term=term, context_before=before, context_after=after, roles=("kennzeichenform",), probe_score=0
        )
    # Unbeschriftete Kennnummern (§22): wie die Kennzeichen nicht durch max_candidates begrenzt. Gesucht wird im lokalen
    # ROHTEXT (Review: im pseudonymisierten Text blieben nur Bruchstuecke neben Platzhaltern - "[KUNDENNUMMER_01] 2345",
    # "12345BG[TELEFON_01]" -, die als Begriff im Original nicht vorkommen; die Bezeichnung davor war teils schon durch die
    # NER ersetzt). Gefragt wird, solange irgendein Teil der Nummer noch unmaskiert im Versandtext steht.
    for raw, start in _trim_at_detector_spans(context_source, _unlabelled_identifiers(context_source)):
        # Ein Leerzeichen fuer jede Leerraumfolge (Umbruch, Tabulator, Doppelleerzeichen): Entscheidungen sind einzeilig;
        # detect_decision_terms trifft die urspruengliche Schreibweise trotzdem.
        term = re.sub(r"\s+", " ", raw)
        if term in found or term in decisions.not_personal or term in decisions.personal:
            continue
        if not _still_readable(neutral, raw, context_source):
            continue  # vollstaendig durch eine andere Stufe maskiert
        before, after = _context(context_source, raw)
        found[term] = ReviewCandidate(
            term=term, context_before=before, context_after=after, roles=("kennnummernform",), probe_score=0
        )
    return list(found.values())


def _trim_at_detector_spans(text: str, candidates: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Ragt ein Kandidat in einen Treffer eines deterministischen Detektors hinein, der UEBER den Kandidaten hinausreicht
    ("Az. 12 O 345/26 1234567": Kandidat "345/26 1234567"), wird er an dieser Grenze gekuerzt und nur der Rest geprueft
    (3. Review-Runde: sonst gewann das Aktenzeichen die Ueberlappung und "1234567" ging im Klartext hinaus). Liegt der
    Treffer ganz INNERHALB der Nummer (Rufnummernmuster mitten in einer IBAN ohne Laenderkennung), bleibt die ganze Nummer
    Kandidat."""
    # Nur Treffer ohne Zeilenumbruch: im lokalen Rohtext liest der PLZ+Ort-Detektor "08154" vor der naechsten Feldzeile
    # ("\n\n\nFasse ...") als Anschrift - im Versandtext nicht; ein Schnitt dort liesse den Rest ungeschuetzt (Messkette h9).
    spans = [s for detector in _ALL_REGEX_DETECTORS for s in detector(text) if "\n" not in s.value]
    result: list[tuple[str, int]] = []
    for raw, start in candidates:
        end = start + len(raw)
        cut_start, cut_end = start, end
        for span in spans:
            if span.start < end and start < span.end and not (start <= span.start and span.end <= end):
                if span.start <= start:
                    cut_start = max(cut_start, span.end)
                else:
                    cut_end = min(cut_end, span.start)
        if (cut_start, cut_end) == (start, end):
            result.append((raw, start))
        elif cut_start < cut_end:
            # Der Rest stammt aus einer kennnummernfoermigen Folge - er bleibt ab fuenf Ziffern unsicher ("143 815" vor
            # einem als PLZ+Ort gelesenen "08154 Belege").
            # Trennzeichen am Rand abschneiden (4. Runde: "-123456" nach dem Aktenzeichen fand die Lookbehind-Grenze nicht).
            while cut_start < cut_end and text[cut_start] in " \t\r\n-/–‑.,":
                cut_start += 1
            while cut_end > cut_start and text[cut_end - 1] in " \t\r\n-/–‑.,":
                cut_end -= 1
            rest = text[cut_start:cut_end]
            result += [(value, cut_start + offset) for value, offset in _unlabelled_identifiers(rest, min_digit_run=5)]
    return result


def _count_word(text: str, word: str) -> int:
    return len(re.findall(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text))


def _still_readable(scan_text: str, raw: str, raw_text: str) -> bool:
    """Steht ein Teil der Nummer `raw` noch im Versandtext - ein ganzer Block mit mind. zwei Ziffern oder ein Wort ab vier
    Zeichen mit Ziffer, das in einem Block der Nummer vorkommt ("WVWZZZ1JZXW" neben [TELEFON_01], Review)? Gezaehlt wird
    gegen die Vorkommen AUSSERHALB der Nummer im Rohtext (Review: "§ 22 BGB", "Rn. 1884", "10:00 Uhr" machten eine bereits
    vollstaendig maskierte Nummer sonst scheinbar lesbar - unnoetige Rueckfrage)."""
    chunks = re.findall(r"[A-Za-z0-9]+", raw)
    outside = raw_text.replace(raw, " ")
    words = {c for c in chunks if sum(ch.isdigit() for ch in c) >= 2}
    words |= {w for w in re.findall(r"[A-Za-z0-9]{4,}", scan_text) if any(ch.isdigit() for ch in w) and any(w in c for c in chunks)}
    return any(_count_word(scan_text, w) > _count_word(outside, w) for w in words)


# Unbeschriftete Kennnummern (Nummern-Strategie 13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §22). Bezeichnete Nummern maskiert
# bereits die deterministische Stufe (detectors.detect_referenznummer u. a.). Hier bleibt die Form OHNE Bezeichnung
# ("auf 1234567890 bei der Sparkasse", "unter 30018847 registriert", "Ref.-frei KZ-778812"): sie ist UNSICHER - Rueckfrage
# im Modus "ask", vorsichtshalber konsistente Maskierung als [KENNUNG_nn] im Modus "mask". Juristisch/sachlich gebrauchte
# Zahlen bleiben lesbar: Betraege, Daten, Mengen mit Einheit, Fundstellen (§, Art., Rn., BGBl., Drucksachen, EU-Rechtsakte,
# Zeitschriften), Normen, Versionen, Seiten/Anlagen, Artikel-/Ersatzteilnummern.
# Zifferngruppen: getrennt durch Bindestrich, Schraegstrich, ein Leerzeichen oder einen Zeilenumbruch (PDF, Review).
# Review: auch Gedankenstrich und geschuetzter Bindestrich (U+2011). Tabulator und mehrere Leerzeichen bewusst NICHT: sie
# verbanden Tabellenspalten zu einer "Nummer" und eine Zahl mit einem Aktenzeichen davor (3. Review-Runde, gemessen) - eine
# so getrennte Nummer bleibt dokumentierte Restluecke.
_DIGIT_RUN = re.compile(
    r"(?<![\w.,/\-–‑])\d+(?:(?:[\-/–‑]| (?=\d)|[ \t]*\r?\n[ \t]*(?=\d))\d+){0,5}(?![\w/\-–‑]|[.,]\d)"
)
# Mit Punkten gruppiert und OHNE Waehrung/Einheit/Dezimalteil ("unter 123.456.789 registriert", Review) - als Betrag nicht
# erkennbar, also unsicher.
_DOTTED_RUN = re.compile(r"(?<![\w.,/\-–])\d{1,3}(?:\.\d{3}){2,}(?![\w/\-–]|[.,]\d)")
# Codes aus Buchstaben und Ziffern, auch klein geschrieben ("kv20391847", Review) - mindestens ein Buchstabe.
_ALNUM_CODE = re.compile(r"(?<![\w.\-/])(?=[A-Za-z0-9\-/]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9\-/]{4,30}(?![\w\-/])")
_KEEP_BEFORE = re.compile(
    r"(?:§§?|art\.|artikel|nr\.\s*\d*\s*vv|rn\.?|randnummer|bgbl\.?(?:\s*[iI]{1,2})?(?:\s*s\.)?|s\.|seite|bl\.|blatt|"
    r"drs\.|drucksache|ecli:?|din(?:\s+en)?|en|iso|iec|vde|version|v\.|kapitel|"
    # EU-Rechtsakte auch mit "Nr." ("Verordnung (EG) Nr. 1393/2007", "VO 1215/2012", Review)
    r"(?:(?:verordnung|richtlinie|vo|rl)(?:\s*\((?:eu|eg|ewg)\))?|\((?:eu|eg|ewg)\))(?:\s*nr\.)?|"
    r"ziff\.?|ziffer|anlage|ersatzteil|teil|artikel-?\s?nr\.?|art\.-?\s?nr\.?|modell|typ|serie|isbn(?:-1[03])?|tz\.|fn\.|"
    # Objekt-/Warenkennungen (Garantie-/Kaufsachverhalte brauchen sie lesbar); Bankleitzahl ist keine Personenkennung
    r"artikelnummer|teilenummer|materialnummer|modellnummer|serien-?\s?(?:nummer|nr\.?)|ean|gtin|blz|bankleitzahl|juris|"
    r"baujahr|jahr|jahre|summe|plz|(?:betrag|zahlung|forderung|summe)(?:\s+von)?|"
    # Festsetzung des Werts ("Gegenstandswert wird auf 2500000 festgesetzt", Review) - nur diese Verbindungswoerter, damit
    # "den Betrag auf 1234567890" (Kontonummer) NICHT darunter faellt
    r"(?:gegenstands|streit|geschäfts|geschaefts)wert(?:\s+(?:wird|ist|beträgt|betraegt|wurde|auf|von))*)"
    # optional eine Buchstabengruppe dazwischen ("Teilenummer HH AB1234")
    r"\s*[:.]?\s*(?:(?-i:[A-Z]{1,3})\s+)?$",
    re.IGNORECASE,
)
# Zeitschrift/Datenbank + Jahr + Komma ("BeckRS 2021, 1234567", "GVBl. 2020, 1234567", "NJW 2019, 1234") und
# EuGH-/EuG-Aktenzeichen ("C-311/18") - Fundstellen.
_CITATION_BEFORE = re.compile(r"(?-i:[A-ZÄÖÜ])[\wÄÖÜäöü.\-]{1,15}\s+(?:19|20)\d{2},\s*$")
_EU_COURT_CASE = re.compile(r"[CT]-\d{1,4}/\d{2}(?:\s?P)?", re.IGNORECASE)
_KEEP_AFTER = re.compile(
    # Keine Ein-Buchstaben-Einheiten: "66 120380 L 512" (Rentenversicherungsnummer) las sich sonst als "Liter".
    r"\s*(?:eur\b|€|euro\b|%|prozent|km\b|kw\b|ps\b|m²|m2\b|qm\b|stück|stk\.?|kg\b|std\.?|tage|wochen|monate|"
    r"jahre|uhr\b|cm\b|mm\b|liter|einwohner|personen|menschen|mitarbeiter|beschäftigte|fälle|faelle)",
    re.IGNORECASE,
)
_CURRENCY_BEFORE = re.compile(r"(?:eur|€|euro|chf|usd|\$)\s*$", re.IGNORECASE)
# Jahr, Jahresspanne, ISO-Datum - auch mit Stunde dahinter ("2025-01-01 10:00", Logauszug; Messkette h9: sonst Kandidat,
# danach las die NER das Nachbarwort als Person und der Gateway blockierte)
_YEAR_SHAPES = re.compile(r"(?:19|20)\d{2}(?:\s*[-/–]\s*(?:19|20)?\d{2})?|\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2})?")
# Teil einer ECLI-Kennung (oeffentliche Gerichtsentscheidung, "ECLI:DE:BGH:2020:120520UVIZR123.19.0") - Fundstelle.
_INSIDE_ECLI = re.compile(r"ECLI:[A-Z]{2}:\S*$")
# Grundbuch: "Blatt" ist dort die Grundbuchblattnummer (Eigentuemer ueber das Grundbuch bestimmbar), keine Seitenangabe.
_LAND_REGISTER_BEFORE = re.compile(r"grundbuch", re.IGNORECASE)


def _unlabelled_identifiers(text: str, *, min_digit_run: int = 7) -> list[tuple[str, int]]:
    """(Wert, Position) kennnummernfoermiger Folgen ohne Bezeichnung - siehe Kommentar oben."""
    found: list[tuple[str, int]] = []
    for pattern, min_digits in ((_DIGIT_RUN, min_digit_run), (_DOTTED_RUN, 7), (_ALNUM_CODE, 4)):
        for match in pattern.finditer(text):
            value = match.group().strip()
            if sum(ch.isdigit() for ch in value) < min_digits or _YEAR_SHAPES.fullmatch(value):
                continue
            if all(_YEAR_SHAPES.fullmatch(group) for group in re.split(r"\s+", value)):
                continue  # Jahresliste ("2019 2020 2021")
            if _EU_COURT_CASE.fullmatch(value) or _INSIDE_ECLI.search(text[max(0, match.start() - 40) : match.start()]):
                continue
            before = text[max(0, match.start() - 40) : match.start()]
            keep = _KEEP_BEFORE.search(before[-30:])
            if keep and not (re.search(r"bl\.|blatt", keep.group(), re.IGNORECASE) and _LAND_REGISTER_BEFORE.search(before)):
                continue
            if _CITATION_BEFORE.search(before) or _CURRENCY_BEFORE.search(before):
                continue
            if _KEEP_AFTER.match(text, match.end()):
                continue
            if any(start <= match.start() < start + len(v) for v, start in found):
                continue
            found.append((value, match.start()))
    return found


def is_identifier_shaped_term(term: str) -> bool:
    """Einzeilige kennnummernfoermige Folge (siehe _unlabelled_identifiers) - als Entscheidung speicherbar wie ein
    Kennzeichen; enthaelt nur Buchstaben, Ziffern, Leerzeichen, Punkt, Bindestrich und Schraegstrich."""
    # Laenge bis MAX_TERM_LENGTH (4. Runde: ein 49-stelliger Kandidat war nicht speicherbar - endlose Rueckfrage)
    if "\n" in term or len(term) > MAX_TERM_LENGTH or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .\-/–‑]{3,}", term):
        return False
    # Schwelle wie fuer den gekuerzten Rest (_trim_at_detector_spans), sonst liesse sich dessen Entscheidung nicht speichern.
    return any(value == term for value, _ in _unlabelled_identifiers(term, min_digit_run=5))


# Kennzeichenfoermige Zeichenfolge OHNE unmittelbar vorangestellte Bezeichnung (Sicherheitspruefung 13.10.2026, gemessen:
# "Kennzeichen des Gegners: D-XY 123" ging im Klartext hinaus). Bezeichnete Kennzeichen maskiert bereits presidio_ner;
# hier bleibt nur die mehrdeutige Form - sie ist UNSICHER, nicht sicher personenbezogen: Rueckfrage im Modus "ask",
# vorsichtshalber Maskierung im Modus "mask" (kein stiller Klartext-Versand, keine globale Maskierungsregel).
# Gemessen auf 916 vorhandenen synthetischen Texten: 6 Treffer, alle echte Kennzeichen. Gross-/Kleinschreibung zaehlt.
# Review: auch die Schreibweise des Schildes selbst ("D XY 123"), Gedankenstrich aus der Autokorrektur ("D–XY 123") und
# ein Zeilenumbruch vor den Ziffern (PDF) gingen im Klartext hinaus.
_PLATE_SHAPE = re.compile(
    r"(?<![\w\-–])(?P<district>[A-ZÄÖÜ]{1,3})(?P<sep>[-– ])(?P<letters>[A-Z]{1,2})(?:[ \t]?|[ \t]*\r?\n[ \t]*)"
    r"(?P<digits>\d{1,4})[EH]?(?![\w\-–])"
)
# Die reine Leerzeichenform ist deutlich mehrdeutiger (gemessen im Fehlalarm-Satz: "VW T 5", "KIA EV 6", "DER B 51",
# "Ihr Zeichen: SB KL 123" - 12 Fehlalarme). Sie zaehlt nur mit mindestens zwei Ziffern, Fahrzeugbezug in der Naehe,
# ohne Zeichen-/Aktenzeichen-Bezeichnung davor und ohne Funktionswort/Marke als erste Gruppe.
_PLATE_SPACE_CONTEXT = re.compile(
    r"kennz|kfz|fahrzeug|pkw|lkw|auto\b|wagen|krad|motorrad|transporter|halter|zugelassen|zulassung|amtl|"
    r"\bfuhr|gefahren|parkte|geparkt|abgestellt",
    re.IGNORECASE,
)
_PLATE_SPACE_REFERENCE_PREFIX = re.compile(r"\b(?:zeichen|az|aktenzeichen|nr)\.?\s*:?\s*$", re.IGNORECASE)
_PLATE_SPACE_NOT_A_DISTRICT = frozenset(
    # Nur Woerter, die KEIN Unterscheidungszeichen sind (AM, AN, KM, RA, SB, DEM sind Kreise - Review 13.10.2026).
    {"DER", "DIE", "DAS", "DEN", "DES", "BEI", "AUF", "IM", "VON", "ZUR", "ZUM", "UND", "MIT", "ZUG", "VW", "BMW",
     "KIA", "NR", "TYP"}
)


def _plausible_spaced_plate(text: str, match: re.Match) -> bool:
    if len(match.group("digits")) < 2 or match.group("district") in _PLATE_SPACE_NOT_A_DISTRICT:
        return False
    if _PLATE_SPACE_REFERENCE_PREFIX.search(text[max(0, match.start() - 20) : match.start()]):
        return False
    return bool(_PLATE_SPACE_CONTEXT.search(text[max(0, match.start() - 80) : match.end() + 40]))
# Gesetzes-/Normenkuerzel ("SGB V 12", "VOB B 13") und roemische Gliederungs-/Senatsnummern ("II A 3", "IX ZR 123/20")
# sind keine Unterscheidungszeichen. Review: KEIN echtes Unterscheidungszeichen in dieser Liste (EN, GG, EU, UN, V, DIN
# ... sind Kreise - deren Kennzeichen gingen sonst wieder im Klartext hinaus). "V" fehlt deshalb bei den roemischen Zahlen.
_ROMAN_NUMERALS = ("I II III IV VI VII VIII IX X XI XII XIII XIV XV XVI XVII XVIII XIX XX").split()
_PLATE_SHAPE_NOT_A_DISTRICT = frozenset({"ISO", "VDE", "VOB", "SGB", "BGB", "ZPO", "HGB", "IEC", *_ROMAN_NUMERALS})


def _is_citation_not_plate(text: str, match: re.Match) -> bool:
    """Norm "DIN EN 1504" und Zeitschriften-Report "NJW-RR 2020, 1234" (Jahr + Komma) - eng gefasst, weil DIN und die
    Buchstaben RR auch in echten Kennzeichen vorkommen."""
    if match.group("district") == "DIN" and match.group("letters") == "EN":
        return True
    return (
        match.group("letters") == "RR"
        and len(match.group("digits")) == 4
        and re.match(r"\s*,", text[match.end() :]) is not None
    )


def is_plate_shaped_term(term: str) -> bool:
    """Einzeiliger kennzeichenfoermiger Begriff - die einzige mehrteilige Form, die als Entscheidung gespeichert werden darf
    (decisions._validate). Sie enthaelt nur Buchstaben, Bindestrich/Gedankenstrich, Leerzeichen und Ziffern."""
    return "\n" not in term and _PLATE_SHAPE.fullmatch(term) is not None


def detect_decision_terms(text: str, terms: set[str] | frozenset[str]) -> list[DetectedSpan]:
    """Fundstellen der als personenbezogen entschiedenen Begriffe (exakte Schreibung, ganze Woerter - "Richter" trifft
    nicht "Schiedsrichter"). Kategorie "person"; kennzeichenfoermige Begriffe (siehe _PLATE_SHAPE) "kennung"."""
    spans: list[DetectedSpan] = []
    for term in sorted(terms, key=len, reverse=True):
        if not term or not term.strip():
            continue
        plate = is_plate_shaped_term(term)
        identifier = plate or is_identifier_shaped_term(term)
        category = "kennung" if identifier else "person"
        body = re.escape(term)
        if identifier:  # Kennzeichen/Kennnummer darf im Text ueber einen Zeilenumbruch laufen (PDF), find_review_candidates
            # Leerzeichen im Begriff = Leerzeichen/Tab ODER Zeilenumbruch (auch Windows-CRLF, Review) - nie "kein Abstand"
            body = body.replace(r"\ ", r"(?:[ \t]+|[ \t]*\r?\n[ \t]*)")
        # Kurze Kennnummer-Entscheidung (weniger als 7 Ziffern, etwa ein gekuerzter Rest): nicht an Fundstellen-, Betrags-
        # oder Einheitsstellen und nicht als Teil einer laengeren Zahl ("19/12345", "§ 12345", "Streitwert 12345 EUR",
        # 4. Review-Runde) - dort ist dieselbe Ziffernfolge keine Kennnummer.
        short = identifier and not plate and sum(ch.isdigit() for ch in term) < 7
        # Bindestrich davor bleibt erlaubt: der Rest haengt oft am Aktenzeichen ("345/26-123456").
        boundary_before = r"(?<![\wÄÖÜäöüß/.,])" if short else r"(?<![\wÄÖÜäöüß])"
        pattern = re.compile(boundary_before + body + r"(?![\wÄÖÜäöüß])")
        for match in pattern.finditer(text):
            if any(s.start < match.end() and match.start() < s.end for s in spans):
                continue
            if short and _number_in_keep_context(text, match.start(), match.end()):
                continue
            spans.append(DetectedSpan(category=category, start=match.start(), end=match.end(), value=match.group()))
    return spans


def _number_in_keep_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 40) : start]
    return bool(
        _KEEP_BEFORE.search(before[-30:]) or _CITATION_BEFORE.search(before) or _CURRENCY_BEFORE.search(before)
        or _KEEP_AFTER.match(text, end) or re.match(r"/\d|[.,]\d", text[end : end + 2])
    )
