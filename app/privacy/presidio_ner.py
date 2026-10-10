"""Presidio-gestützte Namens-/Orts-/Organisationserkennung (NER).

Ergänzt - ERSETZT NICHT - die Regex-Detektoren in `detectors.py`. Die
Regex-Muster dort decken strukturierte Formate zuverlässig ab (E-Mail, IBAN,
Telefon, Datum, Beträge, Aktenzeichen ...); was Regex strukturell nicht
leisten kann, ist echte Named-Entity-Recognition für Personennamen, Orte und
Organisationen in Fließtext. Genau diese Lücke schließt dieses Modul über
Microsoft Presidio (`presidio-analyzer`) mit dem deutschen spaCy-Modell
`de_core_news_lg`.

WICHTIGE ABGRENZUNG: Dieses Modul führt selbst KEINE Ersetzung/Anonymisierung
durch (kein `presidio_anonymizer.AnonymizerEngine`-Einsatz hier). Grund: Die
Architektur verlangt, dass derselbe Wert überall in einer zusammengeführten
Mehrfeld-Anfrage denselben Platzhalter bekommt (siehe Moduldocstring in
gateway.py) - das kann nur EIN gemeinsamer Pseudonymisierungslauf über den
kombinierten Text leisten (`app/privacy/pseudonymizer.py::Pseudonymizer`).
Dieses Modul liefert deshalb nur `DetectedSpan`-Objekte (dieselbe Datenklasse
wie die Regex-Detektoren), die der bestehende `Pseudonymizer` als zusätzliche
Erkennungsquelle verwendet - identisches Downstream-Verhalten (Platzhalter-
Vergabe, Rekonstruktion) wie bei jedem anderen Detektor auch.

`PERSON`/`LOCATION`/`ORGANIZATION` sind bewusst die einzigen angefragten
Presidio-Entitätstypen: E-Mail/Telefon/IBAN/Datum werden bereits von den
Regex-Detektoren abgedeckt (dortige Muster sind bereits getestet und auf den
deutschen Kanzleikontext zugeschnitten) - eine doppelte Erkennung derselben
Werte durch Presidios generische Recognizer würde nur Overlap-Auflösung ohne
zusätzlichen Nutzen erzeugen.

Kategorie-Zuordnung: Presidio kennt keine Mandanten-/Gegner-/Anwalt-/
Gerichts-ROLLEN - diese kommen ausschließlich aus `known_entities`
(strukturierte Aktenbeteiligte, siehe app/ai_providers/local_ai_provider.py).
Ein von Presidio erkannter Personenname, der keiner bekannten Rolle
entspricht (z. B. ein im Fließtext erwähnter Dritter/Zeuge), bekommt daher
die rollenneutrale Kategorie "person", nicht "mandant"/"gegner"/etc.
"""

from __future__ import annotations

import itertools
import re
from functools import lru_cache

from app.privacy.detectors import DetectedSpan

_SPACY_MODEL_NAME = "de_core_news_lg"

_ENTITY_TO_CATEGORY = {
    "PERSON": "person",
    "LOCATION": "ort",
    "ORGANIZATION": "organisation",
}

_REQUESTED_ENTITIES = tuple(_ENTITY_TO_CATEGORY.keys())

# Presidios eigener Default-Score liegt niedrig genug, um auf kurzen,
# fragmentarischen Kanzlei-Textbausteinen (Aktenzeichen-Kuerzel,
# Paragraphen-Abkuerzungen wie "AO", Stichpunkte) spuerbar falsch positiv
# zu reagieren. 0.5 ist bewusst konservativ (lieber ein knapp verpasster
# Name, der dann von der Regel-Heuristik in security_check.py als
# Sicherheitsnetz aufgefangen wird, als eine Kanzlei-Standardformulierung,
# die faelschlich einen Block ausloest).
_MIN_SCORE = 0.5

# Schutz gegen ein Out-of-Distribution-Problem des NER-Modells: dieser
# Detector laeuft (ueber Pseudonymizer/SecurityCheckService) sowohl auf dem
# noch mit internen Gateway-Trennmarkierungen versehenen Rohtext
# (app/privacy/gateway.py: "@@GATEWAY_SACHVERHALT@@" etc.) als auch auf dem
# bereits pseudonymisierten Text (Platzhalter wie "[MANDANT_01]"). Ein auf
# natuerlicher Sprache trainiertes Modell erkennt solche kuenstlichen Tokens
# nicht nur selbst gelegentlich faelschlich als PERSON/LOCATION, sondern
# "verschmilzt" sie teils sogar mit direkt benachbartem echten Text zu EINEM
# fehlklassifizierten Treffer (beobachtet: "@@GATEWAY_SACHVERHALT@@\nAkte:
# Akte A" wurde als ein einziges LOCATION erkannt) - ein reiner Nachfilter
# auf den erkannten Wert allein wuerde diesen Fall nicht zuverlaessig
# abdecken. Deshalb zwei Verteidigungslinien:
# 1. Marker/Platzhalter werden VOR der Analyse durch gleich lange
#    Leerzeichen neutralisiert (`_neutralize_internal_tokens`) - das Modell
#    bekommt sie gar nicht erst zu Gesicht, Zeichenpositionen im Rest des
#    Texts bleiben dabei unveraendert (wichtig fuer korrekte Offsets).
# 2. Zusaetzlich ein Formatfilter auf den erkannten Wert selbst (echte
#    deutsche Personen-/Orts-/Organisationsnamen sind nie rein
#    grossgeschrieben+Ziffern/Unterstrich/@/Leerraum) als zweites
#    Sicherheitsnetz fuer Faelle, die (1) nicht erfasst.
#
# Bewusst als generisches "@@...@@"-/"[...]"-Muster gehalten, nicht als
# Import der konkreten Marker-/Platzhalter-Konstanten aus gateway.py/
# pseudonymizer.py - dieses Modul soll deren interne Formate nicht kennen
# muessen (siehe Modul-Docstring, "ergaenzt, ersetzt nicht").
_INTERNAL_MARKER_PATTERN = re.compile(r"@@[A-Z_]+@@")
_INTERNAL_PLACEHOLDER_PATTERN = re.compile(r"\[[A-Z][A-Z_]*_\d{2}\]")
_LOOKS_LIKE_INTERNAL_TOKEN_PATTERN = re.compile(r"^[A-Z0-9_@\s]+$")

