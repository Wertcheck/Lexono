"""DeadlineExtractor – Protocol + Platzhalter-Implementierung.

`PlaceholderDeadlineExtractor` erkennt:
1. Absolute Datumsangaben (z. B. "15.03.2027" oder "15. März 2027").
2. Relative Fristangaben (z. B. "binnen zwei Wochen", "innerhalb von 14
   Tagen") - OHNE Bezugsdatum kann daraus kein konkretes `due_date`
   berechnet werden; der Rohtext wird trotzdem festgehalten.

Die Konfidenz ist bewusst niedrig gehalten und hängt davon ab, ob in der
Nähe des gefundenen Datums ein Fristen-Schlüsselwort steht (z. B. "Frist",
"bis zum", "spätestens") - ein bloßes Datum im Text (z. B. ein
Referenzdatum "Ihr Schreiben vom 15.03.2027") ist KEIN verlässlicher
Hinweis auf eine echte Frist und erhält daher eine sehr niedrige Konfidenz.

Wie bei Klassifikation/Aktenzuordnung: kein LLM, keine echte
Fristenberechnung nach Fristenrecht (Wochenend-/Feiertagsregeln,
Zustellfiktionen etc.) - das bleibt Aufgabe des Anwalts bei der manuellen
Prüfung. `review_status` wird von diesem Modul nie gesetzt/verändert.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Protocol

from app.deadlines.schema import ExtractedDeadline

_GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

_DEADLINE_KEYWORDS = (
    "frist",
    "bis zum",
    "bis spätestens",
    "spätestens",
    "spaetestens",
    "binnen",
    "innerhalb",
    "termin",
)

# DD.MM.YYYY oder DD.MM.YY, mit optionalen Leerzeichen um die Punkte.
_NUMERIC_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{2,4})\b"
)
# "15. März 2027" o. ä.
_MONTH_NAME_PATTERN = re.compile(
    r"\b(\d{1,2})\.\s*("
    + "|".join(_GERMAN_MONTHS.keys())
    + r")\s+(\d{4})\b",
    re.IGNORECASE,
)
# "binnen zwei Wochen", "innerhalb von 14 Tagen" o. ä.
_RELATIVE_PERIOD_PATTERN = re.compile(
    r"\b(binnen|innerhalb\s+von)\s+"
    r"(\w+)\s*"
    r"(tag|tage|tagen|woche|wochen|monat|monate|monaten)\b",
    re.IGNORECASE,
)

_CONTEXT_WINDOW_CHARS = 60


class DeadlineExtractor(Protocol):
    def extract(self, text: str) -> list[ExtractedDeadline]: ...


class PlaceholderDeadlineExtractor:
    def extract(self, text: str) -> list[ExtractedDeadline]:
        results: list[ExtractedDeadline] = []
        seen_spans: set[tuple[int, int]] = set()

        for match in _NUMERIC_DATE_PATTERN.finditer(text):
            result = self._handle_numeric_date(text, match)
            if result:
                results.append(result)
                seen_spans.add(match.span())

        for match in _MONTH_NAME_PATTERN.finditer(text):
            if match.span() in seen_spans:
                continue
            result = self._handle_month_name_date(text, match)
            if result:
                results.append(result)

        for match in _RELATIVE_PERIOD_PATTERN.finditer(text):
            results.append(self._handle_relative_period(text, match))

        return results

    def _handle_numeric_date(self, text: str, match: re.Match) -> ExtractedDeadline | None:
        day_str, month_str, year_str = match.groups()
        parsed = self._try_parse_date(day_str, month_str, year_str)
        if parsed is None:
            return None
        context = self._context_window(text, match.start(), match.end())
        has_keyword = self._has_nearby_keyword(text, match.start())
        confidence = 0.5 if has_keyword else 0.15
        reasoning = self._build_reasoning(has_keyword, is_relative=False)
        return ExtractedDeadline(
            source_text=context,
            raw_date_text=match.group(0).strip(),
            due_date=parsed,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _handle_month_name_date(self, text: str, match: re.Match) -> ExtractedDeadline | None:
        day_str, month_name, year_str = match.groups()
        month = _GERMAN_MONTHS.get(month_name.lower())
        if month is None:
            return None
        parsed = self._try_parse_date(day_str, str(month), year_str)
        if parsed is None:
            return None
        context = self._context_window(text, match.start(), match.end())
        has_keyword = self._has_nearby_keyword(text, match.start())
        confidence = 0.5 if has_keyword else 0.15
        reasoning = self._build_reasoning(has_keyword, is_relative=False)
        return ExtractedDeadline(
            source_text=context,
            raw_date_text=match.group(0).strip(),
            due_date=parsed,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _handle_relative_period(self, text: str, match: re.Match) -> ExtractedDeadline:
        context = self._context_window(text, match.start(), match.end())
        reasoning = (
            "Platzhalter-Fristerkennung (regelbasiert, kein LLM). Relative "
            "Fristangabe erkannt, aber ohne Bezugsdatum (z. B. Zugangsdatum) "
            "konnte kein konkretes Fälligkeitsdatum berechnet werden. "
            "Konfidenz bewusst niedrig - manuelle Prüfung erforderlich, "
            "diese Frist gilt NICHT als verbindlich bestätigt."
        )
        return ExtractedDeadline(
            source_text=context,
            raw_date_text=match.group(0).strip(),
            due_date=None,
            confidence=0.35,
            reasoning=reasoning,
        )

    @staticmethod
    def _try_parse_date(day_str: str, month_str: str, year_str: str) -> date | None:
        try:
            day = int(day_str)
            month = int(month_str)
            year = int(year_str)
            if year < 100:
                # Zweistellige Jahreszahl - grobe, konservative Annahme:
                # 20xx (Kanzleidokumente sind praktisch nie vor 2000).
                year += 2000
            return date(year, month, day)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _has_nearby_keyword(text: str, match_start: int) -> bool:
        window_start = max(0, match_start - _CONTEXT_WINDOW_CHARS)
        preceding = text[window_start:match_start].lower()
        return any(keyword in preceding for keyword in _DEADLINE_KEYWORDS)

    @staticmethod
    def _context_window(text: str, start: int, end: int) -> str:
        """ECHTER FUND (realer Abnahme-Test, 13.09.): ein einzelnes
        Leerzeichen statt des Zeilenumbruchs zerstoerte die Grenze zwischen
        einer Ueberschrift und dem direkt folgenden Absatz (z. B. Word-
        Absaetze "2. Schriftverkehr" + "Mit Schreiben vom ..." wurden zu
        "... Schriftverkehr Mit Schreiben ..." verschmolzen). Genau ein
        Leerzeichen zwischen zwei grossgeschriebenen Woertern ist aber exakt
        das Kriterium, mit dem `app/privacy/security_check.py::
        _find_possible_unrecognized_names` einen zusammenhaengenden
        Namens-Kandidaten erkennt (bewusst NUR bei genau einem Leerzeichen,
        siehe dort) - die verschmolzene Ueberschrift+Folgesatz-Grenze wurde
        dadurch faelschlich als moeglicher unerkannter Name gewertet und
        blockierte JEDE Chat-Nachricht in der betroffenen Unterhaltung
        dauerhaft, sobald ein Dokument mit nummerierten Ueberschriften
        angehaengt war - real reproduziert, nicht angenommen. Zwei
        Leerzeichen statt eines bewahren die Absatzgrenze fuer diese
        Pruefung (die dort bewusst NUR einfache Leerzeichen als
        "zusammenhaengende Phrase" wertet), bleiben aber weiterhin ein
        lesbarer einzeiliger Ausschnitt.

        ZWEITER ECHTER FUND (selbe Abnahme-Runde): eine feste Zeichenzahl
        vor/nach dem Treffer kann GENAUSO GUT mitten in ein Wort schneiden
        (z. B. "eschäftigungsmonat" statt "Beschäftigungsmonat", wenn das
        Fenster zufällig einen Buchstaben nach dessen Anfang beginnt).
        Presidios NER erkannte dieses abgeschnittene Fragment faelschlich
        als eigene "Entitaet" - und weil das Fragment ZUFAELLIG ein
        Teilstring des an anderer Stelle korrekt geschriebenen Worts ist,
        meldete das Final Payload Gate (app/privacy/security_check.py::
        check_response_placeholder_integrity) faelschlich einen "geleakten
        Originalwert" und blockierte dadurch JEDE Chat-Nachricht in der
        betroffenen Unterhaltung - real reproduziert, nicht angenommen.
        Deshalb: Fenstergrenzen nach aussen auf die naechste Wortgrenze
        "einrasten" (nie mitten in ein alphanumerisches Wort schneiden)."""
        window_start = max(0, start - _CONTEXT_WINDOW_CHARS)
        window_end = min(len(text), end + _CONTEXT_WINDOW_CHARS)
        while (
            window_start > 0
            and text[window_start - 1].isalnum()
            and text[window_start].isalnum()
        ):
            window_start -= 1
        while (
            window_end < len(text)
            and text[window_end - 1].isalnum()
            and text[window_end].isalnum()
        ):
            window_end += 1
        snippet = text[window_start:window_end].replace("\n", "  ").strip()
        return snippet

    @staticmethod
    def _build_reasoning(has_keyword: bool, *, is_relative: bool) -> str:
        base = "Platzhalter-Fristerkennung (regelbasiert, kein LLM)."
        if has_keyword:
            detail = (
                "Datum in der Nähe eines Fristen-Schlüsselworts gefunden "
                "(z. B. 'Frist', 'bis zum', 'spätestens')."
            )
        else:
            detail = (
                "Datum OHNE erkennbares Fristen-Schlüsselwort in der Nähe - "
                "könnte auch ein reines Referenzdatum sein, keine echte Frist."
            )
        return (
            f"{base} {detail} Konfidenz bewusst niedrig - diese Frist gilt "
            "NICHT als verbindlich bestätigt, manuelle Prüfung erforderlich."
        )
