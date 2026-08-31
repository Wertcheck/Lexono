"""Tests für app/setup/wizard.py (Prompt 37).

Migration/Admin-Anlage werden als einfache In-Prozess-Callables injiziert
(siehe Docstring von `run_setup_wizard`) - kein Subprozess-Start in Tests.
"""

import pytest

from app.setup.wizard import WizardError, run_setup_wizard


def test_wizard_creates_directories_and_env_and_calls_injected_steps(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"
    migration_calls: list[bool] = []
    admin_calls: list[tuple[str, str | None]] = []

    result = run_setup_wizard(
        data_dir=data_dir,
        admin_email="anwalt@kanzlei.test",
        admin_password=None,
        run_migrations=lambda: migration_calls.append(True),
        create_admin=lambda email, password: admin_calls.append((email, password)),
    )

    assert result.data_dir == data_dir
    assert result.env_path == data_dir / ".env"
    assert result.env_path.exists()
    assert (data_dir / "data").is_dir()
    assert (data_dir / "logs").is_dir()
    assert migration_calls == [True]
    assert admin_calls == [("anwalt@kanzlei.test", None)]


def test_wizard_passes_admin_password_through_when_provided(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"
    admin_calls: list[tuple[str, str | None]] = []

    run_setup_wizard(
        data_dir=data_dir,
        admin_email="anwalt@kanzlei.test",
        admin_password="s3cr3t!",
        run_migrations=lambda: None,
        create_admin=lambda email, password: admin_calls.append((email, password)),
    )

    assert admin_calls == [("anwalt@kanzlei.test", "s3cr3t!")]


def test_wizard_rejects_blank_email_before_any_side_effect(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"

    with pytest.raises(WizardError):
        run_setup_wizard(
            data_dir=data_dir,
            admin_email="   ",
            admin_password=None,
            run_migrations=lambda: pytest.fail("Migration sollte nicht aufgerufen werden"),
            create_admin=lambda email, password: pytest.fail(
                "Admin-Anlage sollte nicht aufgerufen werden"
            ),
        )

    assert not data_dir.exists()


def test_wizard_rejects_email_without_at_sign(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"

    with pytest.raises(WizardError):
        run_setup_wizard(
            data_dir=data_dir,
            admin_email="not-an-email",
            admin_password=None,
            run_migrations=lambda: pytest.fail("Migration sollte nicht aufgerufen werden"),
            create_admin=lambda email, password: pytest.fail(
                "Admin-Anlage sollte nicht aufgerufen werden"
            ),
        )


def test_wizard_does_not_overwrite_existing_env_without_force(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"
    data_dir.mkdir(parents=True)
    (data_dir / ".env").write_text("EXISTING=1", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_setup_wizard(
            data_dir=data_dir,
            admin_email="anwalt@kanzlei.test",
            admin_password=None,
            run_migrations=lambda: pytest.fail("Migration sollte nicht aufgerufen werden"),
            create_admin=lambda email, password: pytest.fail(
                "Admin-Anlage sollte nicht aufgerufen werden"
            ),
        )

    assert (data_dir / ".env").read_text(encoding="utf-8") == "EXISTING=1"


def test_wizard_overwrites_existing_env_with_force(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"
    data_dir.mkdir(parents=True)
    (data_dir / ".env").write_text("EXISTING=1", encoding="utf-8")

    result = run_setup_wizard(
        data_dir=data_dir,
        admin_email="anwalt@kanzlei.test",
        admin_password=None,
        run_migrations=lambda: None,
        create_admin=lambda email, password: None,
        force=True,
    )

    assert "EXISTING=1" not in result.env_path.read_text(encoding="utf-8")


# --- Lokale KI (Phase 3, §71) ---


def test_wizard_calls_local_ai_setup_when_provided(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"
    calls: list[bool] = []

    result = run_setup_wizard(
        data_dir=data_dir,
        admin_email="anwalt@kanzlei.test",
        admin_password=None,
        run_migrations=lambda: None,
        create_admin=lambda email, password: None,
        run_local_ai_setup=lambda: calls.append(True) or True,
    )

    assert calls == [True]
    assert result.local_ai_setup_succeeded is True


def test_wizard_skips_local_ai_setup_when_not_provided(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"

    result = run_setup_wizard(
        data_dir=data_dir,
        admin_email="anwalt@kanzlei.test",
        admin_password=None,
        run_migrations=lambda: None,
        create_admin=lambda email, password: None,
    )

    assert result.local_ai_setup_succeeded is None


def test_wizard_records_local_ai_setup_failure_without_raising(tmp_path) -> None:
    data_dir = tmp_path / "kanzlei_data"

    result = run_setup_wizard(
        data_dir=data_dir,
        admin_email="anwalt@kanzlei.test",
        admin_password=None,
        run_migrations=lambda: None,
        create_admin=lambda email, password: None,
        run_local_ai_setup=lambda: False,
    )

    assert result.local_ai_setup_succeeded is False
    # Kein Fehler geworfen, Setup insgesamt trotzdem "erfolgreich"
    # (Admin/Migration liefen durch) - .env existiert.
    assert result.env_path.exists()


def test_wizard_survives_local_ai_setup_raising_an_exception(tmp_path) -> None:
    """Ein fehlgeschlagener Local-AI-Einrichtungsversuch (z. B. fehlendes
    Internet waehrend der Installation) darf die gesamte Ersteinrichtung
    NICHT scheitern lassen (Auftrag §33 sinngemaess umgekehrt: Local AI
    darf die Kernanwendung nicht blockieren)."""
    data_dir = tmp_path / "kanzlei_data"

    def _raise() -> bool:
        raise RuntimeError("kein Internetzugang")

    result = run_setup_wizard(
        data_dir=data_dir,
        admin_email="anwalt@kanzlei.test",
        admin_password=None,
        run_migrations=lambda: None,
        create_admin=lambda email, password: None,
        run_local_ai_setup=_raise,
    )

    assert result.local_ai_setup_succeeded is False
    assert result.env_path.exists()


def test_wizard_generates_a_fresh_session_secret_each_call(tmp_path) -> None:
    secrets_found = []
    for index in range(2):
        data_dir = tmp_path / f"instance_{index}"
        run_setup_wizard(
            data_dir=data_dir,
            admin_email="anwalt@kanzlei.test",
            admin_password=None,
            run_migrations=lambda: None,
            create_admin=lambda email, password: None,
        )
        env_content = (data_dir / ".env").read_text(encoding="utf-8")
        secret_line = next(
            line for line in env_content.splitlines() if line.startswith("SESSION_SECRET_KEY=")
        )
        secrets_found.append(secret_line)

    assert secrets_found[0] != secrets_found[1]