# Deterministische Ausnahmeliste fuer Standard-Kanzleibrief-Textbausteine, die
# vom NER-Modell gelegentlich als PERSON/LOCATION/ORGANIZATION fehlklassifiziert
# werden - widerspricht sonst der oben dokumentierten Begruendung fuer
# _MIN_SCORE ("lieber ein knapp verpasster Name ... als eine Kanzlei-
# Standardformulierung, die faelschlich einen Block ausloest"). Real
# beobachtet (Prompt-28-Testfall mit OCR-Verstuemmelung "Mlt" statt "Mit"):
# das isolierte Wort "Gruessen" aus der praktisch in jedem deutschen
# Geschaeftsbrief vorkommenden Grussformel "Mit freundlichen Gruessen/Grüßen"
# wurde als LOCATION erkannt, sobald der Kontext (z. B. durch Platzhalter-
# Neutralisierung direkt davor) etwas ungewoehnlich war - fuehrte zu einem
# unnoetigen Block eines vollkommen unauffaelligen Standardschreibens.
# Bewusst NUR feste, in der Grussformel vorkommende Einzelwoerter (keine
# Namens-/Adressbestandteile) - kein allgemeiner Blocklist-Mechanismus, der
# als Umgehungsweg fuer echte PII missbraucht werden koennte (ein echter
# Personen-/Orts-/Firmenname lautet nie woertlich "Gruessen" oder
# "Hochachtungsvoll").
#
# "erbschaftsteuerbescheid" (24.09., ECHTER FUND beim Live-E2E-Test des
# neuen Erbschaftsteuer-Komplexfalls "ROADMAP-ALIGNED PRODUCT COMPLETION"):
# das isolierte, grossgeschriebene Wort "Erbschaftsteuerbescheid" (Ueber-
# schrift-Zeile des Dokuments, siehe app/synthetic_data/generator.py::
# generate_complex_case_erbschaftsteuer) wird vom Modell zuverlaessig
# (Score 0.85) als PERSON erkannt, obwohl es sich um den Dokumenttyp-Namen
# handelt, nie um einen Namen. Da nur DIESES eine Vorkommen (die Ueber-
# schrift) einen Platzhalter bekam, das Wort aber an anderer Stelle
# desselben zusammengefuehrten Aktenkontexts erneut woertlich auftaucht
# (z. B. "im Erbschaftsteuerbescheid angesetzte Grundbesitzwert" - dort
# NICHT als PERSON erkannt, da nicht isoliert grossgeschrieben), loeste das
# zuverlaessig das ausgehende Final Payload Gate aus
# (`original_value_leaked`) und blockierte JEDE KI-Aktion auf einem
# Erbschaftsteuer-Dokument, bevor ueberhaupt ein Claude-Aufruf erfolgte -
# live reproduziert (2/2), root-caused per direktem Presidio-Analyzer-Aufruf
# (siehe DECISIONS.md fuer die volle Herleitung). Verwandte, bereits laenger
# bestehende Dokumenttyp-Woerter ("Steuerbescheid", "Pruefungsanordnung",
# "Handelsregisterauszug", "Gesellschaftsvertrag", "Nachlassverzeichnis")
# wurden GEGENGEPRUEFT und zeigen dieses Verhalten NICHT - bewusst nur
# dieses eine, konkret belegte Wort ergaenzt, keine vorsorgliche Liste ohne
# Beleg.
#: "offener" (05.10., Owner-Direktive "Abschließende Live-Verifikation
#: nach Aufladung des Anthropic-Guthabens", live mit echtem Claude-Aufruf
#: reproduziert): das haeufige deutsche Adjektiv "offen"/"offener" (z. B.
#: in "Offener Prüfpunkt:" - einer in Anwaltsschreiben alltaeglichen
#: Formulierung, hier Teil der Standard-Textbausteine dieses Projekts
#: selbst, siehe draft_detail.html/response_validation.py) wird vom
#: Modell zuverlaessig als PERSON erkannt, sobald es isoliert
#: grossgeschrieben am Zeilen-/Satzanfang steht. Der dadurch entstehende
#: Platzhalter ("[PERSON_XX] Prüfpunkt:*") loeste beim nachgelagerten
#: Restrisiko-Scan (Punkt 2-4, security_check.py - laeuft auf dem
#: bereits pseudonymisierten, an dieser Stelle kuenstlich luecken-
#: haften Text) einen KASKADIERENDEN Fehlalarm aus: das direkt
#: benachbarte, voellig unverdaechtige Wort "Prüfpunkt:*" wurde SEINERSEITS
#: faelschlich als neue ORGANIZATION erkannt (dieselbe Out-of-
#: Distribution-Schwaeche bei neutralisierten Platzhaltern, die oben
#: bereits fuer "Herr [PERSON_06]" dokumentiert ist) - blockierte dadurch
#: eine voellig unauffaellige, aus einem echten Claude-Aufruf stammende
#: Folgefrage ("Bitte vervollstaendigen") vollstaendig. Ein echter
#: Personen-Vorname lautet nie woertlich "offener" (es ist eine
#: Adjektivform, kein Name) - identisches Ausschlussprinzip wie bei
#: "gruessen"/"hochachtungsvoll" oben.
#
#: "attika", "verblechung(en)", "bitumenbahn", "sicherheitseinbehalt", "prozessvollmacht" (10.10.2026,
#: Qualitaetslauf "falsche Pseudonymisierungstreffer", reproduzierbar mit scripts/diagnose_ner_false_positives.py):
#: seltene Fachsubstantive aus Bau-/Vertragstexten werden vom spaCy-NER (`de_core_news_lg`, Presidio-
#: `SpacyRecognizer`, konstanter Score 0.85 - die Treffer sind keine Konfidenzschaetzung) in korrekt
#: geschriebenem Text als LOCATION erkannt (Wortart NOUN, ohne Wortvektor/OOV; "Attika" PROPN). Gemessen an
#: 100 Fachbegriffen mit je drei Beispielsaetzen: 5 Treffer, genau diese. Folgen: Over-Pseudonymisierung
#: ("[ORT_04] der Dachflaeche" statt "Attika" - Claude kann den Sachverhalt schlechter verwerten) und, wenn die
#: Erkennung zwischen Erst- und Restrisiko-Scan unterschiedlich ausfaellt, ein Fail-Closed-Block. Reine
#: Ganzwort-Ausnahme fuer genau diese Woerter (Gleichheit des gesamten Treffers, nicht Teilstring): ein
#: Firmen-/Personenname, der ein solches Wort ENTHAELT ("Bitumenbahn GmbH"), bleibt geschuetzt. Eine
#: strukturelle Regel (z. B. "NOUN und OOV") wurde bewusst NICHT eingefuehrt: sie haette an 50 seltenen
#: echten Namen/Orten zwar 0 Treffer gehabt, wuerde aber die Erkennung grundsaetzlich abschwaechen.
_NEVER_ENTITY_WORDS = frozenset(
    {
        "gruessen", "grüßen", "grussen",
        "hochachtungsvoll",
        "erbschaftsteuerbescheid",
        "offener",
        "attika", "verblechung", "verblechungen", "bitumenbahn", "sicherheitseinbehalt", "prozessvollmacht",
        # Allgemeine Rollen-/Institutionsbegriffe (11.10.2026, scripts/diagnose_common_noun_entities.py: 10 von 127
        # gewoehnlichen Rechtsbegriffen). Ende-zu-Ende-Beleg: "Partei" wurde als ORGANISATION pseudonymisiert; sobald
        # Claude das Wort selbst schrieb ("die andere Partei"), blockierte der Leak-Check - 4 von 10 Schriftsaetzen
        # eines synthetischen Vertragsfalls waren allein dadurch blockiert. Gleichheit des GANZEN Treffers: ein
        # konkreter Name ("Landgericht Hamburg", "Stadt Beispielstadt", "Verbraucherzentrale Hamburg") ist ein
        # laengerer Treffer und bleibt geschuetzt; das nackte Gattungswort identifiziert niemanden.
        "partei", "amt", "beklagte", "bund", "gemeinde", "kommune", "kreis", "landgericht", "stadt", "verbraucherzentrale",
        # Transliterierte Schreibweise eines der haeufigsten Rechtswoerter (11.10.2026, scripts/diagnose_transliteration.py:
        # 3 zusaetzliche Entitaeten in 16 Saetzen nur bei ae/oe/ue-Schreibweise: "Maengel" als PERSON, "Flachdach-
        # flaeche" als ORT); mit Umlauten wird "Mängel" nie erkannt. Ganzwort-Gleichheit, siehe oben.
        "maengel", "maengeln",
        # Fall L (4 Dokumente, transliteriert): "Lichtkuppel" wurde erst im Restrisiko-Scan (anderer Kontext durch die
        # Platzhalter) als ORT erkannt -> Gateway-Block "weiterhin erkennbare Muster: ort", 4 von 4 Laeufen. Mit dieser
        # Ausnahme passiert dasselbe Dokument das Gateway. "Geschaeftsfuehrerin": PERSON in transliterierter Fassung.
        "lichtkuppel", "lichtkuppeln", "geschaeftsfuehrerin",
    }
)


