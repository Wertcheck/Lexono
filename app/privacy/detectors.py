"""PII-Erkennung nach Kategorien.

Siehe __init__.py für die Gesamtstrategie (Regex + bekannte Entitäten).
Jeder Detektor liefert `DetectedSpan`-Objekte mit Position im Originaltext,
damit der Pseudonymizer präzise nur den betroffenen Ausschnitt ersetzen
kann (nicht den ganzen Satz).
"""

from __future__ import annotations

import re
import unicodedata
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
# ECHTER FUND (Real-E2E 08.10.): "Rechnung Nr. 2026-117" wurde als "2" + Telefon-
# nummer "026-117" erkannt (Muster begann mitten in der Ziffernfolge, weil die
# fuehrende "0" von "2026" als Vorwahl-Null passte) - die Rechnungsnummer wurde
# zerrissen, Claude meldete einen "Platzhalter, der eine Telefonnummer
# maskiert". Eine Rufnummer beginnt nie mitten in einer Ziffernfolge: davor
# darf keine Ziffer stehen (echte Nummern mit "+49"/"0049"/"0" am Anfang eines
# Tokens bleiben erkannt).
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+49|0049|0)\s?\(?\d{2,5}\)?[\s/\-]?\d{3,10}(?:[\s\-]?\d{2,6})?"
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

# ECHTER FUND (Real-E2E 08.10., Request-Capture der installierten .exe): die
# Rechnungsnummer "RE-2026-00417" ging im Klartext an Anthropic. Fruehere
# "Schutz" war nur ein Zufall (Telefon-Fehlalarm auf Teile der Nummer).
_RECHNUNGSNUMMER_PATTERN = re.compile(
    r"(?:rechnungs?-?\s?(?:nummer|nr\.?)|rechnung\s+nr\.?|re\.?-?\s?nr\.?|"
    r"beleg-?\s?(?:nummer|nr\.?)|bestell-?\s?(?:nummer|nr\.?))\s*[:.]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9/\-]{2,24})",
    re.IGNORECASE,
)

# "die Rechnung RE-2026-00417 per E-Mail" (ohne "Nr."): ein ID-foermiger Wert
# DIREKT nach "Rechnung" - Buchstabenpraefix mit Ziffern/Bindestrich/Schraegstrich
# oder Ziffernfolge mit Trennzeichen. Reine Jahreszahlen ("Rechnung 2026") und
# Datumsangaben ("Rechnung 17.09.2026") passen bewusst NICHT.
_RECHNUNG_ID_PATTERN = re.compile(
    r"\brechnung\s*[:.]?\s*"
    r"((?:[A-Za-z]{1,4}[-/]?\d{2,}|\d{2,})(?:[-/]\d+)+|[A-Za-z]{1,4}[-/]?\d{3,})\b",
    re.IGNORECASE,
)

# ECHTER FUND (Real-E2E 08.10., Spy auf den Restrisiko-Scan der echten Pipeline): die
# NER erkennt Firmennamen kontextabhaengig - in "An Elektro Lindqvist KG  z. H. der
# Geschaeftsfuehrung" fand der ERSTE Durchlauf die Firma nicht (sie blieb im Klartext),
# der zweite Durchlauf auf dem pseudonymisierten Text schon: das Gate blockierte eine
# voellig harmlose Analysefrage. Ein Firmenname mit Rechtsform ist dagegen grammatisch
# eindeutig und wird deshalb unabhaengig vom NER-Kontext erkannt.
_LEGAL_FORM_SUFFIX = r"(?:GmbH\s*&\s*Co\.\s*KG|KGaA|GmbH|mbH|OHG|GbR|UG|AG|KG|eG|SE)"
_COMPANY_WORD = r"[A-ZÄÖÜ][\wäöüßÄÖÜ&\-]*"
# Woerter eines Firmennamens sind durch GENAU EIN Leerzeichen getrennt: in der Pipeline
# werden Zeilenumbrueche zu Doppelleerzeichen, die Zeilengrenze darf nie ueberbrueckt
# werden ("Beispielstadt  An Elektro Lindqvist KG", "Gruessen  Nordwind ... GmbH").
_COMPANY_PATTERN = re.compile(
    r"\b((?:(?:" + _COMPANY_WORD + r"|&) ){0,4}" + _COMPANY_WORD + r") "
    + _LEGAL_FORM_SUFFIX
    + r"(?![A-Za-zÄÖÜäöüß])"
)
# Satzanfangs-/Funktionswoerter, die den Namen nicht beginnen ("An Elektro ... KG").
_COMPANY_LEADING_STOPWORDS = frozenset(
    "an die der das dem den des von vom zu zur zum bei mit für fuer im in und oder als auf aus "
    "nach ihre ihr ihren unsere unser firma herr herrn frau sehr wir sie ein eine einer "
    "betreff rechnung schreiben".split()
)

# Gerichts-Aktenzeichen im Format "12 O 345/26", "4 C 123/25", "123 Js 4567/20" -
# das keyword-basierte Muster oben erfasst solche Werte mit Leerzeichen nicht.
_COURT_AKTENZEICHEN_PATTERN = re.compile(r"\b\d{1,3}\s?[A-Za-z]{1,3}\s?\d{1,6}/\d{2,4}\b")

# BIC nur mit vorangestelltem "BIC"/"SWIFT" (sonst Fehlalarme auf normale Woerter).
_BIC_PATTERN = re.compile(
    r"(?:bic|swift)\s*[:.]?\s*([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b",
    re.IGNORECASE,
)

