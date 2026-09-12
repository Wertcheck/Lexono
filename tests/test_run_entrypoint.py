"""Tests für run.py (Prompt 36/37, PyInstaller-Entry-Point; Prompt 46,
natives Fenster).

Testet nur die reine Dispatch-/Pfadlogik - die eigentlichen Subprozess-
Aufrufe (`_run_migrate_subprocess`/`_run_create_admin_subprocess`) und die
Konsoleninteraktion (`cmd_setup`s `input()`/`getpass`) werden hier NICHT
ausgeführt, sondern die jeweiligen `cmd_*`-Funktionen werden für den
Dispatch-Test durch Stubs ersetzt. Ebenso wird `_serve_with_window` (Prompt
46: echter Server-Thread + echtes WebView-Fenster) hier NICHT ausgeführt -
nur die beiden isoliert testbaren Bausteine `_wait_for_server_ready` und
`_is_webview2_runtime_available` sowie die Argument-Dispatch-Logik
(`--no-window`). Der eigentliche Bündelungs-/Installationsvorgang und das
tatsächliche Öffnen eines nativen Fensters sind nur manuell/per
PyInstaller-Build testbar (siehe Bericht am Ende der Sitzung).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import run


@pytest.fixture(autouse=True)
def _restore_cwd() -> Iterator[None]:
    """Regressionsschutz (20.08.): `run.main(...)` wechselt als Nebeneffekt
    des Dispatch in JEDEM Zweig (serve/setup/migrate/create-admin/restore)
    das Arbeitsverzeichnis nach `resolve_data_dir()` (siehe `run.main`,
    kurz nach dem Argument-Parsing) - OHNE es selbst zurueckzusetzen, das
    ist bewusst Aufgabe des jeweiligen Aufrufers (siehe run.py-Kommentare).
    Diese Datei ruft `run.main(...)` in ueber einem Dutzend Tests auf,
    grossteils mit einer im Test geschriebenen `.env` mit `APP_ENV=
    production` (ohne SESSION_SECRET_KEY) - blieb das Arbeitsverzeichnis
    nach EINEM einzigen dieser Tests haengen, scheiterte JEDER spaeter in
    der GESAMTEN Suite ausgefuehrte Login-/Auth-Check hart (`Settings.
    resolved_session_secret_key`), unabhaengig von der betroffenen
    Testdatei - live per vollstaendigem Suite-Lauf reproduziert und
    verifiziert (siehe ARCHITECTURE.md). Statt jeden einzelnen der
    `run.main(...)`-Aufrufe unten manuell mit einem eigenen try/finally
    abzusichern, EIN zentraler, autouse-Fixture-basierter Schutz fuer die
    gesamte Datei - robust auch gegen kuenftige, neu hinzugefuegte Tests
    hier, die denselben Effekt haben koennten."""
    original_cwd = Path.cwd()
    yield
    os.chdir(original_cwd)


def test_bundle_base_dir_in_dev_mode_is_repo_root(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert run._bundle_base_dir() == Path(run.__file__).resolve().parent


def test_self_command_in_dev_mode_uses_python_and_script_path(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command = run._self_command("migrate")
    assert command == [sys.executable, str(Path(run.__file__).resolve()), "migrate"]


def test_self_command_when_frozen_uses_only_executable(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\Test\AppData\Local\Lexono\Lexono.exe")
    command = run._self_command("create-admin")
    assert command == [r"C:\Users\Test\AppData\Local\Lexono\Lexono.exe", "create-admin"]


def test_main_changes_into_resolved_data_dir(tmp_path, monkeypatch) -> None:
    original_cwd = Path.cwd()
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(run, "cmd_migrate", lambda: 0)

    try:
        exit_code = run.main(["migrate"])
        assert exit_code == 0
        assert Path.cwd().resolve() == tmp_path.resolve()
    finally:
        os.chdir(original_cwd)


def test_main_dispatches_migrate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(run, "cmd_migrate", lambda: (calls.append("migrate"), 0)[1])

    assert run.main(["migrate"]) == 0
    assert calls == ["migrate"]


def test_main_dispatches_create_admin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(run, "cmd_create_admin", lambda: (calls.append("create-admin"), 0)[1])

    assert run.main(["create-admin"]) == 0
    assert calls == ["create-admin"]


def test_main_dispatches_reset_admin_password(tmp_path, monkeypatch) -> None:
    """Realer Fund: `scripts/reset_admin_password.py` existierte bereits
    (Recovery-Pfad fuer "Admin existiert, initiales Passwort verloren"),
    war aber - anders als create-admin/restore - nicht als CLI-Subkommando
    angebunden und dadurch aus der installierten .exe nicht erreichbar."""
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        run,
        "cmd_reset_admin_password",
        lambda: (calls.append("reset-admin-password"), 0)[1],
    )

    assert run.main(["reset-admin-password"]) == 0
    assert calls == ["reset-admin-password"]


def test_main_dispatches_local_ai_setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        run, "cmd_local_ai_setup", lambda: (calls.append("local-ai-setup"), 0)[1]
    )

    assert run.main(["local-ai-setup"]) == 0
    assert calls == ["local-ai-setup"]


def test_main_dispatches_restore_with_archive_and_yes_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        run, "cmd_restore", lambda *, archive, yes: (calls.append((archive, yes)), 0)[1]
    )

    exit_code = run.main(["restore", "--archive", "backup.zip", "--yes"])

    assert exit_code == 0
    assert calls == [("backup.zip", True)]


def test_main_dispatches_restore_without_yes_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        run, "cmd_restore", lambda *, archive, yes: (calls.append((archive, yes)), 0)[1]
    )

    run.main(["restore", "--archive", "backup.zip"])

    assert calls == [("backup.zip", False)]


def test_cmd_setup_offers_local_ai_setup_by_default_on_empty_answer(tmp_path, monkeypatch) -> None:
    """Phase 3 (§71): leere Eingabe (nur Enter) bei der Local-AI-Frage muss
    als "Ja" gewertet werden - lokale KI ist der Standardvorschlag."""
    import getpass

    monkeypatch.chdir(tmp_path)
    answers = iter(["admin@kanzlei.test", ""])  # E-Mail, dann Local-AI-Frage (leer = ja)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "")

    captured = {}

    def fake_run_setup_wizard(**kwargs):
        captured.update(kwargs)
        from app.setup.wizard import WizardResult

        return WizardResult(env_path=tmp_path / ".env", data_dir=tmp_path)

    monkeypatch.setattr("app.setup.run_setup_wizard", fake_run_setup_wizard)

    exit_code = run.cmd_setup(tmp_path, force=False)

    assert exit_code == 0
    assert captured["run_local_ai_setup"] is not None


def test_cmd_setup_skips_local_ai_setup_when_declined(tmp_path, monkeypatch) -> None:
    import getpass

    monkeypatch.chdir(tmp_path)
    answers = iter(["admin@kanzlei.test", "n"])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "")

    captured = {}

    def fake_run_setup_wizard(**kwargs):
        captured.update(kwargs)
        from app.setup.wizard import WizardResult

        return WizardResult(env_path=tmp_path / ".env", data_dir=tmp_path)

    monkeypatch.setattr("app.setup.run_setup_wizard", fake_run_setup_wizard)

    exit_code = run.cmd_setup(tmp_path, force=False)

    assert exit_code == 0
    assert captured["run_local_ai_setup"] is None


def test_main_dispatches_setup_with_force_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    recorded: list[tuple[Path, bool]] = []

    def fake_cmd_setup(data_dir: Path, *, force: bool) -> int:
        recorded.append((data_dir, force))
        return 0

    monkeypatch.setattr(run, "cmd_setup", fake_cmd_setup)

    assert run.main(["setup", "--force"]) == 0
    assert recorded == [(tmp_path, True)]


def test_main_serve_without_env_runs_setup_first(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    order: list[str] = []
    monkeypatch.setattr(
        run, "cmd_setup", lambda data_dir, *, force: (order.append("setup"), 0)[1]
    )
    monkeypatch.setattr(
        run, "cmd_serve", lambda *, open_window=True: (order.append("serve"), 0)[1]
    )

    assert run.main([]) == 0
    assert order == ["setup", "serve"]


def _patch_fake_user_session(monkeypatch, *, has_user: bool) -> None:
    """Baut eine echte, isolierte In-Memory-SQLite-Session mit dem echten
    `User`-Modell (kein Mock der Datenbank-Schicht selbst) und ersetzt nur
    `app.db.session.SessionLocal` - so wie `_first_run_setup_required()`
    es tatsaechlich importiert (`from app.db.session import SessionLocal`
    loest sich zur Aufrufzeit neu auf, das Patchen der Modul-Referenz greift
    also korrekt)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.base import Base
    from app.models.user import User

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[User.__table__])
    TestSessionLocal = sessionmaker(bind=engine)

    if has_user:
        session = TestSessionLocal()
        session.add(User(email="bestehender.admin@lexono-test.local", is_active=True))
        session.commit()
        session.close()

    import app.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "SessionLocal", TestSessionLocal)


