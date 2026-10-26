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
import threading
from functools import lru_cache

from app.privacy.detectors import POSSESSIVE_KINSHIP, REFERENCE_LABEL_WORDS, DetectedSpan

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
        # Grussformeln (12.10.2026): "Hallo" wird vom POS-Tagger als PROPN getaggt; das Zweitsignal
        # (detect_unlabelled_proper_nouns) maskierte die Chat-Begruessung "Hallo" als [PERSON_01]
        # (tests/test_drafting_service.py::test_chat_greeting_on_a_matter_titled_with_the_client_name_skips_local_llm).
        # Ganzwort-Gleichheit: "Hallo Fischer" bleibt ueber "Fischer" geschuetzt.
        "hallo", "hey",
        # Anwendungsinterne Bezeichner (12.10.2026): "Schnellentwurf" ist der Titel der automatisch angelegten Chat-Akte
        # ("Akte: Schnellentwurf 2026-..."), "Chat" der Platzhalter-Sachverhalt des aktenlosen Chats. Beide sind vom
        # POS-Tagger PROPN; ohne Ausnahme maskierte das Zweitsignal sie in JEDEM Chat und erzwang damit die lokalen
        # KI-Schichten (tests/test_performance_regression.py Szenario A, tests/test_privacy_gateway.py skip_general_*).
        "schnellentwurf", "chat",
        # Gattungsname einer Bank (Sicherheitspruefung 13.10.2026, Review gemessen: "Kontonummer [KENNUNG_01] bei der
        # Sparkasse, Inhaber ..." - der Restrisiko-Scan hielt "Sparkasse" fuer eine Person und blockierte). Ganzwort-
        # Gleichheit: "Sparkasse Hamburg" bleibt als laengerer Treffer geschuetzt.
        "sparkasse",
        # Systemlabel (§34.15): Lexono schreibt nicht klassifizierte Dokumente als "[Unbekannt] ..." in den Sachverhalt
        # (local_ai_provider); das Zweitsignal hielt das Adjektiv fuer einen Eigennamen (Legal-E2E-Benchmark, 6 Akten).
        "unbekannt",
    }
)
# Bezeichnungen von Kennnummern (Sicherheitspruefung 13.10.2026, gemessen) werden NICHT als Liste gefuehrt, sondern
# strukturell aus `_ID_LABEL_BEFORE` abgeleitet (siehe `_is_identifier_label`): ist die Nummer dahinter maskiert, hielt
# die NER im Restrisiko-Scan die blosse Bezeichnung fuer Ort/Person ("SV-Nr." -> ort, "Kto.-Nr." -> person,
# "Rentenversicherungsnummer" -> person) und blockierte den Text.


_MARKER_WITH_BREAK = re.compile(r"@@[A-Z_]+@@\n?")
_SOFT_WRAP = re.compile(r"(?<=[a-zäöüß,])\n(?=[A-ZÄÖÜ])")


def _neutralize_internal_tokens(text: str) -> str:
    """Ersetzt gateway-/pseudonymizer-interne Marker/Platzhalter durch
    gleich lange Zeichenfolgen - Laenge (und damit jede Zeichenposition
    ausserhalb der Marker) bleibt exakt erhalten. NUR die Analysefassung.

    Ein Marker (samt direkt folgendem Zeilenumbruch) wird zu einem Satzende-Punkt + Leerzeichen, nicht zu
    reinen Leerzeichen (Qualitaetslauf 12.10.2026, scripts/bench_name_detection.py): Mit Leerzeichen
    verlor das Modell am Feldanfang den Satzkontext, ein nacktes "Marquardt hat unterschrieben." (Anmerkungen,
    Argumentationspunkte, Verlauf) blieb im Klartext. Ausserdem wird ein weicher Zeilenumbruch mitten im Satz
    ("durch" + Umbruch + "Fischer", PDF-Spaltenumbruch) zum Leerzeichen; Anzahl/Position aller Zeichen bleiben gleich."""
    text = _MARKER_WITH_BREAK.sub(lambda m: "." + " " * (len(m.group()) - 1), text)
    text = _SOFT_WRAP.sub(" ", text)
    return _PLACEHOLDER_WITH_GENITIVE.sub(lambda m: " " * len(m.group()), text)


# §52 (16.10.2026): die Genitiv-Endung direkt am Platzhalter ("[PERSON_02]s Zeugin", vom Pseudonymizer selbst gesetzt,
# §32/§51) gehoert in der Analysefassung zum Platzhalter - sonst blieb "s Zeugin" stehen, die NER las es als Person und
# der Restrisiko-Scan blockierte. Nur unmittelbar am Platzhalter und nur vor einem Wortende.
_PLACEHOLDER_WITH_GENITIVE = re.compile(_INTERNAL_PLACEHOLDER_PATTERN.pattern + r"(?:s|'s|’s)?(?![\wÄÖÜäöüß])|"
                                        + _INTERNAL_PLACEHOLDER_PATTERN.pattern)


_ENGINE_LOCK = threading.Lock()


def _get_analyzer_engine():
    """§64: unter einer Sperre - die Vorab-Pruefung im Hintergrund (app/documents/precheck_queue.py) und ein Request
    duerfen das spaCy-Modell nicht gleichzeitig zweimal laden (`lru_cache` sperrt nicht: doppelte Speicherspitze)."""
    with _ENGINE_LOCK:
        return _build_analyzer_engine()


@lru_cache(maxsize=1)
def _build_analyzer_engine():
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
    engine = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["de"])
    import presidio_analyzer.predefined_recognizers as predefined

    for name in _GERMAN_ID_RECOGNIZERS:
        official = getattr(predefined, name)()
        # Nur Muster, deren Grundscore die Schwelle OHNE Kontextwort erreicht (siehe Kommentar unten).
        patterns = [pattern for pattern in official.patterns if pattern.score >= _MIN_SCORE]
        engine.registry.add_recognizer(getattr(predefined, name)(patterns=patterns))
    for name in _LABELLED_ID_RECOGNIZERS:
        engine.registry.add_recognizer(getattr(predefined, name)())  # IpRecognizer ist bereits Standard fuer "de"
    from presidio_analyzer import Pattern, PatternRecognizer

    for entity, regex, score in _EXTRA_ID_PATTERNS:
        engine.registry.add_recognizer(
            PatternRecognizer(
                supported_entity=entity, supported_language="de", name=f"Lexono{entity.title()}Format",
                patterns=[Pattern(name=f"{entity} Zusatzformat", regex=regex, score=score)],
            )
        )
    return engine


# Zusaetzliche Formate unter DENSELBEN Entitaeten (Folgezyklus 13.10.2026, erweiterte Leckfaelle; Presidios Muster decken sie
# nicht ab). Fuer bezeichnete Entitaeten (siehe _ID_LABEL_BEFORE) gilt unveraendert: nur mit UNMITTELBAR vorangestellter
# Bezeichnung - das Muster allein loest nie etwas aus, daher Grundscore 0.1. Die Handelsregister-Gruppierung ("HRB 123
# 456") ist wie das offizielle Muster kontextunabhaengig (Score 0.5) und ersetzt dort nur den Rest "456" (Review).
_EXTRA_ID_PATTERNS = (
    ("DE_TAX_NUMBER", r"\b\d{10,13}\b", 0.1),  # Steuernummer ohne Trennzeichen (Laenderformate)
    ("DE_TAX_NUMBER", r"\b\d{2,3}\s\d{3,4}\s\d{4,5}\b", 0.1),  # "143 815 08154"
    ("DE_KFZ", r"\b[A-ZÄÖÜ]{1,3}[\s-][A-Z]{1,2}\d{1,4}[EH]?\b", 0.1),  # "HH AB1234"
    ("DE_HANDELSREGISTER", r"\bHR[AB]\s*\d{1,3}(?:\s\d{3})+\b", 0.5),
    ("DE_SV_NUMMER", r"\b\d{2}\s?\d{6}\s?[A-Z]\s?\d{3}\b", 0.1),  # "65 170839 J 003"
    ("DE_KONTONUMMER", r"\b\d{2,4}(?:[ .]?\d{2,4}){1,4}\b", 0.1),  # "1234567890", "98 765 432"
    ("DE_MITGLIEDSNUMMER", r"\b(?=[A-Z0-9/-]*\d)[A-Z0-9][A-Z0-9/-]{3,20}\b", 0.1),  # mindestens eine Ziffer
)


# Offizielle deutsche Presidio-Recognizer (Presidio-Audit 13.10.2026, scripts/bench_presidio_audit.py): in Presidio
# enthalten, aber standardmaessig deaktiviert (conf/default_recognizers.yaml "enabled: false"). Uebernommen wird nur, was
# die Schwelle OHNE Kontextwort erreicht und ein unverwechselbares Format hat: USt-IdNr. ("DE" + 9 Ziffern, Pruefziffer),
# Handelsregisternummer ("HRB 123456"), Rentenversicherungsnummer im strengen Format (Geburtsdatum + Buchstabe +
# Pruefziffer). Grund (unabhaengiges Review): kontextabhaengige Muster (Grundscore < Schwelle) erreichten die Schwelle im
# Restrisiko-Scan, aber nicht in der Pseudonymisierung - dort stehen Platzhalter (als Leerzeichen analysiert) zwischen
# Kandidat und Kontextwort, das Kontextfenster reicht weiter. Ergebnis waren neue Fail-Closed-Blockaden normaler Texte
# ("Fahrzeuge KL AB 12, KL CD 34 und KL EF 56", 6 von 78 realistischen Saetzen, vorher 0). Ausserdem verworfen nach
# Messung: DeKfz (Fahrzeugmodelle "VW T 6", Strassen "DER B 51"), DeTaxNumber (13-stellige Liefer-/Vorgangsnummern),
# DeLanr/DeBsnr (9-stellige Nummern, Pruefziffer/"Praxis" genuegt), DeIdCard/DePassport/DeHealthInsurance/
# DeFuehrerschein (Teile-/Artikelnummern), DeTaxId (deckt detectors.detect_steuer_id bereits ab), IpRecognizer
# ("Version 2.4.1.0" als IP), UrlRecognizer (Quellenangaben wie gesetze-im-internet.de), DePlz (Grundscore 0.05).
_GERMAN_ID_RECOGNIZERS = ("DeVatIdRecognizer", "DeHandelsregisterRecognizer", "DeSocialSecurityRecognizer")
_ID_ENTITIES = ("DE_VAT_ID", "DE_HANDELSREGISTER", "DE_SOCIAL_SECURITY")


# Kennnummern, deren Presidio-Muster allein zu unspezifisch sind (Folgezyklus 13.10.2026, eingefrorene Testbasis: Steuer-
# nummer, Personalausweis, Kfz-Kennzeichen, IP-Adresse blieben im Klartext): uebernommen wird ein Presidio-Treffer NUR,
# wenn seine Bezeichnung UNMITTELBAR davor steht ("Steuernummer 143/815/08154", "Kennzeichen HH-AB 1234",
# "IP-Adresse 192.168.178.23") - nicht Presidios Kontextfenster (fuenf Woerter, durch Platzhalter im Restrisiko-Scan
# veraenderlich). Damit sind Pseudonymisierung und Restrisiko-Scan konsistent: steht zwischen Bezeichnung und Wert etwas,
# das inzwischen maskiert ist, fehlt die Nachbarschaft, und der Restrisiko-Scan kann keinen neuen Treffer erzeugen.
# Ohne Bezeichnung bleiben "VW T 6", "DER B 51", 13-stellige Liefernummern und "Version 2.4.1.0" unberuehrt.
# Fahrzeugwort unmittelbar vor dem Kennzeichen ("Der PKW HH-AB 1234"); gilt wie das nackte "Kennzeichen" nur mit
# mindestens zwei Ziffern ("Pkw BMW X5", "Lkw VW T 6" bleiben Modellangaben).
_VEHICLE_WORDS = r"pkw|lkw|kfz|fahrzeug|krad|motorrad|anhänger|anhaenger|wohnmobil"
_LABELLED_ID_RECOGNIZERS = (
    "DeTaxNumberRecognizer", "DeIdCardRecognizer", "DePassportRecognizer", "DeFuehrerscheinRecognizer", "DeKfzRecognizer",
)
_ID_LABEL_BEFORE = {
    "DE_TAX_NUMBER": r"steuer-?\s?(?:nummer|nr\.?)|st\.?\s?-?\s?nr\.?",
    "DE_ID_CARD": r"(?:personal)?ausweis(?:-?\s?(?:nummer|nr\.?))?|ausweisdokument(?:-?\s?nr\.?)?",
    "DE_PASSPORT": r"(?:reise)?pass(?:-?\s?(?:nummer|nr\.?))|reisepass",
    "DE_FUEHRERSCHEIN": r"f(?:ü|ue)hrerschein(?:-?\s?(?:nummer|nr\.?))?|fs-?\s?nr\.?",
    "DE_KFZ": r"(?:amtl(?:iches|\.)\s+|kfz-?\s?|fahrzeug-?\s?)?kennzeichen|(?:amtl\.|kfz-)\s?kz\.|kennz\.|" + _VEHICLE_WORDS,
    # Sicherheitspruefung 13.10.2026 (gemessen: im Klartext): Sozialversicherungs-/Rentenversicherungsnummer mit
    # Leerzeichen, Kontonummer, Mitgliedsnummer - jeweils nur mit unmittelbar vorangestellter Bezeichnung.
    "DE_SV_NUMMER": r"(?:sozial|renten)versicherungs-?\s?(?:nummer|nr\.?)|(?:sv|rv)-?\s?nr\.?|versicherungsnummer",
    "DE_KONTONUMMER": r"konto-?\s?(?:nummer|nr\.?)|kto\.?\s?-?\s?nr\.?",
    "DE_MITGLIEDSNUMMER": r"mitglieds-?\s?(?:nummer|nr\.?)",
    "IP_ADDRESS": r"ip-?\s?adresse|ip",
}
_ID_LABEL_PATTERNS = {
    # "Kennzeichen ist/lautet ..." (Review: Wert nicht unmittelbar nach der Bezeichnung) - nur diese zwei Verbindungswoerter.
    entity: re.compile(
        r"(?<![\wÄÖÜäöüß])(?:" + label + r")(?:[ \t]+(?:ist|lautet|lauten))?\s?[:.]?[ \t]{0,2}$", re.IGNORECASE
    )
    for entity, label in _ID_LABEL_BEFORE.items()
}
# Ein NER-Treffer, der GANZ aus einer solchen Bezeichnung besteht, ist kein Name/Ort - abgeleitet aus denselben Mustern,
# keine eigene Wortliste (siehe Kommentar bei _NEVER_ENTITY_WORDS).
# Dazu die Bezeichnungen der Bezugsnummern aus detectors (Nummern-Strategie §22: "Kd.-Nr" neben [KUNDENNUMMER_01] las die
# NER als Person - Fail-Closed-Blockade eines gewoehnlichen Satzes).
_ID_LABEL_FULL = re.compile(
    r"(?:" + "|".join([*_ID_LABEL_BEFORE.values(), REFERENCE_LABEL_WORDS]) + r")\.?", re.IGNORECASE
)


