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


def _find_possible_unrecognized_names(
    text: str, *, pos_tags: dict[tuple[int, int], str] | None = None
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
    ClaudePrivacyGateway)."""
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
        if pos_tags is not None:
            tag2 = pos_tags.get((word2.start(), word2.end()))
            if tag2 != "PROPN":
                continue
            if not word1_is_role_prefix:
                tag1 = pos_tags.get((word1.start(), word1.end()))
                if tag1 != "PROPN":
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


def check_response_placeholder_integrity(
    text: str,
    mappings: list[PseudonymMapping],
    *,
    require_full_coverage: bool = True,
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
        spaCy, siehe dortiger Docstring)."""
        self.ner_detector = ner_detector
        self.pos_tagger = pos_tagger

    def check(
        self,
        pseudonymized_text: str,
        mappings: list[PseudonymMapping],
        *,
        purpose: str,
    ) -> SecurityCheckResult:
        reasons: list[str] = []

        # Punkt 7: Zweck zulässig?
        if purpose not in ALLOWED_PURPOSES:
            reasons.append(
                f"Zweck '{purpose}' ist nicht in der Allowlist erlaubter "
                f"Textproduktions-Aufgaben ({sorted(ALLOWED_PURPOSES)})"
            )

        # Punkt 2/3/4: erneute PII-Pruefung AUF DEM PSEUDONYMISIERTEN TEXT.
        # Bewusst ohne known_entities - genau diese sollten bereits ersetzt
        # sein; ein Treffer hier bedeutet: etwas wurde uebersehen.
        residual_spans = detect_all(pseudonymized_text, ner_detector=self.ner_detector)
        if residual_spans:
            categories = sorted({span.category for span in residual_spans})
            reasons.append(
                f"Nach Pseudonymisierung weiterhin erkennbare Muster: {categories}"
            )

        # Punkt 5: jeder Mapping-Eintrag muss im Text tatsächlich vorkommen.
        reasons.extend(check_placeholders_present(pseudonymized_text, mappings))

        # Punkt 6: heuristischer Hinweis auf evtl. nicht erkannte Namen.
        pos_tags = self.pos_tagger(pseudonymized_text) if self.pos_tagger else None
        unclear = _find_possible_unrecognized_names(pseudonymized_text, pos_tags=pos_tags)
        if unclear:
            reasons.append(
                f"Möglicherweise nicht erkannte Namen/Entitäten gefunden: {unclear}"
            )

        return SecurityCheckResult(passed=len(reasons) == 0, reasons=reasons)