_NUMERIC_DATE_PATTERN = re.compile(r"\b\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}\b")
_MONTH_NAME_DATE_PATTERN = re.compile(
    rf"\b\d{{1,2}}\.\s*(?:{_GERMAN_MONTHS})\s+\d{{4}}\b", re.IGNORECASE
)

# Konkrete Geldbetraege (4.711,00 EUR, 640 EUR, 7,71 EUR) sind sachverhaltsrelevant
# und werden bewusst NICHT pseudonymisiert (Owner-Entscheidung 09.10.): sie sind allein
# nicht identifizierend, werden aber fuer Analyse, Berechnungen und Schriftsaetze gebraucht.
# Es gibt daher keinen Betrags-Detektor; identifizierende Werte (IBAN, Rechnungsnummer ...)
# bleiben davon unberuehrt.

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
# ECHTER FUND (Real-E2E 08.10., Request-Capture der installierten .exe):
# "Hafenkai 3" blieb im Klartext im Cloud-Payload ("kai" fehlte), ebenso waeren
# "Hauptstr. 12", "Rathausmarkt 5" oder "Parkhof 2" nicht erkannt worden.
_STREET_PATTERN = re.compile(
    r"\b[A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ]+(?:straße|strasse|str\.|weg|allee|platz|gasse|ring|"
    r"chaussee|damm|ufer|steig|stieg|promenade|wall|steg|anger|kai|hof|markt|pfad|"
    r"zeile|park|graben|kamp|tor)\s?\d+[a-z]?\b"
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
    spans = [s for s in spans if any(ch.isdigit() for ch in s.value)]
    # Gerichts-Aktenzeichen mit Leerzeichen ("12 O 345/26"), siehe Muster oben.
    spans += _matches_from_pattern(text, _COURT_AKTENZEICHEN_PATTERN, "aktenzeichen")
    return spans


def detect_kundennummer(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _KUNDENNUMMER_PATTERN, "kundennummer", group=1)


def detect_vertragsnummer(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _VERTRAGSNUMMER_PATTERN, "vertrag", group=1)
    # Nur echte Nummern (mit Ziffer), wie bei der Rechnungsnummer. ECHTER FUND (09.10., Ursache der
    # sporadischen original_value_leaked-Blockaden): "... Vertragsnummer.\n- Die Anrede ..." erfasste
    # das "-" des naechsten Aufzaehlungspunkts bzw. ein Folgewort ("Danach") als Vertragsnummer; der
    # Bindestrich wurde pseudonymisiert und jede spaetere Antwort mit einem Bindestrich in einem
    # Wort als geleakter Originalwert blockiert.
    return [sp for sp in spans if any(ch.isdigit() for ch in sp.value)]


def detect_rechnungsnummer(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _RECHNUNGSNUMMER_PATTERN, "rechnungsnummer", group=1)
    spans += _matches_from_pattern(text, _RECHNUNG_ID_PATTERN, "rechnungsnummer", group=1)
    # Nur echte Nummern (mit Ziffer), nicht Flusstext wie "Rechnung nr der ..."
    return [sp for sp in spans if any(ch.isdigit() for ch in sp.value)]


def detect_bic(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _BIC_PATTERN, "bic", group=1)


def detect_company_with_legal_form(text: str) -> list[DetectedSpan]:
    """Firmenname + Rechtsform ("Elektro Lindqvist KG"), unabhaengig vom NER-Kontext.
    Fuehrende Funktionswoerter ("An", "Die", "Firma", ...) gehoeren nicht zum Namen."""
    spans: list[DetectedSpan] = []
    for match in _COMPANY_PATTERN.finditer(text):
        name = match.group(1)
        start = match.start(1)
        words = list(re.finditer(r"\S+", name))
        skip = 0
        while skip < len(words) - 1 and words[skip].group(0).lower().strip(",.:;") in _COMPANY_LEADING_STOPWORDS:
            skip += 1
        if words[skip].group(0).lower().strip(",.:;") in _COMPANY_LEADING_STOPWORDS:
            continue
        begin = start + words[skip].start()
        spans.append(
            DetectedSpan(category="organisation", start=begin, end=match.end(), value=text[begin : match.end()])
        )
    return spans


# Akademischer Titel + Name ("Dr. Wiebe", "Prof. Dr. Jonas Lindqvist", "Herr Dr. Kostka").
# ECHTER FUND (Qualitaetslauf 10.10.2026, scripts/diagnose_titled_names.py): die NER (spaCy) uebersieht den
# NACHNAMEN nach "Dr."/"Prof." kontextabhaengig - bei 60 Testsaetzen ging er in 13 Faellen (22 %) im Klartext in
# den Cloud-Payload ("Gutachter Dr. Wiebe prueft ...", "Herr Dr. Kostka hat ...", "Prof. Dr. Lindqvist ..."), das
# Gateway erlaubte die Anfrage, auch der Restrisiko-Scan fand ihn nicht. Auf dem urspruenglichen Pfad blieb das oft
# unbemerkt, weil lange Dokumente schon nach 5000 Zeichen abgeschnitten wurden. Ein Titel "Dr."/"Prof." gefolgt von
# einem grossgeschriebenen Wort ist im Deutschen praktisch immer ein Personenname - deterministisch und unabhaengig
# vom NER-Kontext. Erfasst wird Titel + Name (hoechstens zwei Namenswoerter: Vor- und Nachname) - der Titel gehoert in den
# Treffer, sonst bliebe "Prof. Dr. [PERSON_01]" im Restrisiko-Scan als "Dr" haengen und blockierte die Anfrage -,
# Titel und erstes Namenswort duerfen durch einen Zeilenumbruch/ein Doppelleerzeichen getrennt sein (Zeilenumbruch
# im Dokument direkt nach "Dr." - im Qualitaetslauf real so beobachtet), Vor- und Nachname nur durch EIN Leerzeichen.
_ACADEMIC_TITLE = (
    r"(?:Prof\.|Dr\.)(?:\s{1,2}(?:Dr\.|Prof\.|med\.|jur\.|rer\. ?nat\.|rer\. ?pol\.|phil\.|h\. ?c\.|habil\.|mult\.|Ing\.))*"
)
_PERSON_NAME_WORD = r"(?:(?:von|van|vom|zu|zur|de|ter|ten) )?[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?"
_TITLED_PERSON_PATTERN = re.compile(
    r"(?<![A-Za-zÄÖÜäöüß])" + _ACADEMIC_TITLE + r"\s{1,2}(" + _PERSON_NAME_WORD + r"(?: " + _PERSON_NAME_WORD + r")?)"
    r"(?![A-Za-zÄÖÜäöüß])"
)


def detect_titled_person(text: str) -> list[DetectedSpan]:
    """Name hinter einem akademischen Titel ("Dr." / "Prof.") - siehe Kommentar bei `_TITLED_PERSON_PATTERN`."""
    return [
        DetectedSpan(category="person", start=m.start(), end=m.end(), value=m.group(0))
        for m in _TITLED_PERSON_PATTERN.finditer(text)
    ]


def detect_datum(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _NUMERIC_DATE_PATTERN, "datum")
    spans += _matches_from_pattern(text, _MONTH_NAME_DATE_PATTERN, "datum")
    return spans


#: Trenner zwischen "Strasse Nr" und "PLZ Ort" einer EINEN Anschrift: Komma/Semikolon und/oder ein
#: Zeilenumbruch (in der Pipeline zu zwei Leerzeichen geworden), nichts sonst.
_STREET_TO_POSTAL_SEPARATOR = re.compile(r"[ \t]*[,;]?[ \t]*(?:\n|[ \t]{2})?[ \t]*")


# Postfachanschrift (Folgezyklus 13.10.2026, eingefrorene Testbasis: "Postfach 10 20 30, 50667 Koeln" - die Postfachnummer
# blieb als einziger Teil der Anschrift im Klartext; weder Presidio noch ein Lexono-Detektor kannte sie). Die Nummer
# identifiziert den Empfaenger wie eine Hausanschrift; sie folgt immer dem Wort "Postfach" (Ziffern, gruppiert durch je
# ein Leerzeichen oder einen Punkt; die naechste Zeile / eine 5-stellige PLZ wird nicht mitgenommen).
# Review: "Im Postfach 3 neue Nachrichten", "Postfach 2025 vom ..." wurden maskiert, "Postfach 0221 4711" verlor die zweite
# Gruppe. Daher: mindestens zwei Zifferngruppen (2-4 Ziffern) oder mindestens 5 Ziffern oder direkt folgende PLZ + Ort.
_PO_BOX_PATTERN = re.compile(
    r"\b(?:Postfach|Postf\.)(?:[ \t]*:)?[ \t]+(?:\d{2,4}(?:[ .]\d{2,4})+|\d{5,6}|\d{1,4}(?=[ \t]*[,;]?[ \t]*(?:\n|[ \t]{2})?[ \t]*\d{5}[ \t]+[A-ZÄÖÜ]))\b"
)


def detect_address(text: str) -> list[DetectedSpan]:
    """Strasse+Hausnummer und PLZ+Ort, die DIREKT aufeinander folgen ("Lindenallee 3, 30000
    Beispielstadt"), sind EINE Anschrift und bekommen EINEN Platzhalter. Real-E2E 09.10.: zwei
    getrennte Platzhalter hinter dem Namen eines Beteiligten fuehrten dazu, dass das Modell keine
    vollstaendige Anschrift erkannte und "[Anschrift einsetzen]" schrieb. Nicht benachbarte Teile
    bleiben getrennte Treffer. Eine Postfachangabe gilt wie Strasse+Hausnummer."""
    streets = _matches_from_pattern(text, _STREET_PATTERN, "adresse") + _matches_from_pattern(text, _PO_BOX_PATTERN, "adresse")
    streets.sort(key=lambda span: span.start)
    postals = _matches_from_pattern(text, _POSTAL_CODE_CITY_PATTERN, "adresse")
    merged: list[DetectedSpan] = []
    used_postals: set[int] = set()
    for street in streets:
        partner = next(
            (
                (index, postal)
                for index, postal in enumerate(postals)
                if index not in used_postals
                and postal.start >= street.end
                and _STREET_TO_POSTAL_SEPARATOR.fullmatch(text[street.end : postal.start])
            ),
            None,
        )
        if partner is None:
            merged.append(street)
            continue
        index, postal = partner
        used_postals.add(index)
        merged.append(
            DetectedSpan(
                category="adresse", start=street.start, end=postal.end, value=text[street.start : postal.end]
            )
        )
    merged += [postal for index, postal in enumerate(postals) if index not in used_postals]
    return merged


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
    detect_rechnungsnummer,
    detect_bic,
    detect_datum,
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
    covered: list[DetectedSpan] = list(spans)
    seen_values: set[tuple[str, str]] = set()
    extra: list[DetectedSpan] = []
    for span in spans:
        key = (span.category, span.value.lower())
        if key in seen_values or len(span.value) < _MIN_REPEATED_OCCURRENCE_LENGTH:
            continue
        seen_values.add(key)
        pattern = re.compile(r"\b" + re.escape(span.value) + r"\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            overlapping = [c for c in covered if match.start() < c.end and match.end() > c.start]
            # ECHTER FUND (Real-E2E 08.10., Drafting "Zahlungsaufforderung der
            # Nordwind ... GmbH an die ... KG"): die NER erkannte im Dokument
            # nur "Nordwind Brandschutz-Service" (ohne "GmbH"), in der
            # Anweisung dagegen "Nordwind Brandschutz-Service GmbH". Das
            # laengere Vorkommen wurde hier frueher uebersprungen, weil es
            # die bereits erkannte KUERZERE Fassung ueberlappt - dieselbe
            # Partei bekam zwei Platzhalter ("[ORGANISATION_01] GmbH" im
            # Dokument, "[ORGANISATION_03]" in der Anweisung), Claude hielt
            # sie fuer zwei Parteien und verweigerte den Entwurf. Ein Vorkommen,
            # das NUR kuerzere Treffer DERSELBEN Kategorie vollstaendig
            # umschliesst (ausserdem einen darin enthaltenen "ort"-Treffer bei
            # einer Organisation: "Ostsee" in "Ostsee Anlagenbau KG" wurde
            # sonst als Ort ersetzt und der Rest der Firma blieb lesbar), darf
            # diese deshalb ablösen (`_resolve_overlaps`
            # waehlt den laengeren). Teilweise Ueberlappungen, gleich lange
            # oder andersartige Treffer bleiben wie bisher unangetastet.
            if overlapping and not all(
                (c.category == span.category or (c.category == "ort" and span.category == "organisation"))
                and match.start() <= c.start
                and c.end <= match.end()
                and (c.end - c.start) < (match.end() - match.start())
                for c in overlapping
            ):
                continue
            new_span = DetectedSpan(
                category=span.category, start=match.start(), end=match.end(), value=match.group(0)
            )
            extra.append(new_span)
            covered.append(new_span)
    if not extra:
        return spans
    return _resolve_overlaps(spans + extra)


def detect_all(
    text: str,
    known_entities: dict[str, list[str]] | None = None,
    *,
    ner_detector: Callable[[str], list[DetectedSpan]] | None = None,
    skip_categories: frozenset[str] = frozenset(),
    ner_span_filter: Callable[[str, list[DetectedSpan]], list[DetectedSpan]] | None = None,
    uppercase_variant: bool = True,
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

    `ner_span_filter` (optional, ECHTER FUND E2E 08.10.): wird AUSSCHLIESSLICH
    auf die Treffer des `ner_detector` angewendet (nie auf Regex-Detektoren
    oder `known_entities`) und erlaubt dem Aufrufer, erkennbare NER-
    Fehlalarme zu verwerfen, siehe
    `presidio_ner.drop_common_noun_persons`. Default `None` = unveraendert.

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
    # Generische Firmenerkennung ueber die Rechtsform: bewusst NACH den bekannten
    # Entitaeten angehaengt (wie die NER) - bei gleich langem Treffer gewinnt der zuerst
    # hinzugefuegte, und eine bekannte, rollenzugeordnete Entitaet (Gegner, Mandant ...)
    # muss einer generischen "organisation" vorgehen.
    all_spans.extend(detect_company_with_legal_form(text))
    all_spans.extend(detect_titled_person(text))
    if ner_detector is not None:
        ner_spans = ner_detector(text)
        if ner_span_filter is not None:
            ner_spans = ner_span_filter(text, ner_spans)
        all_spans.extend(ner_spans)

    if skip_categories:
        all_spans = [span for span in all_spans if span.category not in skip_categories]

    if uppercase_variant:
        all_spans.extend(
            _uppercase_variant_spans(
                text, all_spans, known_entities=known_entities, ner_detector=ner_detector,
                skip_categories=skip_categories, ner_span_filter=ner_span_filter,
            )
        )
    resolved = _resolve_overlaps(all_spans)
    return _extend_with_repeated_occurrences(text, resolved)


# --- Versalien (13.10.2026, scripts/bench_uppercase.py) ----------------------------------------------------------------
# ECHTER FUND (gemessen, 35 von 36 Werten im Klartext): "HERRN / GERNOT HAHN / BIRKENALLEE 17 / 12345 MUSTERSTADT"
# (Anschrift fuer Fensterumschlaege), "Der Beklagte WINKELMANN", Rubrum "HABERLAND ./. WINKELMANN" gingen vollstaendig an
# die Cloud: die Adressmuster verlangen Kleinbuchstaben ("allee", Ortsname mit Kleinbuchstaben), NER und Zweitsignale
# erkennen Namen fast nur an der Gross-/Kleinschreibung. Abhilfe: eine LAENGENGLEICHE zweite Analysefassung, in der
# Versalwoerter (mindestens 4 Buchstaben, mit Vokal - vokallose/kurze Abkuerzungen wie STVG, GMBH, BGB, ZPO bleiben
# unveraendert) in normale Schreibung gebracht werden ("HAHN" -> "Hahn"). Auf ihr laufen ALLE Detektoren. Uebernommen
# wird ein Treffer NUR, wenn er ein umgeschriebenes Versalwort beruehrt UND keinen urspruenglichen Treffer ueberlappt -
# die zusaetzliche Analyse kann bestehende Treffer weder entfernen noch verdraengen (rein additiv, fail-closed). Der Wert
# ist immer der Originalausschnitt.
# NUR in der Pseudonymisierung (Parameter `uppercase_variant`): der Restrisiko-Scan arbeitet unveraendert wie vor dieser
# Erweiterung. Grund (Red-Team 4, C2): die Varianten-NER haengt vom Kontext ab, und im Restrisiko-Scan stehen Platzhalter
# neben den Versalwoertern - als Blockadegrund erzeugte sie neue, inkonsistente Fail-Closed-Blockaden normaler Briefe. Der
# Restrisiko-Scan wird dadurch nicht schwaecher als zuvor; die Pseudonymisierung maskiert mehr.
# Grossbuchstaben aller Schriften des BMP, nicht nur A-Z/ÄÖÜ (Red-Team 4: "ŞAHIN", "GÓMEZ", "RENÉ LEFÈVRE", "MØLLER"
# blieben im Klartext, weil das Wort nicht als Versalwort galt). Fuer A-Z/ÄÖÜß ist das Verhalten unveraendert.
_UPPER = "ß" + "".join(re.escape(chr(c)) for c in range(0x80, 0x10000) if chr(c).isupper() and chr(c).isalpha())
_UC = f"[A-Z{_UPPER}]"
_UPPERCASE_WORD = re.compile(rf"(?<![\wÄÖÜäöüß]){_UC}{{4,}}(?:-{_UC}{{2,}})*(?![\wÄÖÜäöüß])")
# Griechische/kyrillische Vokale (Folgezyklus 13.10.2026, eingefrorene Testbasis: "ΓΙΑΝΝΗΣ ΠΑΠΑΔΟΠΟΥΛΟΣ" - der Nachname
# wurde nie in normale Schreibung gebracht, weil die Vokalpruefung nur lateinische Vokale kannte). Nur der zweite, rein
# additive Durchgang bearbeitet diese Schriften.
_VOWEL = re.compile(r"[AEIOUYÆØŒΑΕΗΙΟΥΩАЕЁИОУЫЭЮЯІЇЄ]")
_LATIN_LETTER = re.compile(r"[A-Za-zÀ-ɏ]")


def _has_vowel(value: str) -> bool:
    # Grundbuchstabe ohne diakritische Zeichen ("Ä" -> "A", "Ó" -> "O"); vokallose Abkuerzungen (STVG, GMBH) bleiben aussen vor.
    base = "".join(ch for ch in unicodedata.normalize("NFD", value) if not unicodedata.combining(ch))
    return _VOWEL.search(base) is not None


# Versalfolge: aufeinanderfolgende Versalwoerter (auch kurze, z. B. "DES", "UND"), getrennt nur durch EIN Leerzeichen oder
# einen Bindestrich. Zeilenwechsel/Doppel-Leerzeichen der Dokumentaufbereitung, Komma, Punkt, Doppelpunkt, "|", "./."
# beenden eine Folge. Gemessen (scripts/bench_uppercase_negatives.py): ganze Versal-SAETZE in normale Schreibung gebracht
# lassen das Modell Verben fuer Eigennamen halten ("SIND", "TELEFAX" als Person) - Namen und Anschriften stehen dagegen in
# KURZEN Folgen (Anschriftzeile, einzelner Name im Satz, Rubrum). Daher nur Folgen aus hoechstens 4 Woertern.
_UPPERCASE_RUN = re.compile(
    rf"(?<![\wÄÖÜäöüß]){_UC}{{2,}}(?:-{_UC}{{2,}})*(?: {_UC}{{2,}}(?:-{_UC}{{2,}})*)*(?![\wÄÖÜäöüß])"
)
_MAX_UPPERCASE_RUN_WORDS = 4
# Erster Durchgang: Folgen/Woerter nur aus A-Z/ÄÖÜß, exakt wie zuvor. Der zweite Durchgang mit Grossbuchstaben aller
# Schriften kommt nur HINZU (Red-Team 5): ein Buchstabe wie "Ş" beendete bisher eine Folge - allein mit dem erweiterten
# Zeichenvorrat wurde "KLAGE DES HERRN EMRE ŞAHIN GEGEN DIE FIRMA" zu EINER langen Folge, und "EMRE", vorher geschuetzt,
# blieb im Klartext; "Zeugin İPEK AYDIN" ergab in der erweiterten Fassung keinen NER-Treffer mehr, vorher "Aydin".
_UPPERCASE_RUN_AZ = re.compile(
    r"(?<![\wÄÖÜäöüß])[A-ZÄÖÜß]{2,}(?:-[A-ZÄÖÜß]{2,})*(?: [A-ZÄÖÜß]{2,}(?:-[A-ZÄÖÜß]{2,})*)*(?![\wÄÖÜäöüß])"
)
_UPPERCASE_WORD_AZ = re.compile(r"(?<![\wÄÖÜäöüß])[A-ZÄÖÜß]{4,}(?:-[A-ZÄÖÜß]{2,})*(?![\wÄÖÜäöüß])")


def _uppercase_variant(text: str, *, extended: bool = False) -> tuple[str, list[tuple[int, int]]] | None:
    regions: list[tuple[int, int]] = []
    chars = list(text)
    patterns = [(_UPPERCASE_RUN_AZ, _UPPERCASE_WORD_AZ)]
    if extended:
        patterns.append((_UPPERCASE_RUN, _UPPERCASE_WORD))
    words = {
        (word.start(), word.end())
        for run_pattern, word_pattern in patterns
        for run in run_pattern.finditer(text)
        if len(run.group().split(" ")) <= _MAX_UPPERCASE_RUN_WORDS
        for word in word_pattern.finditer(text, run.start(), run.end())
    }
    for start, end in sorted(words):
        value = text[start:end]
        if not _has_vowel(value):
            continue  # vokallose Abkuerzung (STVG, GMBH)
        converted = "-".join(part[:1] + part[1:].lower() for part in value.split("-"))
        if len(converted) != len(value):
            continue  # Kleinschreibung aendert die Laenge ("İ" -> "i̇") - Offsets muessen gleich bleiben
        regions.append((start, end))
        chars[start:end] = list(converted)
    if extended:
        # Ein Tabulator ZWISCHEN zwei umgeschriebenen Versalwoertern wird in der Analysefassung zum Leerzeichen ("KLAUS<Tab>
        # BROCKHAGEN" aus PDF-Extraktion: die NER fasste den Namen sonst nicht zusammen, 4/33 im Klartext). Nur wenn es der
        # EINZIGE Tabulator der Zeile ist - Tabellenzeilen haben mehrere ("NACHBAR<Tab>LAERM<Tab>JA" wurde sonst zu einer
        # Person, gemessen 2/49 -> 5/49 verlorene Tabellenwoerter). Nur im zweiten, rein additiven Durchgang; der
        # Payload-Text bleibt unveraendert.
        ordered = sorted(regions)
        for (_, left_end), (right_start, _) in zip(ordered, ordered[1:]):
            if text[left_end:right_start] != "\t":
                continue
            line_start = max(text.rfind("\n", 0, left_end), text.rfind("  ", 0, left_end)) + 1
            line_ends = [pos for pos in (text.find("\n", right_start), text.find("  ", right_start)) if pos != -1]
            if text.count("\t", line_start, min(line_ends, default=len(text))) == 1:
                chars[left_end] = " "
    variant = "".join(chars)
    if not regions or len(variant) != len(text):
        return None
    return variant, regions


# Institutionsbezeichnung direkt vor dem Treffer (Red-Team 4: "LANDGERICHT KOELN" -> "LANDGERICHT [PERSON]",
# "FINANZAMT NORD") - nur fuer Versal-Variantentreffer der Kategorie Person.
_INSTITUTION_BEFORE = re.compile(
    r"(?:gericht|gerichtshof|amt|kammer|behörde|behoerde|kasse|agentur|anstalt|verwaltung|ministerium|präsidium|"
    r"praesidium|direktion|stelle|center|gesellschaft|genossenschaft|werk|werke|bank|versicherung)[ \t]+$",
    re.IGNORECASE,
)


def _uppercase_variant_spans(
    text: str,
    original_spans: list[DetectedSpan],
    *,
    known_entities: dict[str, list[str]] | None,
    ner_detector: Callable[[str], list[DetectedSpan]] | None,
    skip_categories: frozenset[str],
    ner_span_filter: Callable[[str, list[DetectedSpan]], list[DetectedSpan]] | None,
) -> list[DetectedSpan]:
    """Zusaetzliche, VALIDIERTE Treffer aus der Versal-Analysefassung (siehe Kommentarblock oben).

    Validierung je Quelle (gemessen mit scripts/bench_uppercase_negatives.py - ohne sie hielt das Modell in Versal-Saetzen
    Verben/Gattungswoerter fuer Namen: "WEITERLEITEN", "TELEFAX", "SAAL", "TITULIERT"):
    * bekannte Entitaeten, Titel-Muster ("Dr. X"), Strasse+Hausnummer: ohne weitere Pruefung;
    * "PLZ Ort" ohne Strasse: nur wenn der Ort in der Analysefassung ein Eigenname (PROPN) ist ("12345 SCHLUESSEL" nicht);
    * NER-Personen/-Orte: jedes Wort PROPN, und kein einzelnes Versalwort allein auf seiner Zeile (Ueberschrift);
    * NER-Organisationen: nicht uebernommen (in Versal-Fliesstext ueberwiegend Fehlalarme).
    Nur Treffer, die ein umgeschriebenes Versalwort beruehren und keinen urspruenglichen Treffer ueberlappen.
    Zwei Durchgaenge: A-Z/ÄÖÜß wie bisher, danach - nur wenn die Fassung sich unterscheidet - der erweiterte Zeichenvorrat,
    fuer den die Treffer des ersten Durchgangs wie urspruengliche Treffer gelten (rein additiv)."""
    options = {
        "known_entities": known_entities, "ner_detector": ner_detector, "skip_categories": skip_categories,
        "ner_span_filter": ner_span_filter,
    }
    basic = _uppercase_variant(text)
    accepted = _uppercase_variant_pass(text, original_spans, basic, **options) if basic else []
    extended = _uppercase_variant(text, extended=True)
    if extended and (basic is None or extended[0] != basic[0]):
        accepted += _uppercase_variant_pass(text, original_spans + accepted, extended, **options)
    return accepted


def _uppercase_variant_pass(
    text: str,
    original_spans: list[DetectedSpan],
    converted: tuple[str, list[tuple[int, int]]],
    *,
    known_entities: dict[str, list[str]] | None,
    ner_detector: Callable[[str], list[DetectedSpan]] | None,
    skip_categories: frozenset[str],
    ner_span_filter: Callable[[str, list[DetectedSpan]], list[DetectedSpan]] | None,
) -> list[DetectedSpan]:
    variant, regions = converted
    trusted: list[DetectedSpan] = []
    for detector in _ALL_REGEX_DETECTORS:
        if detector is detect_address:
            continue
        trusted.extend(detector(variant))
    if known_entities:
        trusted.extend(detect_known_entities(variant, known_entities))
    trusted.extend(detect_company_with_legal_form(variant))
    trusted.extend(detect_titled_person(variant))
    to_validate: list[DetectedSpan] = list(detect_address(variant))
    if ner_detector is not None:
        ner_spans = ner_detector(variant)
        if ner_span_filter is not None:
            ner_spans = ner_span_filter(variant, ner_spans)
        to_validate.extend(span for span in ner_spans if span.category != "organisation")
    pos_tags: dict[tuple[int, int], str] | None = None
    if to_validate:
        try:
            from app.privacy.presidio_ner import get_pos_tags

            pos_tags = get_pos_tags(variant)
        except Exception:  # noqa: BLE001 - ohne Wortarten keine Validierung moeglich -> nur vertrauenswuerdige Treffer
            pos_tags = None

    def word_tags(start: int, end: int) -> list[str]:
        # Nur die UMGESCHRIEBENEN Versalwoerter pruefen - deren Schreibweise ist unsicher; eine normal geschriebene Anrede im
        # selben Treffer ("Herr THIELKER" -> NER "Herr Thielker", "Herr" = NOUN) ist kein Gegenindiz.
        return [
            tag
            for (s, e), tag in (pos_tags or {}).items()
            if s >= start and e <= end and any(rs <= s and e <= re_ for rs, re_ in regions)
        ]

    def non_latin_extension(span: DetectedSpan) -> bool:
        # Das deutsche Wortartmodell taggt griechische/kyrillische Woerter unzuverlaessig ("Γιαννης" = NOUN neben
        # "Παπαδοπουλος" = PROPN; Folgezyklus 13.10.2026, Testbasis G03 - der Nachname blieb im Klartext). Zugelassen nur,
        # wenn (1) jedes Nicht-PROPN-Wort ausschliesslich aus nicht-lateinischen Buchstaben besteht und (2) der Treffer einen
        # URSPRUENGLICHEN NER-Treffer enthaelt - gleich welcher Kategorie (installierter Build, Fall 13: "ΓΙΑΝΝΗΣ" als
        # Organisation erkannt, "ΠΑΠΑΔΟΠΟΥΛΟΣ" blieb im Klartext). Der urspruengliche Treffer bleibt unveraendert; uebernommen
        # wird nur der Teil ausserhalb (Teiltreffer-Regel unten).
        failing = [
            variant[s:e]
            for (s, e), tag in (pos_tags or {}).items()
            if s >= span.start and e <= span.end and tag != "PROPN" and any(rs <= s and e <= re_ for rs, re_ in regions)
        ]
        if not failing or any(_LATIN_LETTER.search(word) or not word.isalpha() for word in failing):
            return False
        return any(
            other.category in (span.category, "person", "ort", "organisation")
            and span.start <= other.start
            and other.end <= span.end
            for other in original_spans
        )

    def alone_on_line(span: DetectedSpan) -> bool:
        before = variant[: span.start].rsplit("\n", 1)[-1].rsplit("  ", 1)[-1]
        before = re.sub(r"^\s*\[[^\]\n]*\]", "", before)  # Dokumentkopf "[Schreiben] " der Kontextaufbereitung
        after = variant[span.end :].split("\n", 1)[0].split("  ", 1)[0]
        return not before.strip(" -–:|") and not after.strip(" .,:;!-–|") and " " not in span.value.strip()

    validated: list[DetectedSpan] = list(trusted)
    for span in to_validate:
        if pos_tags is None:
            break
        value = variant[span.start : span.end]
        if span.category == "adresse":
            if _STREET_PATTERN.search(value):
                validated.append(span)
                continue
            city = re.search(r"\d{5}\s+(\S.*)$", value)
            if city and word_tags(span.start + city.start(1), span.end) and all(
                tag == "PROPN" for tag in word_tags(span.start + city.start(1), span.end)
            ):
                validated.append(span)
            continue
        tags = word_tags(span.start, span.end)
        if span.category == "person" and _INSTITUTION_BEFORE.search(variant[: span.start]):
            continue  # "LANDGERICHT KOELN", "FINANZAMT NORD" - Sitz/Bezeichnung einer Institution, kein Name
        if tags and all(tag == "PROPN" for tag in tags) and not alone_on_line(span):
            validated.append(span)
        elif tags and not alone_on_line(span) and non_latin_extension(span):
            validated.append(span)

    accepted: list[DetectedSpan] = []
    for span in validated:
        if span.category in skip_categories:
            continue
        overlapping = [other for other in original_spans if span.start < other.end and other.start < span.end]
        # Ein urspruenglicher Treffer wird nie verdraengt oder verkuerzt. Erlaubt ist eine OBERMENGE gleicher Kategorie
        # (Red-Team 4, C1: "HANS MEIER" - der Nachname war schon erkannt; "HANS" blieb sonst im Klartext und der
        # Restrisiko-Scan blockierte den Brief). Ueberlappt der Treffer einen urspruenglichen Treffer ANDERER Kategorie
        # oder ragt dieser hinaus, werden nur die Teile AUSSERHALB der urspruenglichen Treffer uebernommen ("AGNIESZKA
        # LUKASIEWICZ": der Nachname war schon als Ort maskiert - der Vorname blieb sonst im Klartext). Der
        # urspruengliche Treffer bleibt dabei unveraendert.
        is_superset = all(
            other.category == span.category and other.start >= span.start and other.end <= span.end for other in overlapping
        )
        cuts = [] if is_superset else [(other.start, other.end) for other in overlapping]
        # Rubrum-Trenner "./." trennt zwei Parteien ("Dvorak ./. Haberland" als EIN NER-Treffer).
        cuts += [(span.start + m.start(), span.start + m.end()) for m in re.finditer(r"\./\.", text[span.start : span.end])]
        pieces = [(span.start, span.end)]
        for cut_start, cut_end in sorted(cuts):
            pieces = [
                part
                for piece_start, piece_end in pieces
                for part in ((piece_start, min(piece_end, cut_start)), (max(piece_start, cut_end), piece_end))
                if part[0] < part[1]
            ]
        for start, end in pieces:
            # An den Raendern nur Buchstaben/Ziffern (NER lieferte "Haberland ./." - das "./." verschwand im Platzhalter).
            while start < end and not text[start].isalnum():
                start += 1
            while end > start and not text[end - 1].isalnum():
                end -= 1
            if end <= start:
                continue
            if not any(start < region_end and region_start < end for region_start, region_end in regions):
                continue  # nur dort, wo die Versalschreibung die Ursache sein kann
            if any(start < other.end and other.start < end for other in accepted):
                continue
            accepted.append(DetectedSpan(category=span.category, start=start, end=end, value=text[start:end]))
    # Gesetzesabkuerzung direkt nach einem Normzitat ("§ 5 EFZG") ist kein Name - derselbe Filter wie in presidio_ner
    # (Red-Team 4, B1: Abkuerzungen mit Vokal wurden in der Versal-Fassung zu Personen).
    from app.privacy.presidio_ner import drop_statute_abbreviations

    accepted = [
        span
        for span in accepted
        if span.value.upper() not in _UPPERCASE_NON_NAMES
        and not _LETTER_CITATION_BEFORE.search(text[max(0, span.start - 60) : span.start])
    ]
    return drop_statute_abbreviations(text, accepted)


# Nur fuer die ZUSAETZLICHEN Versal-Variantentreffer (urspruengliche Treffer bleiben unberuehrt): Woerter, die auf
# f843ab7 lesbar blieben und erst durch die Versal-Fassung zu Personen/Orten wurden (Red-Team 6: "Art. 6 Abs. 1 lit. b
# DSGVO" -> "[ORT]", "nach dem BEEG", "die STVO", "auf die angegebene IBAN"). Gesetzesabkuerzungen mit Vokal und mindestens
# 4 Buchstaben (kuerzere/vokallose werden ohnehin nicht umgeschrieben).
_UPPERCASE_NON_NAMES = frozenset({
    "DSGVO", "BEEG", "STVO", "STPO", "BETRVG", "ARBZG", "EFZG", "MUSCHG", "BURLG", "ALTZG", "AUFENTHG", "ASYLG", "BAFÖG",
    "BAFOEG", "INSO", "FAMFG", "JVEG", "BEURKG", "BIMSCHG", "BAUGB", "BAUNVO", "ENEV", "HOAI", "URHG", "PATG", "MARKENG",
    "AKTG", "ABGB", "EGBGB", "KAGB", "BETRKV", "WOFLV", "TVÖD", "TVOED", "AGG", "IFSG", "SGB", "OWIG", "ZPO", "STGB",
    "BGB", "IBAN", "COVID",
})
# Normzitat mit Buchstaben-Untergliederung ("Art. 6 Abs. 1 lit. b/f", "§ 3 Nr. 2 lit. a") direkt vor dem Treffer - der
# gemeinsame Filter in presidio_ner kennt nach "lit." nur Ziffern.
_LETTER_CITATION_BEFORE = re.compile(
    r"(?:§§?|Art\.|Artikel)[ \t]*\d+[a-z]?(?:[ \t]*(?:Abs\.|Absatz|S\.|Satz|Nr\.|Ziff\.|lit\.|Buchst\.)[ \t]*(?:\d+|[a-z])"
    r"(?:[ \t]*(?:/|,|und|oder)[ \t]*(?:\d+|[a-z]))*)*[ \t]+$"
)


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
        elif span.end > last_end and resolved and resolved[-1].category == span.category:
            # ECHTER FUND (Qualitaetslauf 10.10.2026, installierter Build, 47-Tsd.-Zeichen-Vertrag): die NER lieferte
            # "Schiedsgutachter Dr." (ohne Namen), der Titel-Detektor "Dr. Wiebe" - der spaeter beginnende, ueber das
            # Ende hinausreichende Treffer wurde komplett verworfen, "Wiebe" blieb im Klartext im Cloud-Payload
            # ("[PERSON_02]  Wiebe."). Ragt ein Treffer GLEICHER Kategorie ueber den vorherigen hinaus, wird der
            # vorherige zur Vereinigung erweitert - nie bleiben Zeichen eines erkannten Treffers unersetzt.
            # (Verschiedene Kategorien bleiben beim bisherigen Verhalten.)
            previous = resolved[-1]
            extension = span.value[last_end - span.start :]
            resolved[-1] = DetectedSpan(
                category=previous.category,
                start=previous.start,
                end=span.end,
                value=previous.value + extension,
            )
            last_end = span.end
        # uebrige ueberlappende, kuerzere/spaetere Treffer werden verworfen.
    return resolved
