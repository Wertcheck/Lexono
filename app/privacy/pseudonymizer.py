"""Pseudonymizer – ersetzt erkannte PII durch Platzhalter wie [MANDANT_01].

WICHTIG: `PseudonymMapping`-Objekte werden von dieser Klasse nur im
Rückgabewert übergeben - es findet HIER keine Persistierung statt. Ob und
wie die Zuordnung lokal gespeichert wird (für die Rückführung nach einem
späteren Claude-API-Aufruf), entscheidet der Aufrufer bzw. der noch zu
bauende `ClaudePrivacyGateway`. Diese Klasse selbst sendet niemals etwas
irgendwohin - reine, seiteneffektfreie Textverarbeitung.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.privacy.detectors import DetectedSpan, detect_all, strip_name_prefix

_PLACEHOLDER_PREFIX_BY_CATEGORY = {
    "mandant": "MANDANT",
    "gegner": "GEGNER",
    "anwalt": "ANWALT",
    "gericht": "GERICHT",
    "aktenzeichen": "AKTENZEICHEN",
    "adresse": "ADRESSE",
    "datum": "DATUM",
    "geburtsdatum": "GEBURTSDATUM",
    "geburtstag": "GEBURTSTAG",
    "todesdatum": "TODESDATUM",
    "grundbuch": "GRUNDBUCH",
    "betrag": "BETRAG",
    "vertrag": "VERTRAG",
    "email": "EMAIL",
    "telefon": "TELEFON",
    "iban": "IBAN",
    "steuer_id": "STEUER_ID",
    "kundennummer": "KUNDENNUMMER",
    "rechnungsnummer": "RECHNUNGSNUMMER",
    "bic": "BIC",
    # Amtliche Kennnummern aus den offiziellen deutschen Presidio-Recognizern (presidio_ner._GERMAN_ID_RECOGNIZERS):
    # Steuernummer, USt-IdNr., Sozialversicherungs-, Ausweis-, Handelsregisternummer, Kfz-Kennzeichen ...
    "kennung": "KENNUNG",
    # Profil-Links sozialer/beruflicher Netzwerke (presidio_ner._PROFILE_URL) - der Pfad ist der Personenname.
    "url": "PROFIL_URL",
    # Presidio-NER-Kategorien (app/privacy/presidio_ner.py) - rollenneutral,
    # da Presidio keine Mandant/Gegner/Anwalt/Gericht-Rolle kennen kann.
    "person": "PERSON",
    "ort": "ORT",
    "organisation": "ORGANISATION",
    # Woertlich im Eingangstext stehende platzhalteraehnliche Zeichenfolgen ("[PERSON_01]", "PERSON_01"), siehe
    # `_literal_placeholder_spans` - kein personenbezogener Wert, sondern Text, der nie mit einem erzeugten
    # Platzhalter verwechselt werden darf.
    "platzhaltertext": "TEXTMARKE",
}
_LITERAL_CATEGORY = "platzhaltertext"


_LEFTOVER_PREFIXES = "|".join(
    sorted((re.escape(v) for v in set(_PLACEHOLDER_PREFIX_BY_CATEGORY.values())), key=len, reverse=True)
)
_LEFTOVER_IN_BRACKETS = re.compile(
    r"\[([^\[\]]*?)\s*(?<![A-Z0-9_])(?:" + _LEFTOVER_PREFIXES + r")_\d{2,}(?![A-Z0-9_])([^\[\]]*)\]"
)
_LEFTOVER_BARE = re.compile(r"(?<![A-Z0-9_])(?:" + _LEFTOVER_PREFIXES + r")_\d{2,}(?![A-Z0-9_])")


def neutralize_leftover_placeholder_tokens(text: str) -> str:
    """Ersetzt nach der Rueckfuehrung uebrig gebliebene INTERNE Tokennamen ("PERSON_02",
    "[Adresse PERSON_02]") durch einen lesbaren Einsetz-Hinweis.

    ECHTER FUND (Real-E2E 08.10., Drafting Fall C): Claude schrieb "[Adresse PERSON_02]"
    in den Briefkopf - eine Mischform aus Wort und Platzhaltername, die weder als Token
    erkannt noch zurueckgefuehrt wird und so dem Anwalt als rohes technisches Kuerzel
    im kopierbaren Schreiben erschien. Echte Tokens sind zu diesem Zeitpunkt bereits
    durch ihre Originalwerte ersetzt; was jetzt noch dem Muster entspricht, ist Rest."""

    def _bracketed(match: re.Match[str]) -> str:
        label = (match.group(1) + " " + match.group(2)).strip(" ,;:-")
        return f"[{label or 'Angabe'} einsetzen]"

    text = _LEFTOVER_IN_BRACKETS.sub(_bracketed, text)
    return _LEFTOVER_BARE.sub("[Angabe einsetzen]", text)


# Platzhalter-Integritaet (Folgezyklus 13.10.2026, gemessen): Ein Dokument kann platzhalteraehnliche Zeichenfolgen bereits
# woertlich enthalten (Lexono-Export, Vorlage, Formularfeld). Bisher bekam "Karl Brenner" neben einem woertlichen
# "[PERSON_01]" ebenfalls [PERSON_01] - Claude sah fuer zwei Dinge denselben Platzhalter, und die Rueckfuehrung setzte
# "Karl Brenner" auch an die Stelle des woertlichen Texts (ebenso [ADRESSE_01], [KENNUNG_01], zwei Dokumente). Ein
# woertliches "[PERSON_07]" ohne Gegenstueck blockierte die Anfrage. Deshalb wird jede solche Zeichenfolge im
# Eingangstext selbst maskiert ([TEXTMARKE_nn], Originalwert = der woertliche Text): danach stammt JEDER Platzhalter in
# der Payload aus der Erzeugung, eine Verwechslung ist ausgeschlossen. Erfasst wird alles, was das Final Payload Gate als
# Platzhalter-Token wertet (security_check._PLACEHOLDER_TOKEN_PATTERN, hier identisch) sowie nackte Tokennamen der
# bekannten Praefixe ("PERSON_01"), die die Rueckfuehrung sonst als Rest neutralisiert haette.
_PLACEHOLDER_LIKE_PATTERN = re.compile(r"\[[A-Za-zÄÖÜäöüß_]+_\d+\]")


# Verwechselbare Zeichen (Sicherheitspruefung 13.10.2026, gemessen): "[PЕRSON_01]" mit kyrillischem "Е" oder griechischem
# "Ο" galt nicht als Platzhalter - "normalisierte" das Modell ihn in der Antwort zu "[PERSON_01]", setzte die Rueckfuehrung
# einen FREMDEN Wert ein. Die Erkennung woertlicher Platzhalter laeuft deshalb auf einer laengengleichen Fassung, in der
# solche Zeichen (kyrillische/griechische Grossbuchstaben in lateinischer Gestalt, Vollbreite) als ASCII gelten. Nur fuer
# diese Erkennung - der Text selbst bleibt unveraendert.
_CONFUSABLE_TO_ASCII = str.maketrans(
    {
        **{chr(code): chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)},  # Vollbreite ASCII
        **dict(zip("АВЕКМНОРСТУХІЈЅ", "ABEKMHOPCTYXIJS")),  # Kyrillisch
        **dict(zip("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ", "ABEZHIKMNOPTYX")),  # Griechisch
    }
)
# Woertlicher Platzhalter mitten in einer Ziffernfolge ("Tel. 030 [TELEFON_01] 1234567"): kein Detektor erkennt die
# zerteilte Nummer - die angrenzenden Zifferngruppen werden mit maskiert (Rueckfuehrung bleibt exakt).
_DIGITS_BEFORE = re.compile(r"\d[\d/\-]*(?: \d[\d/\-]*)* ?$")
_DIGITS_AFTER = re.compile(r"^ ?[/\-]?\d[\d/\-]*(?: \d[\d/\-]*)*")  # auch "143/815/[...]/08154" (Review)
_DIGIT_WINDOW = 200  # Review: mit 40 Zeichen blieb der Rest einer langen Ziffernfolge stehen
# Lockere Klammerform (Review): auch Zeichen, die keine Uebersetzung in ASCII haben - Kombinationszeichen, mathematische
# Buchstaben, "O" statt Null, kyrillische Kleinbuchstaben, Cherokee, Kapitaelchen ("[𝐏𝐄𝐑𝐒𝐎𝐍_01]", "[PERSON_O1]").
# Eine solche Zeichenfolge kann das Modell zu einem echten Platzhalter "normalisieren"; als Textmarke ist sie geschuetzt.
_PLACEHOLDER_LOOSE = re.compile(r"\[[^\[\]\s]{2,30}_[^\[\]\s]{1,3}\]")


def _literal_placeholder_spans(text: str) -> list[DetectedSpan]:
    skeleton = text.translate(_CONFUSABLE_TO_ASCII)
    if len(skeleton) != len(text):
        skeleton = text
    found: list[tuple[int, int]] = [(m.start(), m.end()) for m in _PLACEHOLDER_LIKE_PATTERN.finditer(skeleton)]
    for match in _PLACEHOLDER_LOOSE.finditer(text):
        if not any(start < match.end() and match.start() < end for start, end in found):
            found.append((match.start(), match.end()))
    for match in _LEFTOVER_BARE.finditer(skeleton):
        if not any(start <= match.start() < end for start, end in found):
            found.append((match.start(), match.end()))
    found.sort()
    spans = []
    for start, end in found:
        line_start = max(text.rfind("\n", 0, start) + 1, start - _DIGIT_WINDOW)
        before = _DIGITS_BEFORE.search(text[line_start:start])
        after = _DIGITS_AFTER.match(text[end : end + _DIGIT_WINDOW].split("\n", 1)[0])
        if before and after:
            start, end = line_start + before.start(), end + after.end()
        if spans and start < spans[-1].end:  # zwei Platzhalter in derselben Ziffernfolge: ein gemeinsamer Treffer
            start, end = min(start, spans[-1].start), max(end, spans[-1].end)
            spans.pop()
        spans.append(DetectedSpan(category=_LITERAL_CATEGORY, start=start, end=end, value=text[start:end]))
    return spans


def _merge_literal_spans(text: str, spans: list[DetectedSpan], literal_spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Woertliche Platzhalter neben den Detektor-Treffern. Ueberlappt ein Treffer einen woertlichen Platzhalter nur
    teilweise ("Person_01" in "[Person_01]"; ein Platzhalter mitten in einer Telefonnummer/IBAN), wird die VEREINIGUNG
    unter der Kategorie des Treffers maskiert (Review: sonst ging der Rest der Nummer im Klartext hinaus). Der Wert ist
    immer der Originalausschnitt, die Rueckfuehrung bleibt exakt. Ein woertlicher Platzhalter ohne Treffer wird selbst
    maskiert. Ergebnis ohne Ueberlappungen, nach Position sortiert."""
    merged: list[DetectedSpan] = []
    # Ein Treffer, der ganz INNERHALB eines woertlichen Platzhalters liegt ("Person_01" in "[Person_01]", Ziffern in
    # "[PERSON_000000001]"), ist kein Wert - der woertliche Platzhalter hat Vorrang (Review: sonst wurde er zu [PERSON_02]).
    spans = [s for s in spans if not any(lit.start <= s.start and s.end <= lit.end for lit in literal_spans)]
    for span in sorted(spans, key=lambda s: s.start):
        start, end = span.start, span.end
        for lit in literal_spans:
            if start < lit.end and lit.start < end:
                start, end = min(start, lit.start), max(end, lit.end)
        if merged and start < merged[-1].end:
            previous = merged.pop()
            start, end, category = min(start, previous.start), max(end, previous.end), previous.category
        else:
            category = span.category
        # Unveraenderter Treffer behaelt seinen Wert: bei verankerten Namensvarianten (P05, detectors._anchor_person_variants)
        # ist das der Kernname ("Brenner" fuer den Ausschnitt "Brenners"), ueber den die Identitaet zugeordnet wird.
        unchanged = (start, end) == (span.start, span.end)
        merged.append(DetectedSpan(category=category, start=start, end=end, value=span.value if unchanged else text[start:end]))
    merged += [lit for lit in literal_spans if not any(s.start <= lit.start and lit.end <= s.end for s in merged)]
    return sorted(merged, key=lambda s: s.start)