def test_main_serve_skips_setup_when_env_exists_and_admin_exists(tmp_path, monkeypatch) -> None:
    """Der eigentliche, korrekte Fall: `.env` UND mindestens ein Benutzer
    existieren bereits - dann darf Setup nicht erneut laufen."""
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    _patch_fake_user_session(monkeypatch, has_user=True)

    monkeypatch.setattr(
        run,
        "cmd_setup",
        lambda data_dir, *, force: (_ for _ in ()).throw(
            AssertionError("Setup sollte nicht erneut laufen, wenn bereits ein Benutzer existiert")
        ),
    )
    monkeypatch.setattr(run, "cmd_serve", lambda *, open_window=True: 0)

    assert run.main(["serve"]) == 0


def test_main_serve_reruns_setup_when_env_exists_but_no_admin_was_ever_created(
    tmp_path, monkeypatch
) -> None:
    """Root-Cause-Regressionstest (real reproduziert, siehe OPEN_ISSUES.md):
    `.env` existiert (z. B. weil ein fruehrer Migrations-/Admin-Anlage-
    Schritt fehlgeschlagen ist), aber es wurde nie ein Benutzer angelegt -
    ein echter Endanwender darf hier NICHT direkt zur Login-Seite ohne
    bekannte Zugangsdaten geschickt werden. Setup MUSS erneut laufen, und
    zwar mit `force=True` (sonst wuerde `write_env_file` mit
    `FileExistsError` abbrechen, siehe app/setup/env_writer.py)."""
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    _patch_fake_user_session(monkeypatch, has_user=False)

    recorded: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        run, "cmd_setup", lambda data_dir, *, force: (recorded.append((data_dir, force)), 0)[1]
    )
    monkeypatch.setattr(run, "cmd_serve", lambda *, open_window=True: 0)

    assert run.main(["serve"]) == 0
    assert recorded == [(tmp_path, True)]