def _neutralize_internal_tokens(text: str) -> str:
    """Ersetzt gateway-/pseudonymizer-interne Marker/Platzhalter durch
    gleich lange Leerzeichenfolgen - Laenge (und damit jede Zeichenposition
    ausserhalb der Marker) bleibt exakt erhalten."""
    text = _INTERNAL_MARKER_PATTERN.sub(lambda m: " " * len(m.group()), text)
    return _INTERNAL_PLACEHOLDER_PATTERN.sub(lambda m: " " * len(m.group()), text)


@lru_cache(maxsize=1)
def _get_analyzer_engine():
    """Baut die Presidio-`AnalyzerEngine` genau einmal pro Prozess - das
    Laden des spaCy-Modells ist der mit Abstand teuerste Teil (mehrere
    Sekunden), ein wiederholter Aufbau pro Anfrage wäre nicht praktikabel."""
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "de", "model_name": _SPACY_MODEL_NAME}],
    }
    nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
    return AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["de"])


_LEGAL_FORM = r"(?:GmbH\s*&\s*Co\.\s*KG|KGaA|GmbH|mbH|OHG|GbR|UG|AG|KG|eG|SE|e\.\s?V\.)"
_NO_LETTER_AFTER = r"(?![A-Za-zÄÖÜäöüß])"
_STARTS_WITH_LEGAL_FORM = re.compile(r"^" + _LEGAL_FORM + _NO_LETTER_AFTER)
_ENDS_WITH_LEGAL_FORM = re.compile(r"(?<![A-Za-zÄÖÜäöüß])" + _LEGAL_FORM + r"$")
_FOLLOWED_BY_LEGAL_FORM = re.compile(r"[ \t]{1,2}" + _LEGAL_FORM + _NO_LETTER_AFTER)
_SINGLE_INITIAL = re.compile(r"[A-ZÄÖÜ]\.")
# Ortsname gefolgt von (max. 3) grossgeschriebenen Woertern und einer Rechtsform:
# "Ostsee Anlagenbau KG" - die NER erkennt dort oft nur "Ostsee" als Ort.
_ORG_AFTER_PLACE = re.compile(
    r"(?:[ \t]+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß&\-]*){0,3}?[ \t]+" + _LEGAL_FORM + _NO_LETTER_AFTER
)


