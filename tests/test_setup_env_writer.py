"""Tests für app/setup/env_writer.py (Prompt 37; update_env_values ab 20.08.,
siehe ARCHITECTURE.md - schlüsselweise .env-Aktualisierung fürs Dashboard)."""

import pytest

from app.setup.env_writer import (
    build_env_content,
    format_env_value,
    update_env_values,
    write_env_file,
)


def test_build_env_content_contains_expected_keys(tmp_path) -> None:
    content = build_env_content(
        data_dir=tmp_path, session_secret="secret123", host="127.0.0.1", port=8000
    )
    posix = tmp_path.as_posix()

    assert "APP_ENV=production" in content
    assert f'DATABASE_URL="sqlite:///{posix}/data/kanzlei_ai.db"' in content
    assert f'INTAKE_STORAGE_DIR="{posix}/data/intake"' in content
    assert f'MAIL_ATTACHMENT_STORAGE_DIR="{posix}/data/mail_attachments"' in content
    assert f'LOG_FILE_PATH="{posix}/logs/kanzlei_ai.log"' in content
    assert "SESSION_SECRET_KEY=secret123" in content
    assert "SESSION_COOKIE_SECURE=True" in content
    assert "HOST=127.0.0.1" in content
    assert "PORT=8000" in content


def test_build_env_content_uses_provided_host_and_port(tmp_path) -> None:
    content = build_env_content(
        data_dir=tmp_path, session_secret="x", host="0.0.0.0", port=9090
    )
    assert "HOST=0.0.0.0" in content
    assert "PORT=9090" in content


def test_build_env_content_is_actually_parseable_by_settings(tmp_path) -> None:
    """Integrationstest: der erzeugte Inhalt muss von Settings korrekt
    eingelesen werden - beweist, dass die Anführungszeichen/Pfadformate
    keine reine Behauptung sind."""
    from app.config.settings import Settings

    content = build_env_content(data_dir=tmp_path, session_secret="x" * 40)
    env_path = tmp_path / ".env"
    env_path.write_text(content, encoding="utf-8")

    settings = Settings(_env_file=str(env_path))  # type: ignore[call-arg]

    posix = tmp_path.as_posix()
    assert settings.app_env == "production"
    assert settings.database_url == f"sqlite:///{posix}/data/kanzlei_ai.db"
    assert settings.intake_storage_dir == f"{posix}/data/intake"
    assert settings.mail_attachment_storage_dir == f"{posix}/data/mail_attachments"
    assert settings.log_file_path == f"{posix}/logs/kanzlei_ai.log"
    assert settings.resolved_session_secret_key == "x" * 40
    assert settings.resolved_session_cookie_secure is True
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_write_env_file_creates_parent_dirs_and_file(tmp_path) -> None:
    target = tmp_path / "nested" / ".env"
    write_env_file(target, "APP_ENV=production\n")
    assert target.read_text(encoding="utf-8") == "APP_ENV=production\n"


