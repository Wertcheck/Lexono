"""PII-Erkennung nach Kategorien.

Siehe __init__.py für die Gesamtstrategie (Regex + bekannte Entitäten).
Jeder Detektor liefert `DetectedSpan`-Objekte mit Position im Originaltext,
damit der Pseudonymizer präzise nur den betroffenen Ausschnitt ersetzen
kann (nicht den ganzen Satz).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_GERMAN_MONTHS = (
    "januar|februar|märz|maerz|april|mai|juni|juli|august|september|"
    "oktober|november|dezember"
)


@dataclass
class DetectedSpan:
    category: str
    start: int
    end: int
    value: str


# --- Regex-Muster für strukturierte Formate ---------------------------

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Bewusst nicht erschöpfend (deutsche Rufnummern haben viele Schreib-
# weisen) - Einschränkung ist dokumentiert, siehe __init__.py.
_PHONE_PATTERN = re.compile(
    r"(?:\+49|0049|0)\s?\(?\d{2,5}\)?[\s/\-]?\d{3,10}(?:[\s\-]?\d{2,6})?"
)

_IBAN_PATTERN = re.compile(
    r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,6}(?:\s?[A-Z0-9]{1,4})?\b"
)

# Deutsche Steuer-ID: 11 Ziffern, oft gruppiert. Kein Check-Digit-
# Algorithmus implementiert (Format-Erkennung, keine Validierung).
_STEUER_ID_PATTERN = re.compile(r"\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b")

_AKTENZEICHEN_PATTERN = re.compile(
    r"(?:az\.?|aktenzeichen)\s*[:.]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{2,20})",
    re.IGNORECASE,
)

_KUNDENNUMMER_PATTERN = re.compile(
    r"(?:kundennummer|kd-nr\.?|mandantennummer)\s*[:.]?\s*([A-Za-z0-9\-/]+)",
    re.IGNORECASE,
)

_VERTRAGSNUMMER_PATTERN = re.compile(
    r"(?:vertragsnummer|vertrags-nr\.?)\s*[:.]?\s*([A-Za-z0-9\-/]+)",
    re.IGNORECASE,
)

_NUMERIC_DATE_PATTERN = re.compile(r"\b\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}\b")
_MONTH_NAME_DATE_PATTERN = re.compile(
    rf"\b\d{{1,2}}\.\s*(?:{_GERMAN_MONTHS})\s+\d{{4}}\b", re.IGNORECASE
)

_AMOUNT_PATTERN = re.compile(
    r"\b\d{1,3}(?:\.\d{3})*,\d{2}\s?€"
    r"|€\s?\d{1,3}(?:\.\d{3})*,\d{2}\b"
    r"|\bEUR\s?\d+(?:,\d{2})?\b",
    re.IGNORECASE,
)

# Straße + Hausnummer, sowie getrennt PLZ + Ort.
#
# ECHTER FUND (14.09., Overnight-Direktive Sec12-13 Performance-Benchmark
# eines realen Mietrecht-Schreibens): eine echte Adresse "Elbchaussee 45"
# wurde von diesem Regex-Muster NICHT erfasst (Suffix "chaussee" fehlte in
# der Liste) - blieb dadurch bei der PSEUDONYMISIERUNG unerkannt und wurde
# erst beim spaeteren Restrisiko-Scan (Punkt 2-4, security_check.py) durch
# Presidio/spaCy-NER auf dem TEILWEISE bereits pseudonymisierten Text
# gefunden (inkonsistent zum ersten Durchlauf, da der veraenderte Kontext -
# ein direkt benachbarter, bereits ersetzter Platzhalter - die NER-
# Vorhersage beeinflusst). Ergebnis: kein Datenschutzverstoss (Fail-Closed
# hat korrekt blockiert, bevor irgendetwas an die Cloud ging), aber ein
# unnoetig blockierter, vollkommen gewoehnlicher Kanzleivorgang. Ergaenzung
# um weitere real gebraeuchliche deutsche Strassennamen-Suffixe, damit
# solche Adressen bereits beim ERSTEN, deterministischen Durchlauf sicher
# erkannt werden, statt sich allein auf die (nachweislich Kontext-
# abhaengige) NER-Erkennung zu verlassen.
_STREET_PATTERN = re.compile(
    r"\b[A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ]+(?:straße|strasse|weg|allee|platz|gasse|ring|"
    r"chaussee|damm|ufer|steig|promenade|wall|steg|anger)\s?\d+[a-z]?\b"
)
_POSTAL_CODE_CITY_PATTERN = re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+\b")


def _matches_from_pattern(
    text: str, pattern: re.Pattern, category: str, *, group: int = 0
) -> list[DetectedSpan]:
    spans: list[DetectedSpan] = []
    for match in pattern.finditer(text):
        spans.append(
            DetectedSpan(
                category=category,
                start=match.start(group),
                end=match.end(group),
                value=match.group(group),
            )
        )
    return spans


def detect_email(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _EMAIL_PATTERN, "email")


def detect_phone(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _PHONE_PATTERN, "telefon")


def detect_iban(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _IBAN_PATTERN, "iban")


def detect_steuer_id(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _STEUER_ID_PATTERN, "steuer_id")


def detect_aktenzeichen(text: str) -> list[DetectedSpan]:
    """ECHTER FUND, LIVE REPRODUZIERT (05.10., Owner-Direktive
    "Abschließende Live-Verifikation nach Aufladung des Anthropic-
    Guthabens"): `_AKTENZEICHEN_PATTERN` verlangt nach "Aktenzeichen"/
    "Az." nur IRGENDEIN naechstes Wort, kein tatsaechliches
    Nummernformat. Ein Claude-Entwurf, der ehrlich auf ein FEHLENDES
    Aktenzeichen hinweist ("Das Aktenzeichen der Gegenseite ist nicht
    uebermittelt" / "Vollstaendiges Aktenzeichen und Postanschrift..."),
    verwendet das Wort "Aktenzeichen" in ganz normaler Flusssprache - das
    Muster fing dabei faelschlich die naechsten Woerter ("der", "und")
    als vermeintlichen Aktenzeichen-WERT ein. Diese wurden dadurch als
    `original_value="der"`/`"und"` pseudonymisiert - zwei der haeufigsten
    deutschen Woerter ueberhaupt, die zwangslaeufig an anderer Stelle
    desselben Texts erneut (unpseudonymisiert) auftauchen und dadurch
    den nachgelagerten Original-Leck-Check ausloesten, obwohl kein
    einziges echtes Aktenzeichen im Text stand - blockierte dadurch eine
    voellig unauffaellige Folgefrage vollstaendig.

    Ein echtes Aktenzeichen enthaelt IMMER mindestens eine Ziffer (z. B.
    "123/24", "5 O 123/22", "VN-2024-88471") - "der"/"und" tun das nie.
    Dieser Filter aendert NICHTS an der eigentlichen Regex (bewusst
    minimal-invasiv, keine Neuformulierung des bestehenden Musters) und
    SCHWAECHT die Erkennung nicht: ein echtes Aktenzeichen wird weiterhin
    zuverlaessig erfasst, siehe tests/test_privacy_detectors.py."""
    spans = _matches_from_pattern(text, _AKTENZEICHEN_PATTERN, "aktenzeichen", group=1)
    return [s for s in spans if any(ch.isdigit() for ch in s.value)]


def detect_kundennummer(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _KUNDENNUMMER_PATTERN, "kundennummer", group=1)


def detect_vertragsnummer(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _VERTRAGSNUMMER_PATTERN, "vertrag", group=1)


def detect_datum(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _NUMERIC_DATE_PATTERN, "datum")
    spans += _matches_from_pattern(text, _MONTH_NAME_DATE_PATTERN, "datum")
    return spans


def detect_betrag(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _AMOUNT_PATTERN, "betrag")


def detect_address(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _STREET_PATTERN, "adresse")
    spans += _matches_from_pattern(text, _POSTAL_CODE_CITY_PATTERN, "adresse")
    return spans


def detect_known_entities(
    text: str, known_entities: dict[str, list[str]]
) -> list[DetectedSpan]:
    """Sucht exakt nach bekannten Werten (z. B. Namen aus Party/Client) -
    siehe Moduldocstring in __init__.py zur Begründung dieses Ansatzes."""
    spans: list[DetectedSpan] = []
    for category, values in known_entities.items():
        for value in values:
            if not value or not value.strip():
                continue
            pattern = re.compile(re.escape(value), re.IGNORECASE)
            spans += _matches_from_pattern(text, pattern, category)
    return spans


_ALL_REGEX_DETECTORS = (
    detect_email,
    detect_phone,
    detect_iban,
    detect_steuer_id,
    detect_aktenzeichen,
    detect_kundennummer,
    detect_vertragsnummer,
    detect_datum,
    detect_betrag,
    detect_address,
)


#: Mindestlaenge fuer `_extend_with_repeated_occurrences` (siehe dort) -
#: verhindert, dass ein sehr kurzer, ohnehin unsicherer NER-/Regex-Treffer
#: (z. B. ein abgeschnittenes Fragment) blind im gesamten Text wiederholt
#: gesucht wird und dadurch neue, eigene Fehlalarme erzeugt.
_MIN_REPEATED_OCCURRENCE_LENGTH = 4


def _extend_with_repeated_occurrences(
    text: str, spans: list[DetectedSpan]
) -> list[DetectedSpan]:
    """ECHTER FUND, live reproduziert (05.10., Owner-Direktive
    "Vollstaendiger UX- und Workflow-Audit"): Presidios NER-Erkennung ist
    INNERHALB EINES EINZIGEN Textes nicht zwingend konsequent - derselbe
    Wert ("Bekanntgabefiktion", ein deutscher Rechtsbegriff, fälschlich
    als Ort erkannt) wurde an einer Stelle (in einer Zwischenüberschrift)
    als Entität erkannt und ersetzt, an einer ANDEREN Stelle desselben
    Textes (eingebettet in einem normalen Satz) dagegen NICHT - abhängig
    vom jeweiligen Satzkontext der einzelnen Fundstelle. Ergebnis: der
    Originalwert blieb an der zweiten Stelle woertlich im pseudonymisierten
    Text stehen und loeste beim nachgelagerten Leck-Check
    (`check_response_placeholder_integrity`) einen Abbruch aus - ein
    bereits bekannter, in diesem Modul fuer EINEN konkreten Fall
    ("Elbchaussee 45", siehe `_STREET_PATTERN`-Kommentar oben) bereits
    dokumentiertes Fundmuster, hier erstmals ALLGEMEIN behoben statt nur
    fuer das eine, damals betroffene Regex-Muster.

    Prinzip (identisch zu `detect_known_entities` oben, hier auf NEU per
    NER/Regex gefundene Werte erweitert): ist ein Wert IRGENDWO im Text
    einmal als Entität erkannt worden, werden ALLE weiteren wortgrenzen-
    genauen Vorkommen DESSELBEN Werts im selben Text ebenfalls als
    dieselbe Kategorie behandelt - unabhaengig davon, ob der jeweilige
    Satzkontext die urspruengliche NER-Erkennung an dieser Stelle
    individuell bestaetigt haette. Macht die Pseudonymisierung
    KONSEQUENTER (strikt zusaetzliche Treffer, nie weniger), nicht
    schwaecher - kein bestehender, bereits erkannter Fund wird dadurch
    entfernt oder uebersprungen."""
    covered = [(s.start, s.end) for s in spans]
    seen_values: set[tuple[str, str]] = set()
    extra: list[DetectedSpan] = []
    for span in spans:
        key = (span.category, span.value.lower())
        if key in seen_values or len(span.value) < _MIN_REPEATED_OCCURRENCE_LENGTH:
            continue
        seen_values.add(key)
        pattern = re.compile(r"\b" + re.escape(span.value) + r"\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            if any(match.start() < c_end and match.end() > c_start for c_start, c_end in covered):
                continue
            extra.append(
                DetectedSpan(
                    category=span.category, start=match.start(), end=match.end(), value=match.group(0)
                )
            )
            covered.append((match.start(), match.end()))
    if not extra:
        return spans
    return _resolve_overlaps(spans + extra)


def detect_all(
    text: str,
    known_entities: dict[str, list[str]] | None = None,
    *,
    ner_detector: Callable[[str], list[DetectedSpan]] | None = None,
    skip_categories: frozenset[str] = frozenset(),
) -> list[DetectedSpan]:
    """Führt alle Detektoren aus und löst Überlappungen auf.

    Bei überlappenden Treffern gewinnt der LÄNGERE Treffer (spezifischer)
    - z. B. eine bekannte Entität, die zufällig auch Teil eines
    Datums-/Zahlenmusters wäre. Bei gleicher Länge gewinnt der zuerst
    hinzugefügte Treffer (stabile Sortierung) - deshalb werden `ner_detector`-
    Treffer bewusst NACH `known_entities` angehängt: eine bekannte,
    rollenzugeordnete Entität soll einer generischen NER-Erkennung (siehe
    app/privacy/presidio_ner.py, optional per `ner_detector` injiziert)
    vorgehen.

    `skip_categories` (optional, ECHTER FUND Owner-Direktive "Architektur-
    Audit Privacy-/Chat-Pipeline", 07.10., per Live-QA real reproduziert):
    entfernt Treffer dieser Kategorien VOR `_resolve_overlaps`, nicht erst
    danach. Grund: `_resolve_overlaps` verwirft bei einer Ueberlappung den
    KUERZEREN Treffer UNWIDERRUFLICH (siehe dort) - wuerde man stattdessen
    ERST ueberlappungs-aufloesen und DANACH nach Kategorie filtern, koennte
    ein laengerer "organisation"-Treffer einen kuerzeren, NICHT zu
    ueberspringenden Treffer einer ANDEREN Kategorie (z. B. "ort") in der
    Ueberlappungs-Aufloesung verdraengen - wird der "organisation"-Treffer
    danach herausgefiltert, bleibt fuer diese Textstelle GAR KEIN Treffer
    mehr uebrig, obwohl die andere Kategorie dort haette erkannt werden
    muessen. Real reproduziert: "(Deutschland)" in "Bundeskanzler
    (Deutschland)" wurde durch einen ueberlappenden, laenger reichenden
    "organisation"-Treffer verdraengt - nach dessen nachtraeglicher
    Filterung blieb "Deutschland" komplett unpseudonymisiert im
    ausgehenden Payload. Mit `skip_categories` VOR der Aufloesung nimmt
    der kuerzere "ort"-Treffer stattdessen korrekt am Wettbewerb teil und
    gewinnt, falls kein anderer (nicht uebersprungener) Treffer dieselbe
    Stelle beansprucht - identisches Ergebnis, als haette der
    uebersprungene Detektor diese Stelle nie gemeldet.

    Abschliessend `_extend_with_repeated_occurrences` (05.10., siehe dort):
    stellt sicher, dass ein einmal irgendwo erkannter Wert konsequent an
    JEDER Stelle im Text erfasst wird, nicht nur dort, wo der jeweilige
    Satzkontext die NER-Erkennung individuell bestaetigt hat.
    """
    all_spans: list[DetectedSpan] = []
    for detector in _ALL_REGEX_DETECTORS:
        all_spans.extend(detector(text))
    if known_entities:
        all_spans.extend(detect_known_entities(text, known_entities))
    if ner_detector is not None:
        all_spans.extend(ner_detector(text))

    if skip_categories:
        all_spans = [span for span in all_spans if span.category not in skip_categories]

    resolved = _resolve_overlaps(all_spans)
    return _extend_with_repeated_occurrences(text, resolved)


def _resolve_overlaps(spans: list[DetectedSpan]) -> list[DetectedSpan]:
    # Sortiere nach Startposition, bei Gleichstand nach Länge absteigend
    # (längerer/spezifischerer Treffer zuerst).
    sorted_spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    resolved: list[DetectedSpan] = []
    last_end = -1
    for span in sorted_spans:
        if span.start >= last_end:
            resolved.append(span)
            last_end = span.end
        # Ueberlappender, kuerzerer/spaeterer Treffer wird verworfen.
    return resolved
