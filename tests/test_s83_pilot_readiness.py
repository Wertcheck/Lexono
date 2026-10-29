"""§83: Technische Pilotreife - Endpunktsperre gegen Wrapper/Unterklassen/versteckte Clients, Netzzaun, Unveraenderlichkeit
der Freigabedateien durch Test-Fixtures, Pruefpaket der Referenzloesungen synchron zu den Quelldateien.

Provider werden nur KONSTRUIERT (kein Aufruf); der Netzzaun wird mit nicht routbaren Adressen (TEST-NET 192.0.2.0/24)
geprueft und verweigert VOR jedem Verbindungsaufbau. Externe Aufrufe: 0, Kosten: 0 EUR."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

from scripts.legal_bench import answer_driver, answer_run, answer_specs, net_fence, review_packet
from tests.test_s80_benchmark_driver import EchoProvider, _config, _driver

KEY = "sk-ant-qa-synthetisch-kein-echter-schluessel-0000"


@pytest.fixture
def no_cloud_env(monkeypatch):
    for name in ("ANTHROPIC_BASE_URL", "LEXONO_GATEWAY_URL", "LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


def _real(base_url: str | None = None):  # noqa: ANN202
    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider

    kwargs = {"base_url": base_url} if base_url else {}
    return AnthropicClaudeWritingProvider(api_key=KEY, model="claude-x", **kwargs)


class _Wrapper:
    def __init__(self, inner):  # noqa: ANN001
        self.inner = inner


def _variants():  # noqa: ANN202
    import anthropic
    import httpx

    from app.ai_providers.anthropic_writing_provider import AnthropicClaudeWritingProvider

    class Sub(AnthropicClaudeWritingProvider):
        pass

    return {"echt": _real(), "wrapper": _Wrapper(_real()), "unterklasse": Sub(api_key=KEY, model="claude-x"),
            "liste": _Wrapper([_real()]), "dict": _Wrapper({"p": _real()}), "verschachtelt": _Wrapper(_Wrapper(_real())),
            "roher_anthropic_client": _Wrapper(anthropic.Anthropic(api_key=KEY)),
            "httpx_ohne_endpunkt": _Wrapper(httpx.Client())}


@pytest.mark.usefixtures("no_cloud_env")
@pytest.mark.parametrize("name", ["echt", "wrapper", "unterklasse", "liste", "dict", "verschachtelt",
                                  "roher_anthropic_client", "httpx_ohne_endpunkt"])
def test_endpoint_lock_finds_hidden_real_clients(name) -> None:
    with pytest.raises(answer_driver.RealCloudDisabled):
        answer_driver.assert_local_only(_variants()[name])


def test_loopback_clients_and_plain_mocks_are_allowed(monkeypatch) -> None:
    from app.ai_providers.claude_writing_provider import ClaudeWritingResult

    monkeypatch.delenv("LEXONO_GATEWAY_URL", raising=False)
    monkeypatch.setenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1")
    answer_driver.assert_local_only(_Wrapper(_real("http://127.0.0.1:9")))
    mock = EchoProvider()
    mock.last = ClaudeWritingResult(text="x")  # Datenobjekt aus app.ai_providers - kein Client
    answer_driver.assert_local_only(mock)
    monkeypatch.delenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT")
    with pytest.raises(answer_driver.RealCloudDisabled):  # Loopback ohne ausdrueckliche QA-Freigabe
        answer_driver.assert_local_only(_Wrapper(_real("http://127.0.0.1:9")))


@pytest.mark.usefixtures("no_cloud_env", "synthetic_legal_state")
def test_driver_refuses_a_wrapped_real_provider_before_anything_happens(tmp_path) -> None:
    from scripts.legal_bench.answer_store import RunStore

    store = RunStore(tmp_path / "laeufe")
    with pytest.raises(answer_driver.RealCloudDisabled):
        answer_driver.BenchmarkDriver(_config(tmp_path), store, _Wrapper(_real()), workdir=tmp_path / "w").run()
    assert not store.exists("qa80-lauf")


# --- Netzzaun ------------------------------------------------------------------------------------------------------------


def test_network_fence_blocks_non_loopback_and_restores_afterwards() -> None:
    original = socket.socket.connect, socket.create_connection
    with net_fence.loopback_only():
        with pytest.raises(net_fence.NetworkFenceViolation):
            socket.create_connection(("api.anthropic.com", 443), timeout=1)  # Hostname: vor jeder DNS-Abfrage verweigert
        with pytest.raises(net_fence.NetworkFenceViolation):
            socket.create_connection(("192.0.2.1", 443), timeout=1)
        s = socket.socket()
        try:
            with pytest.raises(net_fence.NetworkFenceViolation):
                s.connect(("192.0.2.1", 443))
            with pytest.raises(net_fence.NetworkFenceViolation):
                s.connect_ex(("10.0.0.1", 443))
        finally:
            s.close()
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        try:
            socket.create_connection(server.getsockname(), timeout=2).close()  # Loopback erlaubt
        finally:
            server.close()
    assert (socket.socket.connect, socket.create_connection) == original


class _HiddenClientProvider(EchoProvider):
    """Simuliert einen Provider mit verstecktem Netzzugang (fuer die Objektpruefung unsichtbar)."""

    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        connect = socket.create_connection
        connect(("192.0.2.1", 443), timeout=1)  # wuerde ohne Zaun eine Verbindung versuchen
        raise AssertionError("unerreichbar")


@pytest.mark.usefixtures("synthetic_legal_state")
def test_network_fence_stops_a_hidden_client_during_a_run(tmp_path) -> None:
    from scripts.legal_bench.answer_store import RunStore

    provider = _HiddenClientProvider()
    result = _driver(_config(tmp_path), RunStore(tmp_path / "laeufe"), provider, tmp_path).run()
    assert result["status"] == "gestoppt_fehlgeschlagen" and len(provider.payloads) == 1
    assert "NetworkFenceViolation" in " ".join(result["diagnose"])


# --- Freigaben und Fixtures --------------------------------------------------------------------------------------------

GUARDED = ["benchmarks/legal_answers_v1/run_config.template.json", "benchmarks/legal_answers_v1/rubric.json",
           "benchmarks/legal_answers_v1/LOCK.json", "benchmarks/legal_answers_v1/specs/dev/LA-D005.json",
           "benchmarks/legal_answers_v1/specs/dev/LA-D006.json", "benchmarks/legal_answers_v1/specs/test/LA-T004.json"]


def _head(path: str) -> bytes:
    return subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, cwd=answer_specs.REPO, check=True).stdout


@pytest.mark.usefixtures("synthetic_legal_state")
def test_fixture_changes_no_approval_file(tmp_path) -> None:
    """Die Positivkontroll-Fixture ersetzt nur eine Funktion im Speicher - Dateien bleiben wie im Commit."""
    from tests.test_s78_benchmark_gate import _valid

    assert answer_run.check(_valid())["runnable"] is True  # Fixture wirkt
    for path in GUARDED:
        assert (answer_specs.REPO / path).read_bytes().replace(b"\r\n", b"\n") == _head(path).replace(b"\r\n", b"\n"), path
    conftest = (answer_specs.REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    start = conftest.index("def synthetic_legal_state")
    source = conftest[start:conftest.index("@pytest.fixture", start)]
    assert "monkeypatch.setattr" in source and "write" not in source and "open(" not in source


def test_without_fixture_no_configuration_is_approvable() -> None:
    from tests.test_s78_benchmark_gate import _valid

    result = answer_run.check(_valid())
    assert result["runnable"] is False and any("Widerspruch" in v for v in result["violations"])
    template = answer_run.load_config(answer_run.TEMPLATE)
    assert template["approved"] is False and all(v is None for v in template["approvals"].values())


# --- Pruefpaket ------------------------------------------------------------------------------------------------------------


def test_review_packet_is_in_sync_with_the_source_files() -> None:
    assert review_packet.PACKET_PATH.read_text(encoding="utf-8") == review_packet.build_review_packet(), (
        "Pruefpaket veraltet - mit review_packet.write_review_packet() neu erzeugen")


def test_review_packet_states_its_limits_and_decides_nothing() -> None:
    text = review_packet.PACKET_PATH.read_text(encoding="utf-8")
    assert text.count("NICHT juristisch geprüft") == 1 and "FEHLT" not in text
    assert text.count("| | | | |") == 9  # 3 Faelle x (J1, J2, J3) - alle Entscheidungsfelder leer
    for spec in answer_specs.load_specs():
        assert f"## {spec['id']}" in text and spec["status"] in text
        for crit in spec["criteria"]:
            assert f"| {crit['id']} | {crit['dimension']} |" in text
    assert json.loads((answer_specs.ROOT / "entscheidungsmatrix.json").read_text(encoding="utf-8"))["c14"] == "GESPERRT"


def test_net_fence_module_opens_no_connection_itself() -> None:
    source = Path(net_fence.__file__).read_text(encoding="utf-8")
    assert "sys.addaudithook(_audit)" in source  # §84: Pruefung in der Laufzeit VOR dem Systemaufruf
    assert "socket.socket(" not in source and "urlopen" not in source and "create_connection(" not in source
