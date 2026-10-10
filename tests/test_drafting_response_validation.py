"""Tests für app/drafting/response_validation.py.

Bisher nur indirekt über tests/test_drafting_service.py abgedeckt (mit
Fake-Providern, ohne den tatsächlichen Prompt-Text zu prüfen) - diese
Datei testet `validate_claude_response`/`_SEMANTIC_CHECK_PROMPT_TEMPLATE`
gezielt, insbesondere die real gefundene Platzhalter-Beispiel-Regression
(P0 Performance-Follow-up, 13.09.)."""

from __future__ import annotations

import re

import pytest

from app.ai_providers.local_llm_provider import LocalLLMUnavailableError
from app.drafting.response_validation import (
    _build_semantic_check_prompt,
    validate_claude_response,
)
from app.privacy.pseudonymizer import PseudonymMapping

_REAL_PLACEHOLDER_PATTERN = re.compile(r"\[[A-Za-zÄÖÜäöüß_]+_\d{2}\]")


class FakeLocalLLMProvider:
    """Minimaler Test-Double: nur `generate_structured()` wird von
    `validate_claude_response` genutzt, `process()` bleibt unbenutzt."""

    def __init__(self, structured_result: dict | None = None, raise_error: bool = False) -> None:
        self.structured_result = structured_result or {"passed": True, "issues": []}
        self.raise_error = raise_error
        self.received_prompts: list[str] = []

    def process(self, payload):  # pragma: no cover - nicht Teil dieses Pfads
        raise NotImplementedError

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        self.received_prompts.append(prompt)
        if self.raise_error:
            raise LocalLLMUnavailableError("simuliert")
        return self.structured_result


def test_prompt_template_placeholder_example_does_not_match_real_placeholder_pattern() -> None:
    """ECHTER FUND (P0 Performance-Follow-up, 13.09.): das Platzhalter-
    Beispiel im Prompt nannte bisher "[PERSON_01]"/"[ADRESSE_01]" - exakt
    das reale Muster (`_PLACEHOLDER_TOKEN_PATTERN`, siehe
    security_check.py). Ein "Thinking"-Modell (qwen3:8b) griff dieses
    Beispiel real als vermeintlich zu pruefenden Inhalt auf und erzeugte
    4 erfundene Inkonsistenz-Befunde rund um "[PERSON_01]", obwohl der
    echte Text nie einen solchen Platzhalter enthielt - inklusive eines
    faelschlichen "passed": false (loeste den Fail-Closed-Mechanismus ohne
    echten Grund aus) UND einer ~10-20x hoeheren Laufzeit (273 statt 12
    Output-Tokens, real gemessen). "XX" statt echter Ziffern vermittelt
    dieselbe Syntax-Information, matcht aber nicht das reale Muster."""
    prompt = _build_semantic_check_prompt(sachverhalt="x", text="y", has_mappings=True)
    assert not _REAL_PLACEHOLDER_PATTERN.search(prompt)


def test_prompt_omits_placeholder_criterion_entirely_when_no_mappings_exist() -> None:
    """ECHTER FUND (05.10., Owner-Direktive "P1-BUGFIX", mit dem real
    konfigurierten lokalen Modell reproduziert): das Platzhalter-Kriterium
    war bereits als "NUR falls der Ausgangssachverhalt überhaupt
    Platzhalter enthält" formuliert - ein schwaches lokales Modell
    befolgte diese Bedingung nachweislich NICHT (beanstandete
    "[KATEGORIE_XX]", obwohl weder Sachverhalt noch Text einen einzigen
    Platzhalter enthielten). Das Kriterium darf deshalb bei leeren
    `mappings` GAR NICHT erst im Prompt auftauchen."""
    prompt = _build_semantic_check_prompt(sachverhalt="x", text="y", has_mappings=False)
    assert "Platzhalter" not in prompt
    assert "KATEGORIE_XX" not in prompt


def test_prompt_includes_placeholder_criterion_when_mappings_exist() -> None:
    prompt = _build_semantic_check_prompt(sachverhalt="x", text="y", has_mappings=True)
    assert "Platzhalter" in prompt
    assert "KATEGORIE_XX" in prompt


def test_validate_claude_response_passes_deterministic_check_then_calls_llm() -> None:
    provider = FakeLocalLLMProvider(structured_result={"passed": True, "issues": []})
    mappings = [PseudonymMapping(placeholder="[PERSON_01]", category="PERSON", original_value="Max Mustermann")]

    result = validate_claude_response(
        "Sehr geehrter [PERSON_01], ...", mappings, "Sachverhalt ohne PII", provider
    )

    assert result.passed is True
    assert result.issues == []
    assert len(provider.received_prompts) == 1


def test_validate_claude_response_fails_closed_on_missing_placeholder_without_calling_llm() -> None:
    """Stufe 1 (deterministisch) ist abschliessend - bei einem fehlenden
    Platzhalter wird das lokale LLM gar nicht erst aufgerufen."""
    provider = FakeLocalLLMProvider()
    mappings = [PseudonymMapping(placeholder="[PERSON_01]", category="PERSON", original_value="Max Mustermann")]

    result = validate_claude_response("Text ohne den Platzhalter.", mappings, "Sachverhalt", provider)

    assert result.passed is False
    assert result.issues
    assert provider.received_prompts == []


