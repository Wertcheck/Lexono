"""Textpruefung fuer `windows/build.ps1` (Build-Orchestrierung).

Kein echter Build-Lauf (PyInstaller + Inno Setup, mehrere Minuten) - geprueft
wird die Eigenschaft, die real Zeit gekostet hat: WO der Inno-Setup-Compiler
gesucht wird.
"""

from __future__ import annotations

from pathlib import Path

_BUILD_SCRIPT = Path(__file__).resolve().parent.parent / "windows" / "build.ps1"


def _read_script() -> str:
    return _BUILD_SCRIPT.read_text(encoding="utf-8")


def test_finds_inno_setup_installed_per_user() -> None:
    """ECHTER Build-Blocker (14.09.): Inno Setup 6 war auf der
    Referenzmaschine NUR fuer den aktuellen Benutzer installiert
    (%LOCALAPPDATA%\\Programs\\Inno Setup 6). Das Skript kannte nur die
    beiden Program-Files-Pfade, also lief der PyInstaller-Schritt (~2,5 Min.)
    komplett durch und der Build brach erst DANACH ab."""
    content = _read_script()
    assert "${env:LOCALAPPDATA}\\Programs\\Inno Setup 6\\ISCC.exe" in content
    assert "${env:ProgramFiles(x86)}\\Inno Setup 6\\ISCC.exe" in content
    assert "${env:ProgramFiles}\\Inno Setup 6\\ISCC.exe" in content


def test_fails_with_an_actionable_message_when_iscc_is_missing() -> None:
    """Vorher endete der fehlende Compiler in einem PowerShell-
    CommandNotFound-Stacktrace ("Die Benennung 'iscc' wurde nicht ...
    erkannt") - fuer den Ausfuehrenden nicht von einem Skriptfehler zu
    unterscheiden. Es muss geprueft und mit einer verwertbaren Meldung
    (gesuchte Pfade + Hinweis) abgebrochen werden."""
    content = _read_script()
    assert 'Get-Command "iscc"' in content
    assert "ISCC.exe (Inno Setup 6) nicht gefunden" in content
    assert "jrsoftware.org/isinfo.php" in content
