"""Smoke-Test für windows/installer.iss (Schritt 3): Installation unter
%LocalAppData% ohne Admin-Rechte statt bisher {autopf} ("Program Files").
Kein echter Inno-Setup-Compile-Lauf (ISCC.exe i. d. R. nicht in der
Sandbox verfügbar) - nur eine Textprüfung der sicherheitsrelevanten
Direktiven."""

from __future__ import annotations

from pathlib import Path

_INSTALLER_PATH = Path(__file__).resolve().parent.parent / "windows" / "installer.iss"


def _read_installer() -> str:
    return _INSTALLER_PATH.read_text(encoding="utf-8")


def test_installs_under_local_app_data_not_program_files() -> None:
    content = _read_installer()
    assert "DefaultDirName={localappdata}\\Lexono" in content
    assert "DefaultDirName={autopf}" not in content


def test_does_not_require_admin_privileges() -> None:
    content = _read_installer()
    assert "PrivilegesRequired=lowest" in content
    assert "PrivilegesRequired=admin" not in content


def test_registers_app_identity_for_apps_and_features() -> None:
    """Diese vier Werte sind Voraussetzung dafür, dass Windows nach der
    Installation überhaupt einen sauberen, eindeutigen Eintrag unter
    "Apps & Features"/"Programme und Funktionen" anlegt (Inno Setup
    generiert Uninstaller + Registrierung daraus automatisch - kein
    zusätzliches Skript nötig)."""
    content = _read_installer()
    assert "AppId={{9F4B9E7A-2B1E-4C77-9C7C-3D9B5E5B0B21}}" in content
    assert 'AppName={#MyAppName}' in content
    assert 'AppVersion={#MyAppVersion}' in content
    assert 'AppPublisher={#MyAppPublisher}' in content


def test_uninstall_entry_shows_the_real_app_icon() -> None:
    content = _read_installer()
    assert "UninstallDisplayIcon={app}\\{#MyAppExeName}" in content


def test_creates_start_menu_shortcut() -> None:
    content = _read_installer()
    # Seit Schritt 3 (stummer Start): Filename zeigt auf wscript.exe +
    # Start.vbs statt direkt auf die .exe, IconFilename bleibt die .exe
    # (damit die Verknüpfung trotzdem das echte App-Icon zeigt).
    assert (
        'Name: "{group}\\{#MyAppName}"; Filename: "wscript.exe"; '
        'Parameters: """{app}\\Start.vbs"""' in content
    )
    assert 'IconFilename: "{app}\\{#MyAppExeName}"' in content
    # Eigener Deinstallations-Eintrag im Startmenü zusätzlich zum
    # automatischen "Apps & Features"-Eintrag.
    assert '{uninstallexe}' in content


def test_creates_desktop_shortcut_checked_by_default() -> None:
    content = _read_installer()
    assert (
        'Name: "{autodesktop}\\{#MyAppName}"; Filename: "wscript.exe"; '
        'Parameters: """{app}\\Start.vbs"""' in content
    )
    # Der Task existiert weiterhin (abwählbar), ist aber seit dieser
    # Anfrage NICHT mehr per "unchecked" abgewählt vorbelegt.
    assert 'Name: "desktopicon"' in content
    assert "Flags: unchecked" not in content


def test_output_filename_matches_requested_exe_name() -> None:
    content = _read_installer()
    assert "OutputBaseFilename=Lexono_Setup" in content


def test_ships_start_vbs_for_silent_launch() -> None:
    content = _read_installer()
    assert 'Source: "..\\Start.vbs"; DestDir: "{app}"' in content


def test_postinstall_run_uses_silent_launcher_too() -> None:
    content = _read_installer()
    assert (
        'Filename: "wscript.exe"; Parameters: """{app}\\Start.vbs"""; '
        'Description:' in content
    )


# --- WebView2-Bundling (ARCHITECTURE.md §70) ---


def test_bundles_webview2_bootstrapper_as_temp_file() -> None:
    content = _read_installer()
    assert 'Source: "vendor\\webview2\\MicrosoftEdgeWebview2Setup.exe"' in content
    # "dontcopy" - nur temporaer entpackt, kein dauerhafter Bestandteil des
    # Installationsverzeichnisses (reiner Einmal-Setup-Schritt).
    assert "Flags: dontcopy" in content


def test_runs_webview2_bootstrapper_silently_before_app_start() -> None:
    content = _read_installer()
    assert '"{tmp}\\MicrosoftEdgeWebview2Setup.exe"' in content
    assert '/silent /install' in content
    # Ein fehlgeschlagener WebView2-Bootstrap darf die gesamte
    # Lexono-Installation NICHT abbrechen - die bestehende Laufzeit-
    # Fehlerbehandlung (run.py) greift beim ersten Programmstart.
    webview2_run_line = next(
        line for line in content.splitlines() if "MicrosoftEdgeWebview2Setup.exe" in line and "Filename" in line
    )
    assert "abortonerror" not in webview2_run_line

    # Reihenfolge: der WebView2-Schritt muss VOR dem App-Start-Eintrag stehen.
    webview2_index = content.index('"{tmp}\\MicrosoftEdgeWebview2Setup.exe"')
    app_start_index = content.index('Parameters: """{app}\\Start.vbs"""; Description:')
    assert webview2_index < app_start_index


def test_webview2_bootstrapper_is_actually_extracted_before_run() -> None:
    """Regression für einen echten, nur bei einem realen Installationslauf
    gefundenen Bug (31.08.): "Flags: dontcopy" allein entpackt eine Datei
    NICHT automatisch nach {tmp} - ohne einen expliziten
    `ExtractTemporaryFile`-Aufruf im [Code]-Abschnitt scheitert der
    [Run]-Schritt mit "Datei kann nicht ausgeführt werden" (CreateProcess-
    Fehlercode 2), obwohl der Installer selbst fehlerfrei durchläuft und
    kein Text-basierter Test dies bemerkt hätte."""
    content = _read_installer()
    assert "[Code]" in content
    assert "ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe')" in content
    # Muss zeitlich vor dem [Run]-Aufruf laufen - ssInstall (Beginn der
    # Dateiinstallation) ist frueh genug, [Run]-Eintraege werden erst danach
    # ausgefuehrt. Inno Setup ist bei der TEXTUELLEN Reihenfolge der
    # Abschnitte in der .iss-Datei selbst frei (CurStepChanged ist ein
    # Callback, kein Ablaufschritt "von oben nach unten") - deshalb hier
    # bewusst KEINE Prüfung auf Text-Reihenfolge, nur auf den korrekten
    # SetupStep.
    code_section = content[content.index("[Code]") :]
    assert "CurStep = ssInstall" in code_section