# Grundbuchangabe aus Bezeichnung + Nummer ("Flur 3", "Blatt 12345", "Flurstück 12/3") - die NER las sie als Ort und der
# Restrisiko-Scan blockierte gewoehnliche Grundstuecksangaben (vorbestehend, gemessen 13.10.2026).
_LAND_REGISTER_LABEL_HIT = re.compile(
    r"(?:flur|blatt|bl\.|flurst(?:ü|ue)ck\w*|flst\.?|grundbuchblatt)\s*\d[\d/]*[a-z]?", re.IGNORECASE
)


def _is_identifier_label(value: str) -> bool:
    return _ID_LABEL_FULL.fullmatch(value.strip()) is not None


# Die Presidio-Muster laufen ohne Gross-/Kleinschreibung: "auf der A 7" passte als Kennzeichen. Ein kleingeschriebenes
# Kennzeichen ("unser kennzeichen ist hh-ab 1234", Chat) wird nur uebernommen, wenn die erste Buchstabengruppe kein
# Artikel/keine Praeposition ist (Folgezyklus 13.10.2026: vorher blieb "1234" neben "[ORT_01]" im Klartext).
_LOWERCASE = re.compile(r"[a-zäöüß]")
_KFZ_FUNCTION_WORDS = frozenset(
    "der die das den dem des ein eine einen auf von vom mit nach bei zur zum im am an in aus bis".split()
)
# Gewoehnliche Woerter, die das ohne Gross-/Kleinschreibung laufende Muster nach "Kennzeichen" als Kennzeichen las
# (Review: "Das Kennzeichen ist um 18 Uhr gestohlen", "ist zu 80 Prozent", "Fahrzeugkennzeichen und Km 45"). Geprueft wird
# die ERSTE Buchstabengruppe (Unterscheidungszeichen): Woerter mit 3+ Buchstaben immer; Zweibuchstaben-Woerter nur in
# Kleinschreibung - "AB" (Aschaffenburg), "UM", "IN", "AM" sind echte Unterscheidungszeichen.
_KFZ_NOT_A_GROUP = _KFZ_FUNCTION_WORDS | frozenset("ist um ab zu und km ca je pro bzw lautet sind war wird".split())


def _plate_has_common_word(value: str) -> bool:
    groups = re.findall(r"[A-Za-zÄÖÜäöüß]+", value)
    if not groups or groups[0].lower() not in _KFZ_NOT_A_GROUP:
        return False
    return len(groups[0]) >= 3 or not groups[0].isupper()
# Profil-Links sozialer/beruflicher Netzwerke (Folgezyklus 13.10.2026, Testbasis P12: "linkedin.com/in/gernot-hahn-1980"
# im Klartext): der Pfad ist der Personenname. Presidios UrlRecognizer findet jede URL - uebernommen wird nur diese
# Plattformart; Quellenangaben (gesetze-im-internet.de, dejure.org, Gerichtsseiten) bleiben lesbar. Allein die URL selbst
# entscheidet, daher gleich in Pseudonymisierung und Restrisiko-Scan.
_PROFILE_URL = re.compile(
    # Grenze vor dem Host (Review: "dropbox.com/..." und "fedex.com/..." enthielten "x.com/"); github.com bewusst nicht -
    # Repository-Links ("github.com/microsoft/presidio") sind Quellenangaben.
    r"(?<![\w.-])(?:(?:www|[a-z]{2})\.)?(?:linkedin\.com/(?:in|pub)/|xing\.com/(?:profile|people)/|"
    r"facebook\.com/(?!sharer|share|events/)|instagram\.com/|(?:twitter|x)\.com/|tiktok\.com/@|threads\.net/@|"
    r"bsky\.app/profile/)[^/\s]+",
    re.IGNORECASE,
)


# Nur das nackte Wort "Kennzeichen" (nicht "amtliches"/"Kfz-"/"Fahrzeugkennzeichen"): "Kennzeichen VW T 6"/"EU A 3"/
# "A B 1" sind Modell- bzw. Gliederungsangaben (Review). Dort gilt ein Kennzeichen nur mit mindestens zwei Ziffern.
# Bewusst KEINE Ausnahme fuer "Ihr/Unser Kennzeichen": ein Bezugszeichen ("RA BK 26") sieht aus wie ein echtes
# Kennzeichen ("Ihr Kennzeichen HH-AB 1234" im Versichererbrief) - im Zweifel maskieren.
_BARE_KFZ_LABEL = re.compile(
    r"(?<![\w-])(?:kennzeichen|" + _VEHICLE_WORDS + r")(?:[ \t]+(?:ist|lautet))?\s?[:.]?[ \t]{0,2}$", re.IGNORECASE
)


def _weak_plate(text: str, start: int, value: str) -> bool:
    if _BARE_KFZ_LABEL.search(text[max(0, start - 40) : start]) is None:
        return False
    return sum(ch.isdigit() for ch in value) < 2


def _id_spans(results, text: str) -> list[DetectedSpan]:
    spans = []
    for result in results:
        value = text[result.start : result.end]
        if result.entity_type in _ID_ENTITIES:
            if result.score < _MIN_SCORE:
                continue
        elif result.entity_type in _ID_LABEL_PATTERNS:
            line_start = text.rfind("\n", 0, result.start) + 1
            if not _ID_LABEL_PATTERNS[result.entity_type].search(text[max(line_start, result.start - 40) : result.start]):
                continue
            if result.entity_type == "DE_KFZ" and (_weak_plate(text, result.start, value) or _plate_has_common_word(value)):
                continue
        elif result.entity_type == "URL":
            if _PROFILE_URL.search(value):
                spans.append(DetectedSpan(category="url", start=result.start, end=result.end, value=value))
            continue
        else:
            continue
        spans.append(DetectedSpan(category="kennung", start=result.start, end=result.end, value=value))
    return spans


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
# Buchstabe JEDER Schrift (Presidio-Audit 13.10.2026, scripts/bench_presidio_audit.py): mit nur A-Z/ÄÖÜß verwarf diese
# Regel - gedacht fuer reine Ziffern-/Satzzeichen-Treffer - jeden griechischen/kyrillischen Namen, den Presidio korrekt
# als PERSON erkannt hatte ("Γιάννης Παπαδόπουλος" -> nur noch "Παπαδόπουλος" ueber eine Zusatzregel, "ΓΙΑΝΝΗΣ" gar nicht).
_HAS_LETTER = re.compile(r"[^\W\d_]")


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


# Absender-Wendung "<Kommunikationsnomen> von X" (26.10.2026, reproduziert mit "Wir bestaetigen den Eingang des Schreibens
# von X vom 16.03.2024."): spaCy etikettiert "Schreibens von Fuchs" als EINEN Ort - der Platzhalter verschluckte Nomen
# und Praeposition ("Eingang des [ORT_01]"), im Gateway sogar das Datum. Dieselbe Liste nutzt die Rueckfrage
# (review._sender_after_communication_noun) fuer Absender, die das Modell als Gattungswort kennt ("König", "Bauer").
COMMUNICATION_NOUNS = frozenset({
    "schreiben", "schreibens", "brief", "briefes", "briefs", "mail", "e-mail", "email", "nachricht", "mitteilung",
    "anruf", "anrufs", "anrufes", "telefonat", "telefonats", "stellungnahme", "gutachten", "gutachtens", "bericht",
    "berichts", "berichtes", "antrag", "antrags", "antrages", "antwort", "erklärung", "erklaerung", "aussage",
    "vollmacht", "rechnung", "angebot", "angebots", "angebotes", "kündigung", "kuendigung", "mahnung", "fax", "faxes",
    "zuschrift", "eingabe", "klage", "widerspruch", "widerspruchs", "einspruch", "einspruchs", "vermerk", "vermerks",
    "protokoll", "protokolls", "hinweis", "hinweises", "rückruf", "rueckruf", "rückrufs", "rueckrufs", "auftrag",
    "auftrags", "auftrages", "post", "unterlagen", "anfrage", "zusage", "absage", "beschwerde", "rüge", "ruege",
})
_SENDER_PREFIX = re.compile(r"(?P<noun>[A-ZÄÖÜ][\wÄÖÜäöüß\-]*)\s+(?P<prep>von|vom)\s+(?=\S)")
_DATE_TAIL = re.compile(r"\s+(?:vom|am|v\.)\s+\d")