# Gueltige erzeugte Platzhalter (Rueckfuehrung): "[PRAEFIX_nn]".
_GENERATED_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z_]*_\d{2,}\]")
# Private-Use-Zeichen als Schutzklammer waehrend der Rueckfuehrung (siehe `Pseudonymizer.reconstruct`).
_GUARD_OPEN, _GUARD_CLOSE = "", ""


_GENITIVE_ENDINGS = ("s", "'s", "’s")


@dataclass
class PseudonymMapping:
    placeholder: str
    category: str
    original_value: str
    # P05 (§32): im Quelltext ersetzte Genitivformen ("Brenners"), die demselben Platzhalter zugeordnet wurden - fuer die
    # Leck-Pruefung der Antwort (vor P05 hatten diese Formen eigene Zuordnungen und wurden so mitgeprueft).
    variants: tuple[str, ...] = ()


#: Rechtsform am Ende eines Firmennamens ("Holzbau Wendeburg GmbH" -> "Holzbau Wendeburg"; Legal-E2E-Benchmark §34)
LEGAL_FORM_END_RE = re.compile(
    r"[\s,]+(?:(?:GmbH|SE|AG)\s*&\s*Co\.?\s*(?:KGaA|KG)|KGaA|gGmbH|GmbH|mbH|OHG|GbR|"
    r"UG(?:\s*\(haftungsbeschr(?:ä|ae)nkt\))?|AG|KG|eG|SE|e\.\s?V\.|PartG(?:\s*mbB)?|Ltd\.?|i\.\s?L\.|"
    r"Aktiengesellschaft)$"
)


