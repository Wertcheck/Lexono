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
_SEMANTIC_CHECK_PROMPT_TEMPLATE = """\
Du prüfst NUR die technische/sprachliche Qualität eines bereits \
pseudonymisierten Textes - KEINE juristische Bewertung, KEINE Aussage über \
rechtliche Richtigkeit oder Vollständigkeit.

Platzhalter wie [PERSON_01], [ADRESSE_01] stehen für bereits pseudonymisierte \
Daten und MÜSSEN unverändert so bleiben.

Prüfe AUSSCHLIESSLICH:
1. Werden Platzhalter konsistent verwendet (nicht vertauscht, nicht einer \
falschen Person/Sache zugeordnet)?
2. Gibt es offensichtliche logische Widersprüche im Text?
3. Passt der Text strukturell noch zum folgenden Ausgangssachverhalt?
4. Gibt es offensichtliche sprachliche/grammatikalische Fehler?

Ausgangssachverhalt (nur zur Orientierung, NICHT inhaltlich neu bewerten):
{sachverhalt}

Zu prüfender Text:
{text}

Antworte AUSSCHLIESSLICH als JSON gemäß Schema. Unsicherheit bei einer \
juristischen Frage ist KEIN Grund für "passed": false - das ist nicht deine \
Aufgabe."""


@dataclass(frozen=True)
class ResponseValidationResult:
    passed: bool
    # Menschlich lesbare Gründe, konsistent mit dem bestehenden Muster
    # (GatewayResult.reasons, SecurityCheckResult.reasons,
    # DraftingResult.blocked_reasons) - kein neuer Rückgabetyp nötig.
    issues: list[str] = field(default_factory=list)


def validate_claude_response(
    text: str,
    mappings: list[PseudonymMapping],
    sachverhalt: str,
    local_llm_provider: LocalLLMProvider,
) -> ResponseValidationResult:
    """Prüft die (noch pseudonymisierte) Claude-Antwort, bevor
    `DraftingService.create_draft` sie rekonstruiert. Wirft
    `LocalLLMUnavailableError` unverändert weiter (Stufe 2) - der Aufrufer
    behandelt das identisch zum bestehenden Fail-Closed-Pfad des
    Vorabanalyse-Schritts."""
    deterministic_issues = check_response_placeholder_integrity(text, mappings)
    if deterministic_issues:
        # Stufe 1 ist abschließend - Stufe 2 (LLM) wird bewusst NICHT mehr
        # aufgerufen, siehe Moduldocstring.
        return ResponseValidationResult(passed=False, issues=deterministic_issues)

    prompt = _SEMANTIC_CHECK_PROMPT_TEMPLATE.format(sachverhalt=sachverhalt, text=text)
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

    return ResponseValidationResult(passed=passed, issues=semantic_issues)
