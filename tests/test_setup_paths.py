"""Tests für app/setup/paths.py (Prompt 36/37; KanzleiAI->Lexono-Migration)."""

import os
from pathlib import Path

from app.setup.paths import resolve_data_dir


def _clear_override_env(monkeypatch) -> None:
    monkeypatch.delenv("LEXONO_DATA_DIR", raising=False)
    monkeypatch.delenv("KANZLEI_AI_DATA_DIR", raising=False)


def test_override_env_var_takes_precedence(monkeypatch, tmp_path) -> None:
    custom = tmp_path / "custom-data-dir"
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("LEXONO_DATA_DIR", str(custom))
    assert resolve_data_dir() == custom


def test_legacy_override_env_var_still_works(monkeypatch, tmp_path) -> None:
    """Der alte Name bleibt gültig - keine stillschweigend brechende
    Änderung für bestehende Entwickler-/Testskripte."""
    custom = tmp_path / "custom-data-dir"
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(custom))
    assert resolve_data_dir() == custom


def test_new_override_env_var_wins_when_both_are_set(monkeypatch, tmp_path) -> None:
    new_custom = tmp_path / "new-data-dir"
    old_custom = tmp_path / "old-data-dir"
    monkeypatch.setenv("LEXONO_DATA_DIR", str(new_custom))
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(old_custom))
    assert resolve_data_dir() == new_custom


def test_windows_default_uses_programdata_lexono(monkeypatch, tmp_path) -> None:
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    assert resolve_data_dir(is_windows=True) == tmp_path / "Lexono"


def test_windows_default_falls_back_without_programdata_env(monkeypatch) -> None:
    _clear_override_env(monkeypatch)
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    assert resolve_data_dir(is_windows=True) == Path(r"C:\ProgramData") / "Lexono"


def test_non_windows_fallback_is_home_based(monkeypatch) -> None:
    _clear_override_env(monkeypatch)
    assert resolve_data_dir(is_windows=False) == Path.home() / ".lexono"


def test_auto_detection_matches_actual_platform(monkeypatch) -> None:
    """Ohne explizite Angabe muss `resolve_data_dir()` genau dem Zweig
    entsprechen, den `os.name` auf der tatsächlichen Plattform auswählen
    würde (das Projekt läuft nur auf Windows, siehe CLAUDE.md - hier nur
    als Konsistenzbeweis zwischen Auto-Erkennung und explizitem Aufruf)."""
    _clear_override_env(monkeypatch)
    expected = resolve_data_dir(is_windows=(os.name == "nt"))
    assert resolve_data_dir() == expected


# --- KanzleiAI -> Lexono Migration (real reproduzierter P0-Vorfall-Kontext:
# bestehende Kanzleidaten unter %PROGRAMDATA%\KanzleiAI duerfen niemals
# verloren gehen) ---


def test_migrates_existing_legacy_kanzleiai_dir_to_lexono(monkeypatch, tmp_path) -> None:
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    legacy_dir = tmp_path / "KanzleiAI"
    legacy_dir.mkdir()
    (legacy_dir / "data").mkdir()
    (legacy_dir / "data" / "kanzlei_ai.db").write_text("echte-testdaten", encoding="utf-8")
    (legacy_dir / ".env").write_text("APP_ENV=production\n", encoding="utf-8")

    result = resolve_data_dir(is_windows=True)

    assert result == tmp_path / "Lexono"
    assert result.exists()
    assert not legacy_dir.exists()
    assert (result / "data" / "kanzlei_ai.db").read_text(encoding="utf-8") == "echte-testdaten"
    assert (result / ".env").exists()


def test_does_not_migrate_when_lexono_dir_already_exists(monkeypatch, tmp_path) -> None:
    """Existiert `Lexono` bereits (auch leer), gilt es als massgeblich -
    niemals zwei Datensaetze vermischen."""
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    legacy_dir = tmp_path / "KanzleiAI"
    legacy_dir.mkdir()
    (legacy_dir / "marker.txt").write_text("legacy", encoding="utf-8")
    new_dir = tmp_path / "Lexono"
    new_dir.mkdir()

    result = resolve_data_dir(is_windows=True)

    assert result == new_dir
    assert legacy_dir.exists()  # unangetastet
    assert not (new_dir / "marker.txt").exists()


def test_uses_fresh_lexono_dir_when_neither_exists(monkeypatch, tmp_path) -> None:
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    result = resolve_data_dir(is_windows=True)

    assert result == tmp_path / "Lexono"
    assert not result.exists()  # resolve_data_dir legt es nicht selbst an


