"""Nachweis auf Netzwerkebene (PRIVACY_REVIEW_ARCHITECTURE.md §47): was der direkte Anthropic-Pfad TATSAECHLICH als
HTTP-Anfrage verschickt - nicht nur, was im SDK-Payload-Objekt steht.

Aufbau: echter Datenschutz-Gateway (Presidio) -> versiegelte Payload -> echter `AnthropicClaudeWritingProvider` aus
der echten Factory (inkl. Endpunkt-Politik) -> echtes Anthropic-SDK -> lokaler Aufzeichnungsserver auf 127.0.0.1
(nur mit der ausdruecklichen QA-Freigabe erlaubt). Ein toter Proxy verhindert jeden Weg nach draussen. Synthetische
Daten, gefaelschter Schluessel, kein Kosten verursachender Aufruf.

Grenze: belegt den direkten Pfad bis zur HTTP-Schicht auf diesem Rechner (unverschluesselt, Loopback). TLS zum echten
Anthropic-Endpunkt und der Relay-/Gateway-Pfad gegen einen realen Server sind damit NICHT belegt."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config.settings import Settings
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload

FAKE_KEY = "sk-ant-qa-synthetisch-kein-echter-schluessel-0000000000"
SYNTHETIC_TEXT = (
    "Unsere Mandantin Henrike Wallbaum-Ostertag, geboren am 03.07.1981, wohnhaft Lindenallee 14, 34117 Kassel, "
    "Telefon 0561 9876543, E-Mail h.wallbaum@beispiel-kanzlei.de, IBAN DE89 3704 0044 0532 0130 00, "
    "verlangt von der Gegnerin Konrad Pfefferkorn GmbH die Rückzahlung der Kaution von 4.250,00 EUR. "
    "Der Mietvertrag endete am 31.03.2026; die Rückgabe der Wohnung erfolgte am 02.04.2026."
)
PROTECTED = ["Wallbaum-Ostertag", "Henrike", "03.07.1981", "Lindenallee 14", "34117", "0561 9876543",
             "h.wallbaum@beispiel-kanzlei.de", "DE89 3704 0044 0532 0130 00", "Pfefferkorn"]
KEPT = ["4.250,00 EUR", "31.03.2026", "02.04.2026", "Kaution"]

_MESSAGE = {"id": "msg_qa", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": "Entwurf (synthetisch)."}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}


def _sse() -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {**_MESSAGE, "content": [], "stop_reason": None}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "Entwurf (synthetisch)."}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                recorder.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "raw": body})
                streaming = json.loads(body).get("stream") is True
                payload = _sse() if streaming else json.dumps(_MESSAGE).encode()
                self.send_response(200)
                self.send_header("content-type", "text/event-stream" if streaming else "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture()
def recorder(monkeypatch) -> Iterator[_Recorder]:
    rec = _Recorder()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", rec.url)
    monkeypatch.setenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")  # Riegel: nichts ausser Loopback erreichbar
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield rec
    rec.server.shutdown()
    rec.server.server_close()


@pytest.fixture(scope="module")
def sealed_payload() -> ClaudeRequestPayload:
    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt=SYNTHETIC_TEXT,
        known_entities={"mandant": ["Henrike Wallbaum-Ostertag", "Wallbaum-Ostertag"],
                        "gegner": ["Konrad Pfefferkorn GmbH"]},
        review_mode="mask",
    )
    assert result.allowed, result.reasons
    return result.payload


def _provider():
    from app.ai_providers.factory import build_writing_provider

    return build_writing_provider(Settings(_env_file=None, anthropic_api_key=FAKE_KEY, lexono_gateway_url=None))


def _assert_wire_clean(request: dict) -> None:
    wire = request["raw"].decode("utf-8")
    decoded = json.dumps(json.loads(wire), ensure_ascii=False)  # auch \\u-maskierte Umlaute vergleichbar machen
    for value in PROTECTED:
        assert value not in wire and value not in decoded, f"Klartext auf der Leitung: {value[:4]}..."
    for value in KEPT:
        assert value in decoded, f"juristisch relevanter Wert fehlt: {value}"
    assert "[" in decoded and "_01]" in decoded  # Platzhalter statt Werte
    assert request["headers"].get("x-api-key") == FAKE_KEY  # nur der gefaelschte Schluessel


def test_write_sends_only_pseudonymized_content_over_http(recorder, sealed_payload) -> None:
    _provider().write(sealed_payload)
    assert len(recorder.requests) == 1 and recorder.requests[0]["path"] == "/v1/messages"
    _assert_wire_clean(recorder.requests[0])


def test_streaming_sends_only_pseudonymized_content_over_http(recorder, sealed_payload) -> None:
    stream = _provider().write_stream(sealed_payload)
    chunks = list(stream)
    assert "".join(chunks) == "Entwurf (synthetisch)."
    assert len(recorder.requests) == 1
    _assert_wire_clean(recorder.requests[0])


def test_unsealed_or_altered_payload_never_reaches_the_network(recorder, sealed_payload) -> None:
    forged = ClaudeRequestPayload(schreibauftrag="formulate_draft", anonymisierter_sachverhalt=SYNTHETIC_TEXT)
    altered = sealed_payload.model_copy(update={"anonymisierter_sachverhalt": SYNTHETIC_TEXT})
    for payload in (forged, altered):
        with pytest.raises(Exception):
            _provider().write(payload)
    assert recorder.requests == []  # keine einzige HTTP-Anfrage


def test_loopback_endpoint_without_qa_flag_is_refused_before_any_request(recorder, monkeypatch, sealed_payload) -> None:
    monkeypatch.delenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT")
    with pytest.raises(Exception):
        _provider().write(sealed_payload)
    assert recorder.requests == []
