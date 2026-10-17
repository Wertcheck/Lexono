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


# Possessiv + Verwandtschaftswort (§34.15): "Mein Sohn" ist kein Name. Gemeinsame Definition fuer die NER-Nachfilterung
# (presidio_ner.drop_possessive_kinship) und die regelbasierte Namensheuristik des Gateways (security_check).
POSSESSIVE_KINSHIP = re.compile(
    r"(?:[Mm]ein|[Dd]ein|[Ss]ein|[Ii]hr|[Uu]nser|[Ee]uer)(?:e|er|en|em|es)?\s+"
    r"(?:Sohn|Sohnes|Söhne|Tochter|Töchter|Vater|Vaters|Mutter|Eltern|Bruder|Bruders|Brüder|Schwester|Schwestern|Kind|"
    r"Kinder|Kindes|Enkel|Enkelin|Enkelkinder|Onkel|Tante|Neffe|Nichte|Cousin|Cousine|Großvater|Großmutter|Opa|Oma|"
    r"Ehemann|Ehefrau|Mann|Frau|Partner|Partnerin|Lebensgefährte|Lebensgefährtin|Schwiegersohn|Schwiegertochter|"
    r"Stiefsohn|Stieftochter|Stiefvater|Stiefmutter)"
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
    # "Kd.-Nr." (Nummern-Strategie 13.10.2026: vorher nur "Kd-Nr." erkannt, "Kd.-Nr. 5512398" ging im Klartext hinaus)
    r"(?:kundennummer|kd\.?-?\s?nr\.?|mandantennummer)\s*[:.]?\s*([A-Za-z0-9\-/]+)",
    re.IGNORECASE,
)

# Personenbeziehbare Bezugsnummern mit Bezeichnung (Nummern-Strategie 13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §22).
# Gemessen vorher im Klartext: Versicherungsschein-, Policen-, Schaden-, Patienten-, Personal-, Zaehler-, Vorgangs-,
# Auftrags-, Fall-, Versichertennummer, Mandatsreferenz, "Ihr/Unser Zeichen", "Ref.". Der Wert muss unmittelbar folgen
# und eine Ziffer enthalten; Datum und Betrag sind nie ein solcher Wert.
_REFERENCE_LABEL = (
    r"(?:versicherungsschein|versicherten|polic(?:e|en)|schadens?|patienten|personal|z(?:ä|ae)hler|vorgangs|auftrags|"
    r"fall|mandats|referenz|antrags|buchungs|kassenzeichen|aktenzeichen|bescheid|beitrags|lieferschein|sendungs|"
    r"matrikel|sch(?:ü|ue)ler|h(?:ä|ae)ftlings|gefangenen(?:buch)?|mitarbeiter|bewohner|"
    r"vertragskonto|(?:ü|ue)berweisungs)-?\s?(?:nummer|nr\.?)|mandatsreferenz|kassenzeichen|ref\.|gz\.|"
    r"(?:ihr|ihre|unser|unsere)\s+zeichen|gesch(?:ä|ae)ftszeichen|vertragskonto"
)
# Gattungswort UNMITTELBAR vor einer Nummer mit mindestens fuenf Ziffern ("Vertrag 88231457", "Kunde 4471123",
# "Police 7781234-55", "Bescheid 260312045", "Sendung 1073132281718") - ein Jahr ("Vertrag 2019") oder eine kleine Zahl
# ("Schaden 3") ist kein solcher Wert. Wie Rechnungs-/Bestellnummern (bereits deterministisch maskiert) sind Liefer-,
# Sendungs-, Vorgangs- und Bescheidnummern Bezuege eines konkreten Vorgangs mit einer Person - ohne Rueckfrage maskiert.
_REFERENCE_NOUN = (
    r"vertrag|kunde|kundin|police|schaden|ausweis|konto|versicherungsschein|mitglied|lieferschein|sendung|vorgang|"
    # "dokument" bewusst NICHT (Review: juris-Dokumentnummern sind oeffentliche Fundstellen)
    r"bescheid|auftrag|beleg|antrag"
)

# Nie freigebbar, auch nicht per Anwaltsentscheidung (Review 13.10.2026): Zahlungskartennummer (13-19 Ziffern, Pruefziffer
# nach Luhn) und der Rest einer Renten-/Sozialversicherungsnummer (Geburtsdatum + Buchstabe + Seriennummer, "120380 L 512";
# gemessen blieb er lesbar, wenn die NER die Bereichsnummer davor als Ort maskierte).
_CARD_PATTERN = re.compile(r"(?<![\w.,/\-])\d{4}(?:[ \-]?\d{3,4}){2,4}(?:[ \-]?\d{1,3})?(?![\w/\-]|[.,]\d)")
_SV_TAIL_PATTERN = re.compile(r"(?<![\w])\d{6}\s?[A-Z]\s?\d{3}(?![\w])")


# Grundbuch (Owner-Vorgabe 13.10.2026, §23): Grundbuchblatt- und Flurstuecksnummer machen den Eigentuemer ueber das
# Grundbuch bestimmbar - nie pauschal harmlos. Deterministisch maskiert, konsistent je Wert ([GRUNDBUCH_nn]); Gemarkung,
# Flur, Grundbuchbezirk und Amtsgericht bleiben lesbar (Zustaendigkeit, Lage - fuer sich nicht eigentuemerbestimmend).
# Review: auch "Blatt-Nr.", "Grundbuchblatt-Nr.:", bis zu 60 Zeichen zwischen "Grundbuch" und "Blatt" ("Grundbuch des
# Amtsgerichts X von Y Blatt 998"), "Flst.-Nr.", Plural "Flurstücksnummern", Tabellenspalten.
_SHEET_NUMBER_LABEL = r"(?:[\s\-]*(?:nr\.?|nummer))?"
_LAND_REGISTER_SHEET_PATTERN = re.compile(
    r"(?:\bgrundbuch(?:es)?\b[^\n]{0,60}?\b(?:blatt|bl\.)" + _SHEET_NUMBER_LABEL + r"|\bgrundbuchblatt" + _SHEET_NUMBER_LABEL
    + r")\s*[:.]?\s*(\d{1,6}[a-z]?)\b",
    re.IGNORECASE,
)
_PARCEL_NUMBER = r"\d{1,5}(?:/\d{1,4})?"
_PARCEL_LABEL = r"(?:flurst(?:ü|ue)ck(?:e|s|nummer|nummern|snummer|snummern)?|flst\.?(?:-?\s?nr\.?)?|flurst\.)"
_PARCEL_PATTERN = re.compile(
    r"\b" + _PARCEL_LABEL + r"\s*(?:nr\.?)?\s*[:.]?\s*"
    r"(" + _PARCEL_NUMBER + r"(?:\s*(?:,|und|u\.)\s*" + _PARCEL_NUMBER + r")*)(?![\w/])",
    re.IGNORECASE,
)
_LAND_REGISTER_HEADER = re.compile(r"\s*(?:" + _PARCEL_LABEL + r"|blatt|grundbuchblatt|bl\.)\s*", re.IGNORECASE)


# IPv4-Adresse auch ohne genaue Bezeichnung ("IP-Adr. 10.0.12.77", "Zugriff von 10.0.12.77"; Messkette h11, 13.10.2026:
# vorher verdeckte die pauschale Datumsmaskierung "10.0.12" nur zufaellig, nach der Datumspolicy ging die Adresse im
# Klartext hinaus). Vier gueltige Oktette; ohne "IP" in der Naehe nur mit erstem Oktett ab 10 und nie nach Versions-,
# Kapitel- oder Gliederungsangaben ("Version 2.4.1.0", "Kapitel 10.2.3.4").
_IPV4_PATTERN = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.]|\.\d)")
_IP_CONTEXT = re.compile(r"\bip\b|ip-?adr|ip-?adresse|server|zugriff|login|host", re.IGNORECASE)
_IP_NOT_AFTER_LABEL = re.compile(
    r"(?:version|ver\.|v\.|kapitel|abschnitt|ziff(?:er|\.)?|gliederung|nr\.|§|art\.|anlage|tz\.|rn\.)\s*$", re.IGNORECASE
)


def detect_ip_adresse(text: str) -> list[DetectedSpan]:
    spans = []
    for match in _IPV4_PATTERN.finditer(text):
        before = text[max(0, match.start() - 30) : match.start()]
        if _IP_NOT_AFTER_LABEL.search(before):
            continue
        if int(match.group().split(".")[0]) < 10 and not _IP_CONTEXT.search(before):
            continue
        spans.append(DetectedSpan(category="kennung", start=match.start(), end=match.end(), value=match.group()))
    return spans