def test_main_serve_reruns_setup_when_users_table_does_not_exist_yet(
    tmp_path, monkeypatch
) -> None:
    """Noch fruehere Fehlschlagsstufe: `.env` existiert, aber die Migration
    ist nie gelaufen (keine `users`-Tabelle) - muss ebenfalls als
    "Setup noch nicht abgeschlossen" gewertet werden, nicht als Fehler
    durchschlagen."""
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    EmptySessionLocal = sessionmaker(bind=engine)  # keine Tabellen angelegt
    import app.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "SessionLocal", EmptySessionLocal)

    recorded: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        run, "cmd_setup", lambda data_dir, *, force: (recorded.append((data_dir, force)), 0)[1]
    )
    monkeypatch.setattr(run, "cmd_serve", lambda *, open_window=True: 0)

    assert run.main(["serve"]) == 0
    assert recorded == [(tmp_path, True)]


def test_main_serve_aborts_if_setup_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(run, "cmd_setup", lambda data_dir, *, force: 1)
    monkeypatch.setattr(
        run,
        "cmd_serve",
        lambda *, open_window=True: (_ for _ in ()).throw(
            AssertionError("Serve sollte nach fehlgeschlagenem Setup nicht aufgerufen werden")
        ),
    )

    assert run.main(["serve"]) == 1


