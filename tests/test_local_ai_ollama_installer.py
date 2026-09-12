"""Tests für app/local_ai/ollama_installer.py (§68).

Alle externen Effekte (Prozessaufrufe, HTTP, Hashing) sind gefakt - kein
echter Prozessstart, kein echter Download. Der reale, bereits einmal
tatsächlich durchgeführte Installationslauf ist in ARCHITECTURE.md §66/§68
dokumentiert, nicht Teil dieser automatisierten Suite."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.local_ai.ollama_installer import OllamaInstaller, OllamaVersionPolicy


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _installer(**overrides) -> OllamaInstaller:
    defaults = dict(
        run_command=lambda *a, **k: _completed(0, "ollama version is 0.32.15"),
        fetch_release_info=lambda *a, **k: {
            "assets": [{"name": "OllamaSetup.exe", "digest": "sha256:" + "ab" * 32}]
        },
        download_file=lambda *a, **k: None,
        compute_sha256=lambda path: "ab" * 32,
    )
    defaults.update(overrides)
    return OllamaInstaller(**defaults)


# --- 1: Ollama bereits installiert ---


def test_already_installed_compatible_version_is_reused(tmp_path: Path) -> None:
    installer = _installer(
        run_command=lambda *a, **k: _completed(0, "ollama version is 0.32.15")
    )
    result = installer.ensure_installed(download_dir=tmp_path)
    assert result.success is True
    assert result.already_installed is True
    assert result.installed_version == "0.32.15"
    assert result.stage == "reused_existing_installation"


# --- 2: Ollama fehlt ---


def test_missing_ollama_triggers_download_and_install(tmp_path: Path) -> None:
    calls = {"version_checks": 0}

    def run_command(args, **kwargs):
        if args[0] == "ollama":
            calls["version_checks"] += 1
            if calls["version_checks"] == 1:
                return _completed(1, "", "not found")  # vor der Installation: fehlt
            return _completed(0, "ollama version is 0.32.15")  # nach der Installation
        return _completed(0)  # Installer-Ausführung

    installer = _installer(run_command=run_command)
    result = installer.ensure_installed(download_dir=tmp_path)

    assert result.success is True
    assert result.already_installed is False
    assert result.stage == "installed"


# --- 3/4: kompatible/inkompatible Version ---


def test_compatible_version_check() -> None:
    installer = _installer()
    assert installer.is_version_compatible("0.32.15") is True
    assert installer.is_version_compatible(installer.version_policy.minimum_supported_version) is True


def test_incompatible_existing_version_is_not_silently_upgraded(tmp_path: Path) -> None:
    installer = _installer(run_command=lambda *a, **k: _completed(0, "ollama version is 0.1.0"))
    result = installer.ensure_installed(download_dir=tmp_path)

    assert result.success is False
    assert result.already_installed is True
    assert result.stage == "incompatible_existing_version"
    assert "kein automatisches Upgrade" in result.error


# --- 5: Ollama-Installation erfolgreich ---


def test_installation_success_returns_installed_version(tmp_path: Path) -> None:
    responses = iter([_completed(1, "", "not found"), _completed(0, "ollama version is 0.32.15")])
    installer = _installer(run_command=lambda args, **k: next(responses) if args[0] == "ollama" else _completed(0))
    result = installer.ensure_installed(download_dir=tmp_path)
    assert result.success is True
    assert result.installed_version == "0.32.15"


# --- 6: Ollama-Installation fehlgeschlagen ---


def test_installer_nonzero_exit_code_is_not_marked_successful(tmp_path: Path) -> None:
    def run_command(args, **kwargs):
        if args[0] == "ollama":
            return _completed(1, "", "not found")
        return _completed(1, "", "installer error")  # Installer-Ausführung schlägt fehl

    installer = _installer(run_command=run_command)
    result = installer.ensure_installed(download_dir=tmp_path)

    assert result.success is False
    assert result.stage == "install_failed"


def test_download_failure_is_reported_and_not_marked_successful(tmp_path: Path) -> None:
    def _raise(*a, **k):
        raise ConnectionError("Netzwerkfehler")

    installer = _installer(
        run_command=lambda *a, **k: _completed(1, "", "not found"), download_file=_raise
    )
    result = installer.ensure_installed(download_dir=tmp_path)

    assert result.success is False
    assert result.stage == "download_failed"


def test_post_install_verification_failure_is_not_marked_successful(tmp_path: Path) -> None:
    """Installer meldet Exit-Code 0, aber 'ollama --version' findet danach
    trotzdem nichts - darf NICHT als Erfolg gelten."""
    def run_command(args, **kwargs):
        if args[0] == "ollama":
            return _completed(1, "", "not found")
        return _completed(0)  # Installer meldet Erfolg

    installer = _installer(run_command=run_command)
    result = installer.ensure_installed(download_dir=tmp_path)

    assert result.success is False
    assert result.stage == "post_install_verification_failed"


# --- Integrität ---


def test_integrity_mismatch_aborts_before_running_installer(tmp_path: Path) -> None:
    install_calls = []

    def run_command(args, **kwargs):
        if args[0] == "ollama":
            return _completed(1, "", "not found")
        install_calls.append(args)
        return _completed(0)

    installer = _installer(
        run_command=run_command,
        fetch_release_info=lambda *a, **k: {
            "assets": [{"name": "OllamaSetup.exe", "digest": "sha256:" + "ab" * 32}]
        },
        compute_sha256=lambda path: "ff" * 32,  # weicht bewusst ab
    )
    result = installer.ensure_installed(download_dir=tmp_path)

    assert result.success is False
    assert result.stage == "integrity_check_failed"
    assert install_calls == []  # Installer wurde NIE ausgeführt


def test_missing_digest_information_does_not_block_installation() -> None:
    """Vorgabe: fehlt eine Integritätsinformation verlässlich, wird NICHTS
    erfunden - hier bedeutet das: es wird ohne Prüfsumme fortgefahren
    (dokumentiert als offene Sicherheitsgrenze, siehe ARCHITECTURE.md),
    statt einen falschen Hash vorzutäuschen."""
    installer = _installer(fetch_release_info=lambda *a, **k: {"assets": []})
    digest = installer._resolve_expected_digest()
    assert digest is None


# --- 7/8: Ollama API erreichbar / nicht erreichbar (ensure_running) ---


def test_ensure_running_returns_true_if_already_reachable() -> None:
    installer = _installer()
    started = []
    installer._start_background_process = lambda args: started.append(args)
    result = installer.ensure_running(is_reachable=lambda: True)
    assert result is True
    assert started == []  # kein Startversuch noetig


def test_ensure_running_starts_process_when_unreachable_and_succeeds() -> None:
    installer = _installer()
    call_count = {"n": 0}

    def is_reachable() -> bool:
        call_count["n"] += 1
        return call_count["n"] > 1  # erst nach dem Startversuch erreichbar

    started = []
    installer._start_background_process = lambda args: started.append(args)
    result = installer.ensure_running(is_reachable=is_reachable, wait_seconds=0.0)

    assert result is True
    assert len(started) == 1


def test_ensure_running_returns_false_when_still_unreachable_after_start() -> None:
    installer = _installer()
    installer._start_background_process = lambda args: None
    result = installer.ensure_running(is_reachable=lambda: False, wait_seconds=0.0, max_attempts=2)
    assert result is False


# --- Version-Erkennung: robust gegen Fehler ---


def test_detect_installed_version_returns_none_on_any_failure() -> None:
    def _raise(*a, **k):
        raise FileNotFoundError("ollama nicht gefunden")

    installer = _installer(run_command=_raise)
    assert installer.detect_installed_version() is None


def test_detect_installed_version_falls_back_to_known_install_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Realer Fund (Installer-Reality-Check, Referenzmaschine i5-1145G7):
    ein bereits laufender Prozess (z. B. `Lexono.exe`, gestartet ueber
    Start.vbs) sieht eine WAEHREND seiner Laufzeit per Installer aktualisierte
    PATH-Registry nicht - `subprocess.run(["ollama", "--version"])` scheitert
    dann mit `FileNotFoundError`, obwohl `ollama.exe` nachweislich am
    erwarteten Installationsort liegt. `detect_installed_version` muss in
    diesem Fall auf den bekannten Pfad zurueckfallen, statt eine tatsaechlich
    funktionierende Installation faelschlich als fehlend zu melden."""
    import app.local_ai.ollama_installer as ollama_installer_module

    fake_cli_path = tmp_path / "ollama.exe"
    fake_cli_path.write_bytes(b"")  # nur .is_file() muss True liefern
    monkeypatch.setattr(
        ollama_installer_module, "default_ollama_cli_path", lambda: fake_cli_path
    )

    def run_command(args, **kwargs):
        if args[0] == "ollama":
            raise FileNotFoundError("PATH veraltet, Prozess laeuft bereits")
        assert args[0] == str(fake_cli_path)
        return _completed(0, "ollama version is 0.34.0")

    installer = _installer(run_command=run_command)

    assert installer.detect_installed_version() == "0.34.0"


def test_version_policy_is_centrally_defined_not_scattered() -> None:
    policy = OllamaVersionPolicy()
    assert policy.installer_source.startswith("https://")
    assert policy.minimum_supported_version
