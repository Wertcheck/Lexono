"""SecurityCheckService – lokale Sicherheitsprüfung vor jedem geplanten
Claude-API-Aufruf (Architekturvorgabe, Schritt 2 von 5).

Deckt die 7-Punkte-Liste aus der Vorgabe ab:
1. "Welche Daten sollen übertragen werden?" - wird vollständig erst in
   Schritt 3 (Gateway mit Allowlist-Payload-Schema) beantwortet; hier
   bereits durch die Prüfung des konkreten, bereits pseudonymisierten
   Texts abgedeckt.
2/3/4. Enthält der Text (noch) personenbezogene/vertrauliche/nicht
   erlaubte Inhalte? -> erneute PII-Prüfung AUF DEM PSEUDONYMISIERTEN
   TEXT (nicht auf dem Original) - deckt auf, wenn die Pseudonymisierung
   etwas übersehen hat.
5. Wurden alle bekannten Platzhalter korrekt gesetzt? -> jeder
   `PseudonymMapping`-Eintrag muss im Text tatsächlich vorkommen.
6. Gibt es möglicherweise nicht erkannte personenbezogene Daten? ->
   heuristischer Hinweis auf Namens-ähnliche Muster, die keiner bekannten
   Entität entsprechen (siehe `_find_possible_unrecognized_names`).
7. Ist der Aufruf für diese konkrete Aufgabe zulässig? -> `purpose` muss
   in einer festen Allowlist stehen (nur Textproduktions-Aufgaben, siehe
   Architekturvorgabe Punkt 2: "Claude API ausschließlich für
   Textproduktion").

KERNREGEL: "Bei einem nicht eindeutigen Ergebnis: KEIN API-AUFRUF." ->
JEDER gefundene Grund führt zu `passed=False`. Es gibt keinen Modus, der
Warnungen ignoriert und trotzdem grünes Licht gibt.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.privacy.detectors import DetectedSpan, detect_all
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.privacy.pseudonymizer import PseudonymMapping
from app.privacy.security_check_schema import SecurityCheckResult

# Nur Textproduktions-Aufgaben - direkte Umsetzung von Vorgabe-Punkt 2.
# Explizit NICHT enthalten: Aktenanalyse, Aktenzuordnung, Rechtsrecherche,
# Fristenbestimmung, Strategieentscheidung, Versand.
ALLOWED_PURPOSES = frozenset(
    {
        "formulate_draft",
        "improve_draft",
        "correct_draft",
        "optimize_style",
        "improve_clarity",
        "apply_house_style",
        "transform_content_to_letter",
        # Review-Engine (Prompt 18): unabhaengige Pruefung eines bereits
        # erstellten Entwurfs - weiterhin reine Textproduktions-/
        # Textanalyse-Aufgabe, keine Rechtsentscheidung.
        "review_draft",
        # ECHTER FUND (realer Abnahme-Test, 13.09.): der zentrale Chat rief
        # DraftingService.create_draft bisher IMMER mit "formulate_draft"
        # auf, unabhaengig davon, ob die Nutzeranfrage ueberhaupt einen
        # Schriftsatz verlangte - jede normale Frage ("Was steht in § 558
        # BGB?") erzeugte dadurch einen formellen Briefentwurf mit
        # Betreff/Anrede. "chat_response" ist der neue, per einfacher
        # Stichwort-Erkennung (siehe app/chat/service.py::
        # _looks_like_drafting_request) gewaehlte Default-Zweck fuer den
        # Chat - weiterhin AUSSCHLIESSLICH Textproduktions-/Textanalyse-
        # Aufgabe (Fragen beantworten, Dokumente/Texte analysieren,
        # Textentwuerfe verbessern), keine neue Kategorie von Aufgabe im
        # Sinne dieser Allowlist, nur ein anderer SYSTEMPROMPT (siehe
        # app/ai_providers/claude_writing_provider.py::select_system_prompt).
        "chat_response",
    }
)

# Grobe Heuristik: zwei aufeinanderfolgende großgeschriebene Wörter -
# im Deutschen werden aber ALLE Substantive grossgeschrieben (nicht nur
# Namen), ebenso die Hoeflichkeitsform "Sie/Ihr". Eine reine
# Grossschreibungs-Heuristik wuerde daher in praktisch jedem normalen
# Kanzleibrief false positives erzeugen ("Ihr Schreiben", "Die
# Finanzbehörde" etc.) und den Check dadurch in der Praxis unbrauchbar
# machen. Deshalb: Stoppwortliste haeufiger Formulierungs-/Substantiv-
# Woerter aus dem Kanzlei-/Steuerkontext - wird NUR als Namenskandidat
# gewertet, wenn KEINES der beiden Woerter in dieser Liste steht.
#
# WICHTIGE EINSCHRAENKUNG (ehrlich benannt, siehe ARCHITECTURE.md): Das
# ist weiterhin keine echte NER-Erkennung, sondern eine bewusst simple
# Zusatzheuristik. Die Liste ist nicht erschoepfend - es bleiben sowohl
# false positives (seltene, hier nicht gelistete Substantive) als auch
# false negatives (ein Name, der zufaellig aus zwei gelisteten Woertern
# besteht) moeglich. Die zuverlaessigere Erkennung uebernimmt inzwischen
# echte deutsche NER via Presidio (siehe app/privacy/presidio_ner.py,
# per `ner_detector` in `check()` eingebunden) - diese Heuristik bleibt
# als zusaetzliche, unabhaengige Sicherheitsebene bestehen (Defense in
# Depth), nicht als deren Ersatz.

_COMMON_GERMAN_FORMAL_WORDS = frozenset(
    {
        # Höflichkeitsform / Pronomen (immer großgeschrieben im Deutschen)
        "ihr", "ihre", "ihrem", "ihren", "ihrer", "ihres", "ihnen", "sie",
        "wir", "uns", "unser", "unsere", "unserem", "unseren", "unserer",
        "unseres", "der", "die", "das", "diese", "dieser", "dieses",
        "diesem", "diesen", "ein", "eine", "einem", "einen", "einer",
        # Haeufige Substantive in Kanzlei-/Steuerkontext
        "schreiben", "kanzlei", "akte", "aktenzeichen", "vertrag",
        "frist", "bescheid", "einspruch", "antrag", "anwalt", "gericht",
        "behörde", "finanzamt", "finanzbehörde", "steuerbescheid",
        "rechnung", "mitteilung", "unterlagen", "dokument", "anlage",
        "betreff", "datum", "angelegenheit", "sachverhalt",
        "stellungnahme", "widerspruch", "bescheinigung", "nachweis",
        "belege", "zahlung", "betrag", "termin", "verfahren", "bezug",
        "damen", "herren", "grüßen", "grüße", "dank", "hinweis",
        "rückfragen", "kenntnisnahme", "prüfung", "übersendung",
        # Anredetitel - kein Namensbestandteil im Sinne dieser Heuristik.
        "herr", "herrn", "frau",
        # Nummerierte Argumentationspunkte (typisch in Schriftsätzen).
        "punkt", "punkte", "erster", "erstens", "zweiter", "zweitens",
        "dritter", "drittens", "vierter", "viertens", "fünfter",
        "fünftens", "letzter", "nächster", "folgender", "obiger",
        # Dokument-Kopfzeilen-/Titel-Woerter (realer Abnahme-Test-Fund,
        # 13.09.: "Synthetisches Testdokument" - ein Dokumenttitel, keine
        # zwei Namensbestandteile - loeste faelschlich einen Block aus).
        # Bewusst nur eindeutig generische Titel-/Kennzeichnungs-Woerter,
        # niemals Namensbestandteile im engeren Sinne (siehe Einschraenkung
        # oben: kein allgemeiner Umgehungsweg fuer echte PII).
        "synthetisches", "synthetisch", "testdokument", "musterdokument",
        "beispieldokument", "testfall", "referenzdokument",
    }
)

# ECHTER FUND (14.09., Overnight-Direktive §8, realer Regressionsfall "Frau
# Müller"): die obige Ausschlussliste verhindert zwar, dass "Herr"/"Herrn"/
# "Frau" selbst als Namensbestandteil gewertet werden (richtig) - macht damit
# aber jeden BLOSSEN "Anrede/Rolle + Nachname"-Fall OHNE Vornamen ("Frau
# Müller", "Herr Müller", "Klägerin Müller", "Der Beklagte Müller")
# strukturell unsichtbar fuer diese Heuristik, weil die bisherige Paar-Regel
# IMMER zwei nicht ausgeschlossene grossgeschriebene Woerter verlangte. Real
# reproduziert: auch Presidio/spaCy (app/privacy/presidio_ner.py) erkennt
# einen blossen Nachnamen ohne Vornamen in gewoehnlicher Satzmitte NICHT
# zuverlaessig als PERSON ("Frau Müller kam gestern vorbei." -> keine
# Treffer). Ohne diese Ergaenzung waere ein solcher Nachname an KEINER der
# Erkennungsebenen (known_entities/NER/Heuristik) erkannt worden und
# unpseudonymisiert in die Cloud-Anfrage gelangt.
#
# Diese Woerter selbst sind weiterhin KEIN Namensbestandteil (wie bei
# _COMMON_GERMAN_FORMAL_WORDS) - sie loesen aber jetzt eine PRUEFUNG des
# unmittelbar folgenden grossgeschriebenen Worts als moeglichen Nachnamen
# aus, auch wenn dieses folgende Wort alleine (ohne Vorname) steht.
_ROLE_OR_TITLE_PREFIX_WORDS = frozenset(
    {
        "frau", "herr", "herrn",
        "mandant", "mandantin", "mandanten",
        "kläger", "klaeger", "klägerin", "klaegerin",
        "beklagte", "beklagter",
        "zeuge", "zeugin", "zeugen",
        "vermieter", "vermieterin",
        "rechtsanwalt", "rechtsanwältin", "rechtsanwaeltin",
    }
)


_WORD_PATTERN = re.compile(r"[A-Za-zÄÖÜäöüß]+")


def _is_name_like_word(
    start: int,
    end: int,
    *,
    pos_tags: dict[tuple[int, int], str] | None,
    entity_types: dict[tuple[int, int], str] | None,
) -> bool:
    """Entscheidet fuer EIN Wort (gegeben durch seine Textposition), ob es
    als Namensbestandteil plausibel ist - siehe `_find_possible_
    unrecognized_names` Docstring fuer die Gesamt-Herleitung.

    `entity_types` (siehe app/privacy/presidio_ner.py::get_entity_types)
    ist das PRIMAERE, praezisere Signal: erkennt spaCys eigene NER-
    Komponente fuer dieses Wort einen Entitaetstyp (nicht-leerer String),
    ist das entscheidend - "PER" => Name, jeder andere Typ ("LOC"/"ORG"/
    "MISC") => KEIN Name (unabhaengig vom POS-Tag). Nur wenn KEINE
    Entitaet erkannt wurde (leerer String oder `entity_types` nicht
    uebergeben), faellt die Pruefung auf den aelteren, rein POS-Tag-
    basierten PROPN-Check zurueck (unveraendertes Defense-in-Depth-
    Verhalten fuer Namen, die die NER-Komponente selbst uebersieht)."""
    if entity_types is not None:
        ent = entity_types.get((start, end), "")
        if ent:
            return ent == "PER"
    if pos_tags is not None:
        return pos_tags.get((start, end)) == "PROPN"
    return True


def _find_possible_unrecognized_names(
    text: str,
    *,
    pos_tags: dict[tuple[int, int], str] | None = None,
    entity_types: dict[tuple[int, int], str] | None = None,
) -> list[str]:
    """Wortbasiertes Scannen statt regex-basiertem Aufeinanderfolgen-Match:
    verhindert, dass ein "verbrauchtes" Wort (z. B. "Herrn" in "Herrn
    Peter") das eigentlich interessante Folgepaar ("Peter Müller")
    unsichtbar macht, weil `re.finditer` keine überlappenden Treffer
    liefert.

    `pos_tags` (optional, siehe app/privacy/presidio_ner.py::get_pos_tags)
    ist ein {(start, end): "PROPN"/"NOUN"/"ADJ"/...}-Dict fuer das GENAU
    diesen Aufruf betreffende `text` - ECHTER FUND (Abnahme-Test, 13.09.):
    ohne diese Verfeinerung wertete diese Funktion JEDES Adjektiv+Substantiv-
    Ueberschrift-Paar ("Synthetisches Testdokument", "Salvatorische
    Klausel") faelschlich als Namenskandidat, weil im Deutschen ALLE
    Substantive grossgeschrieben werden - eine reine Grossschreibungs-
    Heuristik kann Adjektiv+Substantiv nicht von Vorname+Nachname
    unterscheiden. Echte Personennamen werden von spaCys POS-Tagger
    zuverlaessig als PROPN (Eigenname) getaggt; Rechtstitel-Ueberschriften
    sind ADJ+NOUN - ist `pos_tags` angegeben, wird ein Kandidat nur dann
    behalten, wenn BEIDE Woerter als PROPN getaggt sind. Bewusst weiterhin
    OPTIONAL (Default `None` = altes, rein regelbasiertes Verhalten ohne
    Modellabhaengigkeit) - erhaelt die urspruengliche "Defense in Depth
    unabhaengig von Presidio/spaCy"-Eigenschaft dieser Heuristik (siehe
    Moduldocstring), verfeinert sie aber deutlich, wenn ein Tagger
    verfuegbar ist (immer der Fall im echten Produktivbetrieb, siehe
    ClaudePrivacyGateway).

    `entity_types` (optional, ECHTER FUND Owner-Direktive "Verbleibende
    False-Positive-Grenze der Privacy-Namen-Heuristik beheben", 07.10.,
    siehe app/privacy/presidio_ner.py::get_entity_types fuer die volle
    Herleitung): POS=PROPN allein reicht NICHT aus, um einen echten
    Personennamen von einem fremdsprachigen/organisatorischen Begriff zu
    unterscheiden - spaCy taggt z. B. "World"/"Cities" (aus "Was ist der
    World Cities Report?") mangels Vokabeleintrag ebenfalls als PROPN,
    obwohl es kein Name ist. `entity_types` liefert spaCys eigenen,
    praeziseren NER-Entitaetstyp je Wort ("PER"/"LOC"/"ORG"/"MISC"/leer)
    und wird - wenn fuer ein Wort vorhanden - ALS VORRANGIGES Signal vor
    dem POS-Tag verwendet (siehe `_is_name_like_word`): nur "PER" zaehlt
    als Name, jeder andere erkannte Typ schliesst das Wort aus, UNABHAENGIG
    vom POS-Tag. Fehlt fuer ein Wort jede erkannte Entitaet, faellt die
    Pruefung weiterhin auf den PROPN-Check zurueck - ein von der NER-
    Komponente komplett uebersehener echter Name wird dadurch WEITERHIN
    erkannt (unveraendertes Defense-in-Depth-Verhalten)."""
    words = list(_WORD_PATTERN.finditer(text))
    candidates: list[str] = []

    for i in range(len(words) - 1):
        word1, word2 = words[i], words[i + 1]
        between = text[word1.end() : word2.start()]
        if between != " ":
            # Nur direkt durch ein einzelnes Leerzeichen getrennte Wörter
            # gelten als zusammenhaengende Phrase (kein Satzzeichen dazwischen).
            continue
        if not word2.group()[:1].isupper():
            continue
        # ECHTER FUND (07.10., Owner-Direktive "INSTALLER + GIT + CLOUD-
        # E2E-CHAT-QUALITY", per echtem Cloud-E2E-Test reproduziert): ein
        # Gesetzesabkuerzungspaar wie "BGB AT" (Allgemeiner Teil) wurde als
        # moeglicher unerkannter Name gewertet - spaCys POS-Tagger stuft
        # komplette Grossbuchstaben-Abkuerzungen mangels typischer Nomen-/
        # Adjektiv-Morphologie oft ebenfalls als PROPN ein (die oben bereits
        # bestehende `pos_tags`-Verfeinerung griff hier NICHT). Ein echter
        # deutscher Vor-/Nachname wird in Fliesstext praktisch nie
        # vollstaendig grossgeschrieben (anders als z. B. ein formelles
        # Briefkopf-Namensfeld) - ein mehrbuchstabiges ALL-CAPS-Wort ist
        # strukturell ein Indiz fuer eine Abkuerzung/ein Akronym, nicht fuer
        # einen Namen. Rein strukturelle Praezisierung der Heuristik (gilt
        # fuer JEDEN Zweck/Purpose, keine Lockerung der eigentlichen
        # Leck-/Entitaets-Pruefungen an anderer Stelle).
        if len(word1.group()) > 1 and word1.group().isupper():
            continue
        if len(word2.group()) > 1 and word2.group().isupper():
            continue
        word1_is_role_prefix = word1.group().lower() in _ROLE_OR_TITLE_PREFIX_WORDS
        if not word1_is_role_prefix:
            # Bisherige Regel unveraendert: OHNE ein erkanntes Anrede-/
            # Rollenwort verlangen wir weiterhin ZWEI grossgeschriebene,
            # nicht ausgeschlossene Woerter (Vorname + Nachname).
            if not word1.group()[:1].isupper():
                continue
            if word1.group().lower() in _COMMON_GERMAN_FORMAL_WORDS:
                continue
        if word2.group().lower() in _COMMON_GERMAN_FORMAL_WORDS:
            continue
        # Anrede-/Rollenwort direkt gefolgt von einem weiteren Anrede-/
        # Rollenwort (Titel-Stapelung, z. B. "Herr Rechtsanwalt Schmidt") ist
        # selbst noch kein Nachname - die naechste Schleifeniteration prueft
        # dieses zweite Rollenwort dann seinerseits als Praefix.
        if word1_is_role_prefix and word2.group().lower() in _ROLE_OR_TITLE_PREFIX_WORDS:
            continue
        if pos_tags is not None or entity_types is not None:
            if not _is_name_like_word(
                word2.start(), word2.end(), pos_tags=pos_tags, entity_types=entity_types
            ):
                continue
            if not word1_is_role_prefix:
                if not _is_name_like_word(
                    word1.start(), word1.end(), pos_tags=pos_tags, entity_types=entity_types
                ):
                    continue
        candidates.append(f"{word1.group()} {word2.group()}")

    return candidates


def check_placeholders_present(text: str, mappings: list[PseudonymMapping]) -> list[str]:
    """Vorgabe-Punkt 5: jeder `PseudonymMapping`-Eintrag muss im Text
    tatsaechlich vorkommen. Eigenstaendige Funktion (statt Inline-Code in
    `SecurityCheckService.check()`), damit dieselbe Pruefung auch fuer die
    NEUE, umfassendere Pruefung der eingehenden Claude-Antwort
    (`check_response_placeholder_integrity` unten, genutzt von
    app/drafting/response_validation.py) wiederverwendet werden kann, statt
    sie zweimal zu implementieren."""
    reasons: list[str] = []
    for mapping in mappings:
        if mapping.placeholder not in text:
            reasons.append(
                f"Platzhalter {mapping.placeholder} fehlt im Text (Inkonsistenz "
                "zwischen Mapping und Text)"
            )
    return reasons


# Erkennt jedes Platzhalter-foermige Token im Text (z. B. "[PERSON_01]",
# "[STEUER_ID_02]") - bewusst GROSSZUEGIG (erlaubt jede Buchstaben-/
# Unterstrich-Folge vor der Nummer), damit auch eine von Claude leicht
# VERAENDERTE Variante (andere Nummer, anderes Praefix, andere
# Gross-/Kleinschreibung) noch als "platzhalteraehnliches Token" erkannt
# und gegen die tatsaechlich erwarteten Platzhalter abgeglichen wird - eine
# zu enge Regex wuerde genau die Faelle uebersehen, die diese Pruefung
# aufdecken soll.
_PLACEHOLDER_TOKEN_PATTERN = re.compile(r"\[[A-Za-zÄÖÜäöüß_]+_\d+\]")


def _contains_original_value_leak(original_value: str, text: str) -> bool:
    """Prüft, ob `original_value` als EIGENES WORT in `text` vorkommt - NICHT
    als blosse Teilzeichenkette (siehe Docstring von
    `check_response_placeholder_integrity` fuer den vollen Fund, 05.10.,
    Owner-Direktive "Vollständiger UX- und Workflow-Audit").

    ECHTER FUND, SYNTHETISCH REPRODUZIERT: ein naiver `original_value in
    text`-Vergleich (die vorherige Implementierung dieser Pruefung) loeste
    bei JEDEM Wort aus, das den pseudonymisierten Wert als Teilstring
    enthaelt - z. B. blockierte ein Mandant namens "Fischer" jede Antwort,
    die das voellig unabhaengige, in einem Rechtskontext ganz normale Wort
    "Fischereirecht" enthielt ("Fischer" ist Teilstring von
    "Fischereirecht"). Das traf sowohl die EINGEHENDE Antwortpruefung
    (`check_response_placeholder_integrity`) als auch das AUSGEHENDE Final
    Payload Gate (`check_payload_placeholder_integrity`, ruft dieselbe
    Funktion auf) - eine normale Chat-Nachricht/-Antwort konnte dadurch
    blockiert werden, OHNE dass der Mandant ueberhaupt erwaehnt wurde.

    Wortgrenzen-Pruefung (`\\b...\\b`) statt Teilstring behebt das: "Fischer"
    matcht weiterhin zuverlaessig als eigenstaendiges Wort (z. B. "Herr
    Fischer"), nicht aber als gebundenes Praefix eines laengeren,
    unabhaengigen Wortes. Bewusst weiterhin case-SENSITIVE (unveraendert
    gegenueber vorher) - ein zufaelliger Gross-/Kleinschreibungs-Unterschied
    ("fischer" als Teil eines ganz anderen Wortes) soll weiterhin NICHT
    faelschlich treffen; ein ECHTER Leck-Fall verwendet den Originalwert
    ohnehin unveraendert (Claude sieht ihn nie, kann ihn also nicht in
    anderer Schreibweise "erfinden" - ein Treffer hier ist entweder ein
    technischer Fehler an anderer Stelle oder exakt der Originalwert)."""
    if not original_value:
        return False
    pattern = re.compile(r"\b" + re.escape(original_value) + r"\b")
    return bool(pattern.search(text))


#: ECHTER FUND (07.10., Owner-Direktive "INSTALLER + GIT + CLOUD-E2E-CHAT-
#: QUALITY", per echtem Cloud-E2E-Test mit dem real konfigurierten
#: ANTHROPIC_API_KEY reproduziert - siehe Abschlussbericht fuer die volle
#: Herleitung): Presidios generisches NER-Modell (Kategorien "person"/
#: "ort"/"organisation", siehe app/privacy/pseudonymizer.py-Kommentar
#: "rollenneutral") stuft im Deutschen gelegentlich ein ganz gewoehnliches
#: Substantiv als Entitaet ein (reproduziert: "Wohnraum" als "ort", aus
#: dem woertlichen Gesetzestext von § 558 BGB). Landet ein SOLCHER Wert in
#: `gespraechsverlauf` (Claude erklaert in einer Folgefrage erneut
#: korrekt dieselbe Rechtsnorm und verwendet denselben Fachbegriff), wertet
#: `check_response_placeholder_integrity` das bisher als "Originalwert
#: geleakt" und blockiert eine voellig unverdaechtige Antwort.
#:
#: NICHT geloest durch Entfernen/Abschwaechen der Leck-Pruefung selbst
#: (siehe deren Docstring: "fuer JEDEN Zweck weiterhin zwingend aktiv" -
#: bewusst nicht angetastet). Stattdessen: NUR fuer einen Wert, der
#: NACHWEISLICH niemals im lokal verfuegbaren, nicht-KI-generierten
#: Kontext dieser Anfrage vorkam ("locally sourced" - siehe
#: `lawyer_authored_text`-Parameter unten, der aus app/privacy/
#: gateway.py::GatewayResult.locally_sourced_text stammt und seit der
#: untenstehenden Erweiterung NICHT nur Anmerkungen/Chat-Historie,
#: sondern auch Sachverhalt/Vorlage/Quellenverweise - also auch
#: dokumentbasierten Kontext - umfasst), wird ein Mapping von der Leck-
#: Pruefung ausgenommen. Ein Wert, der NIRGENDS lokal-stammend vorkam,
#: kann unmoeglich echte, vom Anwalt eingegebene oder aus einem Dokument
#: extrahierte Mandantendaten sein; er kann daher strukturell kein Daten-
#: Leck sein - bestenfalls eine KI-Neuformulierung bereits oeffentlich-
#: allgemeinen (hier: gesetzlichen) Wissens.
#:
#: ECHTER FUND (Owner-Direktive "Architektur-Audit Privacy-/Chat-
#: Pipeline", 07.10., Live-Reproduktion ueber echte gespeicherte Claude-
#: Antworten aus einer frueheren Session): "person" war bisher
#: VOLLSTAENDIG von dieser Lockerung ausgenommen ("Namen bleiben immer
#: streng geprueft"). Reproduziert wurde aber, dass Presidios NER-Modell
#: GELEGENTLICH ganz gewoehnliche deutsche Woerter/Wortgruppen faelschlich
#: als "person" einstuft - z. B. "ortsuebliche" und "Offener Pruefpunkt"
#: (beides normales Juristendeutsch aus Claudes eigener Antwort zu § 558
#: BGB, keine Namen) wurden zu [PERSON_xx]-Platzhaltern. Verwendet Claude
#: in einer SPAETEREN, thematisch verwandten Antwort erneut ganz normal
#: dasselbe Wort, wertete die bisherige strikte "person"-Ausnahmslosigkeit
#: das faelschlich als geleakten Namen und blockierte eine voellig
#: unverdaechtige Antwort - obwohl der echte Originalwert (ein
#: Alltagswort, keine PII) NIE an Claude gesendet wurde. Deshalb jetzt:
#: "person" nutzt DIESELBE strikte Herleitung wie "ort"/"organisation" -
#: ein ECHTER, vom Anwalt getippter oder aus einem Dokument stammender
#: Name bleibt dadurch GENAUSO streng geschuetzt wie zuvor (er kommt im
#: erweiterten `locally_sourced_text` vor, ist also NICHT exempt) - nur
#: ein Wert, der provably NIE lokal stammte, wird ausgenommen. Das ist
#: keine Lockerung des Namensschutzes, sondern eine Korrektur eines
#: Fehlalarms bei NER-Fehlklassifikationen, die gar keine Namen sind -
#: siehe `find_lenient_leak_exempt_placeholders`."""
_LENIENT_LEAK_CATEGORIES = frozenset({"ort", "organisation", "person"})


def find_lenient_leak_exempt_placeholders(
    mappings: list[PseudonymMapping], *, lawyer_authored_text: str
) -> set[str]:
    """Siehe Modulkommentar oben bei `_LENIENT_LEAK_CATEGORIES`.
    `lawyer_authored_text` (Parametername unveraendert/kompatibel
    beibehalten, Bedeutung seit der "person"-Erweiterung oben PRAEZISIERT):
    muss ALLES enthalten, was lokal-stammend ist, also NICHT von Claude
    generiert wurde - getippter Anwaltstext (aktuelle Nachricht +
    "Anwalt:"-Zeilen der Historie) UND aus einem Dokument extrahierter
    Sachverhalt/Vorlage/Quellenverweise. In der Praxis reicht der
    Aufrufer hier `GatewayResult.locally_sourced_text` (siehe app/privacy/
    gateway.py) durch, das exakt das liefert. NIEMALS von der KI
    generierten Text (keine "Assistent:"-Zeilen) enthalten, sonst waere
    die Herleitung ("kann nicht lokal stammen") falsch."""
    exempt: set[str] = set()
    for mapping in mappings:
        if mapping.category not in _LENIENT_LEAK_CATEGORIES:
            continue
        if not _contains_original_value_leak(mapping.original_value, lawyer_authored_text):
            exempt.add(mapping.placeholder)
    return exempt


def check_response_placeholder_integrity(
    text: str,
    mappings: list[PseudonymMapping],
    *,
    require_full_coverage: bool = True,
    lenient_leak_exempt_placeholders: frozenset[str] = frozenset(),
) -> list[str]:
    """Deterministische (KEIN LLM) Pruefung einer vom Claude-Aufruf
    zurueckgekommenen, noch pseudonymisierten Antwort - VOR jeder
    Rekonstruktion (siehe app/drafting/response_validation.py). Anders als
    `check_placeholders_present` (nur "sind alle erwarteten Platzhalter da")
    prueft diese Funktion zusaetzlich zwei weitere, fuer eine EINGEHENDE
    Antwort relevante Faelle, die beim bestehenden, nur fuer AUSGEHENDEN
    Text gedachten `SecurityCheckService.check()` keine Rolle spielen:

    1. Wurde ein Platzhalter-Token in eine unerwartete/veraenderte Form
       gebracht (z. B. andere Nummer, Tippfehler, Gross-/Kleinschreibung)?
       -> jedes im Text gefundene platzhalteraehnliche Token, das NICHT
       exakt einem der erwarteten `mapping.placeholder`-Werte entspricht,
       ist ein Fund.
    2. Ist einer der URSPRUENGLICHEN (nicht pseudonymisierten) Werte aus dem
       Mapping als EIGENES WORT im Text wieder aufgetaucht (siehe
       `_contains_original_value_leak` - Wortgrenzen-Pruefung, NICHT blosser
       Teilstring-Vergleich, seit 05.10. behobener ECHTER FUND: "Fischer"
       als Mandantenname blockierte zuvor JEDE Antwort mit dem
       unabhaengigen Wort "Fischereirecht")? Claude sieht diese Werte
       strukturell nie (siehe ClaudeRequestPayload/Gateway) - ein Treffer
       hier waere entweder ein technischer Fehler an anderer Stelle oder
       ein Zufallstreffer, in jedem Fall ein Grund zum kontrollierten
       Abbruch statt stillschweigender Weiterverarbeitung.

    `require_full_coverage` (15.09., CHAT-01, Chat-Intelligence-Forensik):
    steuert NUR, ob `check_placeholders_present` (jeder Mapping-Platzhalter
    MUSS im Text vorkommen) mit angewendet wird. Die beiden oben genannten,
    tatsaechlich schuetzenden Pruefungen (Manipulation/Erfindung eines
    Platzhalter-Tokens, Leck des Originalwerts) laufen davon UNBERUEHRT
    IMMER.

    `lenient_leak_exempt_placeholders` (07.10., siehe Modulkommentar bei
    `_LENIENT_LEAK_CATEGORIES`/`find_lenient_leak_exempt_placeholders` fuer
    die volle Herleitung): eine eng begrenzte, EXPLIZIT vom Aufrufer
    berechnete Ausnahmeliste - NICHT dasselbe wie `require_full_coverage`.
    Default ein leeres `frozenset` (unveraendertes, striktes Verhalten fuer
    JEDEN bisherigen Aufrufer, insbesondere das Final Payload Gate). Nur
    ein Mapping-Platzhalter, der in dieser Menge steht, wird von der
    Original-Wert-Leck-Pruefung ausgenommen - die Platzhalter-Token-
    Manipulationspruefung (oben) bleibt davon UNBERUEHRT, ebenso jedes
    Mapping, das NICHT in dieser Menge steht.

    ECHTER FUND: diese Vollstaendigkeitsforderung ergibt fuer einen Brief-
    /Entwurfstext Sinn (der Text IST das Schreiben ueber die Beteiligten -
    jeder referenzierte Platzhalter sollte darin vorkommen), aber nicht
    fuer eine freie Chatantwort. Reproduziert: "Guten Tag, wie kann ich
    Ihnen helfen?" und "Vielen Dank." wurden blockiert, weil sie nicht
    JEDEN im Aktenkontext gefundenen Platzhalter woertlich enthielten - das
    war die direkte Ursache dafuer, dass eine normale Chat-Begruessung
    nicht beantwortet wurde. Default bleibt `True` (unveraendertes
    Verhalten fuer alle bisherigen Aufrufer, insbesondere das AUSGEHENDE
    Final Payload Gate `check_payload_placeholder_integrity` - dort bleibt
    volle Abdeckung weiterhin zwingend, siehe dortiger Docstring). Der
    Aufrufer (app/drafting/response_validation.py, verdrahtet ueber
    app/drafting/service.py) setzt `False` NUR fuer `purpose=
    "chat_response"`."""
    reasons = check_placeholders_present(text, mappings) if require_full_coverage else []

    expected_placeholders = {mapping.placeholder for mapping in mappings}
    found_tokens = set(_PLACEHOLDER_TOKEN_PATTERN.findall(text))
    unexpected_tokens = found_tokens - expected_placeholders
    if unexpected_tokens:
        reasons.append(
            "Unerwartete oder veraenderte Platzhalter-Tokens im Text gefunden "
            f"(Struktur-/ID-Manipulation vermutet): {sorted(unexpected_tokens)}"
        )

    for mapping in mappings:
        if mapping.placeholder in lenient_leak_exempt_placeholders:
            continue
        if _contains_original_value_leak(mapping.original_value, text):
            reasons.append(
                f"Urspruenglicher, nicht pseudonymisierter Wert fuer "
                f"{mapping.placeholder} im Text gefunden - moeglicher Datenschutzverstoss"
            )

    return reasons


def _flatten_payload_text(payload: ClaudeRequestPayload) -> str:
    """Alle String-Werte aus allen sieben Allowlist-Feldern zu EINEM Text
    zusammengefuehrt (Listenfelder aufgeloest) - Grundlage fuer
    `check_payload_placeholder_integrity` unten."""
    dump = payload.model_dump()
    parts: list[str] = []
    for value in dump.values():
        if value is None:
            continue
        if isinstance(value, list):
            parts.extend(v for v in value if isinstance(v, str))
        elif isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def check_payload_placeholder_integrity(
    payload: ClaudeRequestPayload, mappings: list[PseudonymMapping]
) -> list[str]:
    """FINAL PAYLOAD GATE: letzte, deterministische Pruefung der bereits
    fertig zusammengebauten `ClaudeRequestPayload` - unmittelbar bevor
    `ClaudePrivacyGateway.prepare_request()` sie als `allowed=True`
    zurueckgibt (siehe dort). Bewusst KEIN LLM (siehe Diagnose/Benchmarks
    zur lokalen KI in dieser Sitzung - nicht zuverlaessig genug fuer eine
    Aufgabe, bei der Unsicherheit IMMER zu einem Block fuehren muss).

    Ergaenzt, ersetzt NICHT den bestehenden `SecurityCheckService.check()`-
    Durchlauf: jener prueft den ZUSAMMENGEFUEHRTEN Text VOR dem Aufteilen in
    Felder (`gateway.py::_split_combined_text`); diese Funktion prueft
    stattdessen das TATSAECHLICHE, bereits aufgeteilte Payload-Objekt, das
    wirklich an den Cloud-Provider gehen wuerde - schliesst damit die
    Luecke, dass ein Fehler im Aufteilungs-/Wiederzusammensetzungs-Schritt
    selbst (nicht in der Pseudonymisierung) unbemerkt bliebe. Nutzt dieselbe
    bereits bewaehrte Platzhalter-Integritaetslogik wie
    `check_response_placeholder_integrity` (fehlende, veraenderte/erfundene
    oder unerwartet wieder aufgetauchte Original-Platzhalter-Werte), hier
    auf die AUSGEHENDE statt die eingehende Richtung angewendet."""
    combined = _flatten_payload_text(payload)
    return check_response_placeholder_integrity(combined, mappings)


class SecurityCheckService:
    def __init__(
        self,
        *,
        ner_detector: Callable[[str], list[DetectedSpan]] | None = None,
        pos_tagger: Callable[[str], dict[tuple[int, int], str]] | None = None,
        entity_type_tagger: Callable[[str], dict[tuple[int, int], str]] | None = None,
    ) -> None:
        """`ner_detector` (optional, siehe Pseudonymizer.__init__ fuer
        dieselbe Begruendung) wird beim Restrisiko-Scan (Punkt 2/3/4)
        zusaetzlich zu den Regex-Detektoren eingesetzt - schaerft genau den
        Check, der aufdecken soll, ob die Pseudonymisierung etwas
        uebersehen hat.

        `pos_tagger` (optional, siehe app/privacy/presidio_ner.py::
        get_pos_tags) verfeinert Punkt 6 (_find_possible_unrecognized_names)
        - ohne Tagger bleibt die alte, rein regelbasierte Grossschreibungs-
        Heuristik aktiv (funktioniert weiterhin unabhaengig von Presidio/
        spaCy, siehe dortiger Docstring).

        `entity_type_tagger` (optional, ECHTER FUND Owner-Direktive
        "Verbleibende False-Positive-Grenze der Privacy-Namen-Heuristik
        beheben", 07.10., siehe app/privacy/presidio_ner.py::
        get_entity_types) verfeinert Punkt 6 ZUSAETZLICH zu `pos_tagger`:
        POS=PROPN allein unterscheidet keinen fremdsprachigen/
        organisatorischen Begriff ("World Cities Report") von einem
        echten Personennamen - spaCys eigener NER-Entitaetstyp tut das
        zuverlaessiger und wird, wenn vorhanden, als vorrangiges Signal
        verwendet (siehe _is_name_like_word)."""
        self.ner_detector = ner_detector
        self.pos_tagger = pos_tagger
        self.entity_type_tagger = entity_type_tagger

    def check(
        self,
        pseudonymized_text: str,
        mappings: list[PseudonymMapping],
        *,
        purpose: str,
        unrecognized_name_scan_text: str | None = None,
        skip_residual_categories: frozenset[str] = frozenset(),
        residual_ner_span_filter: Callable[[str, list[DetectedSpan]], list[DetectedSpan]] | None = None,
        residual_ignore_ranges: list[tuple[int, int]] | None = None,
    ) -> SecurityCheckResult:
        """`residual_ignore_ranges` (optional, Owner-Direktive "Architektur-
        Audit Privacy-/Chat-Pipeline", 07.10.; NEU DEFINIERT 08.10. nach
        realem Fehler "was kannst du"): Zeichenbereiche (start, ende) von
        `pseudonymized_text`, die von Claude selbst stammen ("Assistent: "-
        Zeilen des Gespraechsverlaufs). Ein Restrisiko-Treffer (Punkt
        2/3/4), der VOLLSTAENDIG in einem solchen Bereich liegt, wird
        verworfen - alle anderen Treffer (aktuelle Nachricht, "Anwalt: "-
        Zeilen, Sachverhalt/Dokumentinhalt, ...) bleiben voll wirksam.
        Punkt 5/6/7 bleiben unveraendert.

        Zweck: Presidios NER stuft gelegentlich gewoehnliche Woerter aus
        einer FRUEHEREN Claude-Antwort faelschlich als PII ein (real: "UN-
        Quelle" als "ort") und blockierte dadurch eine unverwandte
        Folgefrage.

        WARUM ein Positionsfilter statt eines veraenderten Scan-Texts:
        jede Umformung des gescannten Texts (Assistent-Zeilen mit
        Leerzeichen ueberschreiben, Felder neu zusammensetzen, Segmente
        einzeln scannen) veraendert den NER-Kontext und erzeugte nachweislich
        selbst Fehlalarme - real: "was kannst du" wurde blockiert, weil
        "Anwalt: hallo wer bist du" nach dem Leerzeichen-Ueberschreiben am
        Textende stand und das kleingeschriebene "bist du" als PERSON
        erkannt wurde; isoliert gescannte Kurzsegmente wie "Anwalt:
        Erstfrage" wurden ebenfalls als PERSON gewertet. Hier laeuft die NER
        deshalb unveraendert auf dem VOLLEN, strukturgleichen Text (exakt
        der Kontext, den auch die Pseudonymisierung sah) und nur das
        ERGEBNIS wird nach Position gefiltert.

        `check_placeholders_present` (Punkt 5) bleibt bewusst auf dem
        VOLLEN `pseudonymized_text` - ein Mapping-Eintrag kann legitim NUR
        in einer Assistant-Zeile vorkommen und muss dort weiterhin
        auffindbar sein, sonst wuerde Punkt 5 faelschlich
        "Platzhalter fehlt" melden.

        `skip_residual_categories` (optional, ECHTER FUND Owner-
        Direktive "Architektur-Audit Privacy-/Chat-Pipeline", 07.10.):
        MUSS exakt dieselbe Menge sein, die der Aufrufer bereits als
        `skip_categories` an `Pseudonymizer.pseudonymize` uebergeben hat
        (siehe app/privacy/gateway.py). Grund: wird eine Kategorie (aktuell
        ausschliesslich "organisation", siehe dortige Herleitung) bewusst
        NICHT pseudonymisiert, bleibt ihr Klartext naturgemaess im Text -
        OHNE diesen Parameter wuerde Punkt 2/3/4 (Restrisiko-Scan,
        eigentlich gedacht als Sicherheitsnetz fuer von der
        Pseudonymisierung UEBERSEHENE PII) genau diesen ABSICHTLICH
        unveraenderten Text als "weiterhin erkennbares Muster" meldaen und
        die Anfrage blockieren - der neue `skip_categories`-Mechanismus
        waere dadurch wirkungslos (der Block wuerde nur von Punkt 6 auf
        Punkt 2/3/4 verlagert, real genau so beobachtet und hier behoben).
        Default ein leeres `frozenset` (unveraendertes, striktes Verhalten
        fuer JEDEN bisherigen Aufrufer). Betrifft NUR die Restrisiko-
        Meldung selbst - `check_placeholders_present` (Punkt 5) und jede
        andere Pruefung bleiben unberuehrt.

        `unrecognized_name_scan_text` (optional, ECHTER FUND 07.10.,
        Owner-Direktive "Chat-Pipeline Privacy-False-Positive bei
        allgemeinen Fragen"): beschraenkt NUR Punkt 6 auf einen anderen
        Text als `pseudonymized_text` - alle anderen Pruefungen (Punkt
        2/3/4/5/7) laufen unveraendert auf dem VOLLEN `pseudonymized_text`.

        Hintergrund: `_find_possible_unrecognized_names` ist eine
        Heuristik gegen von Menschen VERTIPPTE/von Presidio uebersehene
        Namen in anwaltlich verfasstem Text. Im Chat wird derselbe
        kombinierte Text zusaetzlich aus dem Gespraechsverlauf gebaut,
        der auch bereits erhaltene Claude-Antworten (Rolle "Assistent")
        enthaelt (siehe app/chat/service.py::_build_history). KI-
        generierte Fliesstext-Antworten sind voll von legitimen
        Grossschreibungs-Wortpaaren (Organisationsnamen, Berichtstitel,
        Fachbegriffe wie "World Cities Report") - Punkt 6 loeste darauf
        systematisch falsch aus und blockierte dadurch eine voellig
        unverwandte, saubere NEUE Frage einzig wegen Text in einer
        FRUEHEREN KI-Antwort. Der Aufrufer (app/privacy/gateway.py)
        uebergibt hier einen um "Assistent: "-Zeilen bereinigten Text,
        damit Punkt 6 weiterhin voll auf jedem anwaltlich verfassten Teil
        (aktuelle Nachricht, "Anwalt: "-Zeilen der Historie, alle anderen
        Allowlist-Felder) greift, aber nicht mehr auf Claudes eigener
        Prosa (die urspruengliche Annahme hier, Punkt 2/3/4 pruefe diese
        Prosa bereits ausreichend ab, erwies sich als unvollstaendig -
        siehe `residual_ignore_ranges` oben fuer die Korrektur)."""
        reasons: list[str] = []

        # Punkt 7: Zweck zulässig?
        if purpose not in ALLOWED_PURPOSES:
            reasons.append(
                f"Zweck '{purpose}' ist nicht in der Allowlist erlaubter "
                f"Textproduktions-Aufgaben ({sorted(ALLOWED_PURPOSES)})"
            )

        # Punkt 2/3/4: erneute PII-Pruefung AUF DEM PSEUDONYMISIERTEN TEXT.
        # Bewusst ohne known_entities - genau diese sollten bereits ersetzt
        # sein; ein Treffer hier bedeutet: etwas wurde uebersehen. Siehe
        # Docstring oben zu `residual_ignore_ranges` - Treffer innerhalb
        # KI-stammender Zeilen werden verworfen, der Scan-Text bleibt unveraendert.
        residual_spans = detect_all(
            pseudonymized_text,
            ner_detector=self.ner_detector,
            skip_categories=skip_residual_categories,
            ner_span_filter=residual_ner_span_filter,
        )
        if residual_ignore_ranges:
            residual_spans = [
                span
                for span in residual_spans
                if not any(
                    span.start >= lo and span.end <= hi for lo, hi in residual_ignore_ranges
                )
            ]
        if residual_spans:
            categories = sorted({span.category for span in residual_spans})
            reasons.append(
                f"Nach Pseudonymisierung weiterhin erkennbare Muster: {categories}"
            )

        # Punkt 5: jeder Mapping-Eintrag muss im Text tatsächlich vorkommen.
        reasons.extend(check_placeholders_present(pseudonymized_text, mappings))

        # Punkt 6: heuristischer Hinweis auf evtl. nicht erkannte Namen.
        # Siehe Docstring oben zu `unrecognized_name_scan_text` - NUR diese
        # eine Pruefung bekommt ggf. einen anderen (kleineren) Text als
        # alle anderen Punkte hier.
        scan_text = (
            unrecognized_name_scan_text
            if unrecognized_name_scan_text is not None
            else pseudonymized_text
        )
        pos_tags = self.pos_tagger(scan_text) if self.pos_tagger else None
        entity_types = self.entity_type_tagger(scan_text) if self.entity_type_tagger else None
        unclear = _find_possible_unrecognized_names(
            scan_text, pos_tags=pos_tags, entity_types=entity_types
        )
        if unclear:
            reasons.append(
                f"Möglicherweise nicht erkannte Namen/Entitäten gefunden: {unclear}"
            )

        return SecurityCheckResult(passed=len(reasons) == 0, reasons=reasons)