def test_main_serve_default_opens_window(tmp_path, monkeypatch) -> None:
    """Prompt 46: ohne --no-window ist open_window=True der neue Standard."""
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    monkeypatch.setattr(run, "_first_run_setup_required", lambda data_dir: False)
    recorded: list[bool] = []
    monkeypatch.setattr(
        run, "cmd_serve", lambda *, open_window=True: (recorded.append(open_window), 0)[1]
    )

    assert run.main(["serve"]) == 0
    assert recorded == [True]


def test_main_serve_bare_invocation_without_subcommand_opens_window(tmp_path, monkeypatch) -> None:
    """Auch der implizite Default (gar kein Argument) muss open_window=True
    ergeben - dort hat argparse die serve-Subparser-Attribute nie befüllt,
    das getattr-Fallback in main() muss trotzdem sicher greifen."""
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    monkeypatch.setattr(run, "_first_run_setup_required", lambda data_dir: False)
    recorded: list[bool] = []
    monkeypatch.setattr(
        run, "cmd_serve", lambda *, open_window=True: (recorded.append(open_window), 0)[1]
    )

    assert run.main([]) == 0
    assert recorded == [True]


def test_main_serve_no_window_flag_disables_window(tmp_path, monkeypatch) -> None:
    """WICHTIG (Regressionsfund 20.08.): `run.main` wechselt als Nebeneffekt
    des Dispatch das Arbeitsverzeichnis in KANZLEI_AI_DATA_DIR (dasselbe
    Verhalten wie in test_main_changes_into_resolved_data_dir oben belegt) -
    OHNE es selbst zurueckzusetzen. Diese Datei stubbt zwar `cmd_serve`
    weg, aber der CWD-Wechsel selbst passiert VOR diesem Aufruf in
    `run.main` und bleibt bestehen. Ohne das `try/finally` hier (analog zu
    test_main_changes_into_resolved_data_dir) blieb das Arbeitsverzeichnis
    fuer den GESAMTEN Rest des Testprozesses auf einem tmp_path mit einer
    production-`.env` OHNE SESSION_SECRET_KEY stehen - das liess JEDEN
    spaeter in der Suite ausgefuehrten Login-/Auth-Check hart fehlschlagen
    (`Settings.resolved_session_secret_key`), unabhaengig davon, welche
    Testdatei betroffen war. Live per vollstaendigem Suite-Lauf verifiziert."""
    original_cwd = Path.cwd()
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    monkeypatch.setattr(run, "_first_run_setup_required", lambda data_dir: False)
    recorded: list[bool] = []
    monkeypatch.setattr(
        run, "cmd_serve", lambda *, open_window=True: (recorded.append(open_window), 0)[1]
    )

    try:
        assert run.main(["serve", "--no-window"]) == 0
        assert recorded == [False]
    finally:
        os.chdir(original_cwd)


def test_wait_for_server_ready_returns_on_first_successful_check() -> None:
    calls: list[str] = []

    def fake_check(url: str) -> bool:
        calls.append(url)
        return True

    run._wait_for_server_ready(
        "http://127.0.0.1:8000/health",
        check=fake_check,
        sleep=lambda seconds: None,
        now=lambda: 0.0,
    )

    assert calls == ["http://127.0.0.1:8000/health"]


def test_wait_for_server_ready_retries_until_check_succeeds() -> None:
    results = iter([False, False, True])
    attempts: list[int] = []
    fake_clock = iter([0.0, 0.1, 0.2, 0.3])

    def fake_check(url: str) -> bool:
        attempts.append(1)
        return next(results)

    run._wait_for_server_ready(
        "http://127.0.0.1:8000/health",
        timeout=5.0,
        check=fake_check,
        sleep=lambda seconds: None,
        now=lambda: next(fake_clock),
    )

    assert sum(attempts) == 3


