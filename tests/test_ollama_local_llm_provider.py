"""Tests für app/ai_providers/ollama_provider.py (§65).

Mockt `httpx.get`/`httpx.post` (dasselbe Muster wie
tests/test_web_system_health.py für Ollama-/API-Erreichbarkeitschecks) -
kein echter Ollama-Prozess nötig. Der reale End-zu-Ende-Lauf gegen ein
tatsächlich laufendes Ollama ist bewusst NICHT Teil dieser automatisierten
Suite, siehe scripts/local_ai_smoke_test.py."""

from __future__ import annotations

import httpx
import pytest

from app.ai_providers.local_llm_provider import LocalLLMUnavailableError
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.privacy.gateway_schema import ClaudeRequestPayload


def _provider() -> OllamaLocalLLMProvider:
    return OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:4b")


def _payload(**overrides) -> ClaudeRequestPayload:
    defaults = {
        "schreibauftrag": "formulate_draft",
        "anonymisierter_sachverhalt": "Mandant [MANDANT_01] bittet um Rückmeldung.",
    }
    defaults.update(overrides)
    return ClaudeRequestPayload(**defaults)


class _FakeResponse:
    def __init__(self, *, json_data: dict, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._json_data


# --- check_health ---


def test_health_check_reachable_with_model_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _FakeResponse(json_data={"models": [{"name": "qwen3:4b"}]}),
    )
    provider = _provider()

    status = provider.check_health()

    assert status.reachable is True
    assert status.model_available is True
    assert status.error is None


def test_health_check_reachable_but_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _FakeResponse(json_data={"models": [{"name": "llama3:8b"}]}),
    )
    provider = _provider()

    status = provider.check_health()

    assert status.reachable is True
    assert status.model_available is False
    assert "qwen3:4b" in status.error


def test_health_check_uses_fixed_short_timeout_not_the_inference_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3 (§71): der Erreichbarkeits-Check ist ein reines `GET` ohne
    Inferenz und darf deshalb niemals so lange wie ein echter
    Generierungsaufruf warten - fest auf 5s statt `timeout_seconds`."""
    captured = {}

    def _fake_get(url, *, timeout):
        captured["timeout"] = timeout
        return _FakeResponse(json_data={"models": []})

    monkeypatch.setattr(httpx, "get", _fake_get)
    provider = OllamaLocalLLMProvider(
        base_url="http://localhost:11434", model="qwen2.5:1.5b", timeout_seconds=120.0
    )

    provider.check_health()

    assert captured["timeout"] == 5.0


def test_default_timeout_is_bounded_not_ten_minutes() -> None:
    """Phase 3 (§71): frueherer Default war 600s (fuer qwen3:4b
    dimensioniert). ECHTER FUND (Abnahme-Test, 13.09.): der zwischenzeitliche
    120s-Default reichte nicht fuer `qwen3:8b` (real durch die
    RecommendationEngine automatisch empfohlen, kein Sonderfall) - 76-114s
    real gemessen fuer denselben trivialen Prompt, vereinzelt ueber 120s.
    240s bleibt weiterhin klar unter dem alten 600s-Default (Regressionsschutz
    gegen eine Rueckkehr zu unbegrenzt langem Haengen), deckt aber den real
    gemessenen Bereich zuverlaessig ab."""
    provider = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen3:8b")
    assert provider.timeout_seconds == 240.0
    assert provider.timeout_seconds < 600.0


def test_health_check_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _raise)
    provider = _provider()

    status = provider.check_health()

    assert status.reachable is False
    assert status.model_available is False
    assert status.error is not None


# --- process ---


def test_process_returns_result_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """ECHTER FUND (Performance-Untersuchung, 13.09.): `process()` nutzt
    jetzt denselben Schema-Constraint wie `generate_structured()` (real
    gemessen: ~9x schneller fuer denselben Task, siehe dortiger
    Docstring) - die Fake-Antwort muss deshalb ein JSON-Objekt mit Feld
    "zusammenfassung" sein, kein roher Freitext mehr."""
    import json as json_module

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            json_data={
                "response": json_module.dumps({"zusammenfassung": "Kurze lokale Zusammenfassung."})
            }
        ),
    )
    provider = _provider()

    result = provider.process(_payload())

    assert result.text == "Kurze lokale Zusammenfassung."
    assert result.model == "qwen3:4b"


def test_process_sends_deterministic_temperature_and_schema_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erweitert um den Schema-Constraint-Beweis (Performance-Fix, 13.09.):
    `process()` MUSS jetzt "format"+"/no_think" senden - ohne Constraint
    braucht dasselbe Modell fuer denselben Task real ~9x laenger (siehe
    Docstring von `process()`)."""
    import json as json_module

    captured = {}

    def _fake_post(url, *, json, timeout):
        captured["json"] = json
        return _FakeResponse(
            json_data={"response": json_module.dumps({"zusammenfassung": "Ok."})}
        )

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = _provider()

    provider.process(_payload())

    assert captured["json"]["options"]["temperature"] == 0.0
    assert captured["json"]["model"] == "qwen3:4b"
    assert captured["json"]["format"] == {
        "type": "object",
        "properties": {"zusammenfassung": {"type": "string", "maxLength": 1200}},
        "required": ["zusammenfassung"],
    }
    assert captured["json"]["prompt"].startswith("/no_think")


