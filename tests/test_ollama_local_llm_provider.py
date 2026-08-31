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
    dimensioniert) - mit dem neuen, schnelleren Standardmodell
    (qwen2.5:1.5b, siehe app/config/settings.py) reicht ein deutlich
    kuerzeres, UI-vertretbares Timeout. Regressionsschutz gegen eine
    versehentliche Rueckkehr zum alten 600s-Default."""
    provider = OllamaLocalLLMProvider(base_url="http://localhost:11434", model="qwen2.5:1.5b")
    assert provider.timeout_seconds == 120.0
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
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(json_data={"response": "Kurze lokale Zusammenfassung."}),
    )
    provider = _provider()

    result = provider.process(_payload())

    assert result.text == "Kurze lokale Zusammenfassung."
    assert result.model == "qwen3:4b"


def test_process_sends_deterministic_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _fake_post(url, *, json, timeout):
        captured["json"] = json
        return _FakeResponse(json_data={"response": "Ok."})

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = _provider()

    provider.process(_payload())

    assert captured["json"]["options"]["temperature"] == 0.0
    assert captured["json"]["model"] == "qwen3:4b"


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