def test_wait_for_server_ready_raises_timeout_error_without_real_waiting() -> None:
    fake_time = {"value": 0.0}

    def fake_now() -> float:
        return fake_time["value"]

    def fake_sleep(seconds: float) -> None:
        fake_time["value"] += seconds

    with pytest.raises(TimeoutError, match="antwortete nicht|hat innerhalb von"):
        run._wait_for_server_ready(
            "http://127.0.0.1:8000/health",
            timeout=1.0,
            interval=0.25,
            check=lambda url: False,
            sleep=fake_sleep,
            now=fake_now,
        )


def test_wait_for_server_ready_includes_last_error_in_timeout_message() -> None:
    fake_time = {"value": 0.0}

    def fake_check(url: str) -> bool:
        raise ConnectionError("Verbindung verweigert")

    with pytest.raises(TimeoutError, match="Verbindung verweigert"):
        run._wait_for_server_ready(
            "http://127.0.0.1:8000/health",
            timeout=0.5,
            interval=0.1,
            check=fake_check,
            sleep=lambda seconds: fake_time.__setitem__("value", fake_time["value"] + seconds),
            now=lambda: fake_time["value"],
        )


def test_is_webview2_runtime_available_true_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    assert run._is_webview2_runtime_available() is True


def test_is_webview2_runtime_available_true_when_registry_key_found(monkeypatch) -> None:
    import winreg

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(winreg, "OpenKey", lambda hive, path: object())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: ("120.0.0.0", 1))

    assert run._is_webview2_runtime_available() is True


def test_is_webview2_runtime_available_false_when_no_key_found(monkeypatch) -> None:
    import winreg

    def _raise_not_found(hive, path):
        raise OSError("Registrierungsschlüssel nicht gefunden")

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(winreg, "OpenKey", _raise_not_found)

    assert run._is_webview2_runtime_available() is False


# --- Single-Instance-Mutex-Guard (Schritt 3) ---


def test_single_instance_lock_returns_placeholder_on_non_windows() -> None:
    handle = run._acquire_single_instance_lock(is_windows=False)
    assert handle == -1


def test_release_placeholder_handle_is_a_noop_never_calls_close() -> None:
    def _fail_if_called(handle: int) -> None:
        raise AssertionError("close_handle haette bei einem Platzhalter nicht aufgerufen werden duerfen")

    run._release_single_instance_lock(-1, close_handle=_fail_if_called)


def test_single_instance_lock_acquired_when_no_other_instance_running() -> None:
    def fake_create_mutex(name: str) -> tuple[int, int]:
        assert name == run._SINGLE_INSTANCE_MUTEX_NAME
        return 12345, 0  # ERROR_SUCCESS - frisch erzeugt, kein Konflikt

    handle = run._acquire_single_instance_lock(is_windows=True, create_mutex=fake_create_mutex)

    assert handle == 12345


def test_single_instance_lock_returns_none_when_already_running() -> None:
    def fake_create_mutex(name: str) -> tuple[int, int]:
        return 12345, run._ERROR_ALREADY_EXISTS

    closed: list[int] = []
    handle = run._acquire_single_instance_lock(
        is_windows=True, create_mutex=fake_create_mutex, close_handle=closed.append
    )

    assert handle is None
    assert closed == [12345]  # das ueberzaehlige Mutex-Handle wird sofort wieder geschlossen


def test_single_instance_lock_raises_on_unexpected_create_failure() -> None:
    def fake_create_mutex(name: str) -> tuple[int, int]:
        return 0, 5  # ERROR_ACCESS_DENIED o. Ae. - kein Handle erhalten

    with pytest.raises(OSError):
        run._acquire_single_instance_lock(is_windows=True, create_mutex=fake_create_mutex)


def test_release_single_instance_lock_closes_real_handle() -> None:
    closed: list[int] = []
    run._release_single_instance_lock(999, close_handle=closed.append)
    assert closed == [999]


