"""validate_claude_response – Datenschutz-/Qualitätsprüfung der Claude-
Antwort VOR jeder Rekonstruktion (siehe app/drafting/service.py::create_draft,
Increment "lokale KI als Datenschutz-/Qualitätsschicht").

Zweistufig, mit STRIKTER Reihenfolge (Vorgabe, wörtlich: "Ein LLM darf
niemals eine deterministische Privacy-Regel überschreiben"):

1. Deterministische Prüfung (reines Python, KEIN LLM) -
   `app/privacy/security_check.py::check_response_placeholder_integrity`.
   Schlägt sie fehl, wird Stufe 2 GAR NICHT erst aufgerufen - die
   deterministische Entscheidung ist abschließend und wird nie durch ein
   LLM-Urteil überstimmt.
2. Lokale semantische Prüfung (nur wenn Stufe 1 bestanden) - bewusst KLEIN
   und fokussiert (Platzhalter-Konsistenz, offensichtliche Widersprüche,
   strukturelle Passung zum Sachverhalt, Sprache/Grammatik). AUSDRÜCKLICH
   KEINE juristische Bewertung - das lokale Modell wird explizit angewiesen,
   sich dazu nicht zu äußern (siehe Prompt unten).

Nutzt `LocalLLMProvider.generate_structured()` (bewusst generische
Fähigkeit, siehe app/ai_providers/local_llm_provider.py - wird künftig auch
für PII-Erkennung, Lektorat, globale Suche etc. wiederverwendet, nicht auf
diesen Aufrufer zugeschnitten)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai_providers.local_llm_provider import LocalLLMProvider
from app.privacy.pseudonymizer import PseudonymMapping
from app.privacy.security_check import check_response_placeholder_integrity

# Bewusst klein gehalten (Vorgabe wörtlich: "Das Schema soll möglichst klein
# bleiben, damit auch schwächere lokale Modelle zuverlässig damit arbeiten
# können") - nur die zwei für einen Fail-Closed-Entscheid nötigen Felder.
_RESPONSE_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "severity": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["type", "severity", "description"],
            },
        },
    },
    "required": ["passed", "issues"],
}

# Bewusst eng begrenzt (Vorgabe wörtlich: "Sie soll NICHT versuchen, eine
# vollständige juristische Prüfung zu simulieren") - ausschließlich
# technische/sprachliche Qualitätsprüfung, keine inhaltlich-rechtliche
# Bewertung. `/no_think`-Präfix wird von OllamaLocalLLMProvider ergänzt.
#
# ECHTER FUND (05.10., Owner-Direktive "P1-BUGFIX", mit dem real
# konfigurierten lokalen Modell reproduziert): das Platzhalter-Kriterium
# unten ist bereits selbst als bedingt formuliert ("NUR falls der
# Ausgangssachverhalt überhaupt Platzhalter enthält") - ein schwaches
# lokales Modell befolgt diese Bedingung nachweislich NICHT zuverlässig
# (beanstandete "[KATEGORIE_XX]"-Platzhalter, obwohl weder Sachverhalt
# noch Text einen einzigen Platzhalter enthielten). `_build_prompt`
# LAESST dieses Kriterium deshalb komplett WEG, wenn `mappings` leer ist
# (keine Platzhalter existieren, die ueberhaupt geprueft werden
# koennten) - entfernt einen nachgewiesenen Halluzinations-Ausloeser,
# statt sich auf eine vom Modell ohnehin ignorierte Bedingung zu
# verlassen.
#
# ECHTER FUND (P0 Performance-Follow-up, 13.09.): das Platzhalter-Beispiel
# nutzte bisher ECHTE Ziffern ("[PERSON_01]", "[ADRESSE_01]") - exakt
# dasselbe, bereits in `ollama_provider.py::_LOCAL_LLM_SYSTEM_PROMPT`
# dokumentierte und dort bereits behobene Muster (siehe Kommentar dort):
# "Thinking"-faehige Modelle nehmen ein woertliches Beispiel aus der
# Instruktion als Teil des zu pruefenden Inhalts wahr. Real reproduziert
# (isolierter Ollama-Benchmark, identischer Prompt/Text OHNE echten
# [PERSON_01]-Platzhalter): das Modell "fand" trotzdem eine Inkonsistenz
# rund um "[PERSON_01]" und erzeugte 4 ausfuehrliche, erfundene
# Issue-Eintraege (273 Output-Tokens, ~68-101s) - inklusive eines
# faelschlichen "passed": false, was den bestehenden, korrekt
# funktionierenden Fail-Closed-Mechanismus unnoetig ausloeste. Mit "XX"
# statt echter Ziffern (identisches Beispiel-Muster, matcht aber nicht
# `_PLACEHOLDER_TOKEN_PATTERN`, siehe security_check.py): reale Wiederholung
# desselben Tests ergab "passed": true, "issues": [] - korrekt - bei nur
# 12 Output-Tokens (~3-15s), eine ~10-20x reale Beschleunigung als
# Nebeneffekt der Korrektheits-Korrektur, keine gezielte
# Geschwindigkeits-Kuerzung.
_PLACEHOLDER_CRITERION = (
    "Werden Platzhalter konsistent verwendet (nicht vertauscht, nicht einer "
    "falschen Person/Sache zugeordnet)?"
)
_BASE_CRITERIA = [
    "Gibt es offensichtliche logische Widersprüche im Text?",
    "Passt der Text strukturell noch zum folgenden Ausgangssachverhalt?",
    "Gibt es offensichtliche sprachliche/grammatikalische Fehler?",
]


def _build_semantic_check_prompt(*, sachverhalt: str, text: str, has_mappings: bool) -> str:
    """Siehe Moduldocstring/Kommentar oben ("ECHTER FUND, 05.10."): das
    Platzhalter-Kriterium wird NUR aufgenommen, wenn tatsächlich Mappings
    existieren - kein bedingtes "NUR falls..." mehr, auf dessen Befolgung
    sich ein schwaches lokales Modell nachweislich nicht verlassen lässt."""
    criteria = ([_PLACEHOLDER_CRITERION] if has_mappings else []) + _BASE_CRITERIA
    numbered_criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, start=1))

    placeholder_intro = (
        "\n\nPlatzhalter wie [KATEGORIE_XX] (Kategorie in Grossbuchstaben, gefolgt von "
        "einer laufenden Nummer in eckigen Klammern) stehen für bereits "
        "pseudonymisierte Daten und MÜSSEN unverändert so bleiben, wenn sie im zu "
        "prüfenden Text vorkommen."
        if has_mappings
        else ""
    )

    return (
        "Du prüfst NUR die technische/sprachliche Qualität eines bereits "
        "pseudonymisierten Textes - KEINE juristische Bewertung, KEINE Aussage über "
        f"rechtliche Richtigkeit oder Vollständigkeit.{placeholder_intro}\n\n"
        f"Prüfe AUSSCHLIESSLICH:\n{numbered_criteria}\n\n"
        "Ausgangssachverhalt (nur zur Orientierung, NICHT inhaltlich neu bewerten):\n"
        f"{sachverhalt}\n\n"
        f"Zu prüfender Text:\n{text}\n\n"
        'Antworte AUSSCHLIESSLICH als JSON gemäß Schema. Unsicherheit bei einer '
        'juristischen Frage ist KEIN Grund für "passed": false - das ist nicht deine '
        "Aufgabe."
    )


@dataclass(frozen=True)
class ResponseValidationResult:
    passed: bool
    # Menschlich lesbare Gründe, konsistent mit dem bestehenden Muster
    # (GatewayResult.reasons, SecurityCheckResult.reasons,
    # DraftingResult.blocked_reasons) - kein neuer Rückgabetyp nötig.
    issues: list[str] = field(default_factory=list)
    # ECHTER FUND (05.10., Owner-Direktive "P1-BUGFIX": mit dem real
    # konfigurierten lokalen Modell reproduziert, siehe
    # app/privacy/api_logger.py fuer die volle Herleitung): Stufe 1
    # (deterministisch) ist die TATSAECHLICHE Datenschutz-Durchsetzung -
    # ein Fund dort bedeutet einen echten, nachvollziehbaren Treffer
    # (Platzhalter-Manipulation/Originalwert-Leck). Stufe 2 (lokales LLM)
    # ist laut Moduldocstring "AUSDRUECKLICH KEINE juristische Bewertung",
    # sondern eine Qualitaetspruefung (Grammatik/Struktur-Konsistenz) -
    # bei einem schwachen lokalen Modell NACHWEISLICH unzuverlässig
    # (reproduziert: hielt frei erfundene "Befunde" fuer echte Probleme,
    # u. a. auf einem voellig fehlerfreien, vollstaendigen Entwurf).
    # `stage` erlaubt dem Aufrufer, diese beiden GRUNDVERSCHIEDENEN
    # Vertrauensstufen in Logging UND Nutzermeldung zu unterscheiden,
    # OHNE die eigentliche Fail-Closed-Entscheidung selbst zu veraendern
    # (beide Stufen blockieren weiterhin bei einem Fund - nur die
    # Einordnung wird ehrlicher).
    stage: str = "deterministic"


def validate_claude_response(
    text: str,
    mappings: list[PseudonymMapping],
    sachverhalt: str,
    local_llm_provider: LocalLLMProvider,
    *,
    skip_semantic_check: bool = False,
    require_full_placeholder_coverage: bool = True,
) -> ResponseValidationResult:
    """Prüft die (noch pseudonymisierte) Claude-Antwort, bevor
    `DraftingService.create_draft` sie rekonstruiert. Wirft
    `LocalLLMUnavailableError` unverändert weiter (Stufe 2) - der Aufrufer
    behandelt das identisch zum bestehenden Fail-Closed-Pfad des
    Vorabanalyse-Schritts.

    `skip_semantic_check` (P0 Performance-Follow-up, 13.09.): überspringt
    NUR Stufe 2 (LLM-Semantik) - Stufe 1 (deterministische Platzhalter-
    Integrität) läuft IMMER, unabhängig von diesem Parameter, und bleibt
    weiterhin abschließend bei einem Fund. Der Aufrufer (`DraftingService.
    create_draft`) setzt dies NUR, wenn `mappings` bereits leer ist (kein
    einziger Platzhalter existiert) UND kein Aktendokument in den
    Sachverhalt eingeflossen ist - Stufe 2 prüft ausschließlich
    Platzhalter-Konsistenz/Text-Sachverhalt-Passung, was bei komplett
    fehlenden Platzhaltern und fehlendem Dokumentkontext keine zusätzliche
    Datenschutz-Garantie mehr liefert (siehe DECISIONS.md für die volle
    Begründung).

    `require_full_placeholder_coverage` (15.09., CHAT-01): durchgereicht an
    `check_response_placeholder_integrity` - steuert NUR, ob innerhalb der
    weiterhin IMMER laufenden Stufe 1 zusätzlich verlangt wird, dass JEDER
    Mapping-Platzhalter im Text vorkommt. Die beiden anderen Stufe-1-Prüfungen
    (manipulierte/erfundene Platzhalter-Tokens, geleakter Originalwert)
    bleiben davon unberührt immer aktiv. Default `True` (unverändertes
    Verhalten). Der Aufrufer setzt `False` nur für `purpose="chat_response"`
    - siehe dortige Begründung."""
    deterministic_issues = check_response_placeholder_integrity(
        text, mappings, require_full_coverage=require_full_placeholder_coverage
    )
    if deterministic_issues:
        # Stufe 1 ist abschließend - Stufe 2 (LLM) wird bewusst NICHT mehr
        # aufgerufen, siehe Moduldocstring.
        return ResponseValidationResult(passed=False, issues=deterministic_issues)

    if skip_semantic_check:
        return ResponseValidationResult(passed=True, issues=[])

    prompt = _build_semantic_check_prompt(
        sachverhalt=sachverhalt, text=text, has_mappings=bool(mappings)
    )
    result = local_llm_provider.generate_structured(prompt, _RESPONSE_CHECK_SCHEMA)

    passed = result.get("passed")
    if not isinstance(passed, bool):
        # Kein erfundener Default - eine unlesbare/unvollständige lokale
        # Antwort ist ein Fehlerfall, kein stillschweigendes "passed".
        from app.ai_providers.local_llm_provider import LocalLLMUnavailableError

        raise LocalLLMUnavailableError(
            "Lokale Antwortprüfung lieferte kein gültiges 'passed'-Feld"
        )

    issues_raw = result.get("issues") or []
    semantic_issues = [
        f"{issue.get('type', 'unbekannt')} ({issue.get('severity', 'unbekannt')}): "
        f"{issue.get('description', '')}"
        for issue in issues_raw
        if isinstance(issue, dict)
    ]

    return ResponseValidationResult(passed=passed, issues=semantic_issues, stage="semantic")
