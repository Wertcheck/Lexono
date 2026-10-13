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


def _personal_date_spans(text: str) -> list[tuple[int, int]]:
    """Geburts- und Todesdaten im Text (Datumspolicy §23/§24): sie sind keine Frist. Vorher schlug die Fristerkennung jedes
    Datum vor - ein (maskiertes) Geburtsdatum erschien als "Mögliche Frist" im Argumentationspunkt (installierter Build
    953dc8d, Fall 18). Erkannt mit denselben Regeln wie in der Pseudonymisierung (app/privacy/detectors.py)."""
    from app.privacy.detectors import detect_geburtsdatum, detect_todesdatum

    return [(s.start, s.end) for s in (*detect_geburtsdatum(text), *detect_todesdatum(text))]


def is_personal_date(source_text: str | None, due_date: date | None, full_text: str | None = None) -> bool:
    """True, wenn `due_date` als Geburts-/Todesdatum steht - fuer bereits gespeicherte Fristvorschlaege aus der Zeit vor
    der Korrektur (sie sollen nicht mehr als "Mögliche Frist" erscheinen). `full_text` (Volltext des Quelldokuments)
    erkennt auch Tabellenzeilen, deren Kopfzeile im gespeicherten Ausschnitt fehlt (Review §24). Ein Ausschnitt mit
    Fristen-Schluesselwort gilt nie als Geburts-/Todesdatum - eine echte Frist geht nicht verloren."""
    if not source_text or due_date is None:
        return False
    if any(keyword in source_text.lower() for keyword in _DEADLINE_KEYWORDS):
        return False
    from app.privacy.date_chronology import parse_german_date
    from app.privacy.detectors import detect_geburtsdatum, detect_todesdatum

    return any(
        parse_german_date(span.value) == due_date
        for text in (source_text, full_text) if text
        for span in (*detect_geburtsdatum(text), *detect_todesdatum(text))
    )


def trim_partial_personal_dates(snippet: str | None, full_text: str | None) -> str | None:
    """Gespeicherte Fristausschnitte aus der Zeit vor der Korrektur koennen mit dem Rest eines zerschnittenen Geburts-
    oder Todesdatums beginnen oder enden ("1940, verstorben am ..."). Ein solches Rand-Bruchstueck wird entfernt, wenn es
    Teil eines im Ausschnitt oder Quelldokument erkannten Geburts-/Todesdatums ist (§24)."""
    if not snippet:
        return snippet
    from app.privacy.detectors import detect_geburtsdatum, detect_todesdatum

    personal = {
        span.value for text in (snippet, full_text) if text for span in (*detect_geburtsdatum(text), *detect_todesdatum(text))
    }
    words = snippet.split(" ")

    def is_fragment(word: str, *, head: bool) -> bool:
        core = word.strip(" ,;:()")
        if not core:
            return False
        # Zahlen-Bruchstueck am Rand, das kein vollstaendiges Datum ist ("1940,", "03.1940") - ohne Quelldokument nicht
        # pruefbar, ob es zu einem Geburts-/Todesdatum gehoerte; inhaltlich entbehrlich
        if re.fullmatch(r"\d{1,4}(?:\.\d{1,4}){0,1}\.?", core) and not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", core):
            return True
        return any(core != value and (value.endswith(core) if head else value.startswith(core)) for value in personal)

    while words and is_fragment(words[0], head=True):
        words.pop(0)
    while words and is_fragment(words[-1], head=False):
        words.pop()
    return " ".join(words).strip()


_ANY_DATE = re.compile(r"\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}|\d{1,2}\.\s*[A-Za-zÄÖÜäöü]+\s+\d{4}")


def _keyword_belongs_to(text: str, match: re.Match) -> bool:
    """Gehoert ein Fristen-Schluesselwort zu DIESEM Datum? Nur wenn zwischen Schluesselwort und Datum kein anderes Datum
    steht (Risikoanalyse §25: "geb. 12.03.1980, Frist bis 30.11.2026" machte sonst auch das Geburtsdatum zum Vorschlag)."""
    before = text[max(0, match.start() - _CONTEXT_WINDOW_CHARS) : match.start()]
    lower = before.lower()
    last = max((lower.rfind(k) for k in _DEADLINE_KEYWORDS), default=-1)
    if last >= 0 and not _ANY_DATE.search(before[last:]):
        return True
    after = text[match.end() : match.end() + _CONTEXT_WINDOW_CHARS]
    lower_after = after.lower()
    first = min((i for i in (lower_after.find(k) for k in _DEADLINE_KEYWORDS) if i >= 0), default=-1)
    if first < 0 or _ANY_DATE.search(after[:first]):
        return False
    # Ein Schluesselwort NACH dem Datum gehoert nur dazu, wenn es nicht selbst ein eigenes Datum einleitet
    # ("geb. 12.03.1980, Frist ... bis 30.11.2026": "Frist" gehoert zum 30.11.2026)
    return not _ANY_DATE.search(after[first : first + 40])


class PlaceholderDeadlineExtractor:
    def extract(self, text: str) -> list[ExtractedDeadline]:
        results: list[ExtractedDeadline] = []
        seen_spans: set[tuple[int, int]] = set()
        personal = _personal_date_spans(text)

        def is_personal(match: re.Match) -> bool:
            # Mit Fristen-Schluesselwort in der Naehe bleibt das Datum IMMER ein Vorschlag (Review §24: eine echte Frist
            # darf durch eine falsche Geburts-/Todesdatum-Erkennung nie verloren gehen; der Text laeuft ohnehin durch den
            # Gateway und wird dort maskiert, falls er ein solches Datum enthaelt).
            if _keyword_belongs_to(text, match):
                return False  # auch ein Schluesselwort NACH dem Datum ("05.03.2024: Termin beim Nachlassgericht")
            return any(start < match.end() and match.start() < end for start, end in personal)

        for match in _NUMERIC_DATE_PATTERN.finditer(text):
            if is_personal(match):
                continue  # Geburts-/Todesdatum ist keine Frist
            result = self._handle_numeric_date(text, match)
            if result:
                results.append(result)
                seen_spans.add(match.span())

        for match in _MONTH_NAME_PATTERN.finditer(text):
            if match.span() in seen_spans or is_personal(match):
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
        # DRITTER FUND (installierter Build 5cafe0f, Fall 19, §24): auch ein Datum darf nicht zerschnitten werden - der Punkt
        # in "12.03.1940" ist keine Wortgrenze. Der Ausschnitt begann bei "1940," und gab das Geburtsjahr als Bruchstueck
        # weiter, das keine Datumserkennung mehr trifft. Deshalb bis zum naechsten Leerraum einrasten.
        while window_start > 0 and not text[window_start - 1].isspace() and not text[window_start].isspace():
            window_start -= 1
        while window_end < len(text) and not text[window_end - 1].isspace() and not text[window_end].isspace():
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
