"""Lokal berechnete zeitliche Einordnung der pseudonymisierten Datumsangaben.

ROOT CAUSE (Real-E2E 08.10., Request-Capture der installierten Lexono.exe):
Datumsangaben werden korrekt zu `[DATUM_NN]` pseudonymisiert (Kategorie
"datum"). Claude sieht damit nur undurchsichtige Marken und kann weder
Reihenfolge noch Abstaende noch "abgelaufen oder nicht" ableiten. Folgen in
den Antworten: "aus den Platzhaltern nicht ablesbar", ein Briefdatum VOR der
"neuen" Frist, Fristenlisten in falscher Reihenfolge.

Loesung per strukturierter LOKALER Verarbeitung statt Prompt-Workaround: aus
den echten Werten (nur lokal bekannt) werden Reihenfolge, Tagesabstaende und
die Lage relativ zu heute berechnet und als Argumentationspunkt NUR mit
Platzhaltern und Zahlen an Claude uebergeben. Ein absolutes Datum ist darin
nie enthalten; offengelegt werden ausschliesslich Beziehungen zwischen den
Datumsangaben desselben Auftrags (Reihenfolge, Abstand in Tagen) und ein Bit
je Datum (vergangen/heute/zukuenftig). Das ist eine bewusste, dokumentierte
Abwaegung zugunsten fachlich korrekter Fristenarbeit."""

from __future__ import annotations

import re
from datetime import date

from app.privacy.pseudonymizer import PseudonymMapping

_NUMERIC = re.compile(r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{2,4})")
_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_MONTH_NAME = re.compile(
    r"(\d{1,2})\.\s*(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.IGNORECASE
)

#: Mehr Datumsangaben machen die Einordnung unuebersichtlich und die Zeile zu lang.
MAX_DATES = 20

CHRONOLOGY_MARKER = "Zeitliche Einordnung der Datumsangaben"


def parse_german_date(value: str) -> date | None:
    """Parst "02.10.2026", "2.10.26" und "2. Oktober 2026"; sonst `None`."""
    text = value.strip()
    match = _NUMERIC.fullmatch(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if year < 100:
            year += 2000 if year <= 69 else 1900
    else:
        named = _MONTH_NAME.fullmatch(text)
        if not named:
            return None
        day, year = int(named.group(1)), int(named.group(3))
        month = _MONTHS[named.group(2).lower()]
    try:
        return date(year, month, day)
    except ValueError:
        return None


def build_chronology_note(
    mappings: list[PseudonymMapping], *, today: date | None = None
) -> str | None:
    """Liefert die Einordnungszeile oder `None` (keine auswertbaren Daten)."""
    today = today or date.today()
    dated: dict[str, date] = {}
    for mapping in mappings:
        if mapping.category != "datum":
            continue
        parsed = parse_german_date(mapping.original_value)
        if parsed is not None:
            dated[mapping.placeholder] = parsed
    if not dated or len(dated) > MAX_DATES:
        return None

    ordered = sorted(dated.items(), key=lambda item: (item[1], item[0]))
    parts: list[str] = []

    # Gleiche Tage unter verschiedenen Schreibweisen zusammenfassen.
    by_day: dict[date, list[str]] = {}
    for placeholder, day in ordered:
        by_day.setdefault(day, []).append(placeholder)
    days = sorted(by_day)

    if len(days) > 1:
        sequence = " → ".join(" = ".join(by_day[d]) for d in days)
        parts.append(f"Reihenfolge von früh nach spät: {sequence}.")
        gaps = [
            f"{' = '.join(by_day[a])} bis {' = '.join(by_day[b])}: {(b - a).days} Tage"
            for a, b in zip(days, days[1:])
        ]
        parts.append("Abstände zwischen aufeinanderfolgenden Daten: " + "; ".join(gaps) + ".")
    elif len(by_day[days[0]]) > 1:
        parts.append("Gleiches Datum: " + " = ".join(by_day[days[0]]) + ".")

    past = [p for d in days if d < today for p in by_day[d]]
    same = [p for d in days if d == today for p in by_day[d]]
    future = [p for d in days if d > today for p in by_day[d]]
    relative: list[str] = []
    if past:
        relative.append("in der Vergangenheit: " + ", ".join(past))
    if same:
        relative.append("heute: " + ", ".join(same))
    if future:
        relative.append("in der Zukunft: " + ", ".join(future))
    parts.append("Gegenüber dem heutigen Tag liegen " + "; ".join(relative) + ".")

    return (
        f"{CHRONOLOGY_MARKER} (lokal berechnet und verlässlich; absolute Daten sind "
        "nicht enthalten): " + " ".join(parts)
    )
