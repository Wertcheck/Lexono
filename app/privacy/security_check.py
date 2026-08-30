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
    }
)


_WORD_PATTERN = re.compile(r"[A-Za-zÄÖÜäöüß]+")


def _find_possible_unrecognized_names(text: str) -> list[str]:
    """Wortbasiertes Scannen statt regex-basiertem Aufeinanderfolgen-Match:
    verhindert, dass ein "verbrauchtes" Wort (z. B. "Herrn" in "Herrn
    Peter") das eigentlich interessante Folgepaar ("Peter Müller")
    unsichtbar macht, weil `re.finditer` keine überlappenden Treffer
    liefert."""
    words = list(_WORD_PATTERN.finditer(text))
    candidates: list[str] = []

    for i in range(len(words) - 1):
        word1, word2 = words[i], words[i + 1]
        between = text[word1.end() : word2.start()]
        if between != " ":
            # Nur direkt durch ein einzelnes Leerzeichen getrennte Wörter
            # gelten als zusammenhaengende Phrase (kein Satzzeichen dazwischen).
            continue
        if not (word1.group()[:1].isupper() and word2.group()[:1].isupper()):
            continue
        if (
            word1.group().lower() in _COMMON_GERMAN_FORMAL_WORDS
            or word2.group().lower() in _COMMON_GERMAN_FORMAL_WORDS
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


def check_response_placeholder_integrity(
    text: str, mappings: list[PseudonymMapping]
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
       Mapping woertlich im Text wieder aufgetaucht? Claude sieht diese
       Werte strukturell nie (siehe ClaudeRequestPayload/Gateway) - ein
       Treffer hier waere entweder ein technischer Fehler an anderer Stelle
       oder ein Zufallstreffer, in jedem Fall ein Grund zum kontrollierten
       Abbruch statt stillschweigender Weiterverarbeitung."""
    reasons = check_placeholders_present(text, mappings)

    expected_placeholders = {mapping.placeholder for mapping in mappings}
    found_tokens = set(_PLACEHOLDER_TOKEN_PATTERN.findall(text))
    unexpected_tokens = found_tokens - expected_placeholders
    if unexpected_tokens:
        reasons.append(
            "Unerwartete oder veraenderte Platzhalter-Tokens im Text gefunden "
            f"(Struktur-/ID-Manipulation vermutet): {sorted(unexpected_tokens)}"
        )

    for mapping in mappings:
        if mapping.original_value and mapping.original_value in text:
            reasons.append(
                f"Urspruenglicher, nicht pseudonymisierter Wert fuer "
                f"{mapping.placeholder} im Text gefunden - moeglicher Datenschutzverstoss"
            )

    return reasons


class SecurityCheckService:
    def __init__(
        self, *, ner_detector: Callable[[str], list[DetectedSpan]] | None = None
    ) -> None:
        """`ner_detector` (optional, siehe Pseudonymizer.__init__ fuer
        dieselbe Begruendung) wird beim Restrisiko-Scan (Punkt 2/3/4)
        zusaetzlich zu den Regex-Detektoren eingesetzt - schaerft genau den
        Check, der aufdecken soll, ob die Pseudonymisierung etwas
        uebersehen hat."""
        self.ner_detector = ner_detector

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
        unclear = _find_possible_unrecognized_names(pseudonymized_text)
        if unclear:
            reasons.append(
                f"Möglicherweise nicht erkannte Namen/Entitäten gefunden: {unclear}"
            )

        return SecurityCheckResult(passed=len(reasons) == 0, reasons=reasons)
