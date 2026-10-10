"""Tests fuer das Konfigurationssystem (Prompt 03).

Prueft: sichere Defaults, gueltige und ungueltige Konfigurationen,
und dass Secrets nicht versehentlich in String-/Repr-Darstellungen
auftauchen (Grundregel: keine Secrets in Logs).
"""

import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_defaults_are_safe_when_no_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne jede Konfiguration muessen sicherheitsrelevante Defaults gelten."""
    for key in [
        "APP_ENV",
        "DATABASE_URL",
        "REQUIRE_HUMAN_APPROVAL_BEFORE_SEND",
        "RETENTION_DAYS",
        "OCR_ENABLED",
        "ANTHROPIC_API_KEY",
        "MAIL_PASSWORD",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_env == "development"
    assert settings.database_url.startswith("sqlite:///")
    assert settings.require_human_approval_before_send is True
    assert settings.retention_days == 0
    assert settings.ocr_enabled is False
    assert settings.anthropic_api_key is None
    assert settings.mail_password is None
    assert settings.intake_watched_folders == []
    assert settings.legal_sources_allowed == []


def test_settings_can_be_overridden_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/kanzlei")
    monkeypatch.setenv("RETENTION_DAYS", "30")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_env == "production"
    assert settings.database_url.startswith("postgresql://")
    assert settings.retention_days == 30


def test_database_url_abstraction_allows_postgres_without_code_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zentrale Architekturentscheidung: SQLite->PostgreSQL nur ueber Config."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:pass@db-host:5432/kanzlei"
    )
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "postgresql" in settings.database_url


def test_negative_retention_days_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETENTION_DAYS", "-5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_blank_watched_folder_entry_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTAKE_WATCHED_FOLDERS", '["  "]')
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_invalid_retention_days_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETENTION_DAYS", "not-a-number")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_secrets_are_not_exposed_in_str_or_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("MAIL_PASSWORD", "super-secret-mail-password")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    rendered = f"{settings!s} {settings!r}"
    assert "sk-super-secret-value" not in rendered
    assert "super-secret-mail-password" not in rendered
    # SecretStr-Werte bleiben ueber get_secret_value() gezielt zugreifbar,
    # werden aber nicht "versehentlich" beim Loggen/Drucken offengelegt.
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-super-secret-value"


def test_host_and_port_default_to_localhost_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sicherer Default (Prompt 36): keine Netzwerkweite Erreichbarkeit ohne
    ausdrückliche Konfiguration."""
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_host_and_port_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9090")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.host == "0.0.0.0"
    assert settings.port == 9090


@pytest.mark.parametrize("invalid_port", ["0", "-1", "70000"])
def test_invalid_port_is_rejected(
    monkeypatch: pytest.MonkeyPatch, invalid_port: str
) -> None:
    monkeypatch.setenv("PORT", invalid_port)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_require_human_approval_defaults_true_and_is_explicit_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REQUIRE_HUMAN_APPROVAL_BEFORE_SEND", raising=False)
    assert Settings(_env_file=None).require_human_approval_before_send is True  # type: ignore[call-arg]

    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL_BEFORE_SEND", "false")
    assert Settings(_env_file=None).require_human_approval_before_send is False  # type: ignore[call-arg]


def test_local_summary_mode_default_and_validation() -> None:
    import pytest as _pytest

    from app.config.settings import Settings

    assert Settings().local_summary_mode == "auto"
    assert Settings(local_summary_mode=" NEVER ").local_summary_mode == "never"
    assert Settings(local_summary_mode="always").local_summary_mode == "always"
    with _pytest.raises(ValueError):
        Settings(local_summary_mode="sometimes")