def test_write_env_file_refuses_overwrite_without_force(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text("original", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_env_file(target, "new content", force=False)

    assert target.read_text(encoding="utf-8") == "original"


def test_write_env_file_overwrites_with_force(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text("original", encoding="utf-8")

    write_env_file(target, "new content", force=True)

    assert target.read_text(encoding="utf-8") == "new content"


# --- format_env_value ---


def test_format_env_value_bool() -> None:
    assert format_env_value(True) == "true"
    assert format_env_value(False) == "false"


def test_format_env_value_list_is_json_array() -> None:
    assert format_env_value(["C:/Kanzlei/Eingang", "C:/Kanzlei/Zweit"]) == (
        '["C:/Kanzlei/Eingang", "C:/Kanzlei/Zweit"]'
    )


def test_format_env_value_string_is_always_quoted() -> None:
    assert format_env_value("imap.example.com") == '"imap.example.com"'


def test_format_env_value_escapes_quotes_and_backslashes() -> None:
    assert format_env_value('a"b\\c') == '"a\\"b\\\\c"'


def test_format_env_value_escapes_embedded_newlines_preventing_key_injection() -> None:
    """Regression fuer einen bei unabhaengiger Review (01.09.) gefundenen
    echten Fund: ein Wert mit eingebettetem Zeilenumbruch konnte zuvor aus
    seiner eigenen gequoteten KEY="..."-Zeile ausbrechen und eine beliebige
    neue Zeile in die .env einschleusen (z. B. SESSION_SECRET_KEY
    ueberschreiben)."""
    malicious = "http://localhost:11434\nSESSION_SECRET_KEY=attacker-controlled"
    formatted = format_env_value(malicious)

    # Der escapte Wert darf KEINEN echten Zeilenumbruch mehr enthalten -
    # das gesamte Ergebnis muss eine einzige physische Zeile bleiben.
    assert "\n" not in formatted
    assert formatted == '"http://localhost:11434\\nSESSION_SECRET_KEY=attacker-controlled"'


def test_update_env_values_with_embedded_newline_does_not_inject_new_key(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text(
        "SESSION_SECRET_KEY=real-secret\nOLLAMA_BASE_URL=http://old:11434\n",
        encoding="utf-8",
    )

    malicious = "http://localhost:11434\nSESSION_SECRET_KEY=attacker-controlled"
    update_env_values(target, {"OLLAMA_BASE_URL": malicious})

    content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    # Genau EINE Zeile pro Schluessel - der eingeschleuste Zeilenumbruch
    # darf keine zusaetzliche SESSION_SECRET_KEY-Zeile erzeugt haben.
    session_lines = [line for line in lines if line.startswith("SESSION_SECRET_KEY=")]
    assert session_lines == ["SESSION_SECRET_KEY=real-secret"]


# --- update_env_values ---


def test_update_env_values_replaces_key_preserving_surrounding_lines(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text(
        "APP_ENV=production\n"
        "SESSION_SECRET_KEY=do-not-touch-me\n"
        "MAIL_HOST=old.example.com\n"
        "PORT=8000\n",
        encoding="utf-8",
    )

    update_env_values(target, {"MAIL_HOST": "new.example.com"})

    content = target.read_text(encoding="utf-8")
    assert 'MAIL_HOST="new.example.com"' in content
    assert "old.example.com" not in content
    # Unveraendert, insbesondere Reihenfolge und der Secret-Key.
    assert "APP_ENV=production" in content
    assert "SESSION_SECRET_KEY=do-not-touch-me" in content
    assert "PORT=8000" in content
    lines = content.splitlines()
    assert lines[0] == "APP_ENV=production"
    assert lines[1] == "SESSION_SECRET_KEY=do-not-touch-me"


def test_update_env_values_appends_new_key_at_end(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text("APP_ENV=production\n", encoding="utf-8")

    update_env_values(target, {"MAIL_HOST": "imap.example.com"})

    content = target.read_text(encoding="utf-8")
    assert "APP_ENV=production" in content
    assert 'MAIL_HOST="imap.example.com"' in content
    assert content.index("APP_ENV") < content.index("MAIL_HOST")


def test_update_env_values_removes_key_when_value_is_none(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text("APP_ENV=production\nMAIL_PASSWORD=\"secret\"\n", encoding="utf-8")

    update_env_values(target, {"MAIL_PASSWORD": None})

    content = target.read_text(encoding="utf-8")
    assert "MAIL_PASSWORD" not in content
    assert "APP_ENV=production" in content


def test_update_env_values_creates_file_if_missing(tmp_path) -> None:
    target = tmp_path / "nested" / ".env"

    update_env_values(target, {"RETENTION_DAYS": 30})

    assert target.exists()
    assert "RETENTION_DAYS=30" in target.read_text(encoding="utf-8")


def test_update_env_values_writes_list_as_json_array_readable_by_settings(tmp_path) -> None:
    """Integrationstest: das Ergebnis muss von Settings tatsaechlich als
    Liste eingelesen werden koennen - beweist das JSON-Array-Format ist
    korrekt, nicht nur eine Behauptung."""
    from app.config.settings import Settings

    target = tmp_path / ".env"
    target.write_text("APP_ENV=development\n", encoding="utf-8")

    update_env_values(target, {"INTAKE_WATCHED_FOLDERS": ["C:/Kanzlei/Eingang"]})

    settings = Settings(_env_file=str(target))  # type: ignore[call-arg]
    assert settings.intake_watched_folders == ["C:/Kanzlei/Eingang"]


def test_update_env_values_does_not_touch_unrelated_keys(tmp_path) -> None:
    target = tmp_path / ".env"
    original = "APP_ENV=production\nHOST=127.0.0.1\nPORT=8000\n"
    target.write_text(original, encoding="utf-8")

    update_env_values(target, {"RETENTION_DAYS": 90})

    content = target.read_text(encoding="utf-8")
    assert "APP_ENV=production" in content
    assert "HOST=127.0.0.1" in content
    assert "PORT=8000" in content