def test_validate_claude_response_passes_natural_reply_when_coverage_not_required() -> None:
    """CHAT-01 (15.09., Chat-Intelligence-Forensik): der reproduzierte
    Kernfall - eine natuerliche Chat-Begruessung ohne jeden Platzhalter
    wird nicht mehr blockiert, wenn `require_full_placeholder_coverage=False`
    gesetzt ist (so wie DraftingService es fuer purpose="chat_response"
    tut)."""
    provider = FakeLocalLLMProvider(structured_result={"passed": True, "issues": []})
    mappings = [PseudonymMapping(placeholder="[MANDANT_01]", category="PERSON", original_value="Erika Mustermann")]

    result = validate_claude_response(
        "Guten Tag, wie kann ich Ihnen helfen?",
        mappings,
        "Akte: Einspruch Steuerbescheid 2025",
        provider,
        require_full_placeholder_coverage=False,
    )

    assert result.passed is True
    assert result.issues == []


def test_validate_claude_response_still_blocks_manipulated_token_when_coverage_not_required() -> None:
    """Die Manipulations-/Leck-Pruefungen bleiben unabhaengig von
    `require_full_placeholder_coverage` Pflicht - nur die
    Vollstaendigkeitsforderung wird abgeschaltet."""
    provider = FakeLocalLLMProvider()
    mappings = [PseudonymMapping(placeholder="[MANDANT_01]", category="PERSON", original_value="Erika Mustermann")]

    result = validate_claude_response(
        "Sehr geehrte Frau [MANDANT_99], ...",
        mappings,
        "Sachverhalt",
        provider,
        require_full_placeholder_coverage=False,
    )

    assert result.passed is False
    assert provider.received_prompts == []


def test_validate_claude_response_default_still_requires_full_coverage() -> None:
    """Default unveraendert - bestehende Aufrufer, die den neuen Parameter
    nicht setzen, verhalten sich exakt wie vorher."""
    provider = FakeLocalLLMProvider()
    mappings = [PseudonymMapping(placeholder="[MANDANT_01]", category="PERSON", original_value="Erika Mustermann")]

    result = validate_claude_response(
        "Guten Tag, wie kann ich Ihnen helfen?", mappings, "Sachverhalt", provider
    )

    assert result.passed is False
    assert provider.received_prompts == []


def test_validate_claude_response_raises_when_llm_returns_invalid_passed_field() -> None:
    import pytest

    provider = FakeLocalLLMProvider(structured_result={"issues": []})  # kein "passed"

    with pytest.raises(LocalLLMUnavailableError):
        validate_claude_response("Ein Text ohne Platzhalter.", [], "Sachverhalt", provider)


def test_validate_claude_response_propagates_llm_unavailable() -> None:
    import pytest

    provider = FakeLocalLLMProvider(raise_error=True)

    with pytest.raises(LocalLLMUnavailableError):
        validate_claude_response("Ein Text ohne Platzhalter.", [], "Sachverhalt", provider)


# --- Performance (10.10.): begrenzte Ausgabe, Prefix des Prompts ---------------------------------


def test_check_schema_bounds_the_output_size() -> None:
    """Ollama setzt maxItems/maxLength per Grammatik durch (anyOf/oneOf nicht) - die Ausgabe der
    lokalen Pruefung ist damit nach oben begrenzt (Erzeugung kostet ~0,15 s je Token)."""
    from app.drafting.response_validation import _RESPONSE_CHECK_SCHEMA

    issues = _RESPONSE_CHECK_SCHEMA["properties"]["issues"]
    assert issues["maxItems"] == 2
    assert set(_RESPONSE_CHECK_SCHEMA["required"]) == {"passed", "issues"}
    for field_schema in issues["items"]["properties"].values():
        assert field_schema["maxLength"] <= 120


@pytest.mark.parametrize("has_mappings", [True, False])
def test_prefix_is_the_exact_start_of_the_full_prompt(has_mappings: bool) -> None:
    from app.drafting.response_validation import build_semantic_check_prefix

    prefix = build_semantic_check_prefix(sachverhalt="Sachverhalt X", has_mappings=has_mappings)
    full = _build_semantic_check_prompt(sachverhalt="Sachverhalt X", text="Text Y", has_mappings=has_mappings)
    assert full.startswith(prefix)
    assert "Text Y" not in prefix
    assert full.index("Zu prüfender Text:") == len(prefix)


def test_prompt_tells_the_model_to_leave_issues_empty_when_passed() -> None:
    prompt = _build_semantic_check_prompt(sachverhalt="x", text="y", has_mappings=True)
    assert '"issues": []' in prompt


def test_long_sachverhalt_is_cut_visibly_in_the_semantic_check_prompt_but_text_is_not() -> None:
    from app.drafting.response_validation import _SEMANTIC_CHECK_SACHVERHALT_MAX_CHARS

    sach = "S" * (_SEMANTIC_CHECK_SACHVERHALT_MAX_CHARS + 300)
    text = "T" * 9000
    prompt = _build_semantic_check_prompt(sachverhalt=sach, text=text, has_mappings=True)
    assert "gekürzt" in prompt
    assert "S" * (_SEMANTIC_CHECK_SACHVERHALT_MAX_CHARS + 1) not in prompt
    assert text in prompt  # der zu pruefende Entwurf wird nie gekuerzt

    short = _build_semantic_check_prompt(sachverhalt="kurz", text="t", has_mappings=True)
    assert "gekürzt" not in short