def test_process_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", _raise)
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.process(_payload())


def test_process_raises_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.process(_payload())


def test_process_raises_on_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(json_data={"response": ""})
    )
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.process(_payload())


def test_process_raises_on_missing_response_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(json_data={"done": True}))
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.process(_payload())


def test_local_llm_system_prompt_example_does_not_match_real_placeholder_pattern() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): der System-Prompt fuer die
    lokale Vorabanalyse nannte als Beispiel-Syntax "[MANDANT_01]" - exakt
    das Muster echter Platzhalter (app/privacy/security_check.py::
    _PLACEHOLDER_TOKEN_PATTERN). "Thinking"-Modelle (z. B. qwen3) wiederholen
    Instruktionen haeufig woertlich in ihrer Reasoning-Ausgabe, die als
    Argumentationspunkt an Claude weitergereicht wird - das Beispiel tauchte
    dadurch real als vermeintlicher Platzhalter in der Antwort auf und liess
    die deterministische Platzhalter-Integritaetspruefung faelschlich
    fehlschlagen, obwohl nie eine echte Entitaet dahinterstand."""
    import re

    from app.ai_providers.ollama_provider import _LOCAL_LLM_SYSTEM_PROMPT

    real_placeholder_pattern = re.compile(r"\[[A-Za-zÄÖÜäöüß_]+_\d{2}\]")
    assert not real_placeholder_pattern.search(_LOCAL_LLM_SYSTEM_PROMPT)


def test_local_llm_system_prompt_forbids_inventing_facts_for_empty_sachverhalt() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09., Performance-Untersuchung):
    nachdem `process()` auf das `format`-Schema-Constraint umgestellt wurde
    (~9x schneller, siehe `process()`-Docstring), erfand das Modell bei
    einem praktisch leeren Sachverhalt ("Akte: Schnellentwurf 2026-09-13")
    wiederholt VOELLIG FIKTIVE Fallgeschichten (u. a. eine erfundene
    Veranstaltung mit Kosten, dann eine erfundene Steuerpruefung) - ein
    direkter Verstoss gegen die nicht verhandelbare Regel "Niemals
    Rechtsquellen, Fundstellen oder Zitate erfinden" (CLAUDE.md), hier
    uebertragen auf erfundene SACHVERHALTE statt Rechtsquellen. Real
    behoben durch eine explizite Anweisung im System-Prompt, bei fehlendem
    Sachverhalt woertlich "Kein inhaltlicher Sachverhalt vorhanden." zu
    antworten statt etwas zu erfinden - verifiziert durch 3 wiederholte
    reale Ollama-Aufrufe mit dem exakten Produktions-Prompt (siehe
    DECISIONS.md), hier als dauerhafte Regressionssicherung auf den
    Prompt-Text selbst."""
    from app.ai_providers.ollama_provider import _LOCAL_LLM_SYSTEM_PROMPT

    assert "ERFINDE" in _LOCAL_LLM_SYSTEM_PROMPT
    assert "Kein inhaltlicher Sachverhalt vorhanden" in _LOCAL_LLM_SYSTEM_PROMPT


