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
from functools import lru_cache
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


@lru_cache(maxsize=64)
def _personal_date_spans_cached(text: str) -> tuple[tuple[int, int, str], ...]:
    """Geburts-/Todesdaten eines Textes (Position, Wert) - zwischengespeichert, weil derselbe Volltext fuer jeden
    Fristvorschlag eines Dokuments erneut gebraucht wird (Review §34: 30 Vorschlaege x 200k Zeichen ~ 3 s)."""
    from app.privacy.detectors import detect_geburtsdatum, detect_todesdatum

    return tuple(sorted((s.start, s.end, s.value) for s in (*detect_geburtsdatum(text), *detect_todesdatum(text))))


def _personal_date_values(*texts: str | None) -> set[str]:
    return {value for text in texts if text for _start, _end, value in _personal_date_spans_cached(text)}


def _any_spelling(value: str) -> re.Pattern[str] | None:
    """Alle Schreibweisen desselben Datums: "14.07.1988", "14.7.88", "14. 07. 1988", "14. Juli 1988" (Review §34: die
    wertgleiche Maskierung im Gateway erkennt nur die exakte Schreibweise)."""
    from app.privacy.date_chronology import parse_german_date

    parsed = parse_german_date(value)
    if parsed is None:
        return None
    day = rf"0?{parsed.day}\s*\.\s*"
    names = [name for name, number in _GERMAN_MONTHS.items() if number == parsed.month]
    words = "|".join([*names, re.escape(names[0][:3]) + r"\.?"])
    month = rf"(?:0?{parsed.month}\s*\.\s*|(?:{words})\s+)"
    year = rf"(?:{parsed.year}|{parsed.year % 100:02d})"
    return re.compile(rf"(?<![\d.]){day}{month}{year}(?!\d)", re.IGNORECASE)


def remove_unlabelled_personal_dates(excerpt: str, source_text: str | None) -> str:
    """Entfernt aus einem AUSSCHNITT jedes Geburts-/Todesdatum (in jeder Schreibweise), das laut Quelltext eines ist, im
    Ausschnitt aber ohne seine Bezeichnung steht - und Rand-Bruchstuecke davon ("Juli 1988, ..." aus "14. Juli 1988").

    Ohne Bezeichnung ("geb.", "verstorben am") erkennt die Pseudonymisierung ein solches Datum nicht mehr; es ging im
    Klartext hinaus, wenn der Ausschnitt die Bezeichnung abschnitt und der Quelltext nicht selbst im Sachverhalt stand
    (§34: Fristausschnitt; Schluesselstellen langer Dokumente). Im Ausschnitt BEZEICHNETE Vorkommen bleiben fuer den
    Gateway stehen (dort maskiert, lokal mit Altersangabe). Nur ganze Daten (Ziffergrenzen), nie Teile laengerer Nummern."""
    if not excerpt or not source_text:
        return excerpt
    personal = _personal_date_values(excerpt, source_text)
    if not personal:
        return excerpt
    labelled = [(start, end) for start, end, _value in _personal_date_spans_cached(excerpt)]
    removals: list[tuple[int, int]] = []
    for value in personal:
        pattern = _any_spelling(value)
        matches = pattern.finditer(excerpt) if pattern else re.finditer(r"(?<!\d)" + re.escape(value) + r"(?!\d)", excerpt)
        for match in matches:
            if not any(match.start() < end and start < match.end() for start, end in labelled):
                removals.append((match.start(), match.end()))
    for start, end in sorted(set(removals), reverse=True):
        excerpt = excerpt[:start] + "…" + excerpt[end:]
    return _strip_edge_fragments(excerpt, personal)


def _strip_edge_fragments(excerpt: str, personal: set[str]) -> str:
    """Ausschnitt beginnt mit dem Ende / endet mit dem Anfang eines persoenlichen Datums (ab einer Wortgrenze im
    Datum, mindestens vier Zeichen, danach bzw. davor kein Wortzeichen). Hoechstens einmal je Seite (Review §34)."""
    head = excerpt.lstrip()
    for value in personal:
        suffixes = [value[i:] for i in range(1, len(value) - 3) if value[i - 1] in " ." and value[i] != " "]
        hit = next((s for s in suffixes if head.startswith(s) and not head[len(s):len(s) + 1].isalnum()), None)
        if hit:
            excerpt = head[len(hit):].lstrip(" ,;")
            break
    tail = excerpt.rstrip()
    for value in personal:
        prefixes = [value[:j + (1 if value[j] == "." else 0)] for j in range(4, len(value) - 1) if value[j] in " ."]
        hit = next((p for p in prefixes if len(p) >= 4 and tail.endswith(p) and not tail[:-len(p)][-1:].isalnum()), None)
        if hit:
            excerpt = tail[: -len(hit)].rstrip(" ,;")
            break
    return excerpt


def protect_personal_dates_in(text: str) -> tuple[str, dict[str, str]]:
    """Ersetzt jedes Geburts-/Todesdatum im Text durch ein Zeichen-Token ohne Leerraum/Satzzeichen, damit eine
    nachfolgende Segmentierung oder ein Schnitt an Leerraum das Datum nie zerteilt ("geb. 14. Juli 1988" wurde an den
    Punkten in "14." / "Juli 1988" zerlegt, Review §34). `restore_personal_dates` setzt die Werte wieder ein."""
    tokens: dict[str, str] = {}
    out = text
    taken_until = len(text) + 1
    for index, (start, end, _value) in enumerate(reversed(_personal_date_spans_cached(text))):
        if end > taken_until:  # ueberlappt ein bereits ersetztes Datum
            continue
        taken_until = start
        token = f"⟦PD{index}⟧"
        tokens[token] = text[start:end]
        out = out[:start] + token + out[end:]
    return out, tokens


def restore_personal_dates(text: str, tokens: dict[str, str]) -> str:
    for token, original in tokens.items():
        text = text.replace(token, original)
    return text


def trim_partial_personal_dates(snippet: str | None, full_text: str | None) -> str | None:
    """Gespeicherte Fristausschnitte aus der Zeit vor der Korrektur koennen mit dem Rest eines zerschnittenen Geburts-
    oder Todesdatums beginnen oder enden ("1940, verstorben am ..."). Ein solches Rand-Bruchstueck wird entfernt, wenn es
    Teil eines im Ausschnitt oder Quelldokument erkannten Geburts-/Todesdatums ist (§24).

    Ebenso ein VOLLSTAENDIGES Geburts-/Todesdatum, das laut Quelldokument eines ist, im Ausschnitt aber ohne seine
    Bezeichnung steht (§34, `remove_unlabelled_personal_dates`) - auch wenn es zugleich das Datum des Vorschlags ist:
    im Payload hat der Schutz Vorrang; der Vorschlag bleibt lokal vollstaendig erhalten."""
    if not snippet:
        return snippet
    snippet = remove_unlabelled_personal_dates(snippet, full_text)
    personal = _personal_date_values(snippet, full_text)
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