def strip_legal_form(name: str) -> str:
    """Name ohne Rechtsform am Ende (unveraendert, wenn keine)."""
    return LEGAL_FORM_END_RE.sub("", name.strip()).strip(" ,")


def _canonicalize_alias(
    category: str, value: str, known_entities: dict[str, list[str]] | None
) -> str:
    """Loest einen erkannten Wert auf sein umfassenderes Alias auf, WENN
    beide Formen in derselben `known_entities`-Kategorie bekannt sind UND
    `value` exakt dem letzten Wort (typischer Nachname) des laengeren
    Kandidaten entspricht - spiegelt bewusst exakt die Nachname-Ableitung
    in `app/ai_providers/local_ai_provider.py::_build_known_entities`
    (`parts[-1]`) wider, damit z. B. "Weber" und "Sabine Weber" innerhalb
    EINES Aufrufs denselben Platzhalter erhalten, statt als zwei
    unabhaengige Entitaeten behandelt zu werden.

    Bewusst NICHT ein generischer Substring-Check (`"weber" in "sabine
    weber"`): das wuerde auch faelschlich unterschiedliche reale Personen
    zusammenfuehren, deren Namen zufaellig als Substring ineinander
    vorkommen (z. B. "Weber" faelschlich in "Weberer"). Die Pruefung auf
    "ist exakt das LETZTE Wort" schliesst das aus, ohne die urspruengliche
    Nachname-Erkennung (Privacy-kritisch, siehe dortiger Fund) einzuschraenken.
    """
    if not known_entities:
        return value
    candidates = known_entities.get(category, [])
    for candidate in candidates:
        if candidate.lower() == value.lower():
            continue
        candidate_parts = candidate.split()
        if len(candidate_parts) >= 2 and candidate_parts[-1].lower() == value.lower():
            return candidate
        # Firmenname ohne Rechtsform (§34): dieselbe Partei, derselbe Platzhalter wie der volle Name - spiegelt
        # `_build_known_entities` (sonst zwei Platzhalter und Ablehnung des Entwurfs mangels voller Abdeckung)
        stripped = strip_legal_form(candidate)
        if stripped != candidate.strip() and stripped.lower() == value.lower():
            return candidate
    return value