def test_process_falls_back_to_thinking_field_when_response_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09., mit dem tatsaechlich
    empfohlenen Modell `qwen3:8b` reproduziert): dieselbe, bei
    `generate_structured()` bereits bekannte Ollama-Eigenheit
    ("thinking"-Modelle legen ihre Ausgabe manchmal ins `thinking`- statt
    ins `response`-Feld) betraf bisher auch die einfache `process()`-
    Variante - jede Chat-Nachricht schlug bei aktivierter lokaler KI
    fehl, sobald `response` leer war, obwohl `thinking` eine echte Antwort
    enthielt. Seit `process()` selbst schema-constrained ist (Performance-
    Fix, 13.09.), muss das `thinking`-Feld ein JSON-Objekt enthalten, kein
    Freitext mehr."""
    import json as json_module

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            json_data={
                "response": "",
                "thinking": json_module.dumps(
                    {"zusammenfassung": "Der Sachverhalt betrifft eine Mietsache."}
                ),
            }
        ),
    )
    provider = _provider()

    result = provider.process(_payload())

    assert result.text == "Der Sachverhalt betrifft eine Mietsache."


def test_process_never_sees_the_original_unpseudonymized_text() -> None:
    """Struktureller Beweis (nicht nur Verhalten): `process()` nimmt
    AUSSCHLIESSLICH eine `ClaudeRequestPayload` entgegen - denselben Typ
    wie `ClaudeWritingProvider.write()` - es gibt keinen Parameter, über
    den unpseudonymisierter Text hereinkäme."""
    import inspect

    signature = inspect.signature(OllamaLocalLLMProvider.process)
    params = list(signature.parameters.values())
    assert len(params) == 2  # self, payload
    assert params[1].annotation == "ClaudeRequestPayload"


def test_constructor_rejects_blank_base_url() -> None:
    with pytest.raises(ValueError):
        OllamaLocalLLMProvider(base_url="", model="qwen3:4b")


def test_constructor_rejects_blank_model() -> None:
    with pytest.raises(ValueError):
        OllamaLocalLLMProvider(base_url="http://localhost:11434", model="")


# --- generate_structured (Increment "lokale KI als Datenschutz-/
# Qualitaetsschicht") ---


def test_generate_structured_returns_parsed_json_from_response_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(json_data={"response": '{"passed": true, "issues": []}'}),
    )
    provider = _provider()

    result = provider.generate_structured("Pruefe den Text.", {"type": "object"})

    assert result == {"passed": True, "issues": []}


def test_generate_structured_falls_back_to_thinking_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real beobachtete Ollama-Eigenheit: bei schema-constrained Antworten
    landet das JSON manchmal im `thinking`- statt im `response`-Feld."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            json_data={"response": "", "thinking": '{"passed": false, "issues": []}'}
        ),
    )
    provider = _provider()

    result = provider.generate_structured("Pruefe den Text.", {"type": "object"})

    assert result == {"passed": False, "issues": []}


def test_generate_structured_sends_schema_and_no_think_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def _fake_post(url, *, json, timeout):
        captured["json"] = json
        return _FakeResponse(json_data={"response": '{"passed": true, "issues": []}'})

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = _provider()
    schema = {"type": "object", "properties": {"passed": {"type": "boolean"}}}

    provider.generate_structured("Pruefe [MANDANT_01].", schema)

    assert captured["json"]["format"] == schema
    assert captured["json"]["prompt"].startswith("/no_think\n")
    assert "[MANDANT_01]" in captured["json"]["prompt"]
    assert captured["json"]["options"]["temperature"] == 0.0


def test_generate_structured_sends_extended_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """ECHTER FUND (Streaming-Architekturentscheidung, Folgeuntersuchung,
    13.09.): real gegen die echte, installierte Produktionsinstanz
    gemessen - Ollamas STANDARD-`keep_alive` (5 Minuten) entlaedt
    `qwen3:8b` zwischen realistischen Kanzlei-Arbeitspausen aus dem
    Speicher; ein danach gesendeter Chat mit Aktendokument/PII durchlaeuft
    dann erneut den vollen Kaltstart (real isoliert gemessen: 144.67s
    KALT vs. 6.79-10.26s WARM). `generate_structured()` setzt deshalb
    explizit ein laengeres `keep_alive` (siehe Moduldocstring dort fuer
    die volle Herleitung + reale `/api/ps`-Verifikation)."""
    captured = {}

    def _fake_post(url, *, json, timeout):
        captured["json"] = json
        return _FakeResponse(json_data={"response": '{"passed": true, "issues": []}'})

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = _provider()
    schema = {"type": "object", "properties": {"passed": {"type": "boolean"}}}

    provider.generate_structured("Testfrage.", schema)

    assert captured["json"]["keep_alive"] == "30m"


def test_generate_structured_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", _raise)
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.generate_structured("Pruefe den Text.", {"type": "object"})


def test_generate_structured_raises_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.generate_structured("Pruefe den Text.", {"type": "object"})


def test_generate_structured_raises_on_empty_response_and_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(json_data={"response": "", "thinking": ""})
    )
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.generate_structured("Pruefe den Text.", {"type": "object"})


def test_generate_structured_raises_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(json_data={"response": "kein gueltiges JSON"}),
    )
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.generate_structured("Pruefe den Text.", {"type": "object"})


def test_generate_structured_raises_on_non_object_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein syntaktisch gueltiges JSON-Array ist trotzdem kein verwertbares
    Ergebnis - der Aufrufer erwartet immer ein Objekt (dict)."""
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(json_data={"response": "[1, 2, 3]"})
    )
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.generate_structured("Pruefe den Text.", {"type": "object"})