def detect_grundbuch(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _LAND_REGISTER_SHEET_PATTERN, "grundbuch", group=1)
    for match in _PARCEL_PATTERN.finditer(text):
        numbers = list(re.finditer(_PARCEL_NUMBER, match.group(1)))
        if any(len(n.group()) < 2 for n in numbers):
            # Einstellige Flurstuecksnummer: als Wert waere "1" ueberall im Text "wiedergefunden" (Review: Fail-Closed-
            # Blockade) - dann die ganze Angabe samt Bezeichnung als EIN Wert.
            spans.append(DetectedSpan(category="grundbuch", start=match.start(), end=match.end(1), value=match.group()[: match.end(1) - match.start()]))
            continue
        for number in numbers:
            start = match.start(1) + number.start()
            spans.append(DetectedSpan(category="grundbuch", start=start, end=start + len(number.group()), value=number.group()))
    spans += _table_column_values(text, _LAND_REGISTER_HEADER, re.compile(r"\b" + _PARCEL_NUMBER + r"[a-z]?\b"), "grundbuch")
    return spans


def _table_column_values(text: str, header: re.Pattern, value: re.Pattern, category: str) -> list[DetectedSpan]:
    """Werte in der Tabellen-/CSV-Spalte (Trenner |, ; oder Tabulator), deren Kopfzelle `header` voll trifft - bis zur
    naechsten Leerzeile (Review: "Name | Geburtsdatum", "Gemarkung | Flur | Flurstück" mit Werten in Folgezeilen)."""
    spans: list[DetectedSpan] = []
    offset = 0
    column: tuple[str, int] | None = None
    for line in text.split("\n"):
        separator = next((sep for sep in ("|", ";", "\t") if sep in line), None)
        if not line.strip():
            column = None
        elif separator:
            cells = line.split(separator)
            hits = [i for i, cell in enumerate(cells) if header.fullmatch(cell)]
            if hits:
                column = (separator, hits[0])
            elif column and column[0] == separator and len(cells) > column[1]:
                cell_start = offset + sum(len(c) + 1 for c in cells[: column[1]])
                for match in value.finditer(cells[column[1]]):
                    spans.append(DetectedSpan(category=category, start=cell_start + match.start(),
                                              end=cell_start + match.end(), value=match.group()))
        offset += len(line) + 1
    return spans


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


# Review: etwa jede zehnte lange Zahl besteht die Luhn-Pruefung zufaellig. Ungegliedert gilt sie daher nur mit dem Praefix
# eines Kartennetzes (Visa 4, Mastercard 51-55/22-27, Amex 34/37, Discover 6011/65) und nie vor Waehrung/Einheit oder nach
# Seiten-, Artikel- oder ISBN-Angaben.
_CARD_PREFIX = re.compile(r"4|5[1-5]|2[2-7]|3[47]|6011|65")
_CARD_NOT_AFTER = re.compile(r"\s*(?:eur\b|€|euro\b|%|einwohner|stück|km\b)", re.IGNORECASE)
_CARD_NOT_BEFORE = re.compile(r"(?:seite|s\.|isbn(?:-1[03])?|ean|gtin|artikel(?:nummer)?|art\.-?\s?nr\.?)\s*[:.]?\s*$", re.IGNORECASE)


def detect_zahlungskarte_und_sv_rest(text: str) -> list[DetectedSpan]:
    spans = []
    for match in _CARD_PATTERN.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if not digits.isdigit() or not 13 <= len(digits) <= 19 or not _luhn_valid(digits):
            continue
        grouped = len(re.findall(r"\d+", match.group())) >= 3
        if not grouped and not _CARD_PREFIX.match(digits):
            continue
        if _CARD_NOT_AFTER.match(text, match.end()) or _CARD_NOT_BEFORE.search(text[max(0, match.start() - 25) : match.start()]):
            continue
        spans.append(DetectedSpan(category="kennung", start=match.start(), end=match.end(), value=match.group()))
    spans += _matches_from_pattern(text, _SV_TAIL_PATTERN, "kennung")
    return spans


_REFERENCE_VALUE = r"([A-Za-z0-9](?:[A-Za-z0-9/\-]|\.(?=[A-Za-z0-9])){1,30})(?![\w/\-])"
# Alle Bezeichnungen bezeichneter Bezugsnummern als ein Muster: ein NER-Treffer, der GANZ aus einer solchen Bezeichnung
# besteht ("Kd.-Nr" neben dem Platzhalter als Person), ist kein Name (presidio_ner._is_identifier_label).
REFERENCE_LABEL_WORDS = (
    _REFERENCE_LABEL + r"|kundennummer|kd\.?(?:-?\s?nr)?|mandantennummer|vertragsnummer|vertrags-nr|rechnungs?-?\s?(?:nummer|nr)"
    # "Flur" neben einem maskierten Flurstueck las die Rueckfrage als Name (2. Review-Runde)
    r"|grundbuchblatt|flurst(?:ü|ue)ck(?:e|s|nummer)?|flst|flur|gemarkung"
)
_REFERENCE_PATTERN = re.compile(
    r"(?<![\wÄÖÜäöüß])(?:" + _REFERENCE_LABEL + r")(?:\s+(?:ist|lautet|lauten))?\s*[:.]?\s*" + _REFERENCE_VALUE,
    re.IGNORECASE,
)
_REFERENCE_NOUN_PATTERN = re.compile(r"(?<![\wÄÖÜäöüß-])(?:" + _REFERENCE_NOUN + r")\s+" + _REFERENCE_VALUE, re.IGNORECASE)
_DATE_OR_AMOUNT_AFTER = re.compile(r"\s*(?:eur\b|€|euro\b|%|prozent\b)", re.IGNORECASE)

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


# §49 (16.10.2026, pseudonymization_v1 K04 "karl . brenner @ beispiel-mail . de" im Klartext): OCR setzt um "." und
# "@" Leerzeichen. Erkannt wird nur die Form mit "@" UND einer Domain mit mindestens einem Punkt und alphabetischer
# Endung - ohne "@" ist es keine E-Mail-Adresse (kein Fehlalarm bei "z . B .", Aufzaehlungen, Paragrafen). Leerraum
# NACH einem Punkt zaehlt nur, wenn auch davor Leerraum steht (OCR-Form " . "): "a@b.de. Erneut" ist ein Satzende,
# sonst wuerde das Folgewort als Domain-Endung mitmaskiert.
_SPACED_DOT = r"(?:\.|[ \t]+\.[ \t]*)"
_SPACED_EMAIL_PATTERN = re.compile(
    rf"(?<![\w.@-])[\w+-]+(?:{_SPACED_DOT}[\w+-]+){{0,5}}[ \t]*@[ \t]*[\w-]+(?:{_SPACED_DOT}[\w-]+){{0,4}}"
    rf"{_SPACED_DOT}[A-Za-z]{{2,24}}(?![\w@])"
)


def detect_email(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _EMAIL_PATTERN, "email")
    covered = [(s.start, s.end) for s in spans]
    for match in _SPACED_EMAIL_PATTERN.finditer(text):
        if " " not in match.group() and "\t" not in match.group():
            continue  # kompakte Form deckt _EMAIL_PATTERN ab
        if any(start <= match.start() and match.end() <= end for start, end in covered):
            continue
        spans.append(DetectedSpan(category="email", start=match.start(), end=match.end(), value=match.group()))
    return spans


def detect_phone(text: str) -> list[DetectedSpan]:
    return _matches_from_pattern(text, _PHONE_PATTERN, "telefon")


# §49 (pseudonymization_v1 B03 "I B A N  D E 8 9 3 7 ..." im Klartext): gesperrt gesetzte bzw. per OCR zerlegte IBAN -
# einzelne Zeichen mit Leerraum dazwischen. Bestaetigt wird nur, was nach dem Zusammenziehen die amtliche
# IBAN-Pruefziffer (ISO 13616, mod 97 == 1) besteht; zufaellige Buchstaben-/Ziffernfolgen fallen dadurch heraus.
_SPACED_CHAR_RUN = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9][ \t]{1,3}){14,40}[A-Za-z0-9](?![A-Za-z0-9])")