def trim_sender_phrases(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Ein Namens-/Orts-/Organisations-Treffer, der mit "<Kommunikationsnomen> von|vom" BEGINNT, wird auf den Absender
    gekuerzt; ein mitgefangenes Datum ("Stein vom 16.03.2024") bleibt ausserhalb (Daten erfasst der Datumsdetektor).
    Nur Kuerzen, nie Entfernen: der Absender bleibt maskiert (fail-closed). Ein einzelnes Wort nach "von" (ohne Artikel)
    ist als Absender eine Person ("Schreiben von Fuchs"); nach "vom" (= von dem: "vom Finanzamt") und bei mehreren
    Woertern bleibt die erkannte Kategorie."""
    result: list[DetectedSpan] = []
    for span in spans:
        match = _SENDER_PREFIX.match(span.value)
        if span.category not in ("person", "ort", "organisation") or not match or \
                match.group("noun").lower() not in COMMUNICATION_NOUNS:
            result.append(span)
            continue
        rest = span.value[match.end():]
        tail = _DATE_TAIL.search(rest)
        if tail:
            rest = rest[: tail.start()]
        rest = rest.rstrip(" .,;:")
        if not _HAS_LETTER.search(rest):
            result.append(span)  # nichts Sinnvolles uebrig - lieber unveraendert maskieren
            continue
        start = span.start + match.end()
        single = re.fullmatch(r"[A-ZÄÖÜ][\wÄÖÜäöüß\-]*", rest) is not None
        category = "person" if match.group("prep") == "von" and single else span.category
        result.append(DetectedSpan(category=category, start=start, end=start + len(rest), value=rest))
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


# Ableitungs-Komposita (13.10.2026, Befund installierter Build: "Die Nachbesserungsfrist endet am 30.11.2026." wurde im
# Dokumentkontext zu "Die [PERSON_01] endet ..."). Das NER-Modell etikettiert seltene, ihm unbekannte Komposita
# kontextabhaengig als PER - der Rahmensatz-Test hilft hier nicht ("Nachbesserungsfrist" 5/5), POS=NOUN auch nicht (echte
# Namen werden ebenfalls oft als NOUN getaggt). Gegensignal ist die WORTBILDUNG: Praefix + Ableitungssuffix mit Fugen-s
# (-ungs-, -heits-, -keits-, -schafts-, -tions-, -taets-) + ein juristisch-sachliches Kopfnomen aus einer geschlossenen
# Klasse ("Nachbesserung|s|frist", "Zustellung|s|urkunde", "Kostenfestsetzung|s|beschluss").
# Erste Fassung verlangte nur ein dem Modell bekanntes Kopfwort - der unabhaengige Review wies nach, dass dann auch
# namensfoermige Bildungen ("Siedlungsmann", "Lichtungsberger", "Rodungshausen") frei wurden: Namensbestandteile (-mann,
# -berg, -hausen) sind ebenfalls bekannte Woerter, und weder Modell-Probe noch Vokabular trennen Kopfwoerter zuverlaessig.
# Daher die geschlossene Klasse von Sach-/Rechtsnomen (kein Namens- oder Ortsbestandteil). In 79 Benchmark-Nachnamen nicht
# beobachtet; vollstaendige Freiheit realer Namen ist damit NICHT bewiesen. Gilt nur fuer EINWORT-Treffer (siehe Aufrufer).
_DERIVATIONAL_LINK = re.compile(r"(?<=[a-zäöüß]{3})(?:ungs|heits|keits|schafts|tions|täts)(?=[a-zäöüß]{4,}$)")
_LEGAL_COMPOUND_HEADS = frozenset(
    {
        "frist", "fristen", "urkunde", "urkunden", "schaden", "schäden", "anspruch", "ansprüche", "beschluss",
        "beschlüsse", "erklärung", "erklärungen", "recht", "rechte", "bescheid", "bescheide", "schreiben",
        "verzeichnis", "leistung", "leistungen", "ankündigung", "klage", "klagen", "vertrag", "verträge", "rechnung",
        "rechnungen", "abrechnung", "protokoll", "gutachten", "antrag", "anträge", "verfahren", "forderung",
        "forderungen", "pflicht", "pflichten", "vollmacht", "vereinbarung", "anzeige", "rüge", "vergütung", "strafe",
        "termin", "termine", "gebühr", "gebühren", "kosten", "zinsen", "betrag", "beträge", "summe", "nachweis",
        "verlangen", "frage", "fragen", "zeitraum", "pflichtverletzung", "mangel", "mängel", "unterlagen", "auftrag",
        "aufträge", "verhältnis", "beginn", "ende", "datum", "ort", "stelle", "verbot", "gesetz", "vorschrift",
    }
)


@lru_cache(maxsize=20000)
def is_derivational_compound(word: str) -> bool:
    if not word.isalpha():
        return False
    lower = word.lower()
    # Jede Fugenposition pruefen ("Leistungs|verweigerungs|recht": Kopfwort ist der Rest nach dem LETZTEN Fugenelement).
    return any(lower[match.end() :] in _LEGAL_COMPOUND_HEADS for match in _DERIVATIONAL_LINK.finditer(lower))


# Sach-/Rollen-Komposita OHNE Ableitungsfuge (§34.15, Legal-E2E-Benchmark §34.6: "Reparatur|dauer", "Tagebuch|nummer",
# "Schlaf|zimmer", "Schicht|leiters", "Haus|grundstücks", "Allein|erbin" wurden als Person/Ort maskiert; im Testfall
# LE-T003 erkannte der Restrisiko-Scan "Mess|beamter" in anderem Kontext erneut und blockierte jede Anfrage).
# Gleiches Prinzip wie oben (geschlossene Klasse von Kopfnomen, kein Namens- oder Ortsbestandteil): bewusst NICHT
# -mann, -meister, -berg, -hof, -stein, -schein, -recht, -ort, -ende, -haus, -feld, -bach, -burg, -dorf. Zusaetzlich muss
# der Vorderteil (ggf. ohne Fugen-s/-es/-n/-en) ein dem Modell bekanntes Wort mit mindestens vier Buchstaben sein
# ("Albrecht": Vorderteil "Alb" zu kurz, Kopf "recht" ohnehin nicht in der Klasse). Gilt NICHT nach Anrede oder
# Rollenwort (siehe `_titled_name_spans`, `detect_document_convention_names`): "Herr Waldleiter" bleibt geschuetzt.
# Unabhaengiges Red-Team-Review (§34.15): Kopfnomen, die auch NACHNAMEN bilden (-leiter: "Hochleiter", "Sonnleiter";
# -zimmer, -kosten, -schaden, -frist, -dauer, ...), liessen blosse Nachnamen ohne Anrede im Klartext durch (11 von 13
# Saetzen). Daher zwei Klassen: SICHERE Kopfnomen (abstrakte Dokument-/Verwaltungsbegriffe, bilden keine Nachnamen) und
# NAMENSNAHE Kopfnomen - letztere nur, wenn das GANZE Wort dem Modell als Wort bekannt ist UND die Modell-Probe 0 ergibt
# ("Reparaturdauer", "Zahlungsfrist"); "Holzleiter" (Wort und Nachname, Probe 3) und "Schlafzimmer" (Probe 2) bleiben maskiert.
_COMMON_COMPOUND_HEADS = frozenset(
    {
        "nummer", "nummern", "notiz", "notizen", "mitteilung", "mitteilungen", "vergütung", "vergütungen",
        "grundstück", "grundstücks", "grundstücke", "geschoss", "geschosses", "abteilung", "abteilungen", "beamter",
        "beamten", "beamtin", "erbin", "bescheid", "bescheids", "protokoll", "protokolls", "bericht", "berichts",
        "unterlagen", "vertrag", "vertrags", "rechnung", "gutachten", "antrag", "erklärung", "urkunde", "vollmacht",
        "anzeige", "forderung", "zahlung", "zahlungen",
    }
)
_NAME_PRONE_COMPOUND_HEADS = frozenset(
    {
        "leiter", "leiters", "leiterin", "zimmer", "zimmers", "dauer", "kosten", "frist", "fristen", "termin", "betrag",
        "summe", "schaden", "schadens", "wohnung", "fläche",
    }
)
_MIN_COMPOUND_MODIFIER = 4
# Erbrechtliche Rollen ("Vor|erbin", "Mit|erbe", "Allein|erbin"): der Vorderteil ist ein Praefix - geschlossene Menge.
_INHERITANCE_HEADS = frozenset({"erbin", "erbe", "erben"})
_INHERITANCE_PREFIXES = frozenset({"vor", "nach", "mit", "allein", "ersatz", "schluss", "haupt"})


def _known_modifier(modifier: str) -> bool:
    """Vorderteil (ggf. ohne Fugenelement) ist ein bekanntes Wort - oder selbst aus zwei bekannten Woertern zusammengesetzt
    ("Brutto|monat|s|vergütung")."""
    forms = [modifier]
    for joint in ("es", "en", "s", "n"):
        if modifier.endswith(joint):
            forms.append(modifier[: -len(joint)])
    for form in forms:
        if len(form) >= _MIN_COMPOUND_MODIFIER and _model_knows(form):
            return True
        for split in range(_MIN_COMPOUND_MODIFIER, len(form) - _MIN_COMPOUND_MODIFIER + 1):
            if _model_knows(form[:split]) and _model_knows(form[split:]):
                return True
    return False


@lru_cache(maxsize=20000)
def is_common_compound(word: str) -> bool:
    if not word.isalpha() or len(word) < 7:
        return False
    lower = word.lower()
    for split in range(3, len(lower) - 3):
        head = lower[split:]
        if head in _INHERITANCE_HEADS and lower[:split] in _INHERITANCE_PREFIXES:
            return True
        if split < _MIN_COMPOUND_MODIFIER:
            continue
        safe = head in _COMMON_COMPOUND_HEADS and _known_modifier(word[:split])
        name_prone = head in _NAME_PRONE_COMPOUND_HEADS and _model_knows(word)
        # In BEIDEN Klassen zusaetzlich: das Modell erkennt das Wort in KEINEM neutralen Namensrahmen als Person.
        # Red-Team Runde 2: ohne diese Bedingung gingen erfundene Namen mit "sicherem" Kopfnomen ("Waldnotiz",
        # "Kirchbericht", "Steinerbin", Probe 5) im Klartext hinaus; Fachwoerter wie "Mietvertrag", "Aktennotiz" haben 0.
        if (safe or name_prone) and _name_probe_score(word) == 0:
            return True
    return False


@lru_cache(maxsize=20000)
def is_safe_head_compound(word: str) -> bool:
    """Nur sichere Kopfnomen (fuer die Kategorie "ort": namensnahe Kopfnomen bilden auch Ortsnamen, "Rosenzimmer")."""
    if not word.isalpha() or len(word) < 7:
        return False
    lower = word.lower()
    return _name_probe_score(word) == 0 and any(
        lower[split:] in _COMMON_COMPOUND_HEADS and _known_modifier(word[:split])
        for split in range(_MIN_COMPOUND_MODIFIER, len(lower) - 3)
    )


# Feldbezeichnung, deren Wert NUR aus Titeln/Personenplatzhaltern bzw. einem Namen besteht und dann endet ("Messbeamter:
# Polizeiobermeister Jannik Brettschneider" -> im Restrisiko-Scan "Messbeamter: [PERSON_04] [BETEILIGTER_02]"). Die
# Bezeichnung ist eine Rolle, kein Name (Legal-E2E LE-T003: die Modell-Probe hielt "Messbeamter" fuer einen Namen und der
# Restrisiko-Scan blockierte JEDE Anfrage der Akte). Bewusst eng: der Feldwert muss bis zum Feldende (Zeilenende,
# Doppel-Leerzeichen, Satzende, Komma) ausschliesslich aus Platzhaltern, Titeln oder grossgeschriebenen Namenswoertern
# bestehen - eine Sprecherzeile im Protokoll ("Hochleiter: Ich war dort") geht mit Fliesstext weiter und bleibt geprueft.
_PERSON_VALUE_TOKEN = (
    r"(?:\[(?:PERSON|BETEILIGTER|MANDANT|GEGNER|ANWALT)_\d+\]|Herrn?|Frau|Dr\.|Prof\.|Dipl\.-Ing\.|"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)"
)
_ROLE_LABEL_AFTER = re.compile(
    r":[ \t]*" + _PERSON_VALUE_TOKEN + r"(?:[ \t]+" + _PERSON_VALUE_TOKEN + r"){0,3}(?=[ \t]*(?:$|\n|[ \t]{2}|[.,;]))"
)
_HAS_PERSON_PLACEHOLDER = re.compile(r"\[(?:PERSON|BETEILIGTER|MANDANT|GEGNER|ANWALT)_\d+\]")


def is_role_label_before_person(text: str, end: int) -> bool:
    """True, wenn das Wort, das bei `end` endet, eine Feldbezeichnung ist, deren Wert nur eine (bereits ersetzte) Person
    ist. Verlangt mindestens einen Personenplatzhalter im Feldwert - im ersten Durchlauf (noch keine Platzhalter) greift
    die Regel daher nicht; sie wirkt nur im Restrisiko-Scan, wo die Person bereits sicher ersetzt ist."""
    match = _ROLE_LABEL_AFTER.match(text, end)
    return bool(match and _HAS_PERSON_PLACEHOLDER.search(match.group()))


# §72 (Dev-Fall LE-D007 "Steuer-Bescheide versandt", Befund LE-T004 "Visa-Anträge erfasst"): Verwaltungsgegenstand +
# Verfahrens-Kopfnomen - mit Bindestrich oder zusammengeschrieben - wurde kontextabhaengig als Organisation
# ("Visa-Anträge", "Steuer-Bescheide") oder Person ("Visaanträge") maskiert; der entscheidende Sachverhaltsfakt war im
# Payload unverstaendlich. Die Modell-Probe trennt hier nicht ("Visa-Anträge" 0, "Asyl-Anträge" 4), "is_common_compound"
# griff nicht (Bindestrich, Pluralform). Gleiches Prinzip wie oben, aber BEIDE Teile aus geschlossenen Klassen:
# Gegenstaende behoerdlicher Verfahren (kein Vor-, Nach- oder Firmenname) und Verfahrens-/Dokumentnomen. Firmennamen wie
# "Siemens-Anträge" bleiben maskiert (Vorderteil nicht in der Klasse); Mehrworttreffer ("Visa-Anträge GmbH") ohnehin, die
# Regel gilt nur fuer EIN Wort. Bekannte Beteiligte schuetzt die Abgleichsliste unabhaengig davon.
_ADMIN_SUBJECTS = frozenset(
    {
        "visa", "visum", "pass", "reisepass", "ausweis", "personalausweis", "asyl", "aufenthalt", "aufenthalts",
        "aufenthaltstitel", "einbürgerung", "einbürgerungs", "bau", "steuer", "renten", "rente", "kindergeld",
        "elterngeld", "wohngeld", "bürgergeld", "sozialhilfe", "beihilfe", "führerschein", "gewerbe", "melde",
        "zoll", "insolvenz", "arbeitserlaubnis", "einreise",
    }
)
_ADMIN_PROCEDURE_HEADS = frozenset(
    {
        "antrag", "anträge", "anträgen", "antrags", "bescheid", "bescheide", "bescheiden", "bescheids", "verfahren",
        "verfahrens", "akte", "akten", "sache", "sachen", "formular", "formulare", "formularen", "unterlagen",
        "abteilung", "stelle", "prüfung", "prüfungen", "angelegenheit", "angelegenheiten",
    }
)


@lru_cache(maxsize=20000)
def is_admin_procedure_compound(word: str) -> bool:
    """EIN Wort aus Verwaltungsgegenstand + Verfahrensnomen ("Visa-Anträge", "Steuerbescheide", "Pass-Antrag")."""
    value = word.strip()
    if not value or any(ch.isspace() for ch in value) or value.count("-") > 1:
        return False
    if "-" in value:
        modifier, head = value.lower().split("-")
        return modifier.isalpha() and head.isalpha() and modifier in _ADMIN_SUBJECTS and head in _ADMIN_PROCEDURE_HEADS
    if not value.isalpha():
        return False
    lower = value.lower()
    for split in range(3, len(lower) - 3):
        modifier, head = lower[:split], lower[split:]
        if head in _ADMIN_PROCEDURE_HEADS and (modifier in _ADMIN_SUBJECTS or modifier.endswith("s") and modifier[:-1] in _ADMIN_SUBJECTS):
            return True
    return False


def is_technical_compound(word: str) -> bool:
    """Fachwort-Kompositum (mit oder ohne Ableitungsfuge) - kein Name. Nur fuer Treffer OHNE Anrede/Rollenwort."""
    return is_derivational_compound(word) or is_common_compound(word) or is_admin_procedure_compound(word)


# --- Fachbegriffe im Sachkontext (24.10.2026) ------------------------------------------------------------------------
# ECHTER FUND (installierte App, synthetische Finanzamt-Musterdokumente): Tabellenbeschriftungen und Satzanfaenge wie
# "Rohergebnis  212.900,00 EUR", "Zahlungsstatus pruefen", "Bezirksfinanzamt / Gewerbesteuerstelle", "Festgesetzte
# Gewerbesteuer", "Die Gesellschaft ist ..." wurden als Person/Organisation/Ort maskiert. Schrieb die KI in ihrer Antwort
# diese Alltagswoerter, blockierte die (unveraenderte) Leck-Pruefung jede Antwort. Die wortbezogenen Signale trennen hier
# nicht (gemessen: Namensprobe "Rohergebnis" 5/5, "Berger" 1/5; Vokabular und POS des Einzelworts ebenso wenig) - deshalb
# verlangt jede Regel unten ZWEI unabhaengige Signale: eine Wortbildung, die keine Namen/Orte bildet, UND die Verwendung
# im konkreten Dokumentkontext. Bestehende Regeln und ihre red-team-geprueften Klassen bleiben unveraendert; nichts gilt
# nach Anrede, Titel oder als Feldwert hinter "Bezeichnung:". Bekannte Beteiligte der Akte schuetzt die Abgleichsliste
# unabhaengig davon, und die Leck-Pruefung der Antwort bleibt unveraendert.
#
# Geschlossene Klasse von Kopfnomen des Rechnungs-, Steuer- und Verwaltungswesens - abstrakte Sachnomen, die keine
# Nachnamen- oder Ortsnamenbestandteile sind (bewusst NICHT -mann, -berg, -leiter, -hof, -dorf, -bauer, -wald, ...;
# gemessen: deren Namensprobe trennt nicht, "Leiter"/"Dorf"/"Bauer"/"Wald" haben Probe 0).
_CONTEXT_TERM_HEADS = frozenset(
    {
        "ergebnis", "ergebnisse", "ergebnisses", "status", "stelle", "stellen", "amt", "amts", "ämter", "betrag",
        "beträge", "betrags", "wert", "werte", "werts", "daten", "satz", "sätze", "ertrag", "erträge", "aufwand",
        "aufwendungen", "einkommen", "einkünfte", "umsatz", "umsätze", "gewinn", "verlust", "verluste", "saldo",
        "bilanz", "steuer", "steuern", "abgabe", "abgaben", "zuschlag", "zuschläge", "rückstand", "rückstände",
        "guthaben", "anteil", "anteile", "kapital", "posten", "position", "positionen", "konto", "kennzahl",
        "kennzahlen", "aufstellung", "auswertung", "festsetzung", "veranlagung", "erstattung", "vorauszahlung",
        "vorauszahlungen", "nachzahlung", "einnahmen", "ausgaben", "kosten", "zinsen", "gebühr", "gebühren",
    }
)
# Kurze Kopfnomen verlangen einen laengeren Vorderteil: "Sie|wert" ist ein realer Nachname ("Siewert"), "Roh|ergebnis" nicht.
_LONG_HEAD = 6
# Adjektivisch gebrauchtes Partizip II ("fest|ge|setz|t|es", "an|ge|forder|t|e", "aus|ge|wies|en|e") - Namen tragen keine
# Adjektivendung. Nur attributiv (ein weiteres Wort folgt in derselben Zeile), siehe `is_contextual_technical_term`.
_PARTICIPLE_ADJECTIVE = re.compile(r"(?:[a-zäöü]{2,8})?ge[a-zäöüß]{2,}(?:t|et|en)(?:e|es|er|en|em)")
_ADJECTIVE_ENDING = re.compile(r"(?:e|es|er|en|em)$")
# Beschriftung eines Zahlenfelds: Zellen-/Zeilentrenner und dann ein Zahlenwert ("Rohergebnis  212.900,00 EUR",
# "Nebenkosten (Musterwert)\n120,00 EUR").
_NUMERIC_VALUE_AFTER = re.compile(r"\)?(?:[ \t]{2,}|\t|[ \t]*\|[ \t]*|[ \t]*\n[ \t]*)[-+]?\d")
_SALUTATION_OR_TITLE_BEFORE = frozenset({"herr", "herrn", "frau", "dr", "prof"})
_FINITE_VERB_TAGS = frozenset({"VVFIN", "VAFIN", "VMFIN"})
_AGENT_PREPOSITIONS = frozenset({"von", "durch", "mit", "bei", "für"})


@lru_cache(maxsize=20000)
def _known_word_chain(part: str, depth: int = 0) -> bool:
    """`part` ist ein dem Modell bekanntes Wort (ggf. ohne Fugenelement) oder aus hoechstens drei solchen Woertern
    zusammengesetzt ("Gewerbe|steuer", "Bezirk|s|finanz")."""
    if len(part) < 3:
        return False
    forms = [part] + [part[: -len(joint)] for joint in ("es", "en", "s", "n") if part.endswith(joint) and len(part) - len(joint) >= 3]
    if any(_model_knows(form) for form in forms):
        return True
    if depth >= 2:
        return False
    return any(
        _known_word_chain(part[:split], depth + 1) and _known_word_chain(part[split:], depth + 1)
        for split in range(3, len(part) - 2)
    )


@lru_cache(maxsize=20000)
def is_context_term_compound(word: str) -> bool:
    """Vorderteil aus bekannten Woertern + Kopfnomen aus `_CONTEXT_TERM_HEADS` ("Roh|ergebnis", "Zahlungs|status",
    "Gewerbesteuer|stelle", "Bezirksfinanz|amt", "Steuermess|betrag"). Allein KEIN Freigabegrund - nur zusammen mit dem
    Kontextsignal in `is_contextual_technical_term`."""
    if not word.isalpha() or len(word) < 7:
        return False
    lower = word.lower()
    for split in range(3, len(lower) - 2):
        head = lower[split:]
        if head not in _CONTEXT_TERM_HEADS:
            continue
        if split < (3 if len(head) >= _LONG_HEAD else _MIN_COMPOUND_MODIFIER):
            continue
        if _known_word_chain(word[:split]):
            return True
    return False


def is_contextual_technical_term(text: str, start: int, end: int) -> bool:
    """EIN Wort an [start, end) ist im konkreten Kontext ein Fachbegriff, kein Name (siehe Kommentarblock oben).

    Verlangt (1) eine Wortbildung, die keine Namen bildet (`is_context_term_compound` oder adjektivisches Partizip) UND
    (2) ein Kontextsignal: Gattungsnomen/attributives Adjektiv laut Tagger im Dokumentsatz, oder Beschriftung direkt vor
    einem Zahlenwert. Nie nach Anrede/Titel und nie als Feldwert hinter einem Doppelpunkt."""
    word = text[start:end]
    if not word.isalpha() or not word[0].isupper() or word.isupper():
        return False
    doc = _analysis_doc(text)
    span = doc.char_span(start, end, alignment_mode="expand")
    if span is None or len(span) != 1:
        return False
    token = span[0]
    previous = next((t for t in reversed(doc[: token.i]) if not t.is_space), None)
    if previous is not None and (previous.lower_.rstrip(".") in _SALUTATION_OR_TITLE_BEFORE or previous.text == ":"):
        return False
    following = next((t for t in doc[token.i + 1 :] if not t.is_space), None)
    same_line_word_after = (
        following is not None and following.is_alpha and "\n" not in text[end : following.idx]
        and not re.search(r"[ \t]{2,}", text[end : following.idx])
    )
    # Namensrolle im Satz: Subjekt direkt vor finitem Verb ("X hat angerufen") oder Agens nach "von/durch/mit/bei/fuer"
    # - dann gilt der Tagger-Befund "Gattungsnomen" NICHT als Kontextsignal (am Satzanfang taggt das Modell auch
    # unbekannte Namen als NN; gemessen: "Rohergebnis hat gestern angerufen" -> NN).
    # Kongruenz: ein Einzelname als Subjekt verlangt ein finites Verb im SINGULAR ("X hat angerufen"); eine Pluralform
    # ("Zahlungsstatus pruefen", formgleich mit dem Infinitiv) ist keine Namensrolle. Fehlt die Numerus-Angabe, gilt die
    # Rolle vorsichtshalber als Namensrolle.
    name_role = (
        same_line_word_after and following.tag_ in _FINITE_VERB_TAGS and "Plur" not in following.morph.get("Number")
    ) or (previous is not None and previous.lower_ in _AGENT_PREPOSITIONS)
    if _PARTICIPLE_ADJECTIVE.fullmatch(word.lower()):
        # Das Partizip selbst ("festgesetzt", "angefordert") muss ein bekanntes Wort mit mindestens acht Buchstaben sein:
        # die Form allein traefe auch den Nachnamen "Gerste" (gemessen an 5432 Korpuswoertern: einziger Namenstreffer).
        base = _ADJECTIVE_ENDING.sub("", word)
        return (
            len(base) >= 8 and _model_knows(base) and token.tag_ in ("NN", "ADJA") and same_line_word_after
            and not name_role
        )
    if not is_context_term_compound(word):
        return False
    table = _table_of(text, start, end)
    if table is not None:
        # Tabellenbeschriftung: der Tagger-Befund NN traegt hier nicht (ohne Satzkontext taggt das Modell auch
        # unbekannte Namen als NN - gemessen "Brandhuber  1.900,00 EUR" -> NN). Freigabe nur in einer Fachspalte
        # (mindestens eine ANDERE eindeutige Fachbeschriftung) und nie unter einer Personen-Ueberschrift.
        return not table[1] and _technical_column(text, table[0], table[2])
    if _NUMERIC_VALUE_AFTER.match(text, end):
        return False  # Zahlenwert ohne erkennbare Tabelle: kein Freigabegrund
    return token.tag_ == "NN" and not name_role


# --- Gattungsnomen in ASCII-Umschrift (24.10.2026) -------------------------------------------------------------------
# ECHTER FUND (installierte App, synthetisches Kontrolldokument): "Nach § 1 StVO gilt Ruecksicht." -> "[PERSON_02]". Das
# Modell kennt die Umschrift "Ruecksicht" nicht und etikettiert sie als PER (0,85); die gleichwertige Schreibweise
# "Rücksicht" ist ihm bekannt und in keiner Analyse eine Person. Bisher galt die Vereinigung beider Analysen, die
# Umschrift wurde also schlechter behandelt als dasselbe Wort mit Umlaut. Gemessen (40 synthetische Nachnamen in
# Umschrift, 8 Kontexte): ALLE Nachnamen-Umschriften sind dem Modell als Wort bekannt (dann greift diese Regel nie) oder
# bleiben in der Umlautfassung eine Person; abweichend waren nur Gattungsnomen ("Maengel", "Glaeubiger", "Haerte").
# Die Gegenprobe der ersten Fassung (nur Wort- und Satzsignale) gab jedoch den Nachnamen "Fuerst" frei: "Fürst" ist
# ebenfalls in keinem Namensrahmen eine Person, und "Anwesend war Fuerst" hat das Subjekt hinter dem Verb. Deshalb
# zusaetzlich, wie bei den uebrigen Fachwort-Regeln, eine Wortbildung, die keine Namen bildet: Abstraktsuffix der
# Umlautform (-ung, -heit, -keit, -schaft, -sicht). Freigabe verlangt ALLE Signale: Umschrift unbekannt + Umlautform
# bekannt, Abstraktsuffix, Umlautform in keinem Namensrahmen eine Person, Umlautfassung des Dokuments ohne Treffer an
# dieser Stelle (prueft der Aufrufer), Gattungsnomen-Tag im Satz und keine Namensrolle (nicht nach Anrede/Titel/"laut",
# nicht als Feldwert hinter ":", nicht in Tabellen, nicht Subjekt vor finitem Singular-Verb, nicht Agens nach
# "von/durch/mit/bei/fuer").
_SOURCE_ATTRIBUTION_BEFORE = frozenset({"laut", "lt", "gemäß", "gemäss"})
_ABSTRACT_NOUN_SUFFIX = re.compile(r"[a-zäöüß]{3,}(?:ung|heit|keit|schaft|sicht)(?:en)?")


def is_transliterated_common_noun(text: str, start: int, end: int) -> bool:
    """EIN Wort an [start, end) ist die ASCII-Umschrift eines Gattungsnomens ("Ruecksicht" = "Rücksicht") im Satzkontext."""
    word = text[start:end]
    if not word.isalpha() or not word[0].isupper() or word.isupper():
        return False
    pieces = _restore_word(word)
    if pieces is None:
        return False
    restored = "".join(ch for ch, _a, _b in pieces)
    if not _ABSTRACT_NOUN_SUFFIX.fullmatch(restored.lower()) or _name_probe_score(restored) != 0:
        return False
    doc = _analysis_doc(text)
    span = doc.char_span(start, end, alignment_mode="expand")
    if span is None or len(span) != 1 or span[0].tag_ != "NN":
        return False
    token = span[0]
    previous = next((t for t in reversed(doc[: token.i]) if not t.is_space), None)
    if previous is not None and (
        previous.lower_.rstrip(".") in _SALUTATION_OR_TITLE_BEFORE | _SOURCE_ATTRIBUTION_BEFORE
        or previous.lower_ in _AGENT_PREPOSITIONS
        or previous.text == ":"
    ):
        return False
    if _table_of(text, start, end) is not None:
        return False
    following = next((t for t in doc[token.i + 1 :] if not t.is_space), None)
    return not (
        following is not None and following.tag_ in _FINITE_VERB_TAGS and "\n" not in text[end : following.idx]
        and "Plur" not in following.morph.get("Number")
    )


def is_generic_article_noun(text: str, start: int, end: int) -> bool:
    """Artikel + EIN Gattungsnomen ("Die Gesellschaft ist seit ...") - eine allgemeine Bezugnahme, kein Name: das Nomen ist
    dem Modell als Wort bekannt, im Satz als Gattungsnomen getaggt UND in keinem neutralen Namensrahmen eine Person."""
    doc = _analysis_doc(text)
    span = doc.char_span(start, end, alignment_mode="expand")
    if span is None or len(span) != 2:
        return False
    article, noun = span[0], span[1]
    if article.lower_ not in _ARTICLES or not noun.is_alpha or noun.tag_ != "NN":
        return False
    return _model_knows(noun.text) and _name_probe_score(noun.text) == 0


# --- Im Dokument definierte Fach-Abkuerzung (24.10.2026) -------------------------------------------------------------
# "Die EÜR ist ..." wurde als Organisation maskiert; im selben Dokument steht die Langform "Einnahmenüberschussrechnung"
# (Einnahmen|überschuss|rechnung -> E Ü R). Freigabe nur mit ZWEI Signalen: (1) die Abkuerzung (3-5 Grossbuchstaben)
# ist im selben Text als Initialen EINES Kompositums aus bekannten Woertern definiert, (2) dessen Kopfwort stammt aus
# den geschlossenen Fach-Kopfklassen (Dokument-/Rechts-/Verwaltungsnomen) - ein firmennamensfoermiges Kompositum
# ("Nordbauholding" -> NBH, Kopf "holding") definiert keine freigebbare Abkuerzung. Ohne Langform im Text: maskiert.
_ABBREVIATION = re.compile(r"[A-ZÄÖÜ]{3,5}")
_LONG_FORM_CANDIDATE = re.compile(r"(?<![\wÄÖÜäöüß])[A-ZÄÖÜ][a-zäöüß]{8,}(?![\wÄÖÜäöüß])")


def _technical_head(head: str) -> bool:
    return head in _COMMON_COMPOUND_HEADS or head in _LEGAL_COMPOUND_HEADS or head in _CONTEXT_TERM_HEADS or (
        head in _ADMIN_PROCEDURE_HEADS
    )


@lru_cache(maxsize=20000)
def _compound_initialisms(word: str) -> frozenset[str]:
    """Initialen aller Zerlegungen von `word` in 2-4 bekannte Woerter (je >= 3 Buchstaben, Fugen erlaubt), deren
    letztes Wort ein Fach-Kopfnomen ist."""
    lower = word.lower()
    results: set[str] = set()

    def known(part: str) -> bool:
        return _known_word_chain(part, 2)  # Tiefe 2: nur das Wort selbst bzw. ohne Fugenelement

    def walk(position: int, initials: str) -> None:
        if len(initials) > 4:
            return
        for split in range(position + 3, len(lower) + 1):
            part = lower[position:split]
            if not known(part):
                continue
            if split == len(lower):
                if len(initials) >= 1 and _technical_head(part):
                    results.add((initials + part[0]).upper())
            else:
                walk(split, initials + part[0])

    walk(0, "")
    return frozenset(results)


def is_defined_abbreviation(text: str, value: str) -> bool:
    if not _ABBREVIATION.fullmatch(value):
        return False
    target = value.upper()
    return any(target in _compound_initialisms(word) for word in set(_LONG_FORM_CANDIDATE.findall(text)))


# --- Tabellen aus Beschriftung + Zahlenwert: Spalten teilen ihren Typ (24.10.2026) -----------------------------------
# GRENZFALL (gemessen, auch ohne die Fachbegriff-Regel): in "Lohnliste  Hochleiter  2.300,00 EUR  Brandhuber
# 1.900,00 EUR" ging "Brandhuber" im Klartext hinaus. Ursache: NER ohne Treffer, Modell-Probe 3 (< 4), Wortart NN (kein
# Eigenname) und die Rueckfrage-Rolle "tabellenzelle" verlangt den Personenhinweis in DERSELBEN Zeile
# (review._row_mentions_a_person) - in einer Lohnliste steht er aber in der Spaltenueberschrift ("Mitarbeiter", "Name")
# bzw. folgt aus den Nachbarzeilen (erkannte Personen). Prinzip wie bei Aufzaehlungen (review: "Glieder einer
# Aufzaehlung teilen ihren Typ"): die Beschriftungen EINER Tabelle teilen ihren Typ.
# - Namensspalte (Personen-Ueberschrift oder mindestens eine Nachbarbeschriftung ist eine erkannte Person bzw. ein
#   Personen-Platzhalter): Fachbegriffe werden dort NIE freigegeben, namensartige Beschriftungen zusaetzlich maskiert
#   (`detect_name_column_labels`, nur Hinzufuegen - fail-closed).
# - Die Freigabe "Beschriftung vor Zahlenwert" gilt nur in einer Fachspalte: mindestens eine ANDERE Beschriftung derselben
#   Tabelle ist eindeutig ein Fachbegriff. Ein einzelnes "Waldbetrag  500,00 EUR" ohne solche Nachbarn reicht nicht.
_CELL_SEPARATOR = re.compile(r"[ \t]*(?:\n|\|)[ \t]*|[ \t]{2,}|\t")
_NUMERIC_CELL = re.compile(r"[-+]?\d[\d.]*(?:,\d+)?(?:[ \t]*(?:EUR|€|%|Euro|TEUR|T€))?")
_WORD_IN_CELL = re.compile(r"[A-Za-zÄÖÜäöüß]+")
# Spaltenueberschriften, unter denen Personen stehen (Bedeutung der Spalte, kein Freigabekriterium - die Liste schaltet
# ausschliesslich ZUSAETZLICHEN Schutz ein).
_PERSON_COLUMN_WORDS = frozenset(
    {
        "name", "namen", "vorname", "nachname", "mitarbeiter", "mitarbeiterin", "mitarbeitende", "arbeitnehmer",
        "arbeitnehmerin", "beschäftigte", "beschäftigter", "personal", "lohnliste", "gehaltsliste", "lohnjournal",
        "empfänger", "empfängerin", "zahlungsempfänger", "gläubiger", "gläubigerin", "schuldner", "schuldnerin",
        "gesellschafter", "gesellschafterin", "mieter", "mieterin", "vermieter", "vermieterin", "erbe", "erben",
        "beteiligte", "beteiligter", "kunde", "kundin", "kunden", "mandant", "mandantin", "zeuge", "zeugin", "person",
        "personen", "eigentümer", "eigentümerin", "teilnehmer", "teilnehmerin", "bewohner", "bewohnerin",
    }
)
_PERSON_PLACEHOLDER_IN_CELL = re.compile(r"\[(?:PERSON|BETEILIGTER|MANDANT|GEGNER|ANWALT)_\d+\]")
_DOCUMENT_TYPE_MARKER = re.compile(r"\[[A-Za-zÄÖÜäöüß \-]+\][ \t]+")  # nie ein Platzhalter (die tragen "_" und Ziffern)
_MAX_LABEL_WORDS = 6
_ANY_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")
_SENTENCE_END = re.compile(r"[.!?][\"“”']?\s*$")


@lru_cache(maxsize=8)
def _label_value_tables(text: str) -> tuple[tuple[tuple[tuple[int, int], ...], bool], ...]:
    """Tabellen als ((Beschriftungen als (start, end)), Personen-Ueberschrift). Eine Tabelle ist eine Folge von
    Beschriftung -> Zahlenwert mit hoechstens einer Zwischenzelle; Ueberschrift = bis zu drei Zellen davor."""
    cells: list[tuple[int, int]] = []
    position = 0
    for match in itertools.chain(_CELL_SEPARATOR.finditer(text), [None]):
        cut = len(text) if match is None else match.start()
        segment = text[position:cut]
        if segment.strip():
            lead = len(segment) - len(segment.lstrip())
            # Dokument-Typkopf der Kontextaufbereitung ("[Lohnliste] ") gehoert nicht zur Zelle
            marker = _DOCUMENT_TYPE_MARKER.match(segment, lead)
            if marker is not None and segment[marker.end() :].strip():
                lead = marker.end()
            cells.append((position + lead, position + len(segment.rstrip())))
        if match is not None:
            position = match.end()

    def numeric(index: int) -> bool:
        return bool(_NUMERIC_CELL.fullmatch(text[cells[index][0] : cells[index][1]]))

    def label(index: int) -> bool:
        value = text[cells[index][0] : cells[index][1]]
        return (
            not any(ch.isdigit() for ch in value) and bool(_WORD_IN_CELL.search(value))
            and len(value.split()) <= _MAX_LABEL_WORDS
        ) or bool(_PERSON_PLACEHOLDER_IN_CELL.fullmatch(value))

    pairs = [i for i in range(len(cells) - 1) if label(i) and numeric(i + 1)]
    tables: list[tuple[tuple[tuple[int, int], ...], bool]] = []
    group: list[int] = []
    for i in pairs:
        if group and i - (group[-1] + 1) > 2:
            tables.append(_finish_table(text, cells, group))
            group = []
        group.append(i)
    if group:
        tables.append(_finish_table(text, cells, group))
    return tuple(tables)


def _finish_table(text: str, cells: list[tuple[int, int]], group: list[int]) -> tuple[tuple[tuple[int, int], ...], bool]:
    header_words: set[str] = set()
    for index in range(group[0] - 1, max(group[0] - 4, -1), -1):
        value = text[cells[index][0] : cells[index][1]]
        if _NUMERIC_CELL.fullmatch(value):
            break
        # Nur kurze Beschriftungszellen sind Ueberschriften - ein Fliesstextsatz davor nicht. Platzhalter zaehlen nie als
        # Ueberschriftswort (ECHTER FUND, installierter Build 24.10.2026: im Restrisiko-Scan lieferte "... von
        # [PERSON_02] vom 16.03.2024." das Wort "PERSON" -> Fachtabelle galt als Namensspalte -> Anfrage blockiert).
        if len(value.split()) > _MAX_LABEL_WORDS or _SENTENCE_END.search(value):
            continue
        header_words |= {w.lower() for w in _WORD_IN_CELL.findall(_ANY_PLACEHOLDER.sub(" ", value))}
    return tuple(cells[i] for i in group), bool(header_words & _PERSON_COLUMN_WORDS)


def _table_of(text: str, start: int, end: int):
    """(Beschriftungen, Personen-Ueberschrift, eigene Beschriftung) der Tabelle, deren Beschriftung [start, end) enthaelt."""
    for labels, person_header in _label_value_tables(text):
        for label in labels:
            if label[0] <= start and end <= label[1]:
                return labels, person_header, label
    return None


def _clearly_technical_label(value: str) -> bool:
    """Beschriftung, die unabhaengig vom Kontext ein Fachbegriff ist: enthaelt ein kleingeschriebenes Wort
    ("Festgesetztes zu versteuerndes Einkommen") oder besteht aus Fachwort-Komposita/bekannten Woertern ohne
    Namenslesart ("Gewerbeertrag", "Hebesatz", "Personalaufwand")."""
    words = _WORD_IN_CELL.findall(value)
    if not words or _PERSON_PLACEHOLDER_IN_CELL.search(value):
        return False
    if any(w[0].islower() for w in words):
        return True
    return all(
        is_technical_compound(w) or is_context_term_compound(w) or (_model_knows(w) and _name_probe_score(w) == 0)
        for w in words
    )


def _technical_column(text: str, labels, own_label) -> bool:
    """Mindestens eine ANDERE Beschriftung derselben Tabelle ist eindeutig ein Fachbegriff."""
    return any(label != own_label and _clearly_technical_label(text[label[0] : label[1]]) for label in labels)


def _label_name_like(value: str) -> bool:
    """Beschriftung, die ein Name sein kann: 1-3 grossgeschriebene Woerter ohne Kleinschreibung; das letzte Wort ist kein
    bestehender Fachbegriff und kein bekanntes Wort ohne Namenslesart."""
    words = value.split()
    if not 1 <= len(words) <= 3 or not all(w.replace("-", "").isalpha() and w[0].isupper() and not w.isupper() for w in words):
        return False
    last = words[-1]
    if last.lower() in _NEVER_ENTITY_WORDS or last.lower() in _PERSON_COLUMN_WORDS or is_technical_compound(last):
        return False
    return not (_model_knows(last) and _name_probe_score(last) == 0)


def detect_name_column_labels(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Namensartige Beschriftungen einer Namensspalte (siehe Kommentarblock oben) als Person - NUR Hinzufuegen."""
    added: list[DetectedSpan] = []
    for labels, person_header in _label_value_tables(text):
        # Nur ECHTE Personentreffer zaehlen als Nachbar-Evidenz, keine Platzhalter (ECHTER FUND, installierter Build
        # 24.10.2026): ein vorsichtshalber bzw. nach Rueckfrage maskierter Fachbegriff ("Umsatzerloese" ->
        # "[PERSON_02]") machte im Restrisiko-Scan die Fachtabelle zur Namensspalte -> Anfrage blockiert. Der erste
        # Durchlauf wertet die echten Nachbarn bereits aus und maskiert; der Restrisiko-Scan sieht so dieselbe Evidenz.
        person_sibling = any(
            any(s.category == "person" and a <= s.start and s.end <= b for s in spans) for a, b in labels
        )
        if not (person_header or person_sibling):
            continue
        for a, b in labels:
            if any(s.start < b and a < s.end for s in spans) or not _label_name_like(text[a:b]):
                continue
            added.append(DetectedSpan(category="person", start=a, end=b, value=text[a:b]))
    return added


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


# §49 (16.10.2026, pseudonymization_v1 X02 "K a r l  B r e n n e r", P08 "Kar1 Brenn3r" im Klartext): gesperrt
# gesetzte Namen (Briefkopf-Typografie, gezielte Umgehung) und OCR-Ziffern in Namen erkennt das Modell nicht. Zweite
# Analysefassung nach dem Muster von `_restore_for_analysis`: Einzelbuchstaben-Folgen zusammengezogen, typische
# OCR-Ziffern in sonst alphabetischen Woertern durch Buchstaben ersetzt. Uebernommen werden NUR Personen-Treffer, die
# eine geaenderte Stelle beruehren - die Variante kann keine Treffer an unveraenderten Stellen erzeugen.
_SPACED_LETTER_WORD = re.compile(r"(?<!\S)[A-Za-zÄÖÜäöüß](?: [A-Za-zÄÖÜäöüß]){2,}(?!\S)")
_OCR_DIGIT_LETTERS = {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "8": "b"}
_OCR_DIGIT_WORD = re.compile(r"(?<![\w-])[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9]{3,}(?![\w-])")


def _ocr_digit_word(word: str) -> bool:
    """1-2 OCR-typische Ziffern in einem sonst alphabetischen Wort mit mindestens 3 Buchstaben ("Kar1", "Brenn3r")."""
    digits = [ch for ch in word if ch.isdigit()]
    letters = sum(ch.isalpha() for ch in word)
    return 1 <= len(digits) <= 2 and letters >= 3 and all(ch in _OCR_DIGIT_LETTERS for ch in digits)


def _ocr_variant_for_analysis(text: str) -> tuple[str, list[int], list[int], list[tuple[int, int]]] | None:
    """(Analysetext, Start-/Ende-Index je Zeichen im Original, geaenderte Originalbereiche) oder None."""
    changes: list[tuple[int, int, str]] = []  # (start, end, ersetzter Text) im Original, ueberschneidungsfrei
    for match in _SPACED_LETTER_WORD.finditer(text):
        changes.append((match.start(), match.end(), "spaced"))
    for match in _OCR_DIGIT_WORD.finditer(text):
        if _ocr_digit_word(match.group()) and not any(a < match.end() and match.start() < b for a, b, _ in changes):
            changes.append((match.start(), match.end(), "digits"))
    if not changes:
        return None
    changes.sort()
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    position = 0
    previous_kind = None
    for start, end, kind in changes:
        gap = text[position:start]
        if kind == "spaced" and previous_kind == "spaced" and gap and not gap.strip() and "\n" not in gap:
            # Zwei gesperrte Woerter hintereinander ("K a r l  B r e n n e r"): Doppelleerzeichen -> ein Leerzeichen,
            # damit das Modell den ganzen Namen sieht (sonst nur "Karl", Nachname bliebe im Klartext).
            out.append(" "); starts.append(position); ends.append(start)
        else:
            for i in range(position, start):
                out.append(text[i]); starts.append(i); ends.append(i + 1)
        previous_kind = kind
        for i in range(start, end):
            ch = text[i]
            if kind == "spaced" and ch == " ":
                continue  # Einzelleerzeichen zwischen den Buchstaben entfaellt
            if kind == "digits" and ch in _OCR_DIGIT_LETTERS:
                ch = _OCR_DIGIT_LETTERS[ch]
            out.append(ch); starts.append(i); ends.append(i + 1)
        position = end
    for i in range(position, len(text)):
        out.append(text[i]); starts.append(i); ends.append(i + 1)
    return "".join(out), starts, ends, [(a, b) for a, b, _ in changes]


# §50 (Qualitaetsmeldung seed-008, "bei Rot" -> [ORT_01]): das Sprachmodell selbst setzt "Rot" als Ort (es gibt Orte
# dieses Namens). Die gegengepruefte Regel aus §45.1 (review.py) galt nur fuer Rueckfrage-Kandidaten, nicht fuer
# NER-Treffer. Gleiche, enge Bedingung: "bei" + Farbe + UNMITTELBAR ein Fahr-/Querungsvorgang ("bei Rot in die
# Kreuzung", "bei Grün über die Kreuzung", "bei Gelb gefahren"). "überquerte die Fahrbahn bei Rot." bleibt maskiert
# (Ortslesart "bei Rot" sprachlich moeglich), ebenso "übernachtete bei Rot, direkt an der Kreuzung".
TRAFFIC_LIGHT_COLOURS = frozenset({"Grün", "Rot", "Gelb"})
TRAFFIC_MANOEUVRE_AFTER = re.compile(
    r"[ \t]+(?:"
    r"(?:über|in)[ \t]+(?:die|den)[ \t]+(?:\w*[Kk]reuzung|Ampel|Fahrbahn|Einmündung|Haltelinie|Fußgängerfurt|"
    r"Fussgängerfurt)"
    r"|(?:los|weiter|durch)?gefahren|überquert"
    r")\b"
)
_BEI_BEFORE = re.compile(r"(?:^|[^\w])[Bb]ei[ \t]+$")


def is_traffic_light_colour_at(text: str, start: int, end: int) -> bool:
    return (
        text[start:end] in TRAFFIC_LIGHT_COLOURS
        and bool(_BEI_BEFORE.search(text[max(0, start - 8) : start]))
        and bool(TRAFFIC_MANOEUVRE_AFTER.match(text, end))
    )


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
    all_results = analyzer.analyze(
        text=analysis_text,
        language="de",
        entities=[*_REQUESTED_ENTITIES, *_ID_ENTITIES, *_ID_LABEL_PATTERNS, "URL"],
        score_threshold=0.0,  # Schwelle unten je Art: Namen/Kennnummern _MIN_SCORE, bezeichnete Kennnummern Nachbarschaft
    )
    # Kennnummern (offizielle deutsche Recognizer) laufen im selben Aufruf mit (kein zweiter spaCy-Lauf), aber NICHT durch
    # die Namensfilter unten - "P0550M7M2" fiele sonst unter den Formatfilter fuer interne Tokens.
    id_spans = _id_spans(all_results, text)
    results = [r for r in all_results if r.entity_type in _REQUESTED_ENTITIES and r.score >= _MIN_SCORE]

    spans = _collect_spans(results, text, None)
    restored = _restore_for_analysis(analysis_text)
    if restored is not None:
        restored_text, starts, ends = restored
        restored_results = analyzer.analyze(
            text=restored_text, language="de", entities=list(_REQUESTED_ENTITIES), score_threshold=_MIN_SCORE
        )
        # Gleichbehandlung gleichwertiger Schreibweisen: ein Personen-Treffer NUR der Originalanalyse auf der Umschrift
        # eines Gattungsnomens entfaellt, wenn die Umlautfassung an dieser Stelle nichts findet (siehe
        # `is_transliterated_common_noun`). Spaetere Zweitsignale koennen die Stelle weiterhin maskieren.
        restored_ranges = [
            (starts[r.start], ends[r.end - 1]) for r in restored_results if 0 <= r.start < r.end <= len(starts)
        ]
        spans = [
            sp for sp in spans
            if not (
                sp.category == "person"
                and not any(a < sp.end and sp.start < b for a, b in restored_ranges)
                and is_transliterated_common_noun(text, sp.start, sp.end)
            )
        ]
        known = {(sp.category, sp.start, sp.end) for sp in spans}
        spans.extend(
            sp for sp in _collect_spans(restored_results, text, (starts, ends)) if (sp.category, sp.start, sp.end) not in known
        )
    spans = normalize_organisation_spans(text, trim_sender_phrases(text, normalize_ner_span_boundaries(text, spans)))
    # §49: Varianten-Treffer erst NACH der Grenzen-Normalisierung - die trennt an Doppelleerzeichen (Tabellenzelle) und
    # schnitte "K a r l  B r e n n e r" sonst auf "K a r l" zurueck.
    variant = _ocr_variant_for_analysis(analysis_text)
    if variant is not None:
        variant_text, v_starts, v_ends, changed = variant
        variant_results = analyzer.analyze(
            text=variant_text, language="de", entities=["PERSON"], score_threshold=_MIN_SCORE
        )
        for sp in _collect_spans(variant_results, text, (v_starts, v_ends)):
            if sp.category != "person" or not any(sp.start < b and a < sp.end for a, b in changed):
                continue
            overlapping = [o for o in spans if sp.start < o.end and o.start < sp.end]
            # Ein Varianten-Treffer, der vorhandene Treffer VOLLSTAENDIG umschliesst, ersetzt sie ("Gu5tav Wende1in": die
            # Originalanalyse sah nur "Wende1" als Organisation - Vorname und Rest blieben lesbar). Nur mehr Schutz:
            # ragt ein vorhandener Treffer ueber den Varianten-Treffer hinaus, bleibt alles wie es ist.
            if all(sp.start <= o.start and o.end <= sp.end for o in overlapping):
                spans = [o for o in spans if o not in overlapping] + [sp]
    titled = _titled_name_spans(text)
    weak_titled = {(span.start, span.end) for span, strong in titled if not strong}
    for detector in (
        lambda _text: [span for span, _strong in titled], detect_document_convention_names, detect_feminine_context_names,
        detect_unlabelled_proper_nouns, detect_probed_names, detect_article_names,
    ):
        spans.extend(
            extra
            for extra in detector(text)
            if not any(extra.start < other.end and other.start < extra.end for other in spans)
        )
    # Namensspalten (Spalten teilen ihren Typ): namensartige Beschriftungen neben erkannten Personen bzw. unter einer
    # Personen-Ueberschrift - nur Hinzufuegen (fail-closed), siehe `detect_name_column_labels`.
    spans.extend(detect_name_column_labels(text, spans))
    # Schwache Rollenwort-Treffer werden nur weitergetragen, wenn ein anderes Signal dieselbe Stelle ebenfalls traegt.
    spans = drop_possessive_kinship(drop_statute_abbreviations(text, spans))
    spans = [s for s in spans if not is_traffic_light_colour_at(text, s.start, s.end)]  # §50
    return [*propagate_person_components(text, inverted_name_lines(text, spans), exclude=weak_titled), *id_spans]


# Possessiv + Verwandtschaftswort (§34.15, Legal-E2E-Benchmark: "Mein Sohn soll nichts erhalten." -> "[PERSON_nn] soll
# nichts erhalten", das Weitertragen maskierte zusaetzlich jedes "Mein"). Nur wenn der GANZE Treffer genau aus diesen zwei
# Woertern besteht: "Mein Sohn Lukas Brandhuber" ist ein laengerer Treffer und bleibt geschuetzt.
def drop_possessive_kinship(spans: list[DetectedSpan]) -> list[DetectedSpan]:
    return [s for s in spans if not (s.category == "person" and POSSESSIVE_KINSHIP.fullmatch(s.value.strip()))]


# Gesetzesabkuerzung direkt nach einem Normzitat (Folgezyklus 13.10.2026, scripts/bench_statute_citations.py: "nach § 7 StVG"
# -> "§ 7 [PERSON_01]"; 37 von 180 Zitaten mit StVO, StPO, BetrVG, ArbZG, ... verloren). Abkuerzungen mit Kleinbuchstaben
# sind keine Versalwoerter und fielen daher unter die Namensdetektoren. Kontextgebunden: nur ein Einwort-Treffer in
# Abkuerzungsform (mindestens zwei Grossbuchstaben, hoechstens 12 Zeichen), der UNMITTELBAR auf "§ n"/"§§ n, m"/"Art. n"
# (ggf. mit Abs./Satz/Nr.) folgt. Ein Name folgt dort nicht.
_NORM_CITATION_BEFORE = re.compile(
    r"(?:§§?|Art\.|Artikel)[ \t]*\d+[a-z]?(?:[ \t]*(?:,|und|bis|-|–)[ \t]*\d+[a-z]?)*"
    r"(?:[ \t]*(?:Abs\.|Absatz|S\.|Satz|Nr\.|Ziff\.|lit\.)[ \t]*\d+[a-z]?)*[ \t]+$"
)
_STATUTE_ABBREVIATION = re.compile(r"^(?=(?:[^A-ZÄÖÜ]*[A-ZÄÖÜ]){2})[A-ZÄÖÜ][A-Za-zÄÖÜäöü]{1,11}$")


# Wertbezogen (24.10.2026, Befund "Straßenverkehrs-Ordnung (StVO); nach § 1 StVO ..."): der Wortart-Tagger
# (`detect_unlabelled_proper_nouns`) markierte BEIDE Vorkommen als Person; die positionsbezogene Regel oben entfernte nur
# das Vorkommen hinter "§ 1", und das Weitertragen der Namensbestandteile (`propagate_person_components`) uebertrug den
# verbliebenen Klammertreffer zurueck auf "§ 1 StVO". Steht eine Abkuerzung im SELBEN Text unmittelbar hinter einem
# Normzitat, ist dieser Wert in diesem Text eine Gesetzesabkuerzung - dann entfaellt JEDER Treffer mit genau diesem Wert.
# Ohne Normzitat im Text aendert sich nichts (keine pauschale Freigabe von Abkuerzungen oder Klammerinhalten).
_CITED_STATUTE_ABBREVIATION = re.compile(
    _NORM_CITATION_BEFORE.pattern.removesuffix("$") + r"(?P<abbr>[A-ZÄÖÜ][A-Za-zÄÖÜäöü]{1,11})(?![\wÄÖÜäöüß])"
)


def cited_statute_abbreviations(text: str) -> frozenset[str]:
    """Abkuerzungen, die im Text unmittelbar hinter einem Normzitat stehen ("§ 1 StVO", "§§ 433, 434 BGB")."""
    return frozenset(
        match.group("abbr")
        for match in _CITED_STATUTE_ABBREVIATION.finditer(text)
        if _STATUTE_ABBREVIATION.match(match.group("abbr"))
    )


def drop_statute_abbreviations(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    analysis = _neutralize_internal_tokens(text)
    cited = cited_statute_abbreviations(analysis)
    return [
        span
        for span in spans
        if not (
            _STATUTE_ABBREVIATION.match(span.value)
            and (span.value in cited or _NORM_CITATION_BEFORE.search(analysis[max(0, span.start - 60) : span.start]))
        )
    ]


_PROTOCOL_LABELS = frozenset({"Assistent", "Anwalt"})


@lru_cache(maxsize=2)
def _analysis_doc(text: str):
    """Geparste, neutralisierte Analysefassung (laengengleich zum Original) - ein Parse fuer alle Zweitsignale."""
    return _get_analyzer_engine().nlp_engine.nlp["de"](_neutralize_internal_tokens(text))


# Modell-Probing (Folgelauf 12.10.2026): Ein Wort, das das NER-Modell in der Mehrzahl neutraler Rahmensaetze als PERSON
# etikettiert, ist namensartig - unabhaengig vom Kontext im Dokument. Gemessen an 55 Nachnamen / 153 Fachwoertern:
# >= 4 von 5 Rahmen: 36/55 Namen, 2/153 Fachwoerter (beide ohnehin als Person bekannt); in 19 synthetischen Dokumenten
# (176 Tsd. Zeichen, 145 Kandidaten) 0 zusaetzliche Treffer. Kein Wortlisten-Ersatz fuer Gattungsworte (Richter, Fuerst).
_NAME_PROBE_FRAMES = (
    "Herr {X} hat unterschrieben.",
    "Dazu erklärt {X}, es sei so.",
    "{X} hat angerufen.",
    "Der Zeuge {X} wurde geladen.",
    "Name: {X}, Zeuge",
)
_NAME_PROBE_MIN_SCORE = 4
_NAME_PROBE_MIN_LENGTH = 5


@lru_cache(maxsize=20000)
def _name_probe_frames(word: str) -> tuple[bool, ...]:
    """Je Rahmensatz (Reihenfolge wie `_NAME_PROBE_FRAMES`): etikettiert das Modell das Einzelwort als PER?"""
    texts = [frame.format(X=word) for frame in _NAME_PROBE_FRAMES]
    result = []
    for text, doc in zip(texts, _get_analyzer_engine().nlp_engine.nlp["de"].pipe(texts, batch_size=8)):
        start = text.index(word)
        result.append(any(token.idx == start and token.ent_type_ == "PER" for token in doc))
    return tuple(result)


def _name_probe_score(word: str) -> int:
    """Anzahl der Rahmensaetze (0-5), in denen das Modell das Einzelwort als PER etikettiert."""
    return sum(_name_probe_frames(word))


def detect_probed_names(text: str) -> list[DetectedSpan]:
    """Zweitsignal: bare, grossgeschriebene Woerter, die das Modell im Rahmensatz-Test mindestens `_NAME_PROBE_MIN_SCORE`-mal
    als Person etikettiert. Kandidaten: alphabetisch, >= 5 Buchstaben, keine Abkuerzung, keine Ganzwort-Ausnahme und kein
    Artikel/Adjektiv/Zahl/Pronomen unmittelbar davor. Fehlalarm = zusaetzlicher Platzhalter (fail-closed), nie ein Verlust."""
    if not text or not text.strip():
        return []
    doc = _analysis_doc(text)
    spans: list[DetectedSpan] = []
    for token in doc:
        word = token.text
        if len(word) < _NAME_PROBE_MIN_LENGTH or not word.isalpha() or not word[0].isupper() or word.isupper():
            continue
        if word.lower() in _NEVER_ENTITY_WORDS or word in _PROTOCOL_LABELS or is_technical_compound(word):
            continue
        if is_contextual_technical_term(text, token.idx, token.idx + len(word)):
            continue  # dieselbe Kontextregel wie in `_collect_spans` - sonst kaeme der Fachbegriff hier zurueck
        if is_role_label_before_person(text, token.idx + len(word)):
            continue
        previous = doc[token.i - 1] if token.i else None
        if any(c.i < token.i and c.pos_ in _NOUN_MODIFIER_POS for c in token.children) or (
            previous is not None and previous.pos_ in _NOUN_MODIFIER_POS
        ):
            continue
        if _name_probe_score(word) >= _NAME_PROBE_MIN_SCORE:
            spans.append(DetectedSpan(category="person", start=token.idx, end=token.idx + len(word), value=text[token.idx : token.idx + len(word)]))
    return spans


# --- Anrede-/Rollenkontext und Folgeerwaehnungen (13.10.2026, scripts/bench_name_followups.py) ------------------------
# ECHTER FUND (getrennter Benchmark mit neuen Namen): "Herr Sommer", "Zeugin Koenig", "Rechtsanwaeltin Sommer" gingen
# VOLLSTAENDIG im Klartext in den Payload - Nachnamen, die zugleich Gattungswoerter sind, etikettiert die NER nicht, und die
# Rueckfrage-Rolle "nach Rollenwort" verlangt zusaetzlich die Modell-Probe "Herr X hat unterschrieben" (Fachwort-Filter),
# an der solche Namen scheitern. Eine Anrede ist dagegen ein eindeutiges sprachliches Signal.
# Zweite Fassung nach unabhaengigem Red-Team-Review: die erste (Regex: JEDES grossgeschriebene Wort nach einem Rollenwort)
# maskierte in Nebensaetzen das Objekt ("dass der Mieter Maengel anzeigt" -> "Maengel" im ganzen Text) und verfehlte
# "Die Zeugin X" am Satzanfang, "Herr<Umbruch>X", "Hr."/"Fr.", "Herr von X".
#  * STARKE Anreden (Herr/Herrn/Frau/Hr./Hrn./Fr./Eheleute/Ehepaar/Familie/Fam./gez.): das naechste Wort ist ein Name -
#    auch ueber Zeilen-/Mehrfach-Leerzeichengrenzen der Dokumentaufbereitung; "Dr."/"Prof.", Namenspartikel ("von") und
#    Amts-/Rollenbezeichnungen ("Frau Rechtsanwaeltin Weber", "Herr Vorsitzender") werden uebersprungen.
#  * ROLLENWOERTER (Zeugin, Mandant, ...): nur wenn der Parser das Wort als Teil derselben Nominalgruppe an das
#    Rollenwort haengt ("Zeugin Licht": nk -> Zeugin; "der Mieter Maengel anzeigt": Maengel = Objekt des Verbs -> nein).
#    Diese schwaecheren Treffer werden NICHT im Text weitergetragen (propagate_person_components).
_STRONG_TITLES = frozenset({"herr", "herrn", "frau", "hr.", "hrn.", "fr.", "eheleute", "ehepaar", "familie", "fam.", "gez."})
_TITLE_WORDS = frozenset({"herr", "herrn", "frau", "hr.", "hrn.", "fr."})
_ACADEMIC_TITLES = frozenset({"dr.", "prof.", "dipl.-ing.", "dr.-ing.", "dr", "prof", "med.", "jur.", "rer.", "nat."})
_NAME_PARTICLES = frozenset({"von", "van", "de", "zu", "vom", "zur", "ter", "ten", "den", "der"})
_ROLE_TITLES = frozenset(
    {
        "zeuge", "zeugin", "rechtsanwalt", "rechtsanwältin", "mandant", "mandantin", "gutachter", "gutachterin",
        "sachverständiger", "sachverständige", "notar", "notarin", "geschäftsführer", "geschäftsführerin",
        "klägerin", "kläger", "beklagter", "beklagte", "richterin", "vermieterin", "vermieter", "mieterin", "mieter",
        "antragsteller", "antragstellerin", "antragsgegner", "antragsgegnerin", "schuldner", "schuldnerin",
        "gläubiger", "gläubigerin", "arbeitnehmer", "arbeitnehmerin", "arbeitgeber", "arbeitgeberin", "erbe", "erbin",
        "betreuer", "betreuerin", "kollege", "kollegin",
        # Deklinierte Formen schwacher Nomen ("den Zeugen Nebel", "dem Mandanten Hagel", "den Sachverstaendigen Zorn";
        # unabhaengiger Fallsatz, Folgezyklus 13.10.2026).
        "zeugen", "mandanten", "sachverständigen", "beklagten", "kollegen", "gutachters", "klägers", "mieters",
        "vermieters",
    }
)
# Amts-/Funktionsbezeichnungen: nach einer Anrede nie selbst ein Name ("Herr Vorsitzender", "Frau Kollegin"), werden
# uebersprungen und nie weitergetragen (Red-Team-Befund: "Herr Richter" maskierte sonst "Der Richter am Amtsgericht").
_OFFICE_TITLES = frozenset(
    {
        "vorsitzende", "vorsitzender", "präsident", "präsidentin", "staatsanwalt", "staatsanwältin", "verteidiger",
        "verteidigerin", "minister", "ministerin", "doktor", "bürgermeister", "bürgermeisterin", "richter",
        "gerichtsvollzieher", "gerichtsvollzieherin", "bundeskanzler", "bundeskanzlerin", "abgeordnete",
        "abgeordneter", "professor", "professorin", "pfarrer", "pfarrerin", "direktor", "direktorin", "amtsrichter",
        "amtsrichterin", "oberbürgermeister", "oberbürgermeisterin", "kanzler", "kanzlerin", "senator", "senatorin",
    }
)
_NOT_A_NAME = _TITLE_WORDS | _STRONG_TITLES | _ROLE_TITLES | _OFFICE_TITLES | _ACADEMIC_TITLES
_ARTICLE_BEFORE_COMMON_NOUN = frozenset(
    {
        "die", "der", "den", "dem", "des", "eine", "einer", "einen", "einem", "jede", "jeder", "diese", "dieser",
        "seine", "seiner", "ihre", "ihrer", "deren", "dessen", "junge", "jungen", "alte", "alten", "ältere", "älteren",
    }
)
_NAME_TOKEN = re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?$")


def _ends_noun_phrase(doc, text: str, token) -> bool:
    end = token.idx + len(token.text)
    following = next((t for t in doc[token.i + 1 :] if not t.is_space), None)
    if following is None or "\n" in text[end : following.idx] or "  " in text[end : following.idx]:
        return True
    if following.is_punct or following.lower_ in ("und", "oder", "sowie", "bzw."):
        return True
    return following.pos_ in ("VERB", "AUX") and "Fin" in following.morph.get("VerbForm")


_NON_FINITE_VERB_TAGS = ("VVPP", "VVINF", "VAPP", "VAINF", "VMPP", "VMINF", "VVIZU")


def _before_clause_final_verb(doc, text: str, token) -> bool:
    """Direkt nach dem Wort folgt der satzschliessende Verbkomplex - auch mehrteilig ("aussagen muessen", "belehrt
    worden ist"): nur infinite Verbformen, dann Nominalgruppen-/Satzende (finites Verb, Satzzeichen, Zeilengrenze)."""
    words = [t for t in doc[token.i + 1 :] if not t.is_space]
    if not words or words[0].tag_ not in _NON_FINITE_VERB_TAGS:
        return False
    last = 0
    while last + 1 < len(words) and words[last + 1].tag_ in _NON_FINITE_VERB_TAGS:
        last += 1
    return last + 1 == len(words) or _ends_noun_phrase(doc, text, words[last])


def _titled_name_spans(text: str) -> list[tuple[DetectedSpan, bool]]:
    """(Treffer, stark) - siehe Kommentarblock oben. Fuegt nur Treffer hinzu (fail-closed)."""
    if not text or not text.strip():
        return []
    doc = _analysis_doc(text)
    words = [t for t in doc if not t.is_space]
    found: list[tuple[DetectedSpan, bool]] = []
    for index, title in enumerate(words):
        lower = title.lower_
        # Tokenizer trennt bei manchen Abkuerzungen den Punkt ab ("gez" "." "Sommer").
        dotted = index + 1 < len(words) and words[index + 1].text == "." and words[index + 1].idx == title.idx + len(title.text)
        if dotted and lower + "." in _STRONG_TITLES:
            lower = lower + "."
        strong = lower in _STRONG_TITLES
        role = lower in _ROLE_TITLES
        if role and not strong:
            # Rollenwort OHNE Artikel/Bestimmer davor ("erschien Zeugin Sommer", "Rechtsanwaeltin Sommer", Kopfzeile
            # "Mandant Horn") ist titelartig wie "Herr"/"Frau" - Objekte folgen nur auf Rollenwoerter MIT eigenem Artikel
            # ("der Mieter Maengel anzeigt"); dort gelten unten die strengeren Bedingungen.
            before = words[index - 1] if index else None
            if before is None or before.pos_ not in ("DET", "PRON", "ADJ", "NUM") and before.lower_ not in _ARTICLE_BEFORE_COMMON_NOUN:
                strong = True
        if not (strong or role) or not (title.text[:1].isupper() or lower == "gez."):
            continue
        previous = words[index - 1] if index else None
        if lower in ("frau", "herr", "familie") and previous is not None and previous.lower_ in _ARTICLE_BEFORE_COMMON_NOUN:
            continue  # "die Frau des Mieters", "der Herr des Verfahrens", "die Familie des Klaegers"
        # Zwischen Titel und Name nur Leerraum (auch Umbruch/Mehrfach-Leerzeichen) - kein Satzzeichen, kein Zellwechsel.
        candidate = None
        previous_skipped = ""
        previous_end = (words[index + 1] if dotted else title).idx + len((words[index + 1] if dotted else title).text)
        for following in words[index + 1 + int(dotted) : index + 7]:
            # Abstand im ORIGINALtext ab Ende der Anrede bzw. des zuletzt uebersprungenen Titels. ECHTER FUND (drittes
            # Red-Team): im Restrisiko-Scan steht der Name bereits als Platzhalter, die Analysefassung macht daraus
            # Leerzeichen - "Herrn [PERSON_01] [ADRESSE_01] ... Betreff:" lieferte "Betreff" als Namen und blockierte jeden
            # Brief mit Anschriftenblock. Jetzt: kein Text/Platzhalter dazwischen, hoechstens EIN Zeilenwechsel (in der
            # Dokumentaufbereitung zwei Leerzeichen).
            gap = text[previous_end : following.idx]
            if gap.strip() or len(gap) > 2 or gap.count("\n") > 1:
                break
            if lower in _ROLE_TITLES and gap != " ":
                break  # artikelloses Rollenwort nur in derselben Zeile ("Mieter  Wohnung" ist ein Tabellenkopf)
            previous_end = following.idx + len(following.text)
            low = following.lower_
            # "der"/"den" nur als Teil von "van der"/"von der" - sonst Artikel ("ist Mieterin der Wohnung").
            particle = low in _NAME_PARTICLES and (low not in ("der", "den") or previous_skipped in ("van", "von", "vom"))
            if low in _ACADEMIC_TITLES or particle or low in _OFFICE_TITLES or low in _ROLE_TITLES or low in _TITLE_WORDS:
                previous_skipped = low
                continue
            candidate = following
            break
        if candidate is None or not _NAME_TOKEN.match(candidate.text):
            continue
        if candidate.lower_ in _NOT_A_NAME or candidate.lower_ in _NEVER_ENTITY_WORDS or is_derivational_compound(candidate.text):
            continue
        if not strong:
            # Rollenwort: nur Bestandteil derselben Nominalgruppe ("Zeugin Licht"), nie Objekt/Ergaenzung eines Verbs.
            # Name haengt am Rollenwort ("der Zeuge Sommer": nk -> Zeuge) - NICHT umgekehrt (Fehlparse "der Sachverstaendige
            # Feuchtigkeit festgestellt": Rollenwort haengt am Fachwort).
            attached = candidate.head.i == title.i and candidate.dep_ in ("nk", "pnc", "app")
            # Apposition als zweites Subjekt desselben Verbs ("hat die Zeugin Koenig ausgesagt": beide sb von "hat").
            # Ebenso als zweites Objekt derselben Funktion ("hat den Sachverstaendigen Zorn abgelehnt": beide oa) - ein echtes
            # Objekt hat eine ANDERE Funktion als das Rollenwort ("der Mieter [sb] Maengel [oa]").
            double_subject = (
                candidate.dep_ == title.dep_
                and candidate.dep_ in ("sb", "oa", "da", "og")
                and candidate.head.i == title.head.i
            )
            # Parse-Variante "Die Zeugin Licht hat ...": Name als Subjekt, Rollenwort unverbunden (kein Subjekt).
            dangling_role = candidate.dep_ == "sb" and title.dep_ != "sb" and candidate.i == title.i + 1 and title.head.i == title.i
            if not (attached or double_subject or dangling_role):
                continue
            # Objekte haengt der Parser in verbfinalen Saetzen ebenfalls an ("der Vermieter Kaution fristgerecht
            # abgerechnet"). Ein Name schliesst die Nominalgruppe ab (finites Verb, Satzzeichen, Konjunktion, Zeilengrenze)
            # oder steht direkt vor dem satzschliessenden Partizip/Infinitiv ("... Sommer ausgesagt.") - kein Adverb dazwischen.
            if not (_ends_noun_phrase(doc, text, candidate) or _before_clause_final_verb(doc, text, candidate)):
                continue
        span = DetectedSpan(category="person", start=candidate.idx, end=candidate.idx + len(candidate.text), value=text[candidate.idx : candidate.idx + len(candidate.text)])
        found.append((span, strong))
    return found


# --- Kanzlei-/Briefkonventionen (Folgezyklus 13.10.2026, scripts/bench_letter_conventions.py) --------------------------
# Personenbezogene Feldbezeichnungen ("Ansprechpartner: Sommer") und die invertierte Namenszeile ("Sommer, Klaus") gingen
# bei Gattungswort-Nachnamen im Klartext in den Payload. Eine Rubrum-Regel ("Sommer ./. Baugesellschaft") wurde gemessen
# und VERWORFEN: der Punkt in "./." zaehlt im Imperativ-Filter als Satzende, der Restrisiko-Scan fand den Treffer auf dem
# pseudonymisierten Text dennoch -> inkonsistente Fail-Closed-Blockade.
_PERSON_FIELD_LABELS = frozenset(
    {
        "ansprechpartner", "ansprechpartnerin", "unterschrift", "sachbearbeiter", "sachbearbeiterin", "bearbeiter",
        "bearbeiterin", "unterzeichner", "unterzeichnerin", "zeuge", "zeugin", "mandant", "mandantin",
    }
)
_FIELD_VALUE = re.compile(r"(?<![\wÄÖÜäöüß])(?P<label>[A-ZÄÖÜ][a-zäöüß]+):[ \t]+(?P<name>[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)(?=[ \t]*(?:$|\n|[ \t]{2}|,))", re.MULTILINE)
_INVERTED_NAME = re.compile(
    r"(?:^|\n|[ \t]{2}|\] )(?P<last>[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?), (?P<first>[A-ZÄÖÜ][a-zäöüß]+)(?=$|\n|[ \t]{2})",
    re.MULTILINE,
)


def detect_document_convention_names(text: str) -> list[DetectedSpan]:
    """Feldwert nach personenbezogener Feldbezeichnung (Wert schliesst das Feld ab). Fuegt nur Treffer hinzu (fail-closed)."""
    if not text or not text.strip():
        return []
    analysis = _neutralize_internal_tokens(text)
    spans: list[DetectedSpan] = []

    def add(start: int, value: str) -> None:
        if value.lower() in _NOT_A_NAME or value.lower() in _NEVER_ENTITY_WORDS or is_derivational_compound(value):
            return
        spans.append(DetectedSpan(category="person", start=start, end=start + len(value), value=text[start : start + len(value)]))

    for match in _FIELD_VALUE.finditer(analysis):
        if match.group("label").lower() in _PERSON_FIELD_LABELS:
            add(match.start("name"), match.group("name"))
    return spans


def inverted_name_lines(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """"Sommer, Klaus" als eigene Zeile/Zelle: ist der Vorname bereits als Person erkannt, ist das Wort davor der Nachname."""
    analysis = _neutralize_internal_tokens(text)
    result = list(spans)
    persons = [span for span in spans if span.category == "person"]
    for match in _INVERTED_NAME.finditer(analysis):
        first_start, first_end = match.start("first"), match.end("first")
        if not any(span.start <= first_start and first_end <= span.end for span in persons):
            continue
        start, value = match.start("last"), match.group("last")
        if value.lower() in _NOT_A_NAME or any(start < other.end and other.start < start + len(value) for other in result):
            continue
        result.append(DetectedSpan(category="person", start=start, end=start + len(value), value=text[start : start + len(value)]))
    return result


def detect_titled_names(text: str) -> list[DetectedSpan]:
    """Nachname nach einer Anrede oder (syntaktisch verbunden) nach einem Rollenwort - siehe Kommentarblock oben."""
    return [span for span, _strong in _titled_name_spans(text)]


_COMPONENT_SKIP = _NOT_A_NAME | _NAME_PARTICLES


def _name_components(value: str) -> set[str]:
    parts: set[str] = set()
    for whole in re.findall(r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+(?:-[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+)*", value):
        for part in {whole, *whole.split("-")}:
            lower = part.lower()
            if len(part) < 3 or lower in _COMPONENT_SKIP or lower in _NEVER_ENTITY_WORDS or part in _PROTOCOL_LABELS:
                continue
            parts.add(part)
    return parts


def propagate_person_components(
    text: str, spans: list[DetectedSpan], *, exclude: set[tuple[int, int]] | None = None
) -> list[DetectedSpan]:
    """Folgeerwaehnungen: jeder Namensbestandteil eines erkannten Personentreffers (ohne Anrede/Titel/Amts-/Rollenwort)
    wird an allen weiteren Stellen desselben Texts ebenfalls als Person markiert - auch als Bindestrich-Bestandteil, im
    Genitiv ("Sommers") und in Versalien ("SOMMER"). ECHTER FUND: "Herr Dr. Graf ... Graf hat uebergeben" - ersetzt wurde
    nur "Herr Dr. Graf", der blosse Nachname blieb im Klartext. `exclude`: Treffer aus schwaecheren Signalen (Rollenwort),
    die nicht weitergetragen werden. Nur ganze Woerter, nur dort, wo noch kein Treffer liegt (fail-closed)."""
    components: set[str] = set()
    for span in spans:
        if span.category == "person" and (exclude is None or (span.start, span.end) not in exclude):
            components |= _name_components(span.value)
    if not components:
        return spans
    variants: set[str] = set()
    for part in components:
        variants |= {part, part + "s", part.upper()}
    result = list(spans)
    for part in sorted(variants, key=len, reverse=True):
        pattern = re.compile(r"(?<![\wÄÖÜäöüß])" + re.escape(part) + r"(?![\wÄÖÜäöüß])")
        for match in pattern.finditer(text):
            if any(match.start() < other.end and other.start < match.end() for other in result):
                continue
            result.append(DetectedSpan(category="person", start=match.start(), end=match.end(), value=match.group()))
    return result


# --- Feminin-Singular-Kontext + maskuline Personenbezeichnung (13.10.2026, scripts/bench_name_followups.py Gruppe B) --
# "Nach Ruecksprache hat die Fischer ...", "mit der Kaufmann": eine maskuline Personenbezeichnung ("Fischer", "Kaufmann")
# kann im femininen Singular nicht als Gattungswort stehen - das hiesse "die Fischerin"/"die Kauffrau". Steht sie dort,
# ist es ein (weiblich gelesener) Nachname. Der Parser liefert Genus/Numerus des Nomens nicht verlaesslich (gemessen:
# "hat die Fischer" als Plural); deshalb rein grammatisch: (a) "die X" als Subjekt eines finiten Verbs im SINGULAR, oder
# (b) Dativ-Praeposition + "der X" (der Plural-Dativ hiesse "den Xn"). Maskuline Personenbezeichnung: Endung "-mann" oder
# die movierte Form "X-in" ist dem Modell bekannt (Umlaut-Movierung nur bei Formen ohne "-er": "Koch"->"Koechin",
# "Graf"->"Graefin"; sonst "Kammer"->"Kaemmerin" - Fehlalarm auf "die Kammer").
_DATIVE_PREPOSITIONS = frozenset(
    {"mit", "von", "bei", "zu", "nach", "aus", "seit", "gegenüber", "gemäß", "laut", "neben", "hinter", "vor", "an", "auf", "in", "über", "unter", "zwischen"}
)
_UMLAUT = {"a": "ä", "o": "ö", "u": "ü"}


@lru_cache(maxsize=20000)
def _is_masculine_person_noun(word: str) -> bool:
    if len(word) > 4 and word.endswith("mann"):
        return True
    vocab = _get_analyzer_engine().nlp_engine.nlp["de"].vocab
    if vocab.has_vector(word + "in"):
        return True
    if word.endswith("er"):
        return False
    for i in range(len(word) - 1, -1, -1):
        if word[i] in _UMLAUT:
            return bool(vocab.has_vector(word[:i] + _UMLAUT[word[i]] + word[i + 1 :] + "in"))
    return False


def detect_feminine_context_names(text: str) -> list[DetectedSpan]:
    """Siehe Kommentarblock oben. Fuegt nur Treffer hinzu (fail-closed)."""
    if not text or not text.strip():
        return []
    doc = _analysis_doc(text)
    spans: list[DetectedSpan] = []
    for token in doc:
        word = token.text
        if len(word) < 3 or not word.isalpha() or not word[0].isupper() or word.isupper():
            continue
        if word.lower() in _NEVER_ENTITY_WORDS or word in _PROTOCOL_LABELS or is_technical_compound(word):
            continue
        words_before = [t for t in doc[: token.i] if not t.is_space]
        if not words_before:
            continue
        article = words_before[-1]
        feminine_singular = False
        if article.lower_ == "die" and token.dep_ == "sb":
            head = token.head
            feminine_singular = (
                head.pos_ in ("VERB", "AUX") and "Fin" in head.morph.get("VerbForm") and "Sing" in head.morph.get("Number")
            )
        elif article.lower_ == "der" and len(words_before) >= 2 and words_before[-2].lower_ in _DATIVE_PREPOSITIONS:
            feminine_singular = True
        # Zusaetzlich mindestens EIN Namensrahmen der Modell-Probe: juristische Rollenwoerter ("Mieter", "Klaeger",
        # "Auftraggeber") haben Probe 0 und stehen in fehlerhaften/OCR-Saetzen ("Die Mieter hat ...") sonst unter
        # Verdacht (gemessen: Fehlalarme 1 -> 13 von 132 ohne diese Bedingung, 2 von 132 mit ihr).
        if feminine_singular and _is_masculine_person_noun(word) and _name_probe_score(word) >= 1:
            spans.append(DetectedSpan(category="person", start=token.idx, end=token.idx + len(word), value=text[token.idx : token.idx + len(word)]))
    return spans


# Bestimmter/unbestimmter Artikel direkt vor einem Wort (13.10.2026, scripts/bench_article_noun_persons.py).
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines"})


def detect_article_names(text: str) -> list[DetectedSpan]:
    """Zweitsignal fuer Nachnamen direkt nach einem Artikel ("die Bergmann hat angerufen", "Rueckruf an den Pfannenstiel").

    ECHTER FUND (13.10.2026, Leck-Gegenprobe im Dokumentpfad): 39 von 96 solcher Faelle gingen im Klartext in den
    Payload. Ursache: alle Zweitsignale (`detect_probed_names`, Rueckfrage-Kandidaten) verlangen ein BARES Wort und
    ueberspringen alles nach einem Artikel; die NER etikettiert solche Namen oft nicht. Hier gilt dieselbe Modell-Probe mit
    derselben Schwelle (`_NAME_PROBE_MIN_SCORE`) wie fuer bare Woerter - nur ohne den Artikel-Ausschluss. Fuegt
    ausschliesslich Treffer hinzu (fail-closed); Ableitungs-Komposita (Fachwoerter) sind ausgenommen."""
    if not text or not text.strip():
        return []
    doc = _analysis_doc(text)
    spans: list[DetectedSpan] = []
    for token in doc:
        word = token.text
        if len(word) < _NAME_PROBE_MIN_LENGTH or not word.isalpha() or not word[0].isupper() or word.isupper():
            continue
        previous = doc[token.i - 1] if token.i else None
        if previous is None or previous.lower_ not in _ARTICLES or previous.pos_ != "DET":
            continue
        if word.lower() in _NEVER_ENTITY_WORDS or word in _PROTOCOL_LABELS or is_technical_compound(word):
            continue
        if is_contextual_technical_term(text, token.idx, token.idx + len(word)):
            continue  # dieselbe Kontextregel wie in `_collect_spans` ("auf das Anderkonto ueberwiesen")
        if _name_probe_score(word) >= _NAME_PROBE_MIN_SCORE:
            spans.append(DetectedSpan(category="person", start=token.idx, end=token.idx + len(word), value=text[token.idx : token.idx + len(word)]))
    return spans


def detect_unlabelled_proper_nouns(text: str) -> list[DetectedSpan]:
    """Zweitsignal: vom POS-Tagger als Eigenname (PROPN) erkannte Woerter, die die NER NICHT als Entitaet etikettiert hat.

    Ursache (Qualitaetslauf 12.10.2026): das NER-Modell etikettiert haeufige Nachnamen, die zugleich Gattungswoerter
    sind ("Fischer", "Weber", "Koch", "Wolf", "Braun", "Lange"), und viele seltene Namen ohne Anrede/Titel nicht - der
    POS-Tagger stuft sie aber unabhaengig davon als PROPN ein. Gemessen (1100 synthetische Namensfaelle ueber das
    echte Gateway): Klartext-Lecks 26,9 % -> 7,5 %. Konservativ: nur alphabetische, gross geschriebene Woerter ab
    drei Buchstaben, keine Abkuerzungen in Grossbuchstaben (BGB, VOB), keine Ganzwort-Ausnahmen, nur dort, wo kein
    anderer Treffer liegt. Ein Fehlalarm ist hier ein zusaetzlicher Platzhalter (fail-closed), nie ein Verlust."""
    if not text or not text.strip():
        return []
    doc = _analysis_doc(text)
    spans: list[DetectedSpan] = []
    for token in doc:
        word = token.text
        if token.pos_ != "PROPN" or len(word) < 3 or not word[0].isupper() or word.isupper():
            continue
        if not word.replace("-", "").isalpha() or word.lower() in _NEVER_ENTITY_WORDS or is_technical_compound(word):
            continue
        if is_contextual_technical_term(text, token.idx, token.idx + len(word)):
            continue  # dieselbe Kontextregel wie in `_collect_spans`
        if _is_identifier_label(word):
            continue  # Kennnummer-Bezeichnung neben maskierter Nummer ("Patientennummer [KENNUNG_01]"), wie _collect_spans
        if is_role_label_before_person(text, token.idx + len(word)):
            continue  # Rollenbezeichnung vor bereits ersetzter Person ("Messbeamter: [PERSON_04]"), siehe oben
        if word in _PROTOCOL_LABELS and text[token.idx + len(word) : token.idx + len(word) + 1] == ":":
            continue  # Rollenpraefix des Gespraechsverlaufs ("Assistent: ..."), kein Name
        spans.append(DetectedSpan(category="person", start=token.idx, end=token.idx + len(word), value=text[token.idx : token.idx + len(word)]))
    return spans


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
        if _is_identifier_label(value):
            continue  # blosse Kennnummer-Bezeichnung ("SV-Nr.", "Rentenversicherungsnummer"), siehe _ID_LABEL_BEFORE
        if _LAND_REGISTER_LABEL_HIT.fullmatch(value.strip()):
            continue  # "Flur 3", "Blatt 12345" sind Grundbuchangaben, kein Ort (§23; Blattnummer maskiert detect_grundbuch)
        if value.strip().lower() in _NEVER_ENTITY_WORDS:
            continue
        if category == "person" and is_role_label_before_person(text, end):
            # Bewusst NICHT "ort" (Review §35.6: haette echte Ortsnamen vor einem Personenplatzhalter im Restrisiko-Scan
            # freigegeben; fuer LE-T003 nicht noetig - die erhaltene Feldgrenze genuegt)
            continue  # Rollenbezeichnung vor bereits ersetzter Person ("Messbeamter: [PERSON_04]"), siehe is_role_label_before_person
        if category in ("person", "organisation") and is_common_compound(value.strip()):
            continue  # Sach-/Rollen-Kompositum ohne Ableitungsfuge, siehe `is_common_compound`
        if category in ("person", "organisation") and is_admin_procedure_compound(value):
            continue  # Verwaltungsgegenstand + Verfahrensnomen ("Visa-Anträge"), siehe `is_admin_procedure_compound`
        if category == "ort" and is_safe_head_compound(value.strip()):
            continue  # nur sichere (Dokument-)Kopfnomen - kein Ortsnamenbestandteil
        if category in ("person", "organisation") and is_derivational_compound(value.strip()):
            # Fachwort-Kompositum, kein Name - siehe `is_derivational_compound`. Organisation: ein EINZELNES Ableitungswort
            # ("Zustellungsurkunde", "Versicherungskammer") identifiziert niemanden; ein echter Organisationsname ist ein
            # Mehrworttreffer und bleibt geschuetzt. Bewusst NICHT "ort": reale Ortsnamen dieser Bauart ("Hoffnungsthal").
            continue
        if is_contextual_technical_term(text, start, end):
            continue  # Fachbegriff im Sachkontext (Wortbildung UND Dokumentkontext), siehe `is_contextual_technical_term`
        if category in ("person", "organisation") and is_generic_article_noun(text, start, end):
            continue  # "Die Gesellschaft ist ..." - allgemeine Bezugnahme, siehe `is_generic_article_noun`
        if category == "organisation" and is_defined_abbreviation(text, value.strip()):
            continue  # im Dokument definierte Fach-Abkuerzung ("EÜR" neben "Einnahmenüberschussrechnung")
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


def get_finite_subject_tokens(text: str) -> set[tuple[int, int]]:
    """Token-Bereiche, die Subjekt (Abhaengigkeit "sb") eines nicht-infiniten Verbs/Hilfsverbs im selben Satz sind.

    Ein Anweisungs-Imperativ am Satzanfang ("Ueberarbeite das Schreiben") ist nie Subjekt eines finiten Verbs; ein
    Name am Satzanfang ("Wessolowski hat angerufen") dagegen schon."""
    if not text or not text.strip():
        return set()
    doc = _analysis_doc(text)
    found = {
        (token.idx, token.idx + len(token.text))
        for token in doc
        if token.dep_ == "sb"
        and token.head.pos_ in ("VERB", "AUX")
        and token.head.i > token.i
        and "Inf" not in token.head.morph.get("VerbForm")
    }
    # Auch wenn der Parser das Wort nicht als Subjekt liest (seltener Name -> ADV/mo): unmittelbar gefolgt von einem FINITEN
    # Verb/Hilfsverb ("Eberhaerter hat das bestaetigt") ist es kein Imperativ - dieser hat danach ein Objekt/Artikel.
    for token in doc:
        following = doc[token.i + 1] if token.i + 1 < len(doc) else None
        if following is not None and following.pos_ in ("VERB", "AUX") and "Fin" in following.morph.get("VerbForm"):
            found.add((token.idx, token.idx + len(token.text)))
    return found


_SENTENCE_START_PREFIX = re.compile(r"(?:^|\n|[.!?:]\s+)\s*$")


_CELL_END = re.compile(r"(?:[ \t]{2,}|[ \t]*(?:$|\n|[|;,]|[A-ZÄÖÜ][\wÄÖÜäöüß]*:))")


def _is_bare_cell_value(text: str, end: int) -> bool:
    """Steht hinter dem Treffer nur noch das Zellenende ("Name: X  Funktion: ...", "X | Hamburg", Zeilenende)?
    Dann ist es ein Feldwert, kein Satzanfang mit Imperativ (der Imperativ hat stets weitere Woerter)."""
    return _CELL_END.match(text, end) is not None


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
    # Satzanfang auf der Analysefassung beurteilen: ein weicher Umbruch mitten im Satz ("durch" + Umbruch + Name) ist kein
    # Satzanfang (laengengleich, Offsets gelten unveraendert).
    analysis_text = _neutralize_internal_tokens(text)
    try:
        finite_subjects = get_finite_subject_tokens(text)
    except Exception:
        finite_subjects = set()
    # Namensformen einer an ANDERER Stelle erkannten Person (inkl. Genitiv/Versalien, siehe propagate_person_components)
    # sind kein Imperativ ("Herr von Sonntag ... Sonntags Schreiben ging ein": Satzanfang, Tag NOUN).
    other_names: dict[str, set[int]] = {}
    for span in spans:
        if span.category == "person":
            for part in _name_components(span.value):
                for variant in (part, part + "s", part.upper()):
                    other_names.setdefault(variant, set()).add(span.start)
    # Name nach einer Anrede mit Abkuerzungspunkt ("Hr. Gast", "gez. Sommer"): der Punkt ist kein Satzende.
    try:
        after_title = {(span.start, span.end) for span, strong in _titled_name_spans(text) if strong}
    except Exception:  # noqa: BLE001 - nur ein zusaetzlicher Behalte-Grund; ohne ihn gilt die bisherige Regel
        after_title = set()
    kept: list[DetectedSpan] = []
    for span in spans:
        if span.category != "person" or len(span.value.split()) != 1:
            kept.append(span)
            continue
        if (span.start, span.end) in after_title or other_names.get(span.value, set()) - {span.start}:
            kept.append(span)
            continue
        if not _SENTENCE_START_PREFIX.search(analysis_text[: span.start]):
            kept.append(span)
            continue
        if _is_bare_cell_value(text, span.end):
            kept.append(span)  # Feldwert in Tabelle/Formular, kein Satzanfang
            continue
        if (span.start, span.end) in finite_subjects:
            kept.append(span)  # Subjekt eines finiten Verbs: Name, kein Imperativ
            continue
        tags = [tag for (start, end), tag in pos_tags.items() if start >= span.start and end <= span.end]
        if len(tags) == 1 and tags[0] != "PROPN":
            continue
        kept.append(span)
    return kept


_LABEL_BEFORE = re.compile(r"[A-ZÄÖÜ][\wÄÖÜäöüß]*:[ \t]*$")
_NOUN_MODIFIER_POS = ("DET", "ADJ", "NUM", "PRON")


def get_name_role_tokens(text: str) -> set[tuple[int, int]]:
    """Token-Bereiche mit unabhaengigem GRAMMATISCHEM Namenssignal (Folgelauf 12.10.2026, Chat ohne Akte).

    `drop_common_noun_persons` verwirft NER-Personentreffer, die nur aus Substantiv-Tags bestehen - das schuetzt Fachwoerter
    ("Gewerbemietvertraege"), gab aber echte Nachnamen frei (gemessen 16,4 % Klartext-Lecks). Ein Token behaelt den
    Personentreffer, wenn es (a) Subjekt eines nicht-infiniten Verbs ist ("X hat angerufen"), (b) bares Agens nach
    "von"/"durch" ist, (c) ein Feldwert hinter "Label:" ist, oder (d) ein bares (ohne Artikel/Adjektiv/Zahl/Pronomen
    davor) grossgeschriebenes SINGULAR-Wort ist. Plural-Substantive bleiben verworfen: Fachwoerter wie
    "Gewerbemietvertraege" sind Plural, Namen Singular. Die Signale gelten nur fuer Woerter, die der NER bereits als
    Person etikettiert hat - sie fuehren keine neuen Treffer ein."""
    if not text or not text.strip():
        return set()
    neutral = _neutralize_internal_tokens(text)
    doc = _get_analyzer_engine().nlp_engine.nlp["de"](neutral)
    roles: set[tuple[int, int]] = set()
    for token in doc:
        word = token.text
        if not word[:1].isupper():
            continue
        span = (token.idx, token.idx + len(word))
        head = token.head
        subject = (
            token.dep_ == "sb"
            and head.pos_ in ("VERB", "AUX")
            and head.i > token.i
            and "Inf" not in head.morph.get("VerbForm")
        )
        agent = token.dep_ == "nk" and head.pos_ == "ADP" and head.lower_ in ("von", "durch")
        label_value = bool(_LABEL_BEFORE.search(neutral[: token.idx])) and _is_bare_cell_value(neutral, span[1])
        previous = doc[token.i - 1] if token.i else None
        bare = (
            not any(c.i < token.i and c.pos_ in _NOUN_MODIFIER_POS for c in token.children)
            and not (previous is not None and previous.pos_ in _NOUN_MODIFIER_POS)
            and "Plur" not in token.morph.get("Number")
        )
        if subject or agent or label_value or bare:
            roles.add(span)
    return roles


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
    try:
        name_roles = get_name_role_tokens(text)
    except Exception:
        name_roles = set()
    kept: list[DetectedSpan] = []
    for span in spans:
        if span.category != "person":
            kept.append(span)
            continue
        if (span.start, span.end) in name_roles:
            kept.append(span)  # unabhaengiges grammatisches Namenssignal, siehe get_name_role_tokens
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