class Pseudonymizer:
    def __init__(
        self, *, ner_detector: Callable[[str], list[DetectedSpan]] | None = None
    ) -> None:
        """`ner_detector` ist optional (Default: keine NER, nur Regex +
        known_entities - schnell, für die meisten Tests ausreichend). Der
        produktive Weg (`app/privacy/gateway.py::ClaudePrivacyGateway`)
        setzt hier standardmäßig `presidio_ner.detect_presidio_entities`
        ein (echte deutsche Namens-/Orts-/Organisationserkennung)."""
        self.ner_detector = ner_detector

    def pseudonymize(
        self,
        text: str,
        *,
        known_entities: dict[str, list[str]] | None = None,
        skip_categories: frozenset[str] = frozenset(),
        ner_span_filter: Callable[[str, list[DetectedSpan]], list[DetectedSpan]] | None = None,
    ) -> tuple[str, list[PseudonymMapping]]:
        """Ersetzt alle erkannten PII-Vorkommen durch Platzhalter.

        Derselbe Originalwert erhält innerhalb EINES Aufrufs immer
        denselben Platzhalter (z. B. "Max Mustermann" wird überall zu
        [MANDANT_01], nicht bei jedem Vorkommen neu nummeriert). Dasselbe
        gilt fuer einen blossen Nachnamen desselben bekannten Namens (z. B.
        "Weber" neben "Sabine Weber") - siehe `_canonicalize_alias`
        weiter unten fuer die Begruendung.

        `skip_categories` (optional, ECHTER FUND Owner-Direktive
        "Architektur-Audit Privacy-/Chat-Pipeline", 07.10.): erkannte
        Spans, deren Kategorie in dieser Menge steht, werden VOR der
        Platzhalter-Vergabe verworfen - bleiben also als Klartext stehen,
        bekommen KEIN `PseudonymMapping`. Grund: Presidios generische
        ORGANIZATION-Erkennung pseudonymisiert unterschiedslos auch
        oeffentlich bekannte Organisationen aus allgemeinen Wissensfragen
        ("World Health Organization", "World Trade Organization") - Claude
        bekommt dann nur einen Platzhalter statt des Begriffs und kann die
        Frage nicht mehr sinnvoll beantworten (live reproduziert: Claude
        fragte nach "dem Platzhalter World Trade Organization"). Der
        Aufrufer (app/privacy/gateway.py) uebergibt `skip_categories` NUR,
        wenn vorab bereits feststeht, dass fuer DIESE Anfrage kein
        Akte-/Mandanten-/Dokumentkontext existiert (kein `matter_id`, keine
        `known_entities`, siehe dortige Herleitung) - die eigentliche
        Mandantenschutz-Garantie bleibt strukturell der EXAKTE
        `known_entities`-Abgleich (oben, `_canonicalize_alias`/`detect_all`),
        der von `skip_categories` UNBERUEHRT bleibt (ein in `known_entities`
        bekannter Mandant wird immer erkannt und pseudonymisiert,
        unabhaengig von dieser Option). NIEMALS fuer Kategorie "person"
        verwenden (echte Namen muessen immer streng geprueft bleiben) -
        das erzwingt ausschliesslich der Aufrufer, diese Methode selbst
        prueft das nicht gesondert, da sie bewusst eine generische,
        kategorie-agnostische Mechanik bleibt.

        ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
        Pipeline", 07.10., per Live-QA real reproduziert): `skip_categories`
        wird an `detect_all` durchgereicht (NICHT mehr erst nachtraeglich
        auf dessen Ergebnis angewendet) - siehe dortigen Docstring fuer die
        volle Begruendung (ein nachtraeglicher Filter nach bereits
        erfolgter Ueberlappungs-Aufloesung konnte einen laengeren,
        uebersprungenen "organisation"-Treffer einen KUERZEREN, NICHT zu
        uebersprungenden Treffer einer anderen Kategorie an derselben
        Textstelle verdraengen lassen - nach der Filterung blieb dann
        GAR KEIN Treffer mehr fuer diese Stelle uebrig, real reproduziert
        an "Deutschland" innerhalb von "Bundeskanzler (Deutschland)")."""
        literal_spans = _literal_placeholder_spans(text)
        spans = detect_all(
            text,
            known_entities,
            ner_detector=self.ner_detector,
            skip_categories=skip_categories,
            ner_span_filter=ner_span_filter,
        )
        spans = _merge_literal_spans(text, spans, literal_spans)

        value_to_placeholder: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}
        mappings: list[PseudonymMapping] = []

        # Automatische Nachname-Kanonisierung fuer per NER erkannte Personen (nicht nur fuer
        # `known_entities`): ECHTER FUND (Real-E2E 08.10., Fall A): "Tobias Brandt" im
        # Adressfeld und "Brandt" in der Anrede bekamen zwei Platzhalter, Claude meldete
        # eine erfundene Unstimmigkeit. Ein Einzelwort-Treffer wird NUR dann auf den Vollnamen
        # abgebildet, wenn er das letzte Wort GENAU EINES Vollnamens im selben Text ist
        # (bei "Hans Mueller" und "Anna Mueller" bleibt "Mueller" eigenstaendig).
        # §52: Anrede/Titel/Rolle vor dem Namen gehoeren nicht zur Identitaet - "Dr. Hans-Peter Müller" und "Hans-Peter
        # Müller" (manuell markiert bzw. NER) sind EINE Person und EIN Vollname (sonst war "Herr Müller" mehrdeutig und
        # erhielt einen eigenen Platzhalter). Nur der Schluessel, Erkennung und ersetzter Bereich bleiben unveraendert.
        def _identity(span: DetectedSpan, value: str) -> str:
            return strip_name_prefix(value) if span.category == "person" else value

        surname_to_full: dict[str, set[str]] = {}
        for found in spans:
            name = _identity(found, found.value)
            if found.category == "person" and len(name.split()) >= 2:
                surname_to_full.setdefault(name.split()[-1].lower(), set()).add(found.value)

        def _auto_alias(span: DetectedSpan) -> str:
            words = _identity(span, span.value).split()
            if span.category != "person" or not words:
                return span.value
            if len(words) > 1 and not words[0][0].islower():
                return span.value  # mehrteilig nur als Kurzform mit Namenszusatz ("von Berg"), §52
            candidates = {
                _identity(span, full).lower(): full
                for full in surname_to_full.get(words[-1].lower(), set())
                if len(_identity(span, full).split()) > len(words)
                and [w.lower() for w in _identity(span, full).split()[-len(words):]] == [w.lower() for w in words]
            }
            return next(iter(candidates.values())) if len(candidates) == 1 else span.value

        def _key_and_canonical(span: DetectedSpan) -> tuple[tuple[str, str], str]:
            if span.category == _LITERAL_CATEGORY:
                # woertlicher Text exakt (Review: "[Person_01]" und "[PERSON_01]" wurden sonst zusammengelegt)
                return (span.category, span.value), span.value
            canonical = _canonicalize_alias(span.category, span.value, known_entities)
            if canonical == span.value:
                canonical = _auto_alias(span)
            return (span.category, _identity(span, canonical).lower()), canonical

        for span in spans:
            key, canonical = _key_and_canonical(span)
            if key not in value_to_placeholder:
                counters[span.category] = counters.get(span.category, 0) + 1
                prefix = _PLACEHOLDER_PREFIX_BY_CATEGORY.get(
                    span.category, span.category.upper()
                )
                placeholder = f"[{prefix}_{counters[span.category]:02d}]"
                value_to_placeholder[key] = placeholder
                # `original_value` nutzt bewusst den KANONISCHEN (laengeren/
                # vollstaendigeren) Wert der Aliasgruppe, nicht zwingend
                # `span.value` - ECHTER FUND (20.09.): ohne diese
                # Kanonisierung erhielten "Weber" und "Sabine Weber"
                # (beide aus `_build_known_entities`s Nachname-Ergaenzung,
                # 14.09.-Fix) ZWEI verschiedene Platzhalter fuer dieselbe
                # reale Person - eine korrekte, sichere KI-Antwort, die nur
                # EINE der beiden Formen woertlich verwendete, wurde dadurch
                # faelschlich als "Platzhalter fehlt im Text (Inkonsistenz)"
                # blockiert (real reproduziert: echter Schriftsatz-Entwurf
                # fuer eine Mandantin "Sabine Weber", deren Dokument sie als
                # "Frau Weber" anspricht, wurde vollstaendig verworfen,
                # obwohl die Antwort inhaltlich korrekt und sicher war).
                mappings.append(
                    PseudonymMapping(
                        placeholder=placeholder,
                        category=span.category,
                        original_value=canonical,
                    )
                )

        # P05 (§32): ein verankerter Genitivtreffer ("Brenners", Wert "Brenner") behaelt die Endung hinter dem Platzhalter
        # ("[MANDANT_01]s") - die Rueckfuehrung ergibt "Karl Brenners", die Cloud-KI sieht den Genitiv. Die Form wird als
        # Variante der Zuordnung festgehalten (Leck-Pruefung der Antwort).
        def _genitive_suffix(span: DetectedSpan) -> str:
            surface = text[span.start : span.end]
            if surface != span.value and surface.startswith(span.value) and surface[len(span.value) :] in _GENITIVE_ENDINGS:
                return surface[len(span.value) :]
            return ""

        variants: dict[str, set[str]] = {}
        for span in spans:
            if _genitive_suffix(span):
                key, _ = _key_and_canonical(span)
                variants.setdefault(value_to_placeholder[key], set()).add(text[span.start : span.end])
        mappings = [
            PseudonymMapping(m.placeholder, m.category, m.original_value, tuple(sorted(variants[m.placeholder])))
            if m.placeholder in variants else m
            for m in mappings
        ]

        # Von hinten nach vorne ersetzen, damit sich Indizes vorheriger
        # (frueherer) Treffer durch die Ersetzung nicht verschieben.
        result = text
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            key, _ = _key_and_canonical(span)
            placeholder = value_to_placeholder[key]
            result = result[: span.start] + placeholder + _genitive_suffix(span) + result[span.end :]

        return result, mappings

    def reconstruct(
        self, text: str, mappings: list[PseudonymMapping], *, neutralize_leftovers: bool = False
    ) -> str:
        """Ersetzt Platzhalter wieder durch die Originalwerte - rein
        lokal, nachdem eine (pseudonymisierte) Antwort zurückgekommen ist.

        Platzhalter-Integritaet (Folgezyklus 13.10.2026): EIN Durchgang statt nacheinander ausgefuehrter Ersetzungen -
        ein Originalwert, der selbst platzhalteraehnlich ist ("[PERSON_01]" als woertlicher Text, siehe
        `_literal_placeholder_spans`), wird nie ein zweites Mal ersetzt. Fehlerhafte Rueckfuehrungsdaten werden nicht
        geraten: ein Platzhalter mit widerspruechlichen Werten, ungueltiger Form oder leerem Wert wird NICHT
        zurueckgefuehrt (bleibt Rest; mit `neutralize_leftovers` als "[Angabe einsetzen]"), statt womoeglich einen
        fremden Wert einzusetzen. `neutralize_leftovers` neutralisiert nur Reste aus dem Antworttext, nie einen
        zurueckgefuehrten Originalwert."""
        values: dict[str, str] = {}
        conflicting: set[str] = set()
        for mapping in mappings:
            placeholder = mapping.placeholder
            if not _GENERATED_PLACEHOLDER.fullmatch(placeholder) or not mapping.original_value:
                conflicting.add(placeholder)
            elif values.get(placeholder, mapping.original_value) != mapping.original_value:
                conflicting.add(placeholder)
            else:
                values[placeholder] = mapping.original_value
        for placeholder in conflicting:
            values.pop(placeholder, None)
        text = text.replace(_GUARD_OPEN, "").replace(_GUARD_CLOSE, "")
        ordered = sorted(values, key=len, reverse=True)
        index = {placeholder: str(number) for number, placeholder in enumerate(ordered)}
        if ordered:
            pattern = re.compile("|".join(re.escape(placeholder) for placeholder in ordered))
            text = pattern.sub(lambda m: f"{_GUARD_OPEN}{index[m.group()]}{_GUARD_CLOSE}", text)
        if neutralize_leftovers:
            text = neutralize_leftover_placeholder_tokens(text)
        originals = {number: values[placeholder] for placeholder, number in index.items()}
        return re.sub(_GUARD_OPEN + r"(\d+)" + _GUARD_CLOSE, lambda m: originals[m.group(1)], text)