# --- list_local_models / pull_model (§68) ---


def test_list_local_models_returns_installed_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _FakeResponse(json_data={"models": [{"name": "qwen3:4b"}, {"name": "qwen3:8b"}]}),
    )
    provider = _provider()

    assert provider.list_local_models() == ["qwen3:4b", "qwen3:8b"]


def test_list_local_models_raises_when_ollama_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _raise)
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.list_local_models()


def test_pull_model_success_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(json_data={"status": "success"})
    )
    provider = _provider()

    provider.pull_model()  # darf nicht werfen


def test_pull_model_uses_explicit_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _fake_post(url, *, json, timeout):
        captured["json"] = json
        return _FakeResponse(json_data={"status": "success"})

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = _provider()

    provider.pull_model("qwen3:8b")

    assert captured["json"]["model"] == "qwen3:8b"


def test_pull_model_raises_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(json_data={"status": "error", "error": "not found"})
    )
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.pull_model()


def test_pull_model_raises_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)
    provider = _provider()

    with pytest.raises(LocalLLMUnavailableError):
        provider.pull_model()


def test_pull_model_uses_independent_download_timeout_not_inference_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realer Fund (Installer-Reality-Check, Referenzmaschine i5-1145G7):
    `pull_model` verwendete bisher `timeout_seconds` (Default 120s, fuer
    eine EINZELNE Inferenzanfrage dimensioniert) auch fuer den Download
    selbst - ein Mehrere-GB-Modell (z. B. qwen3:8b) ueberschreitet diese
    Zeit beim Warten auf die erste Response fast immer, was reproduzierbar
    zu `httpx.ReadTimeout` fuehrte, unabhaengig von der tatsaechlichen
    Downloadgeschwindigkeit. Dieser Test verankert die Trennung: ein klein
    gewaehlter `timeout_seconds` (der eine Inferenzanfrage sofort abbrechen
    wuerde) darf den Download-Timeout NICHT beeinflussen - `pull_model` muss
    weiterhin ohne Read-Timeout aufgerufen werden."""
    captured = {}

    def _fake_post(url, *, json, timeout):
        captured["timeout"] = timeout
        return _FakeResponse(json_data={"status": "success"})

    monkeypatch.setattr(httpx, "post", _fake_post)
    # Absichtlich winzig - eine reale Inferenzanfrage wuerde damit sofort
    # scheitern. Der Download-Aufruf darf diesen Wert trotzdem nicht nutzen.
    provider = OllamaLocalLLMProvider(
        base_url="http://localhost:11434", model="qwen3:8b", timeout_seconds=0.001
    )

    provider.pull_model()

    used_timeout = captured["timeout"]
    assert used_timeout is not provider.timeout_seconds
    assert isinstance(used_timeout, httpx.Timeout)
    assert used_timeout.read is None


# --- Performance (10.10.): Prefill, Warm-up, inhaltsfreie Messwerte -------------------------------


def test_prefill_posts_prefix_with_single_token_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_post(url, json=None, timeout=None, **kw):
        seen.update(url=url, body=json)
        return _FakeResponse(json_data={"response": "x"})

    monkeypatch.setattr(httpx, "post", fake_post)
    _provider().prefill("Anweisung und Sachverhalt")

    assert seen["url"].endswith("/api/generate")
    assert seen["body"]["prompt"] == "/no_think\nAnweisung und Sachverhalt"
    assert seen["body"]["options"]["num_predict"] == 1
    assert "format" not in seen["body"]

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    _provider().prefill("egal")  # darf nicht werfen
    _provider().warm_up()  # darf nicht werfen


def test_warm_up_loads_model_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        httpx, "post", lambda url, json=None, **k: seen.update(body=json) or _FakeResponse(json_data={})
    )
    provider = _provider()
    provider.warm_up()
    assert "prompt" not in seen["body"]
    assert seen["body"]["model"] == provider.model
    assert seen["body"]["keep_alive"]


def test_perf_metrics_are_logged_without_content(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    secret_prompt = "GEHEIMER PROMPTINHALT"
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            json_data={
                "response": '{"passed": true, "issues": []}',
                "total_duration": 12_000_000_000,
                "load_duration": 3_000_000_000,
                "prompt_eval_count": 900,
                "prompt_eval_duration": 5_000_000_000,
                "eval_count": 14,
                "eval_duration": 2_000_000_000,
            }
        ),
    )
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("lexono.perf")
    handler = _H(level=logging.INFO)
    old_level, old_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.disabled = False
    try:
        _provider().generate_structured(secret_prompt, {"type": "object", "properties": {"passed": {}}})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.disabled = old_disabled

    messages = " ".join(records)
    assert "PERF_OLLAMA task=validation total_s=12.00 load_s=3.00 prompt_tokens=900" in messages
    assert "eval_tokens=14" in messages
    assert secret_prompt not in messages and 'passed": true' not in messages


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("http://localhost:11434", "http://127.0.0.1:11434"),
        ("http://LOCALHOST:11434/", "http://127.0.0.1:11434"),
        ("http://localhost", "http://127.0.0.1"),
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://ollama.intern:11434", "http://ollama.intern:11434"),
        ("http://localhost.example.com:11434", "http://localhost.example.com:11434"),
    ],
)
def test_localhost_is_addressed_as_ipv4_loopback_other_hosts_unchanged(given: str, expected: str) -> None:
    """Windows: `localhost` -> ::1 zuerst, dort lauscht Ollama nicht -> ~2 s Verlust je Anfrage."""
    from app.ai_providers.ollama_provider import OllamaLocalLLMProvider

    assert OllamaLocalLLMProvider(base_url=given, model="m").base_url == expected
