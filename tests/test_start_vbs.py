"""Smoke-Test für Start.vbs (Schritt 3): stummer Startprozess ohne
sichtbares Konsolenfenster.

Kein automatisierter cscript-Lauf (würde einen echten Server-Prozess
starten) - Textprüfung der sicherheitskritischen/funktionalen Eigenschaften.
Die eigentliche Lauffähigkeit wurde während der Umsetzung dieses Schritts
manuell auf dem echten Windows-Zielsystem verifiziert (echter cscript-Lauf,
app.log korrekt befüllt, /health erreichbar, Prozess sauber beendet) - dabei
wurde auch ein echter cmd.exe-/c-Quoting-Bug gefunden und behoben (siehe
Kommentar in Start.vbs)."""

from __future__ import annotations

from pathlib import Path

_VBS_PATH = Path(__file__).resolve().parent.parent / "Start.vbs"


def _read_vbs() -> str:
    return _VBS_PATH.read_text(encoding="utf-8")


def test_start_vbs_exists_in_project_root() -> None:
    assert _VBS_PATH.exists()


def test_hides_the_window_for_normal_silent_start() -> None:
    content = _read_vbs()
    # 0 = SW_HIDE, muss fuer den stummen Pfad verwendet werden.
    assert 'objShell.Run "cmd /c """ & strRedirectedCommand & """", 0, False' in content


def test_shows_the_window_for_the_interactive_first_run() -> None:
    """Solange die Ersteinrichtung noch nicht abgeschlossen ist, MUSS die
    Konsole sichtbar bleiben - der Setup-Assistent fragt interaktiv
    E-Mail/Passwort ab (siehe app/setup/wizard.py)."""
    content = _read_vbs()
    assert "objShell.Run strCommand, 1, False" in content


def test_checks_for_setup_complete_marker_before_deciding_visibility() -> None:
    """Regressionsschutz (real beim Endanwender aufgetretener P0-Vorfall,
    siehe OPEN_ISSUES.md): NICHT anhand von `.env` allein entscheiden, ob
    die Ersteinrichtung abgeschlossen ist - `.env` wird bereits VOR
    Migration/Admin-Anlage geschrieben, ein `.setup_complete`-Marker aber
    erst nach tatsaechlich erfolgreicher Admin-Anlage
    (app/setup/wizard.py::run_setup_wizard)."""
    content = _read_vbs()
    assert "objFSO.FileExists(strSetupCompletePath)" in content
    assert '"\\.setup_complete"' in content
    assert '"\\.env"' not in content


def test_redirects_stdout_and_stderr_to_app_log() -> None:
    content = _read_vbs()
    assert '">> """' in content or ">> \"\"\"" in content
    assert "app.log" in content
    assert "2>&1" in content


def test_uses_the_outer_quote_wrap_workaround_for_cmd_c() -> None:
    """Regressionsschutz für den waehrend der Umsetzung gefundenen echten
    Bug: ohne ein zusaetzliches, die GESAMTE Befehlszeile umschliessendes
    Anfuehrungszeichenpaar entfernt cmd.exe bei einer mit einem zitierten
    Pfad beginnenden /c-Befehlszeile faelschlich beide aeusseren
    Anfuehrungszeichen und die Befehlszeile zerfaellt (kein Fehler, aber
    auch keine Wirkung - app.log entsteht nie)."""
    content = _read_vbs()
    assert 'cmd /c """ & strRedirectedCommand & """"' in content


def test_falls_back_to_dev_python_when_no_packaged_exe_present() -> None:
    content = _read_vbs()
    assert ".venv\\Scripts\\python.exe" in content
    assert "run.py" in content


def test_looks_for_the_lexono_exe_not_the_legacy_name() -> None:
    """KanzleiAI->Lexono-Produktidentitaets-Bereinigung: das Skript muss
    nach `Lexono.exe` suchen, nicht mehr nach `kanzlei_ai.exe`."""
    content = _read_vbs()
    assert 'strScriptDir & "\\Lexono.exe"' in content
    assert 'strScriptDir & "\\dist\\Lexono\\Lexono.exe"' in content
    assert '"\\kanzlei_ai.exe"' not in content


def test_resolves_data_dir_consistent_with_python_setup_paths() -> None:
    """Muss dieselbe Ableitung wie app/setup/paths.py verwenden - sonst
    prüft dieses Skript die .env am falschen Ort und triggert faelschlich
    den sichtbaren Erststart-Zweig auch bei laengst eingerichteten
    Installationen. Primaer jetzt LEXONO_DATA_DIR/%PROGRAMDATA%\\Lexono -
    der aeltere Name KANZLEI_AI_DATA_DIR bleibt als Fallback unterstuetzt
    (keine stillschweigend brechende Aenderung fuer bestehende
    Entwickler-/Testskripte)."""
    content = _read_vbs()
    assert "LEXONO_DATA_DIR" in content
    assert "KANZLEI_AI_DATA_DIR" in content
    assert "PROGRAMDATA" in content
    assert 'strDataDir = strProgramData & "\\Lexono"' in content