_LINE_BREAK_IN_SPAN = re.compile(r"\s{2,}|\n")
_HAS_LETTER = re.compile(r"[A-Za-zÄÖÜäöüß]")


def normalize_ner_span_boundaries(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Begrenzt NER-Treffer auf eine Zeile und verwirft Treffer ohne Buchstaben.

    ECHTER FUND (Real-E2E 08.10., Fall A): in der Pipeline werden Zeilenumbrueche zu
    Doppelleerzeichen. Folgen im echten Payload:
    - "Tobias Brandt  Musterweg 12" wurde EINE Person ([PERSON_01]), waehrend die Anrede
      "Brandt" eine zweite bekam - Claude meldete eine erfundene Unstimmigkeit
      ("Tobias Brandt Musterweg 12 vs. Brandt").
    - "30.11.2026.  " wurde als ORT gewertet ([ORT_02]); der laengere NER-Treffer verdraengte
      den Datums-Detektor, die Frist erschien als "Ortsplatzhalter".
    Regeln: (1) ein Treffer endet an der ersten Zeilengrenze (Zeilenumbruch oder zwei und
    mehr Leerzeichen) - der Kopf bleibt geschuetzt, der Rest wird von den uebrigen Detektoren
    (Strasse, Datum ...) erfasst; (2) ein Treffer ohne einen einzigen Buchstaben (Ziffern/
    Satzzeichen) ist kein Name, Ort oder keine Organisation und wird verworfen."""
    result: list[DetectedSpan] = []
    for span in spans:
        value = span.value
        match = _LINE_BREAK_IN_SPAN.search(value)
        if match:
            value = value[: match.start()]
        if not value.strip() or not _HAS_LETTER.search(value):
            continue
        value = value.rstrip()
        if value == span.value:
            result.append(span)
        else:
            result.append(
                DetectedSpan(
                    category=span.category,
                    start=span.start,
                    end=span.start + len(value),
                    value=value,
                )
            )
    return result


def normalize_organisation_spans(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Vereinheitlicht die Grenzen erkannter Organisationsnamen.

    ECHTER FUND (Real-E2E 08.10., Drafting "Zahlungsaufforderung der Nordwind
    ... GmbH an die Elektro Lindqvist KG"): die NER ist bei Organisationen
    kontextabhaengig uneinheitlich - dieselbe Firma wurde mal MIT, mal OHNE
    Rechtsform erkannt, und in der Zeile "An Elektro Lindqvist KG  z. H. der
    Geschaeftsfuehrung" markierte sie nur das Bruchstueck "KG  z. H. der
    Geschaeftsfuehrung" als Organisation, NICHT den Firmennamen davor. Folgen:
    der Firmenname blieb im Klartext in der Anfrage an Claude stehen, und die
    Anweisung bekam fuer dieselbe Partei einen anderen Platzhalter - Claude
    meldete eine "nicht vorkommende" Partei.

    Deterministische Normalisierungen (nur Kategorien "organisation"/"ort"):
    - Ein Treffer, der MIT einer Rechtsform beginnt ("KG ...", "GmbH ..."), ist
      kein Firmenname, sondern ein Bruchstueck des vorherigen Namens und wird
      verworfen (er enthaelt selbst keine Personendaten).
    - Eine einzelne Initiale mit Punkt ("H." aus "z. H.") ist nie ein Ort oder
      eine Organisation: ohne diese Regel meldete die Zweiterkennung im
      Restrisiko-Scan "ort 'H.'" und blockierte den Auftrag, sobald das
      Bruchstueck davor nicht mehr als Organisation ersetzt wurde.
    - Folgt direkt auf einen Organisationstreffer eine Rechtsform, wird der
      Treffer darum erweitert, damit alle Vorkommen dieselbe Form (und damit
      denselben Platzhalter) haben.
    Andere Kategorien bleiben unveraendert; es werden nie Treffer entfernt,
    die selbst mehr als ein Rechtsform-Bruchstueck waeren."""
    result: list[DetectedSpan] = []
    for span in spans:
        if span.category not in ("organisation", "ort"):
            result.append(span)
            continue
        stripped = span.value.strip()
        if _STARTS_WITH_LEGAL_FORM.match(stripped) or _SINGLE_INITIAL.fullmatch(stripped):
            continue
        if span.category == "ort":
            # ECHTER FUND (Real-E2E 08.10., Request-Capture): "Ostsee Anlagenbau KG"
            # erschien als "[ORT_01] Anlagenbau KG" - nur der Ortsteil wurde
            # ersetzt, der Rest der Firma blieb lesbar. Folgen direkt
            # Grossgeschriebenes und eine Rechtsform, ist es ein Firmenname.
            company = _ORG_AFTER_PLACE.match(text, span.end)
            if company:
                result.append(
                    DetectedSpan(
                        category="organisation",
                        start=span.start,
                        end=company.end(),
                        value=text[span.start : company.end()],
                    )
                )
            else:
                result.append(span)
            continue
        match = _FOLLOWED_BY_LEGAL_FORM.match(text, span.end)
        if match and not _ENDS_WITH_LEGAL_FORM.search(span.value):
            result.append(
                DetectedSpan(
                    category=span.category,
                    start=span.start,
                    end=match.end(),
                    value=text[span.start : match.end()],
                )
            )
        else:
            result.append(span)
    return result


# --- Zusaetzliche Erkennungsfassung mit wiederhergestellten Umlauten (11.10.2026) --------------------------------
# Transliterierte Texte (ae/oe/ue/ss statt ä/ö/ü/ß) kennt das Modell schlechter. Zusaetzlich zur UNVERAENDERTEN Analyse
# laeuft eine zweite auf einer Fassung, in der unbekannte Woerter mit ae/oe/ue/ss durch ihre Umlautform ersetzt sind,
# WENN diese dem Modell bekannt ist ("Mueller" bleibt, "Kuendigung" -> "Kündigung"). Die Treffer werden positionsgenau
# auf den ORIGINALTEXT zurueckgebildet (Wert = Originalausschnitt); der Originaltext wird nie veraendert.
# WICHTIG (gemessen, scripts/diagnose_normalized_detection.py): die zweite Fassung wird nur VEREINIGT, nie
# stattdessen verwendet - allein hatte sie bei 285 Namenssaetzen 14 Treffer WENIGER als die Originalanalyse
# (z. B. "Herr Fuerst" -> "Herr Fürst" nicht mehr als Person erkannt); vereinigt: 2 Treffer MEHR, kein Verlust.
# Es werden daher auch keine Treffer der Originalanalyse verworfen.
_RESTORE_DIGRAPH_RE = re.compile(r"ae|oe|ue|Ae|Oe|Ue|AE|OE|UE|ss")
_RESTORE_MAP = {"ae": "ä", "oe": "ö", "ue": "ü", "Ae": "Ä", "Oe": "Ö", "Ue": "Ü", "AE": "Ä", "OE": "Ö", "UE": "Ü", "ss": "ß"}
_WORD_PATTERN = re.compile(r"[A-Za-zÄÖÜäöüß]{4,}")


def _model_knows(word: str) -> bool:
    vocab = _get_analyzer_engine().nlp_engine.nlp["de"].vocab
    return bool(vocab.has_vector(word) or vocab.has_vector(word.lower()) or vocab.has_vector(word.capitalize()))


@lru_cache(maxsize=50000)
def _restore_word(word: str) -> tuple[tuple[str, int, int], ...] | None:
    """Umlautform eines unbekannten Wortes als Folge (Zeichen, Start, Ende) im ORIGINALwort, oder None.

    Nur wenn das Originalwort dem Modell unbekannt ist und die Umlautform bekannt (Vokabular mit Wortvektor) -
    mehrdeutige/unbekannte Faelle bleiben unveraendert (dann gilt allein die Originalanalyse)."""
    if _model_knows(word):
        return None
    spots = list(_RESTORE_DIGRAPH_RE.finditer(word))
    if not spots or len(spots) > 4:
        return None
    for size in range(len(spots), 0, -1):
        for combo in itertools.combinations(spots, size):
            pieces: list[tuple[str, int, int]] = []
            last = 0
            for m in combo:
                pieces.extend((word[i], i, i + 1) for i in range(last, m.start()))
                pieces.append((_RESTORE_MAP[m.group()], m.start(), m.end()))
                last = m.end()
            pieces.extend((word[i], i, i + 1) for i in range(last, len(word)))
            if _model_knows("".join(ch for ch, _a, _b in pieces)):
                return tuple(pieces)
    return None


def _restore_for_analysis(text: str) -> tuple[str, list[int], list[int]] | None:
    """(Analysetext, Start-/Ende-Index je Analysezeichen im Originaltext) oder None, wenn nichts wiederherzustellen ist
    oder die Abbildung nicht zweifelsfrei konsistent ist (dann bleibt es bei der Originalanalyse)."""
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    changed = False
    position = 0
    for match in _WORD_PATTERN.finditer(text):
        for i in range(position, match.start()):
            out.append(text[i]); starts.append(i); ends.append(i + 1)
        restored = _restore_word(match.group()) if _RESTORE_DIGRAPH_RE.search(match.group()) else None
        if restored is None:
            for i in range(match.start(), match.end()):
                out.append(text[i]); starts.append(i); ends.append(i + 1)
        else:
            changed = True
            for ch, a, b in restored:
                out.append(ch); starts.append(match.start() + a); ends.append(match.start() + b)
        position = match.end()
    for i in range(position, len(text)):
        out.append(text[i]); starts.append(i); ends.append(i + 1)
    if not changed:
        return None
    analysis = "".join(out)
    consistent = (
        len(analysis) == len(starts) == len(ends)
        and all(0 <= a < b <= len(text) for a, b in zip(starts, ends))
        and all(starts[i] <= starts[i + 1] and ends[i] <= ends[i + 1] for i in range(len(starts) - 1))
    )
    return (analysis, starts, ends) if consistent else None


def detect_presidio_entities(text: str) -> list[DetectedSpan]:
    """Erkennt Personennamen/Orte/Organisationen im übergebenen Text via
    Presidio + deutschem spaCy-Modell und liefert sie als `DetectedSpan`-
    Liste - kompatibel mit `detect_all()` in detectors.py.

    Reine, seitenffektfreie Texterkennung, kein Netzwerkzugriff (das
    spaCy-Modell läuft komplett lokal, siehe CLAUDE.md/ARCHITECTURE.md
    "Local-First"-Grundsatz für die Datenschutz-Schicht)."""
    if not text or not text.strip():
        return []

    analyzer = _get_analyzer_engine()
    analysis_text = _neutralize_internal_tokens(text)
    results = analyzer.analyze(
        text=analysis_text,
        language="de",
        entities=list(_REQUESTED_ENTITIES),
        score_threshold=_MIN_SCORE,
    )

    spans = _collect_spans(results, text, None)
    restored = _restore_for_analysis(analysis_text)
    if restored is not None:
        restored_text, starts, ends = restored
        restored_results = analyzer.analyze(
            text=restored_text, language="de", entities=list(_REQUESTED_ENTITIES), score_threshold=_MIN_SCORE
        )
        known = {(sp.category, sp.start, sp.end) for sp in spans}
        spans.extend(
            sp for sp in _collect_spans(restored_results, text, (starts, ends)) if (sp.category, sp.start, sp.end) not in known
        )
    return normalize_organisation_spans(text, normalize_ner_span_boundaries(text, spans))


def _collect_spans(results, text: str, mapping: tuple[list[int], list[int]] | None) -> list[DetectedSpan]:
    """Presidio-Ergebnisse -> `DetectedSpan` mit den bekannten Filtern. `mapping` bildet Treffer einer
    Umlaut-wiederhergestellten Analysefassung auf den ORIGINALtext ab (Wert immer der Originalausschnitt)."""
    spans: list[DetectedSpan] = []
    for result in results:
        category = _ENTITY_TO_CATEGORY.get(result.entity_type)
        if category is None:
            continue
        if mapping is None:
            start, end = result.start, result.end
        else:
            starts, ends = mapping
            if not (0 <= result.start < result.end <= len(starts)):
                continue
            start, end = starts[result.start], ends[result.end - 1]
        value = text[start:end]
        if "\n" in value:
            # Ein echter Personen-/Orts-/Organisationsname erstreckt sich
            # nie ueber einen Zeilenumbruch hinweg - beobachtet als
            # Tokenisierungs-Artefakt an Zeilenumbruch+Klammer-Uebergaengen
            # (z. B. "thomas\n[" als EIN PERSON-Treffer). Ohne diese Regel
            # kann ein solcher Treffer mit einem direkt danach folgenden
            # zweiten Treffer kollidieren und die interne
            # Trennmarkierungs-Struktur in gateway.py durcheinanderbringen.
            continue
        if _LOOKS_LIKE_INTERNAL_TOKEN_PATTERN.match(value):
            continue
        if value.strip().lower() in _NEVER_ENTITY_WORDS:
            continue
        if _INTERNAL_PLACEHOLDER_PATTERN.search(value):
            # ECHTER FUND (realer Abnahme-Test, 13.09.): auf dem bereits
            # PSEUDONYMISIERTEN Text (zweiter Durchlauf, Restrisiko-Scan in
            # security_check.py Punkt 2/3/4) erkennt das Modell einen
            # Anredetitel unmittelbar vor einem neutralisierten Platzhalter
            # (z. B. "Herr " gefolgt von den zu Leerzeichen neutralisierten
            # Zeichen von "[PERSON_06]") weiterhin als EIGENEN, neuen
            # PERSON-Treffer - real reproduziert: "Herr [PERSON_06] " wurde
            # trotz bereits erfolgreich vergebenem Platzhalter erneut als
            # "moegliche restliche PII" gemeldet und blockierte dadurch
            # JEDE Chat-Nachricht dauerhaft, obwohl die Pseudonymisierung
            # selbst korrekt gearbeitet hatte. Ein Treffer, dessen Wert
            # (aus dem ORIGINALEN, nicht neutralisierten Text) bereits
            # einen erkennbaren Platzhalter-Token enthaelt, ist keine neue
            # PII - der eigentliche Name wurde bereits sicher ersetzt.
            continue
        spans.append(DetectedSpan(category=category, start=start, end=end, value=value))
    return spans


def get_entity_types(text: str) -> dict[tuple[int, int], str]:
    """Liefert den von spaCys eigener NER-Komponente erkannten
    Entitätstyp (IOB-roh, z. B. "PER"/"LOC"/"ORG"/"MISC", leerer String
    wenn keine Entität erkannt wurde) für jedes Token in `text`, als
    {(start, end): ent_type}.

    ECHTER FUND (Owner-Direktive "Verbleibende False-Positive-Grenze der
    Privacy-Namen-Heuristik beheben", 07.10.): security_check.py::
    _find_possible_unrecognized_names (Punkt 6) hielt "World Cities"
    (aus der harmlosen Wissensfrage "Was ist der World Cities Report?")
    fälschlich für einen möglichen Personennamen, weil spaCys POS-Tagger
    beide Wörter als PROPN taggt (unbekannte/fremdsprachige
    grossgeschriebene Wörter werden von spaCy mangels Vokabeleintrag oft
    default-mäßig als Eigenname eingestuft, siehe get_pos_tags) - der
    reine POS-Tag kann also NICHT zuverlässig zwischen "ist ein
    Personenname" und "ist ein fremdsprachiger Organisations-/
    Berichtsname" unterscheiden. SpaCys eigene (vom selben Modell
    mitgelieferte) NER-Komponente dagegen taggt "World Cities Report"
    korrekt als EIN zusammenhängendes "MISC"-Entity (nicht "PER") -
    direkt gegengeprüft: "Peter Müller"/"Max Mustermann" werden
    zuverlässig als "PER" getaggt, "Berlin"/"Musterstrasse" als "LOC".
    Dieser Entitätstyp ist damit ein präziseres Signal als der reine
    POS-Tag und wird in `_find_possible_unrecognized_names` als
    PRIMÄRES Signal verwendet, wenn vorhanden - fehlt für ein Wort jede
    erkannte Entität (leerer String, z. B. weil NER einen echten Namen
    schlicht übersieht), fällt die Heuristik weiterhin auf den
    bestehenden POS-Tag-basierten PROPN-Check zurück (unverändertes
    Defense-in-Depth-Verhalten, identisch zu get_pos_tags).

    Eigene Funktion statt Erweiterung von `get_pos_tags` (dessen
    Rückgabeform von dessen eigenen Tests/anderen Aufrufern als reine
    {span: pos_tag}-Zuordnung erwartet wird) - nutzt aber dieselbe
    bereits geladene Pipeline (`analyzer.nlp_engine.process_text`), kein
    zweites Modell."""
    if not text or not text.strip():
        return {}
    analyzer = _get_analyzer_engine()
    artifacts = analyzer.nlp_engine.process_text(text, "de")
    return {
        (token.idx, token.idx + len(token.text)): token.ent_type_
        for token in artifacts.tokens
    }


def get_pos_tags(text: str) -> dict[tuple[int, int], str]:
    """Liefert die Wortart (Universal-POS-Tag, z. B. "PROPN"/"NOUN"/"ADJ")
    fuer jedes Token in `text`, als {(start, end): pos_tag}.

    ECHTER FUND (realer Abnahme-Test, 13.09.): security_check.py::
    _find_possible_unrecognized_names (Punkt 6, "moeglicherweise nicht
    erkannte Namen") wertete JEDES Paar aus zwei durch genau ein
    Leerzeichen getrennten, grossgeschriebenen Woertern als Namens-
    Kandidaten - im Deutschen werden aber ALLE Substantive grossgeschrieben,
    und Adjektiv+Substantiv-Ueberschriften sind in Rechtstexten allgegenwaertig
    ("Synthetisches Testdokument", "Salvatorische Klausel", "Ordentliche
    Kuendigung" usw.) - real reproduziert: blockierte JEDE Chat-Nachricht in
    einer Unterhaltung mit einem angehaengten, vollkommen gewoehnlichen
    Rechtsdokument. Echte deutsche Personennamen werden von spaCys POS-
    Tagger dagegen zuverlaessig als "PROPN" (Eigenname) getaggt, waehrend
    solche Ueberschriften "ADJ"+"NOUN" sind (siehe reale Beispiele oben,
    waehrend der Untersuchung direkt gegengeprueft) - ein Kandidatenpaar ist
    daher nur dann tatsaechlich namensverdaechtig, wenn BEIDE Woerter als
    PROPN getaggt sind.

    Nutzt DIESELBE bereits geladene spaCy-Pipeline wie Presidio selbst
    (ueber `analyzer.nlp_engine`) - kein zweites Modell, keine zusaetzliche
    Speicherlast (siehe die eigenstaendige, dokumentierte Untersuchung zu
    Presidio+FastEmbed+Ollama-Speicherdruck in dieser Session - ein
    zweites geladenes de_core_news_lg-Modell waere hier fahrlaessig)."""
    if not text or not text.strip():
        return {}
    analyzer = _get_analyzer_engine()
    artifacts = analyzer.nlp_engine.process_text(text, "de")
    return {(token.idx, token.idx + len(token.text)): token.pos_ for token in artifacts.tokens}


_SENTENCE_START_PREFIX = re.compile(r"(?:^|\n|[.!?:]\s+)\s*$")


def drop_sentence_initial_imperatives(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Verwirft "person"-Treffer, die ein EINZELNES Wort am Satzanfang ohne Eigennamen-Tag sind.

    ECHTER FUND (Real-E2E 08.10.): die Anweisungen des Anwalts beginnen mit einem
    grossgeschriebenen Imperativ ("Ueberarbeite das Schreiben ...", "Fasse das Dokument
    zusammen ..."); die NER wertet ihn kontextabhaengig als Person (POS NOUN/VERB, nie
    PROPN). Folgen: im ersten Durchlauf Verfaelschung des Verlaufs ("[PERSON_04] das
    Dokument zusammen"), im Restrisiko-Scan eine Blockade der harmlosen Anweisung.

    Eng gefasst: nur Treffer aus genau EINEM Token, direkt am Satz-/Zeilenanfang oder
    nach "Label: ", ohne ein einziges PROPN-Token. Mehrwort-Namen, Namen mitten im Satz
    und PROPN-getaggte Namen bleiben unberuehrt; bei fehlender POS-Analyse bleibt der
    Treffer erhalten (fail-closed). Restrisiko (dokumentiert): ein echter Nachname, der am
    Satzanfang ausschliesslich als Nicht-Eigenname getaggt wird."""
    if not spans or not any(span.category == "person" for span in spans):
        return spans
    try:
        pos_tags = get_pos_tags(_neutralize_internal_tokens(text))
    except Exception:
        return spans
    kept: list[DetectedSpan] = []
    for span in spans:
        if span.category != "person" or len(span.value.split()) != 1:
            kept.append(span)
            continue
        if not _SENTENCE_START_PREFIX.search(text[: span.start]):
            kept.append(span)
            continue
        tags = [tag for (start, end), tag in pos_tags.items() if start >= span.start and end <= span.end]
        if len(tags) == 1 and tags[0] != "PROPN":
            continue
        kept.append(span)
    return kept


def drop_common_noun_persons(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Verwirft "person"-Treffer, die ausschliesslich aus gewoehnlichen
    Substantiven bestehen (kein einziges Token mit POS=PROPN).

    ECHTER FUND (Real-E2E 08.10., installierter Build): die harmlose
    Folgefrage "Und gilt das auch fuer Gewerbemietverträge?" erreichte Claude
    als "[PERSON_01]" (spaCys NER taggt das Fachwort als PER); Local AI lief
    72 s und Claude fragte nach dem "Platzhalter". Direkt gegengeprueft:
    der Fehlalarm besteht nur aus NOUN-Tokens, echte Namen enthalten
    mindestens ein PROPN-Token ("Herr Müller" = NOUN+PROPN, "Schmidt" =
    PROPN).

    Fail-closed: ein Treffer wird NUR verworfen, wenn fuer JEDES Token seines
    Bereichs ein POS-Tag vorliegt und keines PROPN ist; fehlt ein Tag oder
    schlaegt die POS-Analyse fehl, bleibt der Treffer erhalten. Andere
    Kategorien bleiben unberuehrt. Der Aufrufer (gateway.py) wendet den
    Filter NUR im Chat ohne Akte-/Mandanten-/Dokumentkontext an.
    Restrisiko (dokumentiert): ein echter, ausschliesslich als NOUN getaggter
    Nachname im Akte-losen Chat wuerde im Klartext an Claude gehen."""
    if not spans or not any(span.category == "person" for span in spans):
        return spans
    try:
        # Derselbe neutralisierte Text wie in `detect_presidio_entities`
        # (laengengleich, Offsets bleiben gueltig): auf dem ROHEN Text
        # verschmilzt die interne Trennmarkierung mit dem Nachbarwort zu
        # einem Token ("Gewerbemietverträge?@@GATEWAY_ITEM@@Assistent"), die
        # Treffer-Offsets fanden dann kein Token und der Fehlalarm blieb.
        pos_tags = get_pos_tags(_neutralize_internal_tokens(text))
    except Exception:
        return spans
    kept: list[DetectedSpan] = []
    for span in spans:
        if span.category != "person":
            kept.append(span)
            continue
        tags = [
            tag for (start, end), tag in pos_tags.items() if start >= span.start and end <= span.end
        ]
        covered = sum(len(text[s:e]) for (s, e) in pos_tags if s >= span.start and e <= span.end)
        fully_tagged = bool(tags) and covered >= len(span.value.replace(" ", ""))
        if fully_tagged and "PROPN" not in tags:
            continue
        kept.append(span)
    return kept