def test_cmd_serve_aborts_with_clear_message_when_already_running(monkeypatch, capsys) -> None:
    monkeypatch.setattr(run, "_acquire_single_instance_lock", lambda: None)

    def _fail_if_called() -> int:
        raise AssertionError("cmd_migrate haette bei bereits laufender Instanz nicht aufgerufen werden duerfen")

    monkeypatch.setattr(run, "cmd_migrate", _fail_if_called)

    exit_code = run.cmd_serve(open_window=False)

    assert exit_code == 1
    assert "läuft bereits" in capsys.readouterr().err


def test_cmd_serve_releases_lock_even_when_migration_fails(monkeypatch) -> None:
    monkeypatch.setattr(run, "_acquire_single_instance_lock", lambda: 42)
    released: list[int] = []
    monkeypatch.setattr(run, "_release_single_instance_lock", released.append)
    monkeypatch.setattr(run, "cmd_migrate", lambda: 1)

    exit_code = run.cmd_serve(open_window=False)

    assert exit_code == 1
    assert released == [42]


# --- _apply_light_title_bar (20.08., "kritischer Design-Fix": keine
# schwarze Titelleiste mehr, siehe ARCHITECTURE.md) ---


class _FakeHandle:
    def ToInt32(self) -> int:
        return 12345


class _FakeNative:
    Handle = _FakeHandle()


class _FakeWindow:
    native = _FakeNative()


def test_apply_light_title_bar_calls_dwm_with_correct_attributes(monkeypatch) -> None:
    """Beweis auf Aufrufebene: DWMWA_USE_IMMERSIVE_DARK_MODE (20) wird auf 0
    (hell) gesetzt, DWMWA_CAPTION_COLOR (35) und DWMWA_TEXT_COLOR (36) auf
    die exakten Marken-Farbwerte aus app/web/static/css/app.css."""
    calls: list[tuple[int, int, int]] = []

    class _FakeDwmApi:
        def DwmSetWindowAttribute(self, hwnd, attribute, value_ptr, size):
            import ctypes

            calls.append((hwnd, attribute, ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_int)).contents.value))
            return 0

    import ctypes as ctypes_module

    monkeypatch.setattr(ctypes_module, "windll", type("W", (), {"dwmapi": _FakeDwmApi()})(), raising=False)

    run._apply_light_title_bar(_FakeWindow())

    attributes_seen = {attr for _, attr, _ in calls}
    assert 20 in attributes_seen  # DWMWA_USE_IMMERSIVE_DARK_MODE
    assert 35 in attributes_seen  # DWMWA_CAPTION_COLOR
    assert 36 in attributes_seen  # DWMWA_TEXT_COLOR

    dark_mode_call = next(c for c in calls if c[1] == 20)
    assert dark_mode_call == (12345, 20, 0)  # 0 = helle Titelleiste, NICHT dunkel

    caption_call = next(c for c in calls if c[1] == 35)
    assert caption_call[2] == run._TITLE_BAR_CAPTION_COLORREF

    text_call = next(c for c in calls if c[1] == 36)
    assert text_call[2] == run._TITLE_BAR_TEXT_COLORREF


def test_apply_light_title_bar_never_raises_when_native_handle_missing() -> None:
    """Rein kosmetische Funktion - ein fehlendes/unerwartetes window-Objekt
    (z. B. sehr alte pywebview-Version) darf den App-Start nie gefaehrden."""
    run._apply_light_title_bar(object())  # kein .native Attribut
    run._apply_light_title_bar(None)


def test_title_bar_colorref_constants_match_app_css_brand_colors() -> None:
    """COLORREF ist 0x00BBGGRR (umgekehrte Byte-Reihenfolge zu RGB-Hex) -
    beweist, dass die Konstanten tatsaechlich #F8FAFC/#101828 kodieren,
    nicht nur behauptet werden."""
    assert run._TITLE_BAR_CAPTION_COLORREF == 0x00FCFAF8  # R=F8,G=FA,B=FC -> BB GG RR
    assert run._TITLE_BAR_TEXT_COLORREF == 0x00281810  # R=10,G=18,B=28 -> BB GG RR


