"""§112: Regressionstests zu AUDIT_S111 A21 (pytest-Plugin-Autoload gesperrt, nur freigegebene Plugins, Bypass-Env
entfernt, Fehlerfall BLOCKIERT). Nur lokal: injizierter Runner (kein echter pytest-Start), synthetische Umgebung.
Keine echte venv, keine Plugin-Installation, kein Nachweis realer Isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.legal_bench import isolation_acceptance as acc, isolation_real as real


def _capture(tmp_path, *, junit_ok=True):  # noqa: ANN001, ANN202
    """Injizierter Runner: faengt argv/env ab und simuliert pytest (optional ohne JUnit-Ausgabe = Fehlerfall)."""
    seen = {}

    def runner(argv, **kw):  # noqa: ANN001, ANN202
        seen["argv"], seen["env"] = list(argv), dict(kw["env"])
        if junit_ok:
            junit = argv[-1].split("=", 1)[1]
            Path(junit).write_text(acc.junit_xml({"tests/x.py::t": "passed"}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return runner, seen


@pytest.fixture(autouse=True)
def _real(monkeypatch):  # noqa: ANN001, ANN202
    monkeypatch.setenv(acc.ENV_REAL, "1")


def test_autoload_is_disabled_for_the_child(tmp_path) -> None:
    runner, seen = _capture(tmp_path)
    real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner)
    assert seen["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


@pytest.mark.parametrize("var", ["PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"])
def test_bypass_env_is_stripped(tmp_path, monkeypatch, var) -> None:
    monkeypatch.setenv(var, "0" if var == "PYTEST_DISABLE_PLUGIN_AUTOLOAD" else "-p boese")
    runner, seen = _capture(tmp_path)
    real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner)
    # die Elternwert-Belegung darf nicht durchschlagen: Autoload bleibt gesperrt, kein geerbtes -p boese
    assert seen["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert var not in seen["env"] or var == "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
    assert "boese" not in " ".join(seen["argv"])


def test_only_explicit_plugins_are_loaded(tmp_path) -> None:
    runner, seen = _capture(tmp_path)
    real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner,
                    plugins=("anyio", "xdist"))
    argv = seen["argv"]
    assert argv.count("-p") == 3  # no:cacheprovider + die zwei freigegebenen
    for name in ("anyio", "xdist"):
        i = argv.index(name)
        assert argv[i - 1] == "-p"
    # ohne Freigabe werden KEINE Drittanbieter-Plugins geladen
    runner2, seen2 = _capture(tmp_path)
    real.run_pytest(["tests/x.py"], tmp_path / "k.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner2)
    assert seen2["argv"].count("-p") == 1  # nur no:cacheprovider


def test_invalid_plugin_name_is_refused(tmp_path) -> None:
    runner, _ = _capture(tmp_path)
    with pytest.raises(acc.AcceptanceRefused, match="ungueltiger Plugin-Name"):
        real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner,
                        plugins=("boese; rm -rf",))


def test_missing_plugin_blocks_via_no_junit(tmp_path) -> None:
    """Ein freigegebenes, aber nicht ladbares Plugin laesst pytest scheitern -> kein JUnit -> RuntimeError (BLOCKIERT)."""
    runner, _ = _capture(tmp_path, junit_ok=False)  # simuliert: pytest bricht ab, schreibt kein JUnit
    with pytest.raises(RuntimeError, match="kein JUnit-Protokoll"):
        real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner,
                        plugins=("nicht_installiert",))


def test_run_pytest_requires_real_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(acc.ENV_REAL, raising=False)
    runner, _ = _capture(tmp_path)
    with pytest.raises(acc.AcceptanceRefused, match="verweigert"):
        real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=False, runner=runner)


def test_bytecode_isolation_still_set(tmp_path, monkeypatch) -> None:
    import sys

    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "kein-bytecode"))
    runner, seen = _capture(tmp_path)
    real.run_pytest(["tests/x.py"], tmp_path / "j.xml", manifest_path=tmp_path / "m.json", real=True, runner=runner)
    assert "-B" in seen["argv"] and seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen["env"]["PYTHONPYCACHEPREFIX"] == str(tmp_path / "kein-bytecode")


def test_tool_fingerprint_lists_this_file() -> None:
    from scripts.legal_bench import isolation_gate

    assert "tests/test_s112_review_fixes.py" in isolation_gate.tool_files()
