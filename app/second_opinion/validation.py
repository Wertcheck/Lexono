"""Strikte Pruefung einer Cloud-Zweitmeinung (§43, Stufe 1) - rein lokal, ohne Netz.

Die Antwort eines Cloud-Modells ist untrusted input. Angenommen wird sie nur, wenn sie GENAU dem erwarteten,
kleinen JSON-Schema entspricht; sonst wird sie als Ganzes verworfen (fail-closed, keine Teiluebernahme):

- nur die Felder `typ`, `frist`, `termin`, `fristbeginn` (Pflicht) und `fristdauern` (optional), kein Freitext;
- `typ` aus der festen Lexono-Typliste (`ALLOWED_DOCUMENT_TYPES`);
- Daten nur als gueltiges Kalenderdatum im Format TT.MM.JJJJ;
- jedes Datum muss im TATSAECHLICH gesendeten Text vorkommen (auch als 1.7.2026, 01.07.26 oder 1. Juli 2026) -
  ein erfundenes Datum verwirft die ganze Antwort;
- kein Datum in zwei Arten zugleich (widerspruechlich), keine Platzhalter-Tokens, Groessengrenzen.

Ausserdem die Laengenpruefung VOR einem Versand: ein zu langer Text wird NICHT gekuerzt, sondern gar nicht gesendet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from app.classification.schema import ALLOWED_DOCUMENT_TYPES

DATE_KINDS = ("frist", "termin", "fristbeginn")
_REQUIRED_FIELDS = frozenset({"typ", *DATE_KINDS})
_OPTIONAL_FIELDS = frozenset({"fristdauern"})

MAX_RESPONSE_CHARS = 4000
MAX_DATES = 30
MAX_DURATIONS = 50

_STRICT_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_CODE_FENCE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
# Platzhalter wie im Gateway ("[PERSON_01]", auch locker geschriebene "[Person_1]") und nackte Tokennamen
# ("PERSON_01") - eine Antwort, die so etwas enthaelt, wird verworfen (vgl. security_check/pseudonymizer).
_PLACEHOLDER = re.compile(r"\[[^\[\]\s]{2,30}_[^\[\]\s]{1,3}\]|\b[A-Z][A-Z]+_\d{2,}\b")

_GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
    "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_NUMERIC_DATE_IN_TEXT = re.compile(r"\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4}|\d{2})\b")
_MONTH_NAME_DATE_IN_TEXT = re.compile(
    r"\b(\d{1,2})\.\s*(" + "|".join(_GERMAN_MONTHS) + r")\s+(\d{4})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ValidatedOpinion:
    """Eine gepruefte Zweitmeinung. Unveraenderlich; enthaelt nur Typ, Daten je Art und die Zahl der Dauern."""

    typ: str
    frist: tuple[date, ...]
    termin: tuple[date, ...]
    fristbeginn: tuple[date, ...]
    fristdauern: int = 0

    def dates_by_kind(self) -> dict[str, tuple[date, ...]]:
        return {"frist": self.frist, "termin": self.termin, "fristbeginn": self.fristbeginn}


@dataclass(frozen=True)
class ValidationResult:
    opinion: ValidatedOpinion | None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        return self.opinion is not None and not self.reasons


@dataclass(frozen=True)
class LengthDecision:
    may_send: bool
    reason: str | None = None


def check_length(text: str, max_chars: int) -> LengthDecision:
    """Laengengrenze VOR dem Versand. Bewusst ohne Kuerzungsfunktion: ein zu langer Text wuerde Abschnitte (und
    darin Fristen) stillschweigend abschneiden - er wird deshalb gar nicht gesendet und als `zu_lang` gemeldet."""
    if max_chars <= 0:
        raise ValueError("max_chars muss positiv sein")
    if not text or not text.strip():
        return LengthDecision(False, "leer")
    if len(text) > max_chars:
        return LengthDecision(False, f"zu_lang: {len(text)} > {max_chars} Zeichen - nicht gekuerzt, nicht gesendet")
    return LengthDecision(True)


def dates_mentioned_in(text: str) -> frozenset[date]:
    """Alle Kalenderdaten, die im Text als Ziffern- oder Monatsnamen-Datum vorkommen (zweistellige Jahre als 20JJ)."""
    found: set[date] = set()
    for day, month, year in _NUMERIC_DATE_IN_TEXT.findall(text):
        full_year = int(year) + 2000 if len(year) == 2 else int(year)
        _add_if_valid(found, full_year, int(month), int(day))
    for day, month_name, year in _MONTH_NAME_DATE_IN_TEXT.findall(text):
        _add_if_valid(found, int(year), _GERMAN_MONTHS[month_name.lower()], int(day))
    return frozenset(found)


def _add_if_valid(found: set[date], year: int, month: int, day: int) -> None:
    try:
        found.add(date(year, month, day))
    except ValueError:
        pass


def validate_response(raw: object, sent_text: str) -> ValidationResult:
    """Prueft die Rohantwort (`str`) gegen den tatsaechlich gesendeten Text. Bei JEDEM Verstoss: verworfen."""
    if not isinstance(raw, str) or not raw.strip():
        return ValidationResult(None, ("keine Antwort",))
    if not isinstance(sent_text, str) or not sent_text.strip():
        return ValidationResult(None, ("kein gesendeter Text zum Abgleich",))
    if len(raw) > MAX_RESPONSE_CHARS:
        return ValidationResult(None, ("Antwort zu lang",))
    if _PLACEHOLDER.search(raw):
        return ValidationResult(None, ("Platzhalter in der Antwort",))
    body = raw.strip()
    fenced = _CODE_FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ValidationResult(None, ("kein gueltiges JSON-Objekt (oder Text ausserhalb des JSON)",))
    if not isinstance(data, dict):
        return ValidationResult(None, ("kein JSON-Objekt",))

    reasons: list[str] = []
    keys = set(data)
    if missing := _REQUIRED_FIELDS - keys:
        reasons.append(f"fehlende Felder: {sorted(missing)}")
    if unknown := keys - _REQUIRED_FIELDS - _OPTIONAL_FIELDS:
        reasons.append(f"unzulaessige Felder: {sorted(unknown)}")
    if reasons:
        return ValidationResult(None, tuple(reasons))

    typ = data["typ"]
    if not isinstance(typ, str) or typ not in ALLOWED_DOCUMENT_TYPES:
        reasons.append("unbekannter Dokumenttyp")

    durations = data.get("fristdauern", 0)
    if isinstance(durations, bool) or not isinstance(durations, int) or not 0 <= durations <= MAX_DURATIONS:
        reasons.append("fristdauern ungueltig")

    mentioned = dates_mentioned_in(sent_text)
    parsed: dict[str, tuple[date, ...]] = {}
    total = 0
    for kind in DATE_KINDS:
        values = data[kind]
        if not isinstance(values, list):
            reasons.append(f"{kind}: keine Liste")
            continue
        kind_dates: list[date] = []
        for value in values:
            parsed_date = _strict_date(value)
            if parsed_date is None:
                reasons.append(f"{kind}: ungueltiges Datum")
            elif parsed_date not in mentioned:
                reasons.append(f"{kind}: Datum nicht im gesendeten Text")
            elif parsed_date not in kind_dates:
                kind_dates.append(parsed_date)
        total += len(kind_dates)
        parsed[kind] = tuple(sorted(kind_dates))
    if total > MAX_DATES:
        reasons.append("zu viele Daten")

    if not reasons:
        seen: dict[date, str] = {}
        for kind in DATE_KINDS:
            for value in parsed[kind]:
                if value in seen:
                    reasons.append(f"widerspruechlich: dasselbe Datum als {seen[value]} und {kind}")
                seen.setdefault(value, kind)

    if reasons:
        return ValidationResult(None, tuple(dict.fromkeys(reasons)))
    return ValidationResult(ValidatedOpinion(typ=typ, frist=parsed["frist"], termin=parsed["termin"],
                                             fristbeginn=parsed["fristbeginn"], fristdauern=durations))


def _strict_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    match = _STRICT_DATE.fullmatch(value)
    if not match:
        return None
    day, month, year = (int(group) for group in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
