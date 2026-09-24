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
    return _matches_from_pattern(text, _AKTENZEICHEN_PATTERN, "aktenzeichen", group=1)


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


def detect_all(
    text: str,
    known_entities: dict[str, list[str]] | None = None,
    *,
    ner_detector: Callable[[str], list[DetectedSpan]] | None = None,
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
    """
    all_spans: list[DetectedSpan] = []
    for detector in _ALL_REGEX_DETECTORS:
        all_spans.extend(detector(text))
    if known_entities:
        all_spans.extend(detect_known_entities(text, known_entities))
    if ner_detector is not None:
        all_spans.extend(ner_detector(text))

    return _resolve_overlaps(all_spans)


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