def _iban_checksum_ok(candidate: str) -> bool:
    if not (15 <= len(candidate) <= 34 and candidate[:2].isalpha() and candidate[2:4].isdigit()):
        return False
    rearranged = candidate[4:] + candidate[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def _spaced_ibans(text: str) -> list[DetectedSpan]:
    spans: list[DetectedSpan] = []
    for run in _SPACED_CHAR_RUN.finditer(text):
        chars = [(i, ch.upper()) for i, ch in enumerate(run.group(), run.start()) if ch.isalnum()]
        compact = "".join(ch for _, ch in chars)
        best = None
        for begin in range(len(compact) - 14):
            if not (compact[begin : begin + 2].isalpha() and compact[begin + 2 : begin + 4].isdigit()):
                continue
            for length in range(min(34, len(compact) - begin), 14, -1):
                if _iban_checksum_ok(compact[begin : begin + length]):
                    best = (begin, length)
                    break
            if best:
                break
        if best:
            begin, length = best
            start, end = chars[begin][0], chars[begin + length - 1][0] + 1
            spans.append(DetectedSpan(category="iban", start=start, end=end, value=text[start:end]))
    return spans


def detect_iban(text: str) -> list[DetectedSpan]:
    spans = _matches_from_pattern(text, _IBAN_PATTERN, "iban")
    for span in _spaced_ibans(text):
        if not any(s.start <= span.start and span.end <= s.end for s in spans):
            spans.append(span)
    return spans


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


def detect_referenznummer(text: str) -> list[DetectedSpan]:
    """Bezeichnete personenbeziehbare Bezugsnummern (siehe _REFERENCE_LABEL/_REFERENCE_NOUN). Kategorie "kennung"."""
    spans = []
    # Bezeichnete Nummer ab 3 Ziffern (Review §25: "Mitarbeiter-Nr. 12" - die "12" stand auch anderswo im Text, die
    # Leck-Pruefung blockierte; eine zweistellige Nummer ist fuer sich kaum identifizierend)
    for pattern, min_digits in ((_REFERENCE_PATTERN, 3), (_REFERENCE_NOUN_PATTERN, 5)):
        for match in pattern.finditer(text):
            value = match.group(1)
            if sum(ch.isdigit() for ch in value) < min_digits or _NUMERIC_DATE_PATTERN.fullmatch(value):
                continue
            if _DATE_OR_AMOUNT_AFTER.match(text, match.end(1)) or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
                continue  # Betrag ("Schaden 12.000 EUR")
            spans.append(DetectedSpan(category="kennung", start=match.start(1), end=match.end(1), value=value))
    return spans


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
    """ALLE Datumsangaben (Kategorie "datum"). Nicht mehr Teil der Pseudonymisierung (Datumspolicy 13.10.2026, siehe
    detect_geburtsdatum); bleibt fuer lokale Auswertungen und Kompatibilitaet."""
    spans = _matches_from_pattern(text, _NUMERIC_DATE_PATTERN, "datum")
    spans += _matches_from_pattern(text, _MONTH_NAME_DATE_PATTERN, "datum")
    return spans


# Datumspolicy (Owner-Entscheidung 13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §23): Sachverhalts-, Vertrags-, Frist- und
# Ereignisdaten bleiben LESBAR - Chronologie, Abstaende und Fristberechnung sind juristischer Kern (vorher: alle Daten
# [DATUM_nn], 27 von 27 Sachverhaltsdaten im Messkorpus verdeckt). Geschuetzt wird das GEBURTSDATUM: zusammen mit Wohnort,
# Rolle oder Arbeitgeber ist es ein starkes Quasi-Merkmal (Reidentifikation auch bei pseudonymisiertem Namen), und es ist
# selten selbst rechtserheblich - erheblich ist das Alter, das lokal berechnet wird (date_chronology.build_age_note).
# Erkannt wird es nur am Geburtskontext (Review 13.10.2026 eingearbeitet):
# - Bezeichnung davor, auch mit einigen Woertern dazwischen ("geb.", "geboren am", "Geburtsdatum des Kindes:",
#   "Geburtstag ist der", "geb. am Mittwoch,", "DOB", "date of birth"); "*" nur als Apposition ("Kläger, * 24.12.1968",
#   "(* 12.03.1980)") - nicht "Siehe *12.03.2024 Fußnote";
# - "geboren"/"geborene"/"zur Welt gekommen" danach; ebenso ein Datum, auf das kurz darauf "Geburtstag" folgt ("feierte am
#   12.03.2025 seinen 45. Geburtstag" - Geburtsdatum daraus exakt ableitbar);
# - Datum allein (oder als Lebensdaten "12.03.1940 – 01.01.2026") in Klammern bzw. zwischen Kommas direkt nach einer
#   Personenbezeichnung (Anrede/Rolle + Name), wenn das Jahr als Geburtsjahr plausibel ist (mindestens 14 Jahre zurueck)
#   und danach nicht "seit/ab/bis/Beginn" folgt ("Herr Brenner (01.04.1995) ist seit diesem Tag Mieter" bleibt lesbar);
# - in Tabellen/CSV jede Datumsangabe in der Spalte, deren Kopfzelle "Geburtsdatum"/"geb." lautet.
# Datumsformate: deutsch numerisch, mit Monatsnamen, ISO ("1980-03-12") und mit Schraegstrich ("12/03/1980").
_WEEKDAY = r"(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)"
# "geb." ohne Luecke (nur "am" und ein Wochentag): danach kann ein Geburtsname folgen ("geb. Sommer, kuendigte am ...",
# 2. Review-Runde) - der ist kein Geburtsdatum-Kontext fuer das naechste Datum.
_BIRTH_LABEL_SHORT = r"\bgeb\.(?:\s+am)?(?:\s+" + _WEEKDAY + r",?)?\s*[:.]?\s*$"
_BIRTH_LABEL_LONG = (
    r"(?:\bgeboren|\bgeburtsdatum|\bgeb\.-?\s?datum|\bgeburtstag|\bgeburtsdat\.|\bdob\b|\bdate\s+of\s+birth|"
    r"(?:(?<=,)|(?<=\())\s*\*)"
    # bis zu 25 Zeichen ohne Ziffer, Satz-/Tabellentrenner, Klammer oder Komma ("des Kindes:", "ist der"); ein Komma nur
    # direkt vor dem Datum ("am Mittwoch,") - sonst traf "geboren, Umzug am ..." ein anderes Datum (§24-Review)
    r"[^\n\d.;,|(]{0,25},?[:.]?\s*$|"
    # ", dem/am" nur nach "Geburtstag" ("An seinem 45. Geburtstag, dem 12.03.2025") - nicht "geboren, am 05.03.2024
    # kuendigte er" (Review §25: sonst Ereignisdatum als Geburtsdatum mit falscher Altersangabe)
    r"\bgeburtstag[^\n\d.;,|(]{0,25},?\s*(?:dem|am)[:.]?\s*$"
)
_BIRTH_LABEL_BEFORE = re.compile(r"(?:" + _BIRTH_LABEL_SHORT + r")|(?:" + _BIRTH_LABEL_LONG + r")", re.IGNORECASE)
_BIRTH_WORD_AFTER = re.compile(
    r"(?:\s+in\s+[A-ZÄÖÜ][\wäöüß\-]+(?:\s+[A-ZÄÖÜ][\wäöüß\-]+)?)?\s+(?:geboren(?:e[nmrs]?)?\b|zur\s+welt\s+gekommen)",
    re.IGNORECASE,
)
# Datum eines Geburtstags (Jahrestag): verraet mit dem Alter das Geburtsdatum, ist aber selbst keines -> eigene
# Kategorie "geburtstag" (maskiert, ohne Altersangabe). Punkt nur als Ordinalzahl, nie nach einer Jahreszahl.
_BIRTHDAY_AFTER = re.compile(r"(?:[^\n.;]|(?<=\d)(?<!\d{4})\.){0,30}\bgeburtstag\b", re.IGNORECASE)
_PERSON_BEFORE_APPOSITION = re.compile(
    r"(?:\b(?:herrn?|frau|kläger(?:in)?|klaeger(?:in)?|beklagte[rn]?|mandant(?:in)?|zeug(?:e|in)|kind(?:es)?|sohn|"
    r"tochter|ehe(?:mann|frau)|antragsteller(?:in)?|antragsgegner(?:in)?|erblasser(?:in)?|schuldner(?:in)?|"
    r"gläubiger(?:in)?|glaeubiger(?:in)?|arbeitnehmer(?:in)?|mieter(?:in)?|vermieter(?:in)?|betroffene[rn]?|"
    r"verstorbene[rn]?)"
    r"(?:\s+(?:Dr\.|Prof\.)?\s*[A-ZÄÖÜ][\wäöüß\-]+){0,3})\s*[(,]\s*$",
    re.IGNORECASE,
)
# Lebensdaten auch mit Monatsnamen ("12. März 1940 - 1. Februar 2024", Review §24)
_APPOSITION_END = re.compile(r"\s*(?:[-–]\s*([^),\n]{1,25}?)\s*)?[),]")
# Traueranzeige "* 12.03.1940 † 01.02.2024": der Stern gilt hier auch ohne Komma/Klammer davor (vorher Geburtsdatum lesbar)
_OBITUARY_STAR_BEFORE = re.compile(r"(?<![\w*])\*\s*$")
_OBITUARY_CROSS_AFTER = re.compile(r"\s*[,;]?\s*†")
_LIFESPAN_PERSON = re.compile(r"\b(?:erblasser(?:in)?|verstorbene[rn]?)\b", re.IGNORECASE)
_NOT_A_BIRTH_AFTER = re.compile(r"[^\n.;]{0,25}\b(?:seit|ab|bis|beginn|eintritt|einzug|mietbeginn)\b", re.IGNORECASE)
_ISO_OR_SLASH_DATE = re.compile(r"\b(?:(?:19|20)\d{2}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/(?:19|20)?\d{2})\b")
_MINIMUM_BIRTH_AGE_YEARS = 14


def _date_year(value: str) -> int | None:
    iso = re.match(r"((?:19|20)\d{2})-\d{2}-\d{2}$", value)
    if iso:
        return int(iso.group(1))
    digits = re.findall(r"\d+", value)
    if not digits:
        return None
    year = int(digits[-1])
    if year < 100:
        year += 2000 if year <= 69 else 1900
    return year


_BIRTH_HEADER = re.compile(r"\s*(?:geburtsdatum|geb\.(?:-?\s?datum)?|geboren|dob)\s*", re.IGNORECASE)
_ANY_DATE_IN_CELL = re.compile(
    r"\b\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}\b|\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/(?:19|20)?\d{2}\b"
)


def _birth_table_dates(text: str) -> list[DetectedSpan]:
    """Datumsangaben in der Spalte "Geburtsdatum"/"geb." einer Tabelle bzw. CSV (siehe _table_column_values)."""
    return _table_column_values(text, _BIRTH_HEADER, _ANY_DATE_IN_CELL, "geburtsdatum")


def detect_geburtsdatum(text: str, *, current_year: int | None = None) -> list[DetectedSpan]:
    """Geburtsdaten (Kategorie "geburtsdatum") und Geburtstags-Jahrestage ("geburtstag") - siehe Kommentar oben."""
    from datetime import date

    this_year = current_year or date.today().year
    spans = _birth_table_dates(text)
    candidates = [*detect_datum(text), *_matches_from_pattern(text, _ISO_OR_SLASH_DATE, "datum")]
    for span in candidates:
        if any(s.start < span.end and span.start < s.end for s in spans):
            continue
        line_start = text.rfind("\n", 0, span.start) + 1
        before = text[max(line_start, span.start - 45) : span.start]
        category = "geburtsdatum"
        if (
            _BIRTH_LABEL_BEFORE.search(before)
            or _BIRTH_WORD_AFTER.match(text, span.end)
            or (_OBITUARY_STAR_BEFORE.search(before) and _OBITUARY_CROSS_AFTER.match(text, span.end))
        ):
            birth = True
            # "An seinem 45. Geburtstag, dem 12.03.2025" ist ein Jahrestag, kein Geburtsdatum (Risikoanalyse §25: sonst
            # falsche Altersangabe "1 vollendete Jahre") - Ordinalzahl vor "Geburtstag" oder Jahr juenger als 14 Jahre
            year = _date_year(span.value)
            if re.search(r"\bgeburtstag", before, re.IGNORECASE) and (
                re.search(r"\d{1,3}\.\s*geburtstag", before, re.IGNORECASE)
                or (year is not None and year > this_year - _MINIMUM_BIRTH_AGE_YEARS)
            ):
                category = "geburtstag"
        elif _BIRTHDAY_AFTER.match(text, span.end):
            birth, category = True, "geburtstag"
        else:
            year = _date_year(span.value)
            end = _APPOSITION_END.match(text, span.end)
            person = _PERSON_BEFORE_APPOSITION.search(text[max(line_start, span.start - 80) : span.start])
            birth = (
                year is not None
                and this_year - 120 <= year <= this_year - _MINIMUM_BIRTH_AGE_YEARS
                and person is not None
                and end is not None
                and not _NOT_A_BIRTH_AFTER.match(text, span.end)
            )
            if birth and end.group(1):
                # Zeitraum "(01.04.1995 – 31.03.2005)" ist nur bei Verstorbenen bzw. ab 14 Jahren Spanne eine Lebensangabe
                other = _date_year(end.group(1).rstrip(".,)"))
                birth = bool(_LIFESPAN_PERSON.search(person.group()) or (other and other - year >= _MINIMUM_BIRTH_AGE_YEARS))
        if birth:
            spans.append(DetectedSpan(category=category, start=span.start, end=span.end, value=span.value))
    return spans


# Todesdaten (Owner-Vorgabe 13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §24) - nach demselben Prinzip wie Geburtsdaten:
# zusammen mit Ort/Rolle ueber Traueranzeigen und Nachrufe reidentifizierend. Maskiert als [TODESDATUM_nn] (konsistent je
# Wert), lokal nur die vollen Jahre seit dem Tod (date_chronology.build_death_note). Erkannt am Todeskontext:
# - Bezeichnung davor ("verstorben am", "gestorben am", "verstarb am", "Todestag", "Todesdatum", "Sterbedatum",
#   "gest.", "verst.", "†", "der Tod (des Vaters) trat am ... ein", "Erbfall am/vom" - der Erbfall IST der Todestag);
# - "verstorben"/"gestorben"/"verschieden" danach ("ist am 01.02.2024 in Koeln verstorben");
# - zweites Datum von Lebensdaten nach einer Personenbezeichnung ("(12.03.1940 - 01.01.2026)");
# - in Tabellen/CSV die Spalte "Sterbedatum"/"Todesdatum"/"verstorben"/"gest.".
# NICHT: Woerter, die "Tod" nur enthalten ("Unfall mit Todesfolge am", "Todesanzeige erschien am") - dort ist das Datum
# ein gewoehnliches, lesbares Ereignisdatum.
_DEATH_LABEL_BEFORE = re.compile(
    # Bezeichnung (mit Wortgrenze: "Verstorbenen" ist keine Bezeichnung, Review §24), dann eine Luecke von bis zu 45
    # Zeichen ohne Ziffer, Satz-/Tabellentrenner, Klammer oder Komma; ein Komma nur direkt vor dem Datum.
    r"(?P<label>\b(?:verstorben|gestorben|verstarb|starb|verschied|verschieden|todestag|todesdatum|todeszeitpunkt|sterbedatum|"
    r"sterbetag|sterbefall|todesfall|ableben|erbfall(?:s)?)\b|\b(?:gest|verst)\.|†|"
    r"\btod\s+(?:(?:de[rs]|seine[rs]?|ihre[rs]?|von)\s+(?:(?:herrn?|frau)\s+)?[\wäöüß]+\s+)?(?:ist\s+|trat\s+)?(?=am\b)|"
    r"\b(?:died|passed\s+away)\b)"
    r"(?P<gap>[^\n\d.;,|(]{0,45}),?(?:\s*dem)?[:.]?\s*$",
    re.IGNORECASE,
)
# Woerter in der Luecke, die das folgende Datum zu einer Frist oder einem anderen Ereignis machen ("des Verstorbenen haben
# Frist bis", "verstorben und wurde am ... beigesetzt", "Erbfall wurde Frist bis zum ...").
_DEATH_GAP_STOP = re.compile(
    r"\b(?:frist|bis|spätestens|spaetestens|termin|wurde|wurden|und|oder|beigesetzt|bestattet|beerdigt|beerdigung|"
    r"eröffnet|eroeffnet|antrag|zahlung|schreiben|vertrag)\b",
    re.IGNORECASE,
)
_DEATH_WORD_AFTER = re.compile(
    # Nur feste Fuellwoerter zwischen Datum und Verb ("in Koeln", "im Krankenhaus", "nach langer Krankheit") - eine freie
    # Luecke traf Kenntnis-, Bescheid- und Schreibensdaten ("Am 05.03.2024 erfuhr die Tochter dass der Vater verstorben
    # ist", 2. Review-Runde §24); die bleiben lesbar und als Frist erkennbar.
    r"(?:\s+in\s+[A-ZÄÖÜ][\wäöüß\-]+(?:\s+[A-ZÄÖÜ][\wäöüß\-]+)?|\s+im\s+alter\s+von\s+\d{1,3}\s+jahren|"
    r"\s+im\s+[\wäöüß\-]+(?:\s+[A-ZÄÖÜ][\wäöüß\-]+)?|"
    r"\s+nach\s+(?:langer|kurzer|schwerer)\s+[\wäöüß]+|\s+zu\s+hause)?"
    # "Die am 01.02.2024 Verstorbene" (Substantiv, gross) zaehlt; "des am ... verstorbenen Vaters" (Adjektiv) ebenso nur
    # direkt nach dem Datum - "Schreiben vom ... an die Erben des verstorbenen Vaters" hat Woerter dazwischen
    r"\s+(?:verstorben|gestorben|verschieden)\b(?!\s+(?:ist\s+)?(?:sei|war)\b)|\s+(?-i:Verstorbene[nrs]?)\b|"
    # Traueranzeige ("ist am ... sanft entschlafen", "hat uns am ... fuer immer verlassen") und "am ... eingetretenen Tod"
    r"(?:\s+(?:sanft|friedlich|ruhig|still))?\s+(?:entschlafen|heimgegangen)\b|\s+(?:f(?:ü|ue)r\s+immer|von\s+uns)\s+"
    r"(?:verlassen|gegangen)\b|\s+eingetretene[nm]?\s+tod\b",
    re.IGNORECASE,
)
_DEATH_PERSON_BEFORE_PAREN = re.compile(r"\bverstorbene[nrs]?\s+[\wäöüß]+\s*\(\s*$", re.IGNORECASE)
_DEATH_HEADER = re.compile(
    r"\s*(?:sterbedatum|todesdatum|todestag|todeszeitpunkt|verstorben(?:\s+am)?|gest\.|gestorben)\s*", re.IGNORECASE
)
_LIFESPAN_DASH = re.compile(r"\s*[-–]\s*")


_LINE_LABELS = re.compile(
    r"verstorben|gestorben|todestag|todesdatum|todeszeitpunkt|sterbedatum|sterbetag|gest\.|verst\.|†", re.IGNORECASE
)


def _label_window(text: str, start: int, width: int) -> str:
    """Text vor `start` innerhalb der Zeile. Steht das Datum allein am Zeilenanfang und besteht die Zeile davor NUR aus
    einer Todesdatum-Bezeichnung ("Todesdatum:\n01.02.2024"), zaehlt diese Bezeichnung mit - nicht aber eine beliebige
    Vorzeile ("Todesfall gemeldet\n05.03.2024: Termin ...", 2. Review-Runde §24)."""
    line_start = text.rfind("\n", 0, start) + 1
    if not text[line_start:start].strip() and line_start > 0:
        previous_start = text.rfind("\n", 0, line_start - 1) + 1
        previous = text[previous_start : line_start - 1].strip()
        label = _DEATH_LABEL_BEFORE.search(previous)  # Bezeichnung am ENDE der Vorzeile (Praefixe wie "[Schreiben] ")
        # nur ausdrueckliche Datumsbezeichnungen, nicht "Erbfall"/"Todesfall" als Ueberschrift vor einem Termin
        if (
            label is not None
            and not label.group("gap").strip(" \t:.")
            and _LINE_LABELS.fullmatch(label.group("label").strip())
        ):
            return previous + " "
    return text[max(line_start, start - width) : start]


def _death_label_matches(before: str) -> bool:
    match = _DEATH_LABEL_BEFORE.search(before)
    return match is not None and not _DEATH_GAP_STOP.search(match.group("gap"))


def detect_todesdatum(text: str, *, current_year: int | None = None) -> list[DetectedSpan]:
    """Todesdaten (Kategorie "todesdatum") - siehe Kommentar oben."""
    spans = _table_column_values(text, _DEATH_HEADER, _ANY_DATE_IN_CELL, "todesdatum")
    candidates = [*detect_datum(text), *_matches_from_pattern(text, _ISO_OR_SLASH_DATE, "datum")]
    # Lebensdaten: das Datum direkt nach "Geburtsdatum – " (alle Datumsformate, Review §24)
    for birth in detect_geburtsdatum(text, current_year=current_year):
        dash = _LIFESPAN_DASH.match(text, birth.end)
        if dash and dash.end() > birth.end:
            second = next((c for c in candidates if c.start == dash.end()), None)
            if second is not None:
                spans.append(DetectedSpan(category="todesdatum", start=second.start, end=second.end, value=second.value))
    for span in candidates:
        if any(s.start < span.end and span.start < s.end for s in spans):
            continue
        before = _label_window(text, span.start, 70)
        if (
            _death_label_matches(before)
            or _DEATH_WORD_AFTER.match(text, span.end)
            or (_DEATH_PERSON_BEFORE_PAREN.search(before) and re.match(r"\s*\)", text[span.end : span.end + 3]))
        ):
            spans.append(DetectedSpan(category="todesdatum", start=span.start, end=span.end, value=span.value))
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
    detect_referenznummer,
    detect_zahlungskarte_und_sv_rest,
    detect_grundbuch,
    detect_ip_adresse,
    detect_bic,
    detect_geburtsdatum,
    detect_todesdatum,
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
    analysis = _TextAnalysis(text)  # §33: Wortarten hoechstens einmal je Text
    all_spans = _drop_known_name_in_ordinary_adjective(analysis, all_spans, known_entities)
    all_spans = _separate_conflicting_surname_aliases(analysis, all_spans, known_entities)
    all_spans = _anchor_person_variants(all_spans, known_entities, analysis=analysis)
    all_spans = _handle_citations(text, all_spans)
    all_spans = _drop_contact_labels(text, all_spans)
    resolved = _resolve_overlaps(all_spans)
    return _extend_with_repeated_occurrences(text, resolved)


# --- Namensvarianten (P05, PRIVACY_REVIEW_ARCHITECTURE.md §32) ----------------------------------------------------------
# ECHTER FUND (Benchmark pseudonymization_v1, Fall P05): "Herr Brenner", "Herrn Brenner" und "Brenners" erhielten im selben
# Text drei Platzhalter. Ursache: die NER-Treffer enthielten die Anrede ("Herrn Brenner") bzw. das Genitiv-s ("Brenners"),
# begannen dadurch frueher bzw. waren laenger als der bekannte Name "Brenner" und gewannen die Ueberlappungsaufloesung;
# die Alias-Regel im Pseudonymizer greift nur fuer unveraenderte, einwortige Nachnamen. Abhilfe: Personentreffer werden
# VOR der Ueberlappungsaufloesung um eine fuehrende Anrede gekuerzt und - nur wenn der Rest EINDEUTIG verankert ist - um
# ein Genitiv-s. Verankert = bekannter Name (aus known_entities) oder Nachname GENAU EINES mehrteiligen Personentreffers
# im selben Text. Gleiche Nachnamen verschiedener Personen sind nie verankert (keine spekulative Zusammenfuehrung).
# Treffer werden nur VERKLEINERT, nie erweitert: die Anrede bleibt lesbar ("Herrn [MANDANT_01]"); ein Genitivtreffer behaelt
# seinen Bereich, die Endung wird beim Ersetzen hinter den Platzhalter gestellt ("[MANDANT_01]s", Pseudonymizer).
_VARIANT_SALUTATIONS = ("herr", "herrn", "frau", "hr.", "hrn.", "fr.")
_GENITIVE_SUFFIXES = ("’s", "'s", "s")


def _surname_owners(spans: list[DetectedSpan], known_entities: dict[str, list[str]] | None) -> dict[str, set[str]]:
    """Nachname/bekannter Name (kleingeschrieben) -> Menge der zugehoerigen Vollnamen (fuer die Eindeutigkeitspruefung)."""
    owners: dict[str, set[str]] = {}
    for values in (known_entities or {}).values():
        for value in values:
            words = (value or "").split()
            if len(words) >= 2:
                owners.setdefault(words[-1].lower(), set()).add(value.lower())
    for values in (known_entities or {}).values():
        for value in values:
            words = (value or "").split()
            if len(words) == 1 and words[0].lower() not in owners:
                owners.setdefault(words[0].lower(), set()).add(words[0].lower())
    for span in spans:
        words = span.value.split()
        if span.category == "person" and len(words) >= 2 and words[0].lower() not in _VARIANT_SALUTATIONS:
            owners.setdefault(words[-1].lower(), set()).add(span.value.lower())
    return owners


def _anchor_person_variants(
    spans: list[DetectedSpan], known_entities: dict[str, list[str]] | None, *, analysis: "_TextAnalysis | None" = None
) -> list[DetectedSpan]:
    analysis = analysis or _TextAnalysis("")
    owners = _surname_owners(spans, known_entities)
    # bekannter Name (kleingeschrieben) -> Kategorie, nur wenn eindeutig genau EINER Kategorie zugeordnet
    known_category: dict[str, set[str]] = {}
    for category, values in (known_entities or {}).items():
        for value in values:
            if value and value.strip():
                known_category.setdefault(value.strip().lower(), set()).add(category)

    def anchored(name: str) -> bool:
        key = name.lower()
        if key in known_category:
            return len(known_category[key]) == 1 and len(owners.get(name.split()[-1].lower(), ())) <= 1
        return len(owners.get(name.split()[-1].lower(), ())) == 1

    def is_own_name(value: str) -> bool:
        """Ist die (Genitiv-)Form selbst ein Name? - ohne den Treffer selbst als Namenstraeger zu zaehlen (§33: "Peter
        Langes" stand sonst als eigener Vollname in `owners` und verhinderte die Verankerung auf "Peter Lange")."""
        if value.lower() in known_category:
            return True
        return bool(owners.get(value.split()[-1].lower(), set()) - {value.lower()})

    result: list[DetectedSpan] = []
    for span in spans:
        if span.category != "person":
            result.append(span)
            continue
        start, end, value = span.start, span.end, span.value
        # §33: fuehrende Zahlen-/Datumstoken gehoeren nicht zum Namen ("01.03.2026. Langes" - die NER zog das Datum davor
        # in den Personentreffer; Frist ging verloren, und der Rest wurde einer anderen Person zugeordnet). Nur Verkleinerung.
        while True:
            lead = re.match(r"[\d.,:/-]+\s+", value)
            if not lead or not value[lead.end():].strip():
                break
            start, value = start + lead.end(), value[lead.end():]
        if (start, value) != (span.start, span.value):
            span = DetectedSpan(category=span.category, start=start, end=end, value=value)
        words = value.split()
        # fuehrende Anrede abtrennen, wenn der Rest eindeutig verankert ist ("Herrn Brenner", "Herrn Karl Brenner")
        if len(words) >= 2 and words[0].lower() in _VARIANT_SALUTATIONS:
            offset = value.index(words[1], len(words[0]))
            rest = value[offset:]
            core_rest = rest
            for suffix in _GENITIVE_SUFFIXES:
                if rest.endswith(suffix) and anchored(rest[: -len(suffix)]) and not is_own_name(rest):
                    core_rest = rest[: -len(suffix)]
                    break
            if anchored(core_rest):
                start, value = start + offset, rest
        # Genitiv-s: der ersetzte Bereich behaelt das "s" (kein "[X]s"-Rest, den der Restrisiko-Scan als Ort las), die
        # Identitaet wird ueber den verankerten Kern zugeordnet
        core = value
        salutation_cut = start != span.start
        if " " not in value:
            for suffix in _GENITIVE_SUFFIXES:
                if value.endswith(suffix) and len(value) - len(suffix) >= 3:
                    candidate = value[: -len(suffix)]
                    # §33: ohne Anrede nur ein Eigenname ("Brenners Anspruch": PROPN), kein Adjektiv ("Langes Zoegern": ADJ)
                    if (anchored(candidate) and not is_own_name(value)
                            and (salutation_cut or analysis.tag_at(span.start + span.value.rfind(value)) == "PROPN")):
                        core = candidate
                    break
        elif " " in value:
            for suffix in _GENITIVE_SUFFIXES:
                candidate = value[: -len(suffix)]
                # §33: mehrteiliger Kern nur, wenn er EXAKT ein bekannter oder im Text vorhandener Vollname ist ("Peter Langes" ->
                # "Peter Lange"); ein Nachname allein genuegt nicht ("01.03.2026. Langes" wurde sonst verankert)
                exact = candidate.lower() in known_category or candidate.lower() in owners.get(candidate.split()[-1].lower(), set())
                if value.endswith(suffix) and exact and anchored(candidate) and not is_own_name(value):
                    core = candidate
                    break
        if (start, core) == (span.start, span.value):
            result.append(span)
            continue
        if not anchored(core):
            result.append(span)
            continue
        categories = known_category.get(core.lower(), set())
        category = next(iter(categories)) if len(categories) == 1 else span.category
        # §33: Anrede, deren Geschlecht dem bekannten Vollnamen widerspricht ("Frau Brenner" bei "Karl Brenner") -> eigene
        # Person, nicht der bekannte Name (weiterhin maskiert)
        if category != span.category and _salutation_conflicts(analysis, span.start, core, category, known_entities):
            category = span.category
        result.append(DetectedSpan(category=category, start=start, end=end, value=core))
    return result


# --- Gleicher Nachname, andere Person (§33) -------------------------------------------------------------------------------
# ECHTER FUND: die Nachnamen-Ergaenzung der bekannten Namen (RuleBasedLocalAIProvider._build_known_entities: "Karl Brenner"
# -> zusaetzlich "Brenner") ordnete JEDE Anrede+Nachname dem Mandanten zu - auch "Frau Brenner", ausdruecklich eine andere
# Person. Abhilfe: steht vor dem blossen Nachnamen eine Anrede, deren Geschlecht dem bekannten Vollnamen WIDERSPRICHT, wird
# der Treffer eine eigene Person (bleibt maskiert, eigener Platzhalter). Geschlecht des Vollnamens: Anrede/Rollenwort
# unmittelbar vor dem Vollnamen im Text und eine kleine Liste eindeutiger Vornamen; widersprechen sich die Belege, gilt das
# Geschlecht als unbekannt. Ohne eindeutigen Beleg bleibt die bisherige Zuordnung (dokumentierte Grenze) - es wird nie eine
# Identitaet ERZEUGT, nur eine unbelegte aufgehoben.
_SALUTATION_GENDER = {"herr": "m", "herrn": "m", "hr.": "m", "hrn.": "m", "frau": "f", "fr.": "f"}
_ROLE_GENDER_BEFORE_NAME = {
    "mandant": "m", "mandantin": "f", "kläger": "m", "klägerin": "f", "beklagter": "m", "beklagte": "f",
    "zeuge": "m", "zeugin": "f", "mieter": "m", "mieterin": "f", "vermieter": "m", "vermieterin": "f",
}
# Nur eindeutige Vornamen (Review §33: "Andrea", "Simone" sind je nach Sprache maennlich oder weiblich - nicht enthalten)
_MALE_FIRST_NAMES = frozenset(
    "karl peter hans klaus michael thomas andreas stefan stephan markus frank uwe jürgen günter gerhard wolfgang dieter "
    "horst bernd werner heinz manfred rolf jan jens lars tobias florian sebastian alexander christian daniel martin "
    "matthias max moritz paul felix lukas leon jonas tim tom david simon philipp benjamin johannes georg friedrich "
    "wilhelm heinrich otto ernst walter kurt gernot quirin dietmar ralf olaf sven björn holger detlef helmut".split()
)
_FEMALE_FIRST_NAMES = frozenset(
    "anna maria petra sabine claudia susanne monika ursula renate brigitte ilse ute eva julia laura lena lea sarah "
    "sophie katharina christina stefanie nicole anja kerstin silke heike birgit karin gabriele ingrid helga gertrud "
    "elisabeth margarete henrike wiebke brunhilde theda erika hannah emma mia lina marie johanna franziska miriam "
    "carina tanja sandra melanie jessica nina lisa kathrin martina barbara christine angelika doris".split()
)
_SALUTATION_BEFORE = re.compile(r"(?:^|[\s(\[,;:])(Herrn?|Hrn?\.|Frau|Fr\.)\s+(?:(?:Dr|Prof)\.\s+)*$", re.IGNORECASE)
_WORD_BEFORE = re.compile(r"(\w+)\s+$")


class _TextAnalysis:
    """Einmalige, lazy Analyse eines Textes fuer die §33-Regeln (Review: spaCy lief vorher einmal JE Treffer - bei einem
    20k-Zeichen-Dokument ohne Satzzeichen ueber 100 s). Wortarten werden hoechstens einmal je Text ermittelt, das Geschlecht
    je Vollname zwischengespeichert. Faellt spaCy aus, liefert `tag_at` None (sichere Richtung: nichts wird verworfen oder
    verankert)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._tags: list[tuple[int, int, str]] | None = None
        self._failed = False
        self._gender: dict[str, str | None] = {}

    def tag_at(self, offset: int) -> str | None:
        if self._tags is None and not self._failed:
            try:
                from app.privacy.presidio_ner import get_pos_tags  # lokal: presidio_ner importiert dieses Modul

                self._tags = sorted((s, e, tag) for (s, e), tag in get_pos_tags(self.text).items())
            except Exception:  # noqa: BLE001 - sichere Richtung, siehe Docstring
                self._failed = True
        for start, end, tag in self._tags or ():
            if start <= offset < end:
                return tag
            if start > offset:
                break
        return None

    def salutation_gender(self, start: int) -> str | None:
        match = _SALUTATION_BEFORE.search(self.text[max(0, start - 40) : start])
        return _SALUTATION_GENDER.get(match.group(1).lower()) if match else None

    def full_name_gender(self, full_name: str) -> str | None:
        key = full_name.lower()
        if key not in self._gender:
            self._gender[key] = self._compute_gender(full_name)
        return self._gender[key]

    def _compute_gender(self, full_name: str) -> str | None:
        genders: set[str] = set()
        for match in re.finditer(re.escape(full_name), self.text, re.IGNORECASE):
            gender = self.salutation_gender(match.start())
            if gender is None:
                word = _WORD_BEFORE.search(self.text[max(0, match.start() - 30) : match.start()])
                gender = _ROLE_GENDER_BEFORE_NAME.get(word.group(1).lower()) if word else None
            if gender:
                genders.add(gender)
        first = full_name.split()[0].lower()
        if first in _MALE_FIRST_NAMES:
            genders.add("m")
        elif first in _FEMALE_FIRST_NAMES:
            genders.add("f")
        return next(iter(genders)) if len(genders) == 1 else None


def _salutation_conflicts(
    analysis: _TextAnalysis, span_start: int, surname: str, category: str, known_entities: dict[str, list[str]] | None
) -> bool:
    text = analysis.text
    if not text:
        return False
    gender = analysis.salutation_gender(span_start)
    if gender is None:
        # Anrede innerhalb des (ungekuerzten) Treffers selbst ("Frau Brenner")
        head = text[span_start : span_start + 8].split()
        gender = _SALUTATION_GENDER.get(head[0].lower()) if head else None
    if gender is None:
        return False
    owners = [v for v in (known_entities or {}).get(category, []) if len(v.split()) >= 2
              and v.split()[-1].lower() == surname.lower()]
    if len(owners) != 1:
        return False
    owner_gender = analysis.full_name_gender(owners[0])
    return owner_gender is not None and owner_gender != gender


def _separate_conflicting_surname_aliases(
    analysis: _TextAnalysis, spans: list[DetectedSpan], known_entities: dict[str, list[str]] | None
) -> list[DetectedSpan]:
    if not known_entities:
        return spans
    result: list[DetectedSpan] = []
    for span in spans:
        if (span.category in known_entities and len(span.value.split()) == 1
                and analysis.salutation_gender(span.start) is not None
                and _salutation_conflicts(analysis, span.start, span.value, span.category, known_entities)):
            result.append(DetectedSpan(category="person", start=span.start, end=span.end, value=span.value))
        else:
            result.append(span)
    return result


# §33: bekannte Namen werden bewusst OHNE Wortgrenze gesucht (Genitiv "Brenners", Komposita "Brennerhaus" bleiben so
# maskiert). Ein Nachname, der zugleich ein Adjektiv ist ("Groß", "Kurz", "Lange"), traf dadurch gewoehnliche Woerter:
# "Großes Interesse" -> "[MANDANT_01]es Interesse". Verworfen wird ein solcher Treffer NUR, wenn er Anfang eines laengeren
# Wortes mit reiner Adjektivendung ist UND spaCy dieses Wort als Adjektiv taggt. Ableitungen vom Namen ("Brennersche Erben"),
# Genitive und Komposita bleiben maskiert.
_ADJECTIVE_ENDINGS = ("e", "es", "er", "en", "em")


def _drop_known_name_in_ordinary_adjective(
    analysis: _TextAnalysis, spans: list[DetectedSpan], known_entities: dict[str, list[str]] | None
) -> list[DetectedSpan]:
    if not known_entities:
        return spans
    result: list[DetectedSpan] = []
    for span in spans:
        if span.category in known_entities and len(span.value.split()) == 1:
            tail = re.match(r"[\wÄÖÜäöüß]+", analysis.text[span.end :])
            suffix = tail.group(0) if tail else ""
            if suffix.lower() in _ADJECTIVE_ENDINGS and analysis.tag_at(span.start) == "ADJ":
                continue
        result.append(span)
    return result


# --- Juristische Fundstellen (§33) ----------------------------------------------------------------------------------------
# ECHTER FUND (Benchmark pseudonymization_v1, P16): "BGH, Urt. v. 12.10.2022 - VIII ZR 1/21" wurde zu "BGH, [PERSON_01]. v.
# ... - [PERSON_02] ZR 1/21"; "BVerfG" als Ort maskiert.
# (a) Nie als Person/Ort/Organisation: roemische Senatsnummer UNMITTELBAR vor Registerzeichen und Nummer, Abkuerzungen mit
#     Punkt ("Urt.", "Beschl.", "Vgl.", "Rn.", "Az."), Kuerzel der Bundesgerichte.
# (b) Aktenzeichen im Format der Bundesgerichte - auch mit roemischer Senatsnummer ("VIII ZR 99/25") - werden als
#     Aktenzeichen ERKANNT und maskiert (vorher deckte nur der zufaellige Personentreffer auf "VIII" einen Teil ab). Lesbar
#     bleiben sie NUR in der kanonischen Zitierform "<Bundesgericht>, Urt./Beschl. v. <Datum> - <Az>" und nie, wenn im Satz
#     davor ein Hinweis auf das eigene Verfahren steht (Review §33: "Unsere Verfassungsbeschwerde beim BVerfG, 1 BvR 77/25"
#     ging sonst im Klartext hinaus). Aktenzeichen unterer Instanzen und eigene Aktenzeichen bleiben immer maskiert.
_FEDERAL_COURTS = frozenset({"BGH", "BVerfG", "BAG", "BFH", "BSG", "BVerwG", "BPatG", "EuGH", "EGMR", "BGHZ", "BGHSt"})
# §66 (Korpus privacy_sources_v1): "Unterabs." und "Buchst." in Normzitaten ("Art. 6 Abs. 1 Unterabs. 1 Buchst. f
# DSGVO") - "Buchst" wurde als Person maskiert, das Zitat war im Payload unvollstaendig. Wie "Abs." nur mit Punkt.
_LEGAL_ABBREVIATIONS = frozenset({"urt", "beschl", "vgl", "rn", "az", "hs", "abs", "bgbl", "anm", "unterabs", "buchst"})
_FEDERAL_REGISTER = r"(?:ZR|ZB|ZA|AZR|ABR|AZB|BvR|BvL|BvF|BvE|StR|KZR|KVR)"
_ROMAN_SENATE_AFTER = re.compile(r"\s+" + _FEDERAL_REGISTER + r"\s+\d{1,5}/\d{2,4}\b")
_FEDERAL_FILE_NUMBER = re.compile(r"(?<![\w/])(?:[IVXL]{1,6}|\d{1,2})\s+" + _FEDERAL_REGISTER + r"\s+\d{1,5}/\d{2,4}\b")
# Zitierformen: "<Bundesgericht>, Urt./Beschl. v. <Datum> - <Az>" und "<Bundesgericht>[,] <Az>" (unmittelbar; frueherer
# Stand §22: "Vgl. BGH VI ZR 123/19, NJW 2020, 2345" bleibt lesbar). Reporter-Formen ("BGH NJW 2023, 123 (I ZR 23/20)")
# bleiben konservativ maskiert.
_COURT_ALTERNATIVES = r"\b(?:" + "|".join(sorted(_FEDERAL_COURTS)) + r")"
# §66: Datum auch mit ausgeschriebenem Monat ("BGH, Urteil vom 8. Oktober 2025 – VIII ZR 18/24", die uebliche Zitierform
# in Schriftsaetzen und Entscheidungen) - vorher nur numerisch, sodass dieselbe kanonische Zitierform maskiert wurde.
# Unveraendert: nur Bundesgerichte, nur Bundes-Aktenzeichen, nie bei Hinweis auf das eigene Verfahren (_OWN_PROCEEDING_HINT).
_GERMAN_MONTHS = r"(?:Januar|Jänner|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)"
_CITATION_WITH_DATE_BEFORE = re.compile(
    _COURT_ALTERNATIVES + r",?\s+(?:Teil-|Versäumnis-|End-)?(?:Urt|Beschl|Urteil|Beschluss)\.?"
    r"\s+v(?:om|\.)\s+(?:\d{1,2}\.\s?\d{1,2}\.\s?\d{4}|\d{1,2}\.\s?" + _GERMAN_MONTHS + r"\s+\d{4})\s*[-–—,]\s*$"
)
# Kurzform "<Bundesgericht>[,] <Az>" nur mit roemischer Senatsnummer (Stand vor §33, §22: "Vgl. BGH VI ZR 123/19");
# arabische Kurzformen ("BAG 2 AZR 55/25") blieben vorher maskiert und bleiben es (Review §33)
_CITATION_SHORT_BEFORE = re.compile(_COURT_ALTERNATIVES + r",?\s*$")
_OWN_PROCEEDING_HINT = re.compile(
    r"\b(?:unser\w*|eigene\w*|Mandant\w*|Revision|Beschwerde|Verfassungsbeschwerde|eingelegt|anhängig|anhaengig|"
    r"Az|Aktenzeichen|Zeichen|Gz|Geschäftszeichen|Verfahren)\b",
    re.IGNORECASE,
)


def _handle_citations(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    # (b) Bundes-Aktenzeichen erkennen (zusaetzlich zu bestehenden Treffern; Ueberlappungen loest _resolve_overlaps)
    added = [DetectedSpan(category="aktenzeichen", start=m.start(), end=m.end(), value=m.group(0))
             for m in _FEDERAL_FILE_NUMBER.finditer(text)]
    result: list[DetectedSpan] = []
    for span in [*spans, *added]:
        value = span.value.strip()
        if span.category in ("person", "ort", "organisation"):
            if re.fullmatch(r"[IVXL]{1,6}", value) and _ROMAN_SENATE_AFTER.match(text, span.end):
                continue
            if value.lower() in _LEGAL_ABBREVIATIONS and text[span.end : span.end + 1] == ".":
                continue
            if value in _FEDERAL_COURTS:
                continue
        if span.category in ("aktenzeichen", "kennung") and _FEDERAL_FILE_NUMBER.fullmatch(value):
            window_start = max(0, span.start - 80)
            before = text[window_start : span.start]
            citation = _CITATION_WITH_DATE_BEFORE.search(before)
            if citation is None and re.match(r"[IVXL]", value):
                citation = _CITATION_SHORT_BEFORE.search(before)
            if citation:
                court_pos = window_start + citation.start()
                # Hinweis auf das eigene Verfahren in den 120 Zeichen davor oder 80 danach - bewusst OHNE Schnitt an Punkt,
                # Zeilenumbruch oder Semikolon (Review §33: "vgl.", "z. B.", "Az." schnitten das Fenster sonst ab)
                context = text[max(0, court_pos - 120) : court_pos] + " " + text[span.end : span.end + 80]
                if not _OWN_PROCEEDING_HINT.search(context):
                    continue
        result.append(span)
    return result


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


# §52: Woerter VOR einem Namen, die nicht zum Namen gehoeren - der Browser entfernt sie beim Markieren (chat.html,
# gleiche Liste, Test prueft die Gleichheit), der Server lehnt einen Begriff ab, der damit beginnt.
MANUAL_NAME_PREFIX_WORDS = frozenset({
    "herr", "herrn", "frau", "dr", "prof", "dres", "dipl", "ing", "mag", "richter", "richterin", "vorsitzende",
    "vorsitzender", "rechtspfleger", "rechtspflegerin", "sachverständige", "sachverständiger", "gutachter",
    "gutachterin", "zeuge", "zeugin", "rechtsanwalt", "rechtsanwältin", "staatsanwalt", "staatsanwältin", "notar",
    "notarin", "gerichtsvollzieher", "gerichtsvollzieherin", "mandant", "mandantin", "kläger", "klägerin", "beklagte",
    "beklagter", "schöffe", "schöffin",
})
_PREFIX_WORD = r"(?:" + "|".join(sorted(MANUAL_NAME_PREFIX_WORDS, key=len, reverse=True)) + r")\.?"
# Anrede/Titel/Rolle(n) VOR einem Namen (ein NER-Treffer "Dr. Hans-Peter Müller" bzw. "Richter Brummelbeck")
NAME_PREFIX_RUN = re.compile(r"(?:" + _PREFIX_WORD + r"[ \t]+)+", re.IGNORECASE)


# §52/§53: Namenszusaetze (kleingeschriebene Teile eines Personennamens: "Anna von Berg", "van der Meer")
NAME_PARTICLES = frozenset({
    "von", "vom", "van", "de", "der", "den", "del", "della", "di", "da", "du", "dos", "das", "zu", "zum", "zur",
    "ten", "ter", "le", "la", "y", "bin", "al", "el", "und",
})


def is_name_prefix_word(word: str) -> bool:
    """§53: Anrede/Titel/Rolle vor einem Namen ("Herr", "Dr.", "Dipl.-Ing.", "Richterin")."""
    parts = [part for part in re.split(r"[.\-]", word.lower().rstrip(".:,")) if part]
    return bool(parts) and all(part in MANUAL_NAME_PREFIX_WORDS for part in parts)


def name_context(text_before: str) -> tuple[str, tuple[str, ...]] | None:
    """§53: Steht unmittelbar vor einer Textstelle (gleiche Zeile) eine Anrede/Titel/Rolle, gefolgt von hoechstens
    Namenszusaetzen? -> (letztes Anrede-/Titel-/Rollenwort in Kleinschreibung ohne Punkt, Zusaetze). Sonst None.
    "Frau von " -> ("frau", ("von",)); "Herr Dr. " -> ("dr", ()); "Brief von " -> None."""
    words = text_before.split("\n")[-1].split()
    particles: list[str] = []
    while words and words[-1] in NAME_PARTICLES:
        particles.insert(0, words.pop())
    if not words or not is_name_prefix_word(words[-1]):
        return None
    return words[-1].lower().rstrip(".:,").replace("herrn", "herr"), tuple(particles)


def strip_name_prefix(value: str) -> str:
    """§52: Name ohne fuehrende Anrede/Titel/Rolle ("Dr. Hans-Peter Müller" -> "Hans-Peter Müller"); unveraendert, wenn
    danach nichts uebrig bliebe."""
    match = NAME_PREFIX_RUN.match(value)
    return value[match.end():] if match and value[match.end():].strip() else value


# §50 (16.10.2026, Qualitaetsmeldung seed-009 "Herrn Gustav Wendelin, Mobil 0170 1234567"): die NER las "Mobil" (im
# Gateway-Text sogar "Mobil 0170") als Person. Kontaktbezeichnungen sind keine Namen, wenn UNMITTELBAR dahinter eine
# erkannte Telefonnummer/E-Mail-Adresse beginnt (dieselbe Wortliste wie die Rueckfrage, review.py §75.2). Nur NER-artige
# Kategorien; ein Name an derselben Stelle ("Lehmann 0170 1234567") bleibt geschuetzt.
CONTACT_LABEL_WORDS = frozenset({
    "telefon", "tel", "telefonnummer", "rufnummer", "durchwahl", "festnetz", "mobil", "mobilnummer", "mobiltelefon",
    "handy", "handynummer", "telefax", "fax", "faxnummer", "mail", "email", "emailadresse", "kontakt",
})
_CONTACT_LABEL_HEAD = re.compile(r"[A-Za-zÄÖÜäöüß-]+")
_CONTACT_LABEL_GAP = re.compile(r"\.?[ \t]*:?[ \t]*")
_NER_LIKE_CATEGORIES = frozenset({"person", "ort", "organisation"})
_CONTACT_CATEGORIES = frozenset({"telefon", "email"})
# Restrisiko-Scan (security_check) prueft den bereits pseudonymisierten Text - dort steht die Nummer als Platzhalter.
# KENNUNG: eine Rufnummer, die die Kennnummern-Erkennung laenger erfasst als die Telefon-Regex (pseudonymization_v1 K04
# "Mobil 0171-234 56 78" -> "Mobil [KENNUNG_01]"), sonst las der Scan "Mobil" erneut als Person und blockierte.
_CONTACT_PLACEHOLDER = re.compile(r"\[(?:TELEFON|EMAIL|KENNUNG)_\d+\]")


def _drop_contact_labels(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    contact_end: dict[int, int] = {}
    for span in spans:
        if span.category in _CONTACT_CATEGORIES:
            contact_end[span.start] = max(contact_end.get(span.start, span.end), span.end)
    for match in _CONTACT_PLACEHOLDER.finditer(text):
        contact_end[match.start()] = max(contact_end.get(match.start(), match.end()), match.end())
    if not contact_end:
        return spans
    kept: list[DetectedSpan] = []
    for span in spans:
        if span.category in _NER_LIKE_CATEGORIES:
            head = _CONTACT_LABEL_HEAD.match(span.value)
            if head and head.group().lower().replace("-", "") in CONTACT_LABEL_WORDS:
                gap = _CONTACT_LABEL_GAP.match(text, span.start + head.end())
                if gap.end() in contact_end and span.end <= contact_end[gap.end()]:
                    continue  # Bezeichnung (ggf. mit Anfang der Nummer) - die Nummer selbst bleibt als eigener Treffer
        kept.append(span)
    return kept


# §50: strukturierte, formatgepruefte Treffer gehen bei einer Ueberlappung einem NER-Treffer vor, der sie nur anschneidet.
_STRUCTURED_CATEGORIES = frozenset({"telefon", "email", "iban", "bic", "url"})


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
        elif span.end > last_end and resolved:
            # §50 (seed-009: "Mobil 0170" Person + "0170 1234567" Telefon -> "[PERSON_02] 1234567", der Rest der Nummer ging
            # im Klartext hinaus): auch bei VERSCHIEDENEN Kategorien bleibt kein Zeichen eines erkannten Treffers unersetzt.
            previous = resolved[-1]
            head = previous.value[: max(0, span.start - previous.start)].rstrip()
            if (previous.category in _NER_LIKE_CATEGORIES and span.category in _STRUCTURED_CATEGORIES
                    and previous.start < span.start and head.strip()):
                # NER-Treffer endet vor der Nummer/Adresse; die strukturierte Angabe bleibt vollstaendig ein Treffer
                resolved[-1] = DetectedSpan(category=previous.category, start=previous.start,
                                            end=previous.start + len(head), value=head)
                resolved.append(span)
            else:
                rest = span.value[last_end - span.start :]
                lead = len(rest) - len(rest.lstrip())
                if rest.strip():
                    resolved.append(DetectedSpan(category=span.category, start=last_end + lead, end=span.end,
                                                 value=rest[lead:]))
            last_end = span.end
        # uebrige ueberlappende, kuerzere/spaetere Treffer werden verworfen.
    return resolved
