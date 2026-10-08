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

from app.privacy.detectors import DetectedSpan, detect_all

_PLACEHOLDER_PREFIX_BY_CATEGORY = {
    "mandant": "MANDANT",
    "gegner": "GEGNER",
    "anwalt": "ANWALT",
    "gericht": "GERICHT",
    "aktenzeichen": "AKTENZEICHEN",
    "adresse": "ADRESSE",
    "datum": "DATUM",
    "betrag": "BETRAG",
    "vertrag": "VERTRAG",
    "email": "EMAIL",
    "telefon": "TELEFON",
    "iban": "IBAN",
    "steuer_id": "STEUER_ID",
    "kundennummer": "KUNDENNUMMER",
    "rechnungsnummer": "RECHNUNGSNUMMER",
    "bic": "BIC",
    # Presidio-NER-Kategorien (app/privacy/presidio_ner.py) - rollenneutral,
    # da Presidio keine Mandant/Gegner/Anwalt/Gericht-Rolle kennen kann.
    "person": "PERSON",
    "ort": "ORT",
    "organisation": "ORGANISATION",
}


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


@dataclass
class PseudonymMapping:
    placeholder: str
    category: str
    original_value: str


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
        spans = detect_all(
            text,
            known_entities,
            ner_detector=self.ner_detector,
            skip_categories=skip_categories,
            ner_span_filter=ner_span_filter,
        )

        value_to_placeholder: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}
        mappings: list[PseudonymMapping] = []

        # Automatische Nachname-Kanonisierung fuer per NER erkannte Personen (nicht nur fuer
        # `known_entities`): ECHTER FUND (Real-E2E 08.10., Fall A): "Tobias Brandt" im
        # Adressfeld und "Brandt" in der Anrede bekamen zwei Platzhalter, Claude meldete
        # eine erfundene Unstimmigkeit. Ein Einzelwort-Treffer wird NUR dann auf den Vollnamen
        # abgebildet, wenn er das letzte Wort GENAU EINES Vollnamens im selben Text ist
        # (bei "Hans Mueller" und "Anna Mueller" bleibt "Mueller" eigenstaendig).
        surname_to_full: dict[str, set[str]] = {}
        for found in spans:
            if found.category == "person" and len(found.value.split()) >= 2:
                surname_to_full.setdefault(found.value.split()[-1].lower(), set()).add(found.value)

        def _auto_alias(span: DetectedSpan) -> str:
            if span.category != "person" or len(span.value.split()) != 1:
                return span.value
            candidates = {full.lower(): full for full in surname_to_full.get(span.value.lower(), set())}
            return next(iter(candidates.values())) if len(candidates) == 1 else span.value

        def _key_and_canonical(span: DetectedSpan) -> tuple[tuple[str, str], str]:
            canonical = _canonicalize_alias(span.category, span.value, known_entities)
            if canonical == span.value:
                canonical = _auto_alias(span)
            return (span.category, canonical.lower()), canonical

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

        # Von hinten nach vorne ersetzen, damit sich Indizes vorheriger
        # (frueherer) Treffer durch die Ersetzung nicht verschieben.
        result = text
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            key, _ = _key_and_canonical(span)
            placeholder = value_to_placeholder[key]
            result = result[: span.start] + placeholder + result[span.end :]

        return result, mappings

    def reconstruct(self, text: str, mappings: list[PseudonymMapping]) -> str:
        """Ersetzt Platzhalter wieder durch die Originalwerte - rein
        lokal, nachdem eine (pseudonymisierte) Antwort zurückgekommen ist."""
        result = text
        for mapping in mappings:
            result = result.replace(mapping.placeholder, mapping.original_value)
        return result
