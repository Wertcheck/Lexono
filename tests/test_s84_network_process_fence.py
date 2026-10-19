"""§84: Angriffstests gegen Netz- und Prozesszaun (scripts/legal_bench/net_fence.py) und Providerpruefung.

Sicherheit der Tests selbst (technisch, nicht nur Absicht):
1. Jeder Angriff laeuft nur bei nachweislich aktivem Zaun (`_attack` prueft `net_fence.active()`).
2. Ziele sind ausschliesslich nicht routbare Adressen (TEST-NET-1 192.0.2.0/24, RFC 5737) bzw. reservierte Namen
   (`.invalid`, RFC 2606) - selbst bei einem Fehler erreicht nichts einen echten Endpunkt.
3. Ein zweiter, test-eigener Audit-Hook (`_safety_net`) laeuft NACH dem Zaun: erreicht ein externes Ziel diese Stufe, ist
   der Zaun durchlaessig - der Hook bricht die Operation ab und der Test schlaegt fehl.
Unterprozess-Angriffe weisen ueber eine Marker-Datei nach, dass der Kindprozess nie startete. Kosten 0 EUR."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from scripts.legal_bench import answer_driver, net_fence
from tests.test_s80_benchmark_driver import EchoProvider, _config, _driver

TEST_NET = "192.0.2.1"
INVALID = "lexono-fence-test.invalid"
_SAFETY = {"armed": False, "breaches": []}


def _external(host) -> bool:  # noqa: ANN001
    if host in (None, "", b""):
        return False
    host = host.decode() if isinstance(host, bytes) else str(host)
    if host.lower() in ("localhost", "localhost."):
        return False
    try:
        return not ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return True


def _safety_net(event: str, args: tuple) -> None:
    """Zweite Linie NUR fuer diese Tests: wird ein externes Ziel bis hierher durchgereicht, ist der Zaun undicht."""
    if not _SAFETY["armed"]:
        return
    host = None
    if event in ("socket.connect", "socket.sendto", "socket.sendmsg") and isinstance(args[1], tuple):
        host = args[1][0]
    elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"):
        host = args[0]
    if host is not None and _external(host):
        _SAFETY["breaches"].append(f"{event}:{host}")
        raise AssertionError(f"Zaun undicht: {event} zu {host!r} erreichte die zweite Linie")


@pytest.fixture(scope="module", autouse=True)
def safety_net():
    net_fence._install()  # Zaun-Hook zuerst, Test-Hook danach (Reihenfolge der Hooks)
    if not getattr(sys, "_lexono_s84_safety", False):
        sys.addaudithook(_safety_net)
        sys._lexono_s84_safety = True  # noqa: SLF001
    _SAFETY["armed"] = True
    yield
    _SAFETY["armed"] = False
    assert _SAFETY["breaches"] == [], _SAFETY["breaches"]


@pytest.fixture
def fence():
    with net_fence.loopback_only(check_existing_connections=False):
        assert net_fence.active()
        yield


def _attack(fn, expected=net_fence.NetworkFenceViolation):  # noqa: ANN001, ANN202
    assert net_fence.active(), "Angriff nur bei aktivem Zaun"
    with pytest.raises(BaseException) as exc:
        fn()
    chain, cur = [], exc.value
    while cur is not None and len(chain) < 12:
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__ or next(
            (a for a in getattr(cur, "args", ()) if isinstance(a, BaseException)), getattr(cur, "reason", None)
            if isinstance(getattr(cur, "reason", None), BaseException) else None)
    marker = "prozesszaun:" if expected is net_fence.ProcessFenceViolation else "netzzaun:"
    text = " ".join(f"{type(e).__name__}: {e}" for e in chain).lower()
    assert any(isinstance(e, expected) for e in chain) or marker in text, text[:400]
    return exc.value


# --- Netzwerk: direkte Sockets ---------------------------------------------------------------------------------------


def test_raw_and_c_level_sockets_are_blocked(fence) -> None:
    import _socket

    for connect in (lambda s: s.connect((TEST_NET, 443)), lambda s: s.connect_ex((TEST_NET, 443))):
        s = socket.socket()
        try:
            _attack(lambda: connect(s))
        finally:
            s.close()
    raw = _socket.socket()
    try:
        _attack(lambda: raw.connect((TEST_NET, 443)))  # roher C-Socket ohne Python-Klasse
    finally:
        raw.close()


def test_udp_send_is_blocked(fence) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _attack(lambda: s.sendto(b"x", (TEST_NET, 53)))
    finally:
        s.close()


def test_dns_resolution_of_external_names_is_blocked_before_any_query(fence) -> None:
    _attack(lambda: socket.getaddrinfo(INVALID, 443))
    _attack(lambda: socket.gethostbyname(INVALID))
    _attack(lambda: socket.gethostbyname_ex(INVALID))
    _attack(lambda: socket.gethostbyaddr(TEST_NET))
    _attack(lambda: socket.create_connection((INVALID, 443), timeout=1))


# --- Netzwerk: alternative HTTP-Bibliotheken, asyncio, Threads ------------------------------------------------------------


def test_alternative_http_libraries_are_blocked(fence) -> None:
    import http.client
    import urllib.request

    import httpx
    import requests

    _attack(lambda: urllib.request.urlopen(f"http://{INVALID}/", timeout=1))  # noqa: S310
    _attack(lambda: http.client.HTTPConnection(TEST_NET, 80, timeout=1).request("GET", "/"))
    _attack(lambda: requests.get(f"http://{INVALID}/", timeout=1))
    _attack(lambda: requests.get(f"http://{TEST_NET}/", timeout=1))
    _attack(lambda: httpx.get(f"http://{INVALID}/", timeout=1))
    _attack(lambda: httpx.get(f"https://{TEST_NET}/", timeout=1))


def test_asyncio_connections_are_blocked(fence) -> None:
    _attack(lambda: asyncio.run(asyncio.wait_for(asyncio.open_connection(TEST_NET, 443), 2)))
    _attack(lambda: asyncio.run(asyncio.wait_for(asyncio.open_connection(INVALID, 443), 2)))


def test_threads_are_covered_process_wide(fence) -> None:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            socket.create_connection((TEST_NET, 443), timeout=1)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(10)
    assert errors and isinstance(errors[0], net_fence.NetworkFenceViolation), errors


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DLLs")
def test_native_network_apis_via_ctypes_are_blocked(fence) -> None:
    import ctypes

    _attack(lambda: ctypes.WinDLL("winhttp"))
    _attack(lambda: ctypes.WinDLL("ws2_32"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DLLs")
def test_symbol_lookup_on_preloaded_network_dll_is_blocked() -> None:
    import ctypes

    preloaded = ctypes.WinDLL("ws2_32")  # VOR dem Zaun geladen (erlaubt)
    with net_fence.loopback_only(check_existing_connections=False):
        _attack(lambda: preloaded.connect)
        _attack(lambda: preloaded.getaddrinfo)


# --- Netzwerk: erlaubte lokale Verbindungen (Positivtests) -----------------------------------------------------------------


def test_loopback_recorder_connections_stay_possible(fence) -> None:
    import urllib.request

    import requests

    from tests.test_privacy_wire_payload import _Recorder

    rec = _Recorder()
    try:
        assert socket.getaddrinfo("localhost", 80)
        assert socket.getaddrinfo("127.0.0.1", 80)
        socket.create_connection(("127.0.0.1", rec.server.server_address[1]), timeout=2).close()
        assert requests.post(rec.url + "/v1/messages", json={"stream": False}, timeout=5).status_code == 200
        req = urllib.request.Request(rec.url + "/v1/messages", data=b'{"stream": false}', method="POST",
                                     headers={"content-type": "application/json"})
        assert urllib.request.urlopen(req, timeout=5).status == 200  # noqa: S310
        assert len(rec.requests) == 2
    finally:
        rec.server.shutdown()
        rec.server.server_close()


# --- Prozesse ---------------------------------------------------------------------------------------------------------------


def _child(marker: Path) -> list[str]:
    code = f"open({str(marker)!r}, 'w').write('gestartet'); import socket; socket.create_connection(({TEST_NET!r}, 443), timeout=1)"
    return [sys.executable, "-c", code]


def test_subprocesses_are_blocked_before_they_start(fence, tmp_path) -> None:
    marker = tmp_path / "kind_gestartet.txt"
    _attack(lambda: subprocess.run(_child(marker), timeout=20), net_fence.ProcessFenceViolation)
    _attack(lambda: subprocess.Popen(" ".join(f'"{a}"' for a in _child(marker)[:2]) + " -V", shell=True),
            net_fence.ProcessFenceViolation)
    _attack(lambda: os.system(f'"{sys.executable}" -V'), net_fence.ProcessFenceViolation)
    _attack(lambda: os.popen(f'"{sys.executable}" -V'), net_fence.ProcessFenceViolation)
    _attack(lambda: os.spawnl(os.P_WAIT, sys.executable, sys.executable, "-V"), net_fence.ProcessFenceViolation)
    if sys.platform == "win32":
        import _winapi

        _attack(lambda: os.startfile(str(tmp_path)), net_fence.ProcessFenceViolation)
        _attack(lambda: _winapi.CreateProcess(sys.executable, f'"{sys.executable}" -V', None, None, False, 0, None, None,
                                              subprocess.STARTUPINFO()), net_fence.ProcessFenceViolation)
    assert not marker.exists()  # kein Kindprozess wurde gestartet


def test_only_allowlisted_programs_with_exact_arguments_start() -> None:
    # §85: nur mit absolutem, festgeschriebenem Pfad; der blosse Name wuerde von Windows eigenstaendig gesucht
    git = net_fence.trusted_executable("git")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    with net_fence.loopback_only([("git", ("rev-parse", "HEAD"))], check_existing_connections=False):
        out = subprocess.run([git, "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parents[1], env=env)
        assert out.returncode == 0 and len(out.stdout.strip()) == 40
        _attack(lambda: subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True), net_fence.ProcessFenceViolation)
        _attack(lambda: subprocess.run([git, "fetch"], capture_output=True, env=env), net_fence.ProcessFenceViolation)
        _attack(lambda: subprocess.run([git, "rev-parse", "HEAD", "--remote"], capture_output=True, env=env),
                net_fence.ProcessFenceViolation)


def test_tesseract_allowlist_matches_the_driver() -> None:
    from scripts.legal_bench import e2e

    tesseract = e2e._tesseract()
    if tesseract is None:
        pytest.skip("Tesseract nicht verfuegbar")
    rules = answer_driver.allowed_programs()
    assert [r.name for r in rules] == ["tesseract"]  # §85: git ist nicht mehr zugelassen
    with net_fence.loopback_only(rules, check_existing_connections=False):
        assert subprocess.run([tesseract, "--version"], capture_output=True).returncode == 0


def test_missing_allowlisted_program_fails_closed() -> None:
    with pytest.raises(net_fence.ProcessFenceViolation):
        with net_fence.loopback_only([("lexono-gibt-es-nicht-84.exe", None)], check_existing_connections=False):
            pass
    assert not net_fence.active()


# --- Lebenszyklus: Aktivieren, Ueberwachen, Beenden -------------------------------------------------------------------------


def test_fence_is_released_after_exceptions_and_nesting(tmp_path) -> None:
    with pytest.raises(RuntimeError):
        with net_fence.loopback_only(check_existing_connections=False):
            raise RuntimeError("Fehler im Lauf")
    assert not net_fence.active()
    with net_fence.loopback_only(check_existing_connections=False):
        with net_fence.loopback_only(check_existing_connections=False):
            assert net_fence.active()
        assert net_fence.active()  # innerer Kontext beendet den aeusseren nicht
    assert not net_fence.active()
    marker = tmp_path / "danach.txt"
    subprocess.run([sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ok')"], check=True, timeout=30)
    assert marker.exists()  # nach dem Zaun wieder normal (kein dauerhafter Nebeneffekt)


def test_activation_fails_closed_on_existing_external_connection_or_check_error(monkeypatch) -> None:
    monkeypatch.setattr(net_fence, "existing_external_connections", lambda: [f"{TEST_NET}:443"])
    with pytest.raises(net_fence.NetworkFenceViolation, match="Bestehende externe Verbindung"):
        with net_fence.loopback_only():
            pass

    def broken():  # noqa: ANN202
        raise OSError("netstat nicht verfuegbar")

    monkeypatch.setattr(net_fence, "existing_external_connections", broken)
    with pytest.raises(net_fence.NetworkFenceViolation, match="Startpruefung"):
        with net_fence.loopback_only():
            pass
    assert not net_fence.active()


@pytest.mark.skipif(sys.platform != "win32", reason="netstat-Pruefung nur unter Windows")
def test_existing_connection_check_runs_on_this_machine() -> None:
    assert net_fence.existing_external_connections() == []  # dieser Testprozess haelt keine externe Verbindung


# --- Providerpruefung und Treiber ---------------------------------------------------------------------------------------


class _Holder:
    def __init__(self, inner):  # noqa: ANN001
        self.inner = inner


def test_provider_check_refuses_other_http_clients(monkeypatch) -> None:
    import httpx
    import requests

    monkeypatch.delenv("LEXONO_GATEWAY_URL", raising=False)
    monkeypatch.delenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", raising=False)
    for client in (requests.Session(), httpx.Client(base_url=f"https://{INVALID}"), _Holder(_Holder(httpx.Client()))):
        with pytest.raises(answer_driver.RealCloudDisabled):
            answer_driver.assert_local_only(_Holder(client))
    monkeypatch.setenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1")
    answer_driver.assert_local_only(_Holder(httpx.Client(base_url="http://127.0.0.1:9")))  # Positivfall


class _SpawningProvider(EchoProvider):
    def __init__(self, marker: Path) -> None:
        super().__init__()
        self.marker = marker

    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        subprocess.run(_child(self.marker), timeout=20)  # versteckter Netzzugriff ueber einen Unterprozess
        raise AssertionError("unerreichbar")


class _DnsProvider(EchoProvider):
    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        import requests

        requests.get(f"https://{INVALID}/v1/messages", timeout=1)
        raise AssertionError("unerreichbar")


@pytest.mark.usefixtures("synthetic_legal_state")
@pytest.mark.parametrize("kind", ["unterprozess", "dns_http"])
def test_driver_run_stops_hidden_subprocess_and_dns_access(tmp_path, kind) -> None:
    from scripts.legal_bench.answer_store import RunStore

    marker = tmp_path / "kind_gestartet.txt"
    provider = _SpawningProvider(marker) if kind == "unterprozess" else _DnsProvider()
    result = _driver(_config(tmp_path), RunStore(tmp_path / "laeufe"), provider, tmp_path).run()
    assert result["status"] == "gestoppt_fehlgeschlagen" and len(provider.payloads) == 1, result["diagnose"]
    expected = "ProcessFenceViolation" if kind == "unterprozess" else "ConnectionError"
    assert expected in " ".join(result["diagnose"]), result["diagnose"]
    assert not marker.exists()
    assert not net_fence.active()  # nach dem Lauf wieder freigegeben


def test_existing_connection_parser_is_locale_independent(monkeypatch) -> None:
    """netstat lokalisiert den Status (deutsch: HERGESTELLT/ABHOEREN) - erkannt wird unabhaengig davon."""
    pid = os.getpid()
    sample = ("Aktive Verbindungen\n\n  Proto  Lokale Adresse         Remoteadresse          Status           PID\n"
              f"  TCP    0.0.0.0:135            0.0.0.0:0              ABHÖREN          {pid}\n"
              f"  TCP    127.0.0.1:50000        127.0.0.1:50001        HERGESTELLT      {pid}\n"
              f"  TCP    10.0.0.5:51234         {TEST_NET}:443         HERGESTELLT      {pid}\n"
              f"  TCP    10.0.0.5:51235         {TEST_NET}:443         HERGESTELLT      99999999\n")
    monkeypatch.setattr(net_fence.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, sample, ""))
    monkeypatch.setattr(net_fence.sys, "platform", "win32")
    assert net_fence.existing_external_connections() == [f"{TEST_NET}:443", f"{TEST_NET}:443"]  # TCP + TCPv6-Aufruf


@pytest.mark.skipif(sys.platform != "win32", reason="Proactor nur unter Windows")
def test_windows_proactor_is_fenced_and_restored() -> None:
    """Befund P2: asyncio-Proactor (Overlapped ConnectEx) loest kein Audit-Ereignis aus - vorher Timeout statt Sperre."""
    import asyncio.windows_events as windows_events

    original = windows_events.IocpProactor.connect, windows_events.IocpProactor.sendto
    with net_fence.loopback_only(check_existing_connections=False):
        assert windows_events.IocpProactor.connect is not original[0]
        _attack(lambda: asyncio.run(asyncio.wait_for(asyncio.open_connection(TEST_NET, 443), 5)))

        errors: list = []

        class Recording(asyncio.DatagramProtocol):
            def error_received(self, exc):  # noqa: ANN001
                errors.append(exc)  # asyncio meldet Sendefehler des Proactors hier (nicht als Ausnahme)

        async def udp():  # noqa: ANN202
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(Recording, local_addr=("127.0.0.1", 0))
            try:
                transport.sendto(b"x", (TEST_NET, 53))
                await asyncio.sleep(0.3)
            finally:
                transport.close()

        asyncio.run(udp())
        assert any(isinstance(e, net_fence.NetworkFenceViolation) for e in errors), errors
    assert (windows_events.IocpProactor.connect, windows_events.IocpProactor.sendto) == original


def test_safety_net_of_these_tests_works_without_the_fence() -> None:
    """Nachweis der Testsicherheit: auch OHNE Zaun bricht der Test-Wachter eine externe Aufloesung VOR der Abfrage ab."""
    assert not net_fence.active()
    with pytest.raises(AssertionError, match="Zaun undicht"):
        socket.getaddrinfo(INVALID, 443)
    assert _SAFETY["breaches"] == [f"socket.getaddrinfo:{INVALID}"]
    _SAFETY["breaches"].clear()  # erwarteter Befund dieses Nachweises - kein echter Durchbruch
