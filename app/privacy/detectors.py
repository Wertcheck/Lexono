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


def detect_address(text: str) -> list[DetectedSpan]:
    """Strasse+Hausnummer und PLZ+Ort, die DIREKT aufeinander folgen ("Lindenallee 3, 30000
    Beispielstadt"), sind EINE Anschrift und bekommen EINEN Platzhalter. Real-E2E 09.10.: zwei
    getrennte Platzhalter hinter dem Namen eines Beteiligten fuehrten dazu, dass das Modell keine
    vollstaendige Anschrift erkannte und "[Anschrift einsetzen]" schrieb. Nicht benachbarte Teile
    bleiben getrennte Treffer."""
    streets = _matches_from_pattern(text, _STREET_PATTERN, "adresse")
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
