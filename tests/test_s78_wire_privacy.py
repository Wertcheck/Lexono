"""§78 – Datenschutzpfad des Antwort-Benchmarks bis auf die HTTP-Leitung (nur lokaler QA-Recorder, Testschluessel).

Die Akten des Antwort-Benchmarks (LE-D005, LE-D006, LE-T004) laufen in EINER Datenbank durch die echte Pipeline
(Import, Verarbeitung, Chat, Schriftsatz). Statt nur den Payload vor der Cloud-Grenze zu erfassen, reicht der Writer
ihn an den ECHTEN Anthropic-Provider aus der echten Factory weiter (Siegelpruefung, Endpunkt-Politik, SDK). Dieser
spricht ausschliesslich den lokalen Aufzeichnungsserver auf 127.0.0.1 an (QA-Freigabe, toter Proxy als Riegel,
gefaelschter Schluessel) - kein Modell, keine Kosten.

Geprueft werden die tatsaechlich gesendeten Bytes: kein geschuetzter Wert (inkl. Varianten) irgendeiner der drei
Akten, nur der Testschluessel, nur /v1/messages. Fail-Closed: faellt die Namenserkennung aus, erreicht keine einzige
Anfrage die Leitung.

Grenze: belegt den Pfad Lexono-Pipeline -> HTTP auf diesem Rechner. NICHT belegt: TLS/realer Endpunkt, Relay/Gateway-
Server, eine kuenftige Benchmark-Ausfuehrung (es gibt keine - C-14 offen) und Vollstaendigkeit der Erkennung jenseits
dieser synthetischen Akten."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.legal_bench import e2e
from scripts.legal_bench.corpus import load_cases

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_privacy_wire_payload as wire  # noqa: E402
from fake_embedding_provider import FakeEmbeddingProvider  # noqa: E402

CASES = ("LE-D005", "LE-D006", "LE-T004")


def _strings(value) -> list[str]:  # noqa: ANN001
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _wire_text(request: dict) -> str:
    """Alle Zeichenketten der tatsaechlich gesendeten JSON-Bytes (JSON-Escapes wie \\n aufgeloest)."""
    return "\n".join(_strings(json.loads(request["raw"].decode("utf-8"))))


class _ForwardingWriter(e2e.CapturingWriter):
    """Wie CapturingWriter, sendet den Payload aber ueber den echten Provider an den lokalen Recorder."""

    provider = None
    sent: list = []

    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        type(self).sent.append(payload)
        return type(self).provider.write(payload)


@pytest.fixture(scope="module")
def wire_env():
    if e2e._tesseract() is None:
        pytest.skip("Tesseract nicht verfuegbar - Scans waeren nicht auswertbar")
    rec = wire._Recorder()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_BASE_URL", rec.url)
        mp.setenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1")
        mp.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        mp.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        mp.setenv("NO_PROXY", "127.0.0.1,localhost")
        mp.setattr(_ForwardingWriter, "provider", wire._provider())
        mp.setattr(_ForwardingWriter, "sent", [])
        mp.setattr(e2e, "CapturingWriter", _ForwardingWriter)
        yield rec, mp
    rec.server.shutdown()
    rec.server.server_close()


@pytest.fixture(scope="module")
def outcome(wire_env, tmp_path_factory):
    rec, _ = wire_env
    cases = [c for c in load_cases() if c["id"] in CASES]
    assert {c["id"] for c in cases} == set(CASES)
    result = e2e.run(cases, tmp_path_factory.mktemp("s78_wire"), embedding=(FakeEmbeddingProvider(), "fake"))
    protect = {c["id"]: sorted({v for p in c["reference"]["privacy"]["protect"] for v in e2e.leak_variants(p)})
               for c in cases}
    return {"result": result, "requests": list(rec.requests), "protect": protect, "sent": list(_ForwardingWriter.sent)}


def test_every_cloud_payload_actually_went_over_the_wire(outcome) -> None:
    tasks = [t for r in outcome["result"]["results"] for t in r["tasks"] if t["kind"] in ("chat", "schriftsatz")]
    with_payload = [t for t in tasks if "payload_chars" in t]
    assert with_payload, "keine Cloud-Anfrage erzeugt - Nachweis waere leer"
    # Aufgaben der Akten + Zusatzpruefungen der Pipeline (fremder Anhang, Freigabe) - jede einzelne ging ueber die Leitung
    assert len(outcome["sent"]) >= len(with_payload)
    assert len(outcome["requests"]) == len(outcome["sent"])
    assert not [t for t in tasks if t.get("status") == "error"]


def test_wire_bytes_contain_no_protected_value_of_any_case(outcome) -> None:
    """Eigene UND fremde Akten: kein geschuetzter Wert (inkl. Nachname, Datumsvarianten) auf der Leitung."""
    findings = []
    for i, request in enumerate(outcome["requests"]):
        text = _wire_text(request)
        raw = request["raw"].decode("utf-8")
        for case_id, variants in outcome["protect"].items():
            findings += [(i, case_id, v[:4]) for v in variants if e2e._leaked(v, text) or v in raw]
    assert findings == [], findings


def test_wire_uses_only_the_fake_key_and_the_messages_endpoint(outcome) -> None:
    for request in outcome["requests"]:
        assert request["path"] == "/v1/messages"
        assert request["headers"].get("x-api-key") == wire.FAKE_KEY
        assert "authorization" not in request["headers"]
        assert "[" in _wire_text(request)  # Platzhalter vorhanden


def test_pipeline_summary_reports_no_privacy_finding(outcome) -> None:
    summary = e2e.summarise(outcome["result"]["results"])
    for split in ("dev", "test"):
        assert summary[split]["kritisch"] == [] and summary[split]["pii_leaks"] == 0
        assert summary[split]["fremde_akte_im_payload"] == 0


def test_fail_closed_when_name_recognition_fails(wire_env, tmp_path) -> None:
    rec, mp = wire_env
    import app.privacy.gateway as gateway_module

    def _broken(_text):  # noqa: ANN001, ANN202
        raise RuntimeError("Namenserkennung ausgefallen (QA)")

    before = len(rec.requests)
    with pytest.MonkeyPatch.context() as local:
        local.setattr(gateway_module, "detect_presidio_entities", _broken)
        cases = [c for c in load_cases() if c["id"] == "LE-T004"]
        result = e2e.run(cases, tmp_path, embedding=(FakeEmbeddingProvider(), "fake"))
    tasks = [t for r in result["results"] for t in r["tasks"] if t["kind"] in ("chat", "schriftsatz")]
    assert tasks and all("payload_chars" not in t for t in tasks), tasks
    assert len(rec.requests) == before, "trotz Ausfall der Namenserkennung wurde gesendet"
    by_id = {t["id"]: t for t in tasks}
    assert by_id["Q1"]["status"] == "blockiert"  # Chat: sauber blockiert, Hinweis an die Nutzerin
    # §78 Befund B1, behoben in §79: auch der Schriftsatzpfad blockiert jetzt geordnet mit festem, inhaltsfreiem Grund
    from app.privacy.gateway import PRIVACY_CHECK_FAILED_REASON

    assert by_id["Q2"]["status"] == "blockiert", by_id["Q2"]
    assert by_id["Q2"]["blocked_reasons"] == [PRIVACY_CHECK_FAILED_REASON]