def test_migration_repairs_stale_absolute_legacy_paths_inside_env(monkeypatch, tmp_path) -> None:
    """ROOT CAUSE real gefunden (direkte Dateisystem-/DB-Inspektion einer
    echten Installation): eine `.env`, die geschrieben wurde, WAEHREND
    `resolve_data_dir()` noch den Legacy-Pfad lieferte, enthaelt absolute
    Pfad-Strings (DATABASE_URL/INTAKE_STORAGE_DIR/...), die den reinen
    Verzeichnis-Rename ueberleben, OHNE mitaktualisiert zu werden - die
    Anwendung wuerde nach der Migration eine ANDERE, am alten Pfad liegende
    SQLite-Datei oeffnen als die, die tatsaechlich neben der (bereits
    umbenannten) `.env` liegt. Migration MUSS diese Pfade mitkorrigieren."""
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    legacy_dir = tmp_path / "KanzleiAI"
    legacy_dir.mkdir()
    (legacy_dir / "data").mkdir()
    (legacy_dir / "data" / "kanzlei_ai.db").write_text("echte-testdaten", encoding="utf-8")
    legacy_posix = legacy_dir.as_posix()
    (legacy_dir / ".env").write_text(
        "APP_ENV=production\n"
        f'DATABASE_URL="sqlite:///{legacy_posix}/data/kanzlei_ai.db"\n'
        f'INTAKE_STORAGE_DIR="{legacy_posix}/data/intake"\n'
        f'LOG_FILE_PATH="{legacy_posix}/logs/kanzlei_ai.log"\n'
        "SESSION_SECRET_KEY=echtes-geheimnis-bleibt-erhalten\n",
        encoding="utf-8",
    )

    result = resolve_data_dir(is_windows=True)

    new_posix = result.as_posix()
    env_content = (result / ".env").read_text(encoding="utf-8")
    assert legacy_posix not in env_content
    assert f'DATABASE_URL="sqlite:///{new_posix}/data/kanzlei_ai.db"' in env_content
    assert f'INTAKE_STORAGE_DIR="{new_posix}/data/intake"' in env_content
    assert f'LOG_FILE_PATH="{new_posix}/logs/kanzlei_ai.log"' in env_content
    assert "SESSION_SECRET_KEY=echtes-geheimnis-bleibt-erhalten" in env_content
    assert (result / "data" / "kanzlei_ai.db").read_text(encoding="utf-8") == "echte-testdaten"


def test_repairs_stale_legacy_paths_in_already_existing_lexono_dir(monkeypatch, tmp_path) -> None:
    """Realer, auf einer tatsaechlichen Installation beobachteter Fall:
    `Lexono` existiert bereits (kein Rename noetig/moeglich), ihre `.env`
    enthaelt aber noch absolute Pfade auf das alte `KanzleiAI`-Verzeichnis -
    z. B. weil sie aus einem frueheren, teilweisen Migrationslauf stammt,
    der vor dieser Reparaturlogik entstand. Muss auch OHNE einen aktuellen
    Rename-Vorgang korrigiert werden, sonst bleibt eine bestehende
    Installation dauerhaft auf der falschen Datenbank haengen."""
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    legacy_dir = tmp_path / "KanzleiAI"
    legacy_dir.mkdir()
    (legacy_dir / "data").mkdir()
    (legacy_dir / "data" / "kanzlei_ai.db").write_text("verwaiste-leere-db", encoding="utf-8")

    new_dir = tmp_path / "Lexono"
    new_dir.mkdir()
    (new_dir / "data").mkdir()
    (new_dir / "data" / "kanzlei_ai.db").write_text("echte-nutzerdaten", encoding="utf-8")
    legacy_posix = legacy_dir.as_posix()
    (new_dir / ".env").write_text(
        "APP_ENV=production\n"
        f'DATABASE_URL="sqlite:///{legacy_posix}/data/kanzlei_ai.db"\n'
        "SESSION_SECRET_KEY=bleibt-unveraendert\n",
        encoding="utf-8",
    )

    result = resolve_data_dir(is_windows=True)

    assert result == new_dir
    new_posix = new_dir.as_posix()
    env_content = (new_dir / ".env").read_text(encoding="utf-8")
    assert legacy_posix not in env_content
    assert f'DATABASE_URL="sqlite:///{new_posix}/data/kanzlei_ai.db"' in env_content
    assert "SESSION_SECRET_KEY=bleibt-unveraendert" in env_content
    # Beide physischen Verzeichnisse bleiben unangetastet (kein Loeschen/
    # Kopieren von Datendateien) - nur der Text der `.env` wird korrigiert.
    assert (new_dir / "data" / "kanzlei_ai.db").read_text(encoding="utf-8") == "echte-nutzerdaten"
    assert (legacy_dir / "data" / "kanzlei_ai.db").read_text(encoding="utf-8") == "verwaiste-leere-db"


def test_env_without_legacy_paths_is_left_untouched(monkeypatch, tmp_path) -> None:
    """Kein unnoetiges Schreiben, wenn bereits alles konsistent ist -
    idempotent, kein Effekt bei einem bereits korrekten Zustand."""
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    new_dir = tmp_path / "Lexono"
    new_dir.mkdir()
    env_path = new_dir / ".env"
    original_content = (
        f'DATABASE_URL="sqlite:///{new_dir.as_posix()}/data/kanzlei_ai.db"\n'
        "SESSION_SECRET_KEY=unveraendert\n"
    )
    env_path.write_text(original_content, encoding="utf-8")
    original_mtime = env_path.stat().st_mtime_ns

    result = resolve_data_dir(is_windows=True)

    assert result == new_dir
    assert env_path.read_text(encoding="utf-8") == original_content
    assert env_path.stat().st_mtime_ns == original_mtime


def test_falls_back_to_legacy_dir_if_rename_fails(monkeypatch, tmp_path) -> None:
    """Schlaegt der Rename fehl (hier simuliert), duerfen Daten niemals
    unerreichbar werden - der alte Pfad bleibt nutzbar."""
    _clear_override_env(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    legacy_dir = tmp_path / "KanzleiAI"
    legacy_dir.mkdir()
    (legacy_dir / "wichtige_daten.txt").write_text("echte-daten", encoding="utf-8")

    def _failing_rename(self, target):
        raise OSError("simulierter Dateisystemfehler (z. B. Datei in Benutzung)")

    monkeypatch.setattr(Path, "rename", _failing_rename)

    result = resolve_data_dir(is_windows=True)

    assert result == legacy_dir
    assert (legacy_dir / "wichtige_daten.txt").read_text(encoding="utf-8") == "echte-daten"