# --- _apply_rounded_corners (native abgerundete Fensterecken, Windows 11) ---


def test_apply_rounded_corners_calls_dwm_with_correct_attribute(monkeypatch) -> None:
    """Beweis auf Aufrufebene: DWMWA_WINDOW_CORNER_PREFERENCE (33) wird auf
    DWMWCP_ROUND (2) gesetzt - dieselbe hwnd-Ermittlung/dasselbe
    Aufrufmuster wie _apply_light_title_bar."""
    calls: list[tuple[int, int, int]] = []

    class _FakeDwmApi:
        def DwmSetWindowAttribute(self, hwnd, attribute, value_ptr, size):
            import ctypes

            calls.append((hwnd, attribute, ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_int)).contents.value))
            return 0

    import ctypes as ctypes_module

    monkeypatch.setattr(ctypes_module, "windll", type("W", (), {"dwmapi": _FakeDwmApi()})(), raising=False)

    run._apply_rounded_corners(_FakeWindow())

    assert calls == [(12345, run._DWMWA_WINDOW_CORNER_PREFERENCE, run._DWMWCP_ROUND)]


def test_apply_rounded_corners_never_raises_when_native_handle_missing() -> None:
    """Rein kosmetische Funktion - darf den App-Start nie gefaehrden."""
    run._apply_rounded_corners(object())  # kein .native Attribut


class _FakeSetupResult:
    def __init__(self, *, success: bool, stage: str = "ready", installed_model: str | None = "qwen3:1.7b", error: str | None = None) -> None:
        self.success = success
        self.stage = type("S", (), {"value": stage})()
        self.installed_model = installed_model
        self.error = error


def test_cmd_local_ai_setup_prints_heartbeat_during_long_running_setup(monkeypatch, capsys) -> None:
    """Beweis, dass waehrend eines langen `run_setup()`-Aufrufs NICHT
    einfach nur Stille herrscht (real beobachtet: 5,2-GB-Modell, ueber
    eine Stunde, keine Konsolenausgabe zwischen Start und Ende - siehe
    OPEN_ISSUES.md). Heartbeat-Intervall wird fuer den Test drastisch
    verkuerzt statt echte 30s zu warten."""
    import time as time_module

    monkeypatch.setattr(run, "_LOCAL_AI_SETUP_HEARTBEAT_INTERVAL_SECONDS", 0.05)

    class _FakeService:
        def run_setup(self, *, download_dir):
            time_module.sleep(0.2)
            return _FakeSetupResult(success=True)

    import app.local_ai.setup_orchestrator as setup_orchestrator_module

    monkeypatch.setattr(setup_orchestrator_module, "LocalAiSetupService", _FakeService)

    exit_code = run.cmd_local_ai_setup()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.count("laeuft noch") >= 1
    assert "eingerichtet" in out


def test_cmd_local_ai_setup_stops_heartbeat_thread_after_fast_setup(monkeypatch, capsys) -> None:
    """Fuer eine schnell abgeschlossene Einrichtung darf kein Heartbeat
    anspringen (kein unnoetiges Rauschen) und der Hintergrund-Thread darf
    nicht ueber die Funktion hinaus weiterlaufen (kein Thread-Leak)."""
    import threading as threading_module

    monkeypatch.setattr(run, "_LOCAL_AI_SETUP_HEARTBEAT_INTERVAL_SECONDS", 30.0)

    class _FakeService:
        def run_setup(self, *, download_dir):
            return _FakeSetupResult(success=True)

    import app.local_ai.setup_orchestrator as setup_orchestrator_module

    monkeypatch.setattr(setup_orchestrator_module, "LocalAiSetupService", _FakeService)

    threads_before = {t.ident for t in threading_module.enumerate()}
    exit_code = run.cmd_local_ai_setup()
    threads_after = {t.ident for t in threading_module.enumerate()}

    assert exit_code == 0
    assert "laeuft noch" not in capsys.readouterr().out
    assert threads_after - threads_before == set()
    run._apply_rounded_corners(None)
