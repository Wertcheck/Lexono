"""§85: Schutzgrenzen des Netz-/Prozesszauns - Zulassungsliste (Pfad, Hash, Argumente, Umgebung), Commit ohne Prozess,
Loopback-Portbindung und dokumentierte Grenzen gegenueber nativem Code.

Kein Test erreicht ein externes Ziel: Netzwerkziele sind nur Loopback-Ports dieses Prozesses; Unterprozesse werden VOR dem
Start abgewiesen (Pruefung im Audit-Hook) oder sind lokal ohne Netzwerk (`tesseract --version`, OCR einer lokalen PNG).
Die Nachweise der Grenzen (native Pfade) laufen ausschliesslich gegen einen lokalen, nicht freigegebenen Port."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.legal_bench import answer_driver, answer_run, answer_specs, e2e, net_fence
from tests.test_s80_benchmark_driver import EchoProvider, _config, _driver

TESSERACT = e2e._tesseract()
needs_tesseract = pytest.mark.skipif(TESSERACT is None, reason="Tesseract nicht verfuegbar")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows")


def _png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (40, 20), "white").save(path, format="PNG")
    return path


def _spawn_refused(fn) -> net_fence.ProcessFenceViolation:  # noqa: ANN001
    assert net_fence.active()
    with pytest.raises(net_fence.ProcessFenceViolation) as exc:
        fn()
    return exc.value


# --- 1. Programmaufloesung und Pfadmanipulation -----------------------------------------------------------------------


@windows_only
def test_trusted_resolution_ignores_cwd_relative_and_unc_path_entries(tmp_path, monkeypatch) -> None:
    planted = tmp_path / "git.exe"
    planted.write_bytes(b"MZ-unschaedlicher-Platzhalter")  # wird nie ausgefuehrt
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "relativ\\bin", "\\\\lexono.invalid\\share", str(tmp_path),
                                                 os.environ["PATH"]]))
    resolved = net_fence.trusted_executable("git")
    assert resolved is None or Path(resolved).parent != tmp_path  # aktuelles Verzeichnis nie (auch als absoluter Eintrag)
    assert net_fence.trusted_executable("\\\\lexono.invalid\\share\\git.exe") is None  # UNC nie


@needs_tesseract
def test_bare_name_other_binary_and_cwd_binary_are_refused(tmp_path) -> None:
    other = tmp_path / "tesseract.exe"
    shutil.copy(TESSERACT, other)  # gleiche Datei, anderer Ort
    with net_fence.loopback_only(answer_driver.allowed_programs(), check_existing_connections=False):
        _spawn_refused(lambda: subprocess.run(["tesseract", "--version"], capture_output=True))
        _spawn_refused(lambda: subprocess.run([str(other), "--version"], capture_output=True))
        _spawn_refused(lambda: subprocess.run([sys.executable, "-V"], capture_output=True))
        _spawn_refused(lambda: subprocess.run(f'"{TESSERACT}" --version & whoami', shell=True, capture_output=True))


@windows_only
def test_binary_or_directory_changed_after_activation_is_refused(tmp_path) -> None:
    folder = tmp_path / "werkzeug"
    folder.mkdir()
    exe = folder / "werkzeug.exe"
    shutil.copy(Path(os.environ["WINDIR"]) / "System32" / "where.exe", exe)  # harmloses lokales Werkzeug
    rule = net_fence.ProgramRule("werkzeug", str(exe), ("/?",))
    with net_fence.loopback_only([rule], check_existing_connections=False):
        assert subprocess.run([str(exe), "/?"], capture_output=True).returncode in (0, 1)  # Positivfall
        with open(exe, "ab") as fh:
            fh.write(b"\x00")  # Binaerdatei manipuliert
        error = _spawn_refused(lambda: subprocess.run([str(exe), "/?"], capture_output=True))
        assert "veraendert" in str(error)
    exe.write_bytes((Path(os.environ["WINDIR"]) / "System32" / "where.exe").read_bytes())
    with net_fence.loopback_only([rule], check_existing_connections=False):
        (folder / "untergeschoben.dll").write_bytes(b"x")  # DLL neben das Programm gelegt
        assert "veraendert" in str(_spawn_refused(lambda: subprocess.run([str(exe), "/?"], capture_output=True)))


# --- 2. Argumente und Umgebung -------------------------------------------------------------------------------------------


@needs_tesseract
@pytest.mark.parametrize("make_args", [
    lambda d: ["http://lexono.invalid/bild.png", str(d / "out"), "-l", "deu", "txt"],
    lambda d: ["\\\\lexono.invalid\\share\\bild.png", str(d / "out"), "-l", "deu", "txt"],
    lambda d: [str(d / "bild.png"), "\\\\lexono.invalid\\share\\out", "-l", "deu", "txt"],
    lambda d: [str(d / "bild.png"), str(d / "out"), "--tessdata-dir", "\\\\lexono.invalid\\t", "-l", "deu", "txt"],
    lambda d: [str(d / "bild.png"), str(d / "out"), "-l", "deu", "-c", "tessedit_write_images=1", "txt"],
    lambda d: [str(d / "bild.png"), str(d / "out"), "-l", "deu;whoami", "txt"],
    lambda d: [str(d / "liste.txt"), str(d / "out"), "-l", "deu", "txt"],  # Bildliste (Zeilen koennten URLs sein)
    lambda d: ["bild.png", str(d / "out"), "txt"],  # relativer Pfad
    lambda d: ["-", str(d / "out"), "txt"],  # Standardeingabe
    lambda d: [str(d / "bild.png"), str(d / "out"), "pdf"],  # andere Ausgabe
], ids=["url_libcurl", "unc_eingabe", "unc_ausgabe", "tessdata_unc", "config_option", "sprache_injection",
        "bildliste", "relativ", "stdin", "pdf_ausgabe"])
def test_tesseract_argument_injection_is_refused(tmp_path, make_args) -> None:
    _png(tmp_path / "bild.png")
    (tmp_path / "liste.txt").write_text("http://lexono.invalid/bild.png\n", encoding="utf-8")
    with net_fence.loopback_only(answer_driver.allowed_programs(), check_existing_connections=False):
        error = _spawn_refused(lambda: subprocess.run([TESSERACT, *make_args(tmp_path)], capture_output=True, timeout=30))
    assert "unzulaessigen Argumenten" in str(error)


@needs_tesseract
def test_tesseract_regular_ocr_call_is_allowed(tmp_path) -> None:
    import pytesseract

    image = _png(tmp_path / "bild.png")
    with net_fence.loopback_only(answer_driver.allowed_programs(), check_existing_connections=False):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT
        from PIL import Image

        with Image.open(image) as img:
            pytesseract.image_to_string(img, lang="deu")  # derselbe Aufrufweg wie app/documents/ocr.py


@needs_tesseract
def test_tesseract_environment_is_checked(tmp_path, monkeypatch) -> None:
    with net_fence.loopback_only(answer_driver.allowed_programs(), check_existing_connections=False):
        env = {**os.environ, "TESSDATA_PREFIX": "\\\\lexono.invalid\\tessdata"}
        assert "Umgebung" in str(_spawn_refused(lambda: subprocess.run([TESSERACT, "--version"], env=env, capture_output=True)))
        env = {**os.environ, "TESSDATA_PREFIX": str(tmp_path / "gibt_es_nicht")}
        _spawn_refused(lambda: subprocess.run([TESSERACT, "--version"], env=env, capture_output=True))


def test_git_environment_rule_rejects_git_variables() -> None:
    assert answer_driver._git_env({"PATH": "x"}) is None
    for key in ("GIT_EXEC_PATH", "GIT_CONFIG_PARAMETERS", "GIT_SSH_COMMAND", "git_dir"):
        assert answer_driver._git_env({key: "x"}) is not None


def test_validator_errors_fail_closed(tmp_path) -> None:
    def broken(_args):  # noqa: ANN001, ANN202
        raise RuntimeError("Validator defekt")

    exe = net_fence.trusted_executable("where") if sys.platform == "win32" else shutil.which("true")
    rule = net_fence.ProgramRule("werkzeug", exe, None, validate_args=broken)
    with net_fence.loopback_only([rule], check_existing_connections=False):
        with pytest.raises(RuntimeError):
            subprocess.run([exe, "/?"], capture_output=True)
    rule = net_fence.ProgramRule("werkzeug", exe, None)  # ohne Argumentpruefung: nicht zugelassen
    with net_fence.loopback_only([rule], check_existing_connections=False):
        assert "keine Argumentpruefung" in str(_spawn_refused(lambda: subprocess.run([exe, "/?"], capture_output=True)))


# --- 3. Commit ohne Prozess ------------------------------------------------------------------------------------------------


def test_commit_is_read_without_a_process_and_matches_git() -> None:
    git = net_fence.trusted_executable("git")
    expected = subprocess.run([git, "rev-parse", "HEAD"], capture_output=True, text=True, cwd=answer_specs.REPO).stdout.strip()
    seen: list[str] = []

    def hook(event, args):  # noqa: ANN001, ANN202
        if event == "subprocess.Popen":
            seen.append(event)

    sys.addaudithook(hook)
    assert answer_run._git_commit() == expected
    assert seen == []  # kein Unterprozess


@pytest.mark.parametrize("head", ["kaputt", "ref: refs/heads/../../x", "ref: refs/heads/gibt-es-nicht", ""])
def test_unreadable_commit_fails_closed(tmp_path, monkeypatch, head) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(head, encoding="utf-8")
    monkeypatch.setattr(answer_specs, "REPO", tmp_path)
    assert answer_run._git_commit() is None
    result = answer_run.check(answer_run.load_config(answer_run.TEMPLATE))
    assert any("aktueller Commit nicht ermittelbar" in v for v in result["violations"])


@pytest.mark.usefixtures("synthetic_legal_state")
def test_driver_run_starts_no_git_process(tmp_path) -> None:
    from scripts.legal_bench.answer_store import RunStore

    programs: list[str] = []

    def hook(event, args):  # noqa: ANN001, ANN202
        if event == "subprocess.Popen" and net_fence.active():
            programs.append(os.path.basename(str(args[1]).split()[0]).lower())

    sys.addaudithook(hook)
    result = _driver(_config(tmp_path), RunStore(tmp_path / "laeufe"), EchoProvider(), tmp_path).run()
    assert result["status"] == "abgeschlossen", result["diagnose"]
    assert not [p for p in programs if p.startswith(("git", '"git'))], programs


# --- 4. Loopback nur zum vorgesehenen Pruefendpunkt -------------------------------------------------------------------------


def _listener() -> socket.socket:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    return server


def test_loopback_is_limited_to_the_validated_ports() -> None:
    allowed, other = _listener(), _listener()
    try:
        with net_fence.loopback_only(check_existing_connections=False, allowed_loopback_ports={allowed.getsockname()[1]}):
            socket.create_connection(allowed.getsockname(), timeout=2).close()
            with pytest.raises(net_fence.NetworkFenceViolation, match="nicht als Pruefendpunkt vorgesehen"):
                socket.create_connection(other.getsockname(), timeout=2)
        with net_fence.loopback_only(check_existing_connections=False, allowed_loopback_ports=set()):
            with pytest.raises(net_fence.NetworkFenceViolation):
                socket.create_connection(allowed.getsockname(), timeout=2)
        assert net_fence._STATE["ports"] is None  # nach dem Abbau zurueckgesetzt
    finally:
        allowed.close()
        other.close()


class _LocalSideChannelProvider(EchoProvider):
    """Mock, der heimlich einen ANDEREN lokalen Dienst anspricht (z. B. einen lokalen Proxy)."""

    def __init__(self, port: int) -> None:
        super().__init__()
        self.port = port

    def write(self, payload):  # noqa: ANN001
        self.payloads.append(payload)
        socket.create_connection(("127.0.0.1", self.port), timeout=2)
        raise AssertionError("unerreichbar")


@pytest.mark.usefixtures("synthetic_legal_state")
def test_driver_mock_run_allows_no_loopback_side_channel(tmp_path) -> None:
    from scripts.legal_bench.answer_store import RunStore

    other = _listener()
    try:
        provider = _LocalSideChannelProvider(other.getsockname()[1])
        result = _driver(_config(tmp_path), RunStore(tmp_path / "laeufe"), provider, tmp_path).run()
    finally:
        other.close()
    assert result["status"] == "gestoppt_fehlgeschlagen" and "NetworkFenceViolation" in " ".join(result["diagnose"])


def test_driver_derives_ports_from_the_validated_recorder(monkeypatch) -> None:
    from tests.test_privacy_wire_payload import _provider, _Recorder

    rec = _Recorder()
    try:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", rec.url)
        monkeypatch.setenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1")
        monkeypatch.delenv("LEXONO_GATEWAY_URL", raising=False)
        assert answer_driver.assert_local_only(_provider()) == {rec.server.server_address[1]}
        assert answer_driver.assert_local_only(EchoProvider()) == set()  # Mock: kein Loopback-Endpunkt
    finally:
        rec.server.shutdown()
        rec.server.server_close()


# --- 5. Dokumentierte Grenzen: kein Schutz gegen nativen Code ------------------------------------------------------------


@windows_only
def test_limit_direct_overlapped_connectex_bypasses_the_audit_hook() -> None:
    """GRENZE (bewusst als Nachweis): ein direkter Overlapped-ConnectEx (ohne asyncio-Proactor) loest kein Audit-Ereignis aus.
    Nachweis NUR gegen einen lokalen, NICHT freigegebenen Port - waere es ein externes Ziel, ginge die Verbindung hinaus."""
    import _overlapped

    target = _listener()
    client = socket.socket()
    try:
        with net_fence.loopback_only(check_existing_connections=False, allowed_loopback_ports={1}):
            with pytest.raises(net_fence.NetworkFenceViolation):
                socket.create_connection(target.getsockname(), timeout=2)  # regulaerer Weg: gesperrt
            _overlapped.BindLocal(client.fileno(), socket.AF_INET)
            ov = _overlapped.Overlapped(0)
            ov.ConnectEx(client.fileno(), target.getsockname())
            ov.getresult(True)
        accepted, _ = target.accept()
        accepted.close()  # Verbindung kam zustande -> Hook ist keine Grenze fuer native Aufrufe
    finally:
        client.close()
        target.close()


@windows_only
def test_limit_preresolved_native_function_pointer_bypasses_the_audit_hook() -> None:
    """GRENZE: ein VOR dem Zaun beschaffter Zeiger auf ws2_32.connect umgeht die Pruefung (nur lokaler Nachweis)."""
    import ctypes

    class SockaddrIn(ctypes.Structure):
        _fields_ = [("family", ctypes.c_ushort), ("port", ctypes.c_ushort), ("addr", ctypes.c_ubyte * 4),
                    ("zero", ctypes.c_ubyte * 8)]

    connect = ctypes.WinDLL("ws2_32").connect  # vor dem Zaun
    target = _listener()
    client = socket.socket()
    try:
        port = target.getsockname()[1]
        address = SockaddrIn(socket.AF_INET, socket.htons(port), (ctypes.c_ubyte * 4)(127, 0, 0, 1))
        with net_fence.loopback_only(check_existing_connections=False, allowed_loopback_ports={1}):
            result = connect(ctypes.c_size_t(client.fileno()), ctypes.byref(address), ctypes.sizeof(address))
        assert result == 0  # verbunden trotz Zaun
        accepted, _ = target.accept()
        accepted.close()
    finally:
        client.close()
        target.close()
