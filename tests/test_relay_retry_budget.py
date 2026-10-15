"""§44.1: Laufzeit- und Wiederholungsbudget des Relay-Schreibpfads.

1. Startpruefung `GatewaySettings.retry_budget_fits_client_timeout`: (Wiederholungen + 1) x Timeout + Wiederholungen x
   60 s (laengste vom SDK befolgte retry-after-Wartezeit) muss unter dem Client-Timeout liegen - sonst startet das Relay
   nicht.
2. Charakterisierung des ECHTEN Anthropic-SDK ueber `gateway.relay.call_anthropic` gegen einen lokalen Fake-Server
   (127.0.0.1, Zufallsport) - kein externer Netzverkehr, synthetische Daten, Zeiten im Sekundenmassstab.
   Belegt die Annahmen der Startpruefung - und ihre Grenze: der Timeout gilt je Lesevorgang, nicht als Gesamtfrist."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import gateway.relay as relay
from gateway.config import GatewaySettings, get_gateway_settings
from gateway.relay import RelayError, call_anthropic
from gateway.schemas import RelayRequest

# --- 1. Startpruefung ----------------------------------------------------------------------------------------------


def test_defaults_fit_the_client_timeout() -> None:
    settings = GatewaySettings(_env_file=None)
    assert (settings.anthropic_timeout_seconds, settings.anthropic_max_retries, settings.client_timeout_seconds) == (
        55.0, 0, 60.0)


@pytest.mark.parametrize("kwargs", [
    {"anthropic_max_retries": 1},                                      # 2 x 55 + 60 = 170 s
    {"anthropic_max_retries": 2},                                      # 3 x 55 + 120 = 285 s
    {"anthropic_timeout_seconds": 60.0},                               # genau gleich: nicht darunter
    {"anthropic_timeout_seconds": 90.0},                               # nur Relay-Timeout erhoeht
    {"anthropic_max_retries": 1, "anthropic_timeout_seconds": 5.0},    # 2 x 5 + 60 = 70 s >= 60 s
])
def test_configurations_beyond_the_client_timeout_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValidationError, match="CLIENT_TIMEOUT_SECONDS"):
        GatewaySettings(_env_file=None, **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"anthropic_timeout_seconds": 59.0},
    {"anthropic_max_retries": 1, "anthropic_timeout_seconds": 20.0, "client_timeout_seconds": 120.0},  # 100 < 120
])
def test_consistent_configurations_are_accepted(kwargs: dict) -> None:
    GatewaySettings(_env_file=None, **kwargs)


def test_relay_does_not_start_with_an_inconsistent_environment(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MAX_RETRIES", "2")
    get_gateway_settings.cache_clear()
    try:
        from gateway.main import app

        with pytest.raises(ValidationError, match="CLIENT_TIMEOUT_SECONDS"):
            with TestClient(app):
                pass
    finally:
        get_gateway_settings.cache_clear()


# --- 1b. Kopplung an den tatsaechlichen Client-Timeout (nur gespiegelt, nicht abgeglichen) -------------------------

def _template_value(name: str, key: str) -> str:
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
    match = re.search(rf"^{key}=(.+)$", text, re.MULTILINE)
    assert match, f"{key} fehlt in {name}"
    return match.group(1).strip()


def test_client_timeout_defaults_and_templates_are_mirrored() -> None:
    """Das Relay spiegelt den Client-Timeout (CLIENT_TIMEOUT_SECONDS) - Standardwerte und Vorlagen muessen gleich sein."""
    from app.config.settings import Settings

    client_default = Settings.model_fields["lexono_gateway_timeout_seconds"].default
    relay_default = GatewaySettings.model_fields["client_timeout_seconds"].default
    assert client_default == relay_default == 60.0
    assert float(_template_value(".env.example", "LEXONO_GATEWAY_TIMEOUT_SECONDS")) == float(
        _template_value(".env.gateway.example", "CLIENT_TIMEOUT_SECONDS")) == 60.0


def test_relay_cannot_see_a_shorter_client_timeout(monkeypatch) -> None:
    """Grenze (§44.1): CLIENT_TIMEOUT_SECONDS ist eine eigene Relay-Einstellung, kein Abgleich mit den Kanzlei-
    Installationen. Ein kuerzerer Client-Timeout wird vom Client tatsaechlich verwendet, die Startpruefung des Relays
    akzeptiert ihre Standardwerte trotzdem - der Relay-Versuch kann dann nach dem Client-Abbruch weiterlaufen."""
    from unittest.mock import patch

    from app.ai_providers.factory import build_writing_provider
    from app.config.settings import Settings
    from app.privacy.gateway import ClaudePrivacyGateway

    monkeypatch.setenv("LEXONO_GATEWAY_TIMEOUT_SECONDS", "30")
    client = Settings(_env_file=None, lexono_gateway_url="http://127.0.0.1:8700", lexono_gateway_client_id="cid",
                      lexono_gateway_client_secret="lxg_secret_synthetisch")
    assert client.lexono_gateway_timeout_seconds == 30.0
    payload = ClaudePrivacyGateway().prepare_request(purpose="formulate_draft",
                                                     sachverhalt="Die Anlage ist mangelhaft.").payload
    with patch("app.ai_providers.gateway_relay_client.httpx.post") as post:
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"text": "ok", "input_tokens": 1, "output_tokens": 1}
        build_writing_provider(client).write(payload)
    assert post.call_args.kwargs["timeout"] == 30.0  # tatsaechlicher Client-Timeout
    relay = GatewaySettings(_env_file=None)          # Relay mit Standardwerten: Startpruefung besteht
    assert relay.client_timeout_seconds == 60.0 and relay.anthropic_timeout_seconds > client.lexono_gateway_timeout_seconds


# --- 2. Echtes SDK gegen lokalen Fake ----------------------------------------------------------------------------

BODY = json.dumps({"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                   "content": [{"type": "text", "text": "synthetisch"}], "stop_reason": "end_turn",
                   "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
REQUEST = RelayRequest(model="claude-sonnet-5", max_tokens=10, system=[{"type": "text", "text": "System"}],
                       messages=[{"role": "user", "content": "synthetisch"}], thinking={"type": "disabled"})


class Fake:
    def __init__(self) -> None:
        self.times: list[float] = []
        self.plan: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("content-length", 0)))
                fake.times.append(time.monotonic())
                step = fake.plan[min(len(fake.times), len(fake.plan)) - 1]
                try:
                    if step.get("trickle"):
                        self.send_response(200)
                        self.send_header("content-length", str(len(BODY)))
                        self.end_headers()
                        size = len(BODY) // 4 + 1
                        for start in range(0, len(BODY), size):
                            time.sleep(step["trickle"])
                            self.wfile.write(BODY[start:start + size])
                            self.wfile.flush()
                        return
                    time.sleep(step.get("delay", 0))
                    status = step.get("status", 200)
                    body = BODY if status == 200 else b'{"type":"error","error":{"type":"x","message":"x"}}'
                    self.send_response(status)
                    for key, value in step.get("headers", {}).items():
                        self.send_header(key, value)
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture()
def fake(monkeypatch) -> Iterator[Fake]:
    server = Fake()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")  # Riegel: nichts ausser localhost erreichbar
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(relay, "_ANTHROPIC_API_BASE_URL", server.url)
    yield server
    server.server.shutdown()
    server.server.server_close()


def _call(timeout: float, retries: int) -> tuple[str, float]:
    started = time.monotonic()
    try:
        call_anthropic(api_key="sk-ant-local-fake", model="claude-sonnet-5", request=REQUEST,
                       timeout_seconds=timeout, max_retries=retries)
        outcome = "ok"
    except RelayError:
        outcome = "fehler"
    return outcome, time.monotonic() - started


@pytest.mark.parametrize("step", [
    {"status": 500}, {"status": 529}, {"status": 429, "headers": {"retry-after": "1"}},
    {"status": 400, "headers": {"x-should-retry": "true"}}, {"delay": 2.0},
])
def test_without_retries_the_sdk_makes_exactly_one_upstream_attempt(fake, step) -> None:
    fake.plan = [step, {"status": 200}]
    outcome, _ = _call(timeout=1.0, retries=0)
    assert outcome == "fehler" and len(fake.times) == 1


def test_the_sdk_honours_retry_after_between_attempts(fake) -> None:
    """Grundlage des 60-s-Terms der Startpruefung: die Wartezeit folgt dem Header, nicht dem kurzen Backoff."""
    fake.plan = [{"status": 429, "headers": {"retry-after": "2"}}, {"status": 200}]
    outcome, _ = _call(timeout=1.0, retries=1)
    assert outcome == "ok" and len(fake.times) == 2
    assert fake.times[1] - fake.times[0] >= 1.9


def test_timeout_is_per_read_not_a_total_deadline(fake) -> None:
    """Grenze jeder Laufzeitaussage: eine stueckweise Antwort (je Stueck unter dem Timeout) ueberschreitet ihn insgesamt."""
    fake.plan = [{"trickle": 0.6}]
    outcome, duration = _call(timeout=1.0, retries=0)
    assert outcome == "ok" and len(fake.times) == 1 and duration > 2.0
