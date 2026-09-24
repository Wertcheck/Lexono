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
_NEVER_ENTITY_WORDS = frozenset(
    {
        "gruessen", "grüßen", "grussen",
        "hochachtungsvoll",
        "erbschaftsteuerbescheid",
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

    spans: list[DetectedSpan] = []
    for result in results:
        category = _ENTITY_TO_CATEGORY.get(result.entity_type)
        if category is None:
            continue
        value = text[result.start : result.end]
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
        spans.append(
            DetectedSpan(category=category, start=result.start, end=result.end, value=value)
        )
    return spans


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
