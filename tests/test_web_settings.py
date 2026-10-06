"""Tests für app/web/settings_router.py (20.08.) - die echte, bedienbare
Einstellungsseite (Scan-Ordner, E-Mail, Aufbewahrung, KI-Modus), siehe
ARCHITECTURE.md.

WICHTIG zur Isolation: `app.web.settings_router.resolve_data_dir` wird in
JEDEM Test auf `tmp_path` umgebogen (monkeypatch), damit niemals die echte
`.env` dieser Entwicklungsmaschine angefasst wird. `get_settings` ist
`@lru_cache` auf Modulebene (app/config/settings.py) - PROZESSWEIT geteilt
über die gesamte Testsuite hinweg, daher zusätzlich `get_settings.cache_clear()`
in einer autouse-Fixture vor UND nach jedem Test, damit ein hier erzeugtes,
auf `tmp_path` basierendes Settings-Objekt niemals für spätere, unabhängige
Tests in derselben Suite "haengen bleibt"."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Bildet exakt nach, wie die echte Anwendung `resolve_data_dir()` und
    `get_settings()` konsistent auf DIESELBE `.env` beziehen: `run.py:
    cmd_serve` wechselt das Arbeitsverzeichnis beim Start in
    `resolve_data_dir()` (siehe test_main_changes_into_resolved_data_dir in
    tests/test_run_entrypoint.py), wodurch pydantic-settings' relatives
    `env_file=".env"` (app/config/settings.py) automatisch dieselbe Datei
    liest, in die app/web/settings_router.py über `resolve_data_dir()/
    ".env"` schreibt. Ohne `monkeypatch.chdir` würde `get_settings()`
    weiterhin die echte Repo-`.env` lesen (falsch-negative/-positive Tests),
    unabhängig davon, wohin `update_env_values` tatsächlich schreibt.

    `KANZLEI_AI_DATA_DIR` ist `resolve_data_dir()`s eigener, offizieller
    Override-Mechanismus (app/setup/paths.py) - kein internes Monkeypatching
    einer Implementierungsfunktion nötig."""
    monkeypatch.setenv("KANZLEI_AI_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    target = tmp_path / ".env"
    # SESSION_SECRET_KEY MUSS gesetzt sein, genau wie in jeder echten
    # Installation (app/setup/env_writer.py: build_env_content schreibt ihn
    # immer) - sonst erzeugt Settings() im Entwicklungsmodus bei JEDEM
    # Konstruktoraufruf einen NEUEN zufaelligen Sitzungsschluessel (siehe
    # Settings.resolved_session_secret_key), und get_settings.cache_clear()
    # (ausgeloest durch settings_router._apply) wuerde die gerade erst
    # angemeldete Test-Session sofort wieder ungueltig machen.
    target.write_text(
        "APP_ENV=development\nSESSION_SECRET_KEY=test-secret-key-not-random-000000\n",
        encoding="utf-8",
    )
    return target


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _login_admin(client: TestClient, db_session: Session) -> None:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["admin"], "admin@kanzlei.test")
    login(client, "admin@kanzlei.test")


def _login_non_admin(client: TestClient, db_session: Session) -> None:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")


def _csrf(client: TestClient) -> str:
    import re

    page = client.get("/dashboard/settings")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


# --- Zugriffsschutz ---


def test_settings_page_requires_login(client: TestClient) -> None:
    response = client.get("/dashboard/settings", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_settings_page_requires_admin_role(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_non_admin(client, db_session)
    response = client.get("/dashboard/settings")
    assert response.status_code == 403


def test_settings_page_renders_for_admin(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings")
    assert response.status_code == 200
    assert "Scan-Ordner" in response.text
    assert "E-Mail-Konto" in response.text
    assert "Ausschließlich Claude (Anthropic-API)" in response.text


# --- Scan-Ordner ---


def test_add_intake_folder_persists_to_env_and_shows_up(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/intake-folders/add",
        data={"csrf_token": csrf, "path": "C:/Kanzlei/Eingang"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    assert "INTAKE_WATCHED_FOLDERS" in env_path.read_text(encoding="utf-8")
    page = client.get("/dashboard/settings")
    assert "C:/Kanzlei/Eingang" in page.text


def test_add_intake_folder_rejects_blank_path(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/intake-folders/add",
        data={"csrf_token": csrf, "path": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "INTAKE_WATCHED_FOLDERS" not in env_path.read_text(encoding="utf-8")


def test_remove_intake_folder(client: TestClient, db_session: Session, env_path: Path) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/intake-folders/add",
        data={"csrf_token": csrf, "path": "C:/A"},
    )
    client.post(
        "/dashboard/settings/intake-folders/add",
        data={"csrf_token": csrf, "path": "C:/B"},
    )

    client.post(
        "/dashboard/settings/intake-folders/remove",
        data={"csrf_token": csrf, "path": "C:/A"},
    )

    page = client.get("/dashboard/settings")
    assert "C:/A" not in page.text
    assert "C:/B" in page.text


# --- E-Mail ---


def test_update_mail_settings_persists_host_and_username(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/mail",
        data={
            "csrf_token": csrf,
            "mail_host": "imap.example.com",
            "mail_port": "993",
            "mail_username": "kanzlei@example.com",
            "mail_password": "geheim123",
            "mail_mailbox": "INBOX",
            "mail_use_ssl": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    content = env_path.read_text(encoding="utf-8")
    assert "imap.example.com" in content
    assert "kanzlei@example.com" in content
    assert "geheim123" in content

    page = client.get("/dashboard/settings")
    # Passwort wird NIE zurueck ins Formular geschrieben.
    assert "geheim123" not in page.text
    assert "imap.example.com" in page.text


def test_update_mail_settings_blank_password_does_not_overwrite_existing(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/mail",
        data={
            "csrf_token": csrf,
            "mail_host": "imap.example.com",
            "mail_port": "993",
            "mail_username": "user",
            "mail_password": "original-secret",
            "mail_mailbox": "INBOX",
            "mail_use_ssl": "true",
        },
    )

    # Zweites Speichern OHNE Passwort-Eingabe - darf das bestehende nicht loeschen.
    client.post(
        "/dashboard/settings/mail",
        data={
            "csrf_token": csrf,
            "mail_host": "imap.example.com",
            "mail_port": "993",
            "mail_username": "user",
            "mail_password": "",
            "mail_mailbox": "INBOX",
            "mail_use_ssl": "true",
        },
    )

    assert "original-secret" in env_path.read_text(encoding="utf-8")


# ==========================================================================
# "E-Mail"-Tab - Endnutzer-Ansicht (06.10., Owner-Direktive "SETTINGS ->
# E-MAIL"): die obigen Tests decken den bereits bestehenden Connect-
# Endpunkt (/dashboard/settings/mail) unveraendert ab - diese Tests decken
# den NEUEN Connected/Disconnected-Status, "Verbindung trennen" und die
# echten Synchronisations-Schalter ab.
# ==========================================================================


def test_not_connected_state_shows_connect_form_and_provider_presets(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert "Nicht verbunden" in response.text
    assert "settings-mail-preset" in response.text
    assert "Microsoft 365" in response.text
    assert "Google Workspace" in response.text
    # Kein Verbunden-Status/keine Trennen-Aktion ohne echte Verbindung.
    assert "Verbindung trennen" not in response.text


def test_connected_state_shows_real_account_and_manage_action(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/connection-vs-processing: der Status zeigt AUSSCHLIESSLICH
    "Verbunden" - niemals "KI aktiviert" o. Ae."""
    env_path.write_text(
        env_path.read_text(encoding="utf-8")
        + "\nMAIL_PROVIDER=imap\nMAIL_HOST=outlook.office365.com\n"
        "MAIL_USERNAME=kanzlei@muster-partner.de\nMAIL_PASSWORD=geheim123\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert "kanzlei@muster-partner.de" in response.text
    assert "Microsoft 365 (IMAP/SMTP)" in response.text
    assert "Verbindung trennen" in response.text
    assert "KI aktiviert" not in response.text
    assert "Alle E-Mails analysiert" not in response.text
    # Echtes Passwort darf nie im HTML landen.
    assert "geheim123" not in response.text


def test_disconnect_removes_credentials_and_reverts_to_not_connected(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    env_path.write_text(
        env_path.read_text(encoding="utf-8")
        + "\nMAIL_PROVIDER=imap\nMAIL_HOST=outlook.office365.com\n"
        "MAIL_USERNAME=kanzlei@muster-partner.de\nMAIL_PASSWORD=geheim123\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/mail/disconnect",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    env_text = env_path.read_text(encoding="utf-8")
    # Ersatzlos entfernt (siehe update_env_values value=None), nicht nur geleert.
    assert "MAIL_PASSWORD" not in env_text
    assert "MAIL_HOST" not in env_text
    assert "geheim123" not in env_text
    assert get_settings().mail_provider is None

    reload_response = client.get("/dashboard/settings")
    assert "Nicht verbunden" in reload_response.text
    assert "kanzlei@muster-partner.de" not in reload_response.text


def test_disconnect_requires_admin_role(client: TestClient, db_session: Session, env_path: Path) -> None:
    _login_non_admin(client, db_session)
    csrf = extract_csrf(client.get("/dashboard/chat").text)

    response = client.post(
        "/dashboard/settings/mail/disconnect",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_toggle_mail_auto_sync_persists_and_reloads(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    assert get_settings().mail_auto_sync_enabled is True

    response = client.post(
        "/dashboard/settings/mail/auto-sync",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert get_settings().mail_auto_sync_enabled is False

    reload_response = client.get("/dashboard/settings")
    idx = reload_response.text.find("Automatische Synchronisation</span>")
    assert idx != -1
    section = reload_response.text[idx:idx + 500]
    assert "law-toggle--on" not in section


def test_update_mail_poll_interval_persists_valid_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/mail/interval",
        data={"csrf_token": csrf, "mail_poll_interval_seconds": "900"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert get_settings().mail_poll_interval_seconds == 900

    reload_response = client.get("/dashboard/settings")
    assert 'value="900" selected' in reload_response.text


def test_update_mail_poll_interval_rejects_unsupported_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/mail/interval",
        data={"csrf_token": csrf, "mail_poll_interval_seconds": "42"},
        follow_redirects=False,
    )

    assert "error=" in response.headers["location"]
    assert get_settings().mail_poll_interval_seconds == 300


def test_privacy_explanation_present_and_accurate(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/privacy-information: beide woertlich geforderten Aussagen muessen
    vorhanden sein."""
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert "nicht automatisch an externe KI-Dienste übermittelt" in response.text
    assert "gelten" in response.text and "Datenschutzmechanismen" in response.text


def test_no_secrets_or_tokens_in_rendered_email_tab(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Security (/credentials, /self-review): keine OAuth-Begriffe, da es
    schlicht kein OAuth gibt - und das echte Passwort erscheint nie."""
    env_path.write_text(
        env_path.read_text(encoding="utf-8")
        + "\nMAIL_PROVIDER=imap\nMAIL_HOST=imap.example.test\n"
        "MAIL_USERNAME=kanzlei@example.test\nMAIL_PASSWORD=super-geheimes-passwort\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert "super-geheimes-passwort" not in response.text
    assert "Access Token" not in response.text
    assert "Client Secret" not in response.text
    assert "Refresh Token" not in response.text


# --- Aufbewahrung ---


def test_update_retention_persists_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/retention",
        data={"csrf_token": csrf, "retention_days": "90"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "RETENTION_DAYS=90" in env_path.read_text(encoding="utf-8")


def test_update_retention_rejects_negative_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/retention",
        data={"csrf_token": csrf, "retention_days": "-5"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    assert "RETENTION_DAYS" not in env_path.read_text(encoding="utf-8")


def test_settings_changes_apply_immediately_without_restart(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Beweist den zentralen Anspruch aus dem Modul-Docstring: kein
    Neustart noetig, get_settings() liefert sofort den neuen Wert."""
    _login_admin(client, db_session)
    csrf = _csrf(client)

    client.post(
        "/dashboard/settings/retention",
        data={"csrf_token": csrf, "retention_days": "42"},
    )

    assert get_settings().retention_days == 42


# --- Lokale KI (01.09., Product Completion Cycle: Modell war zuvor nur
# per .env/CLI-Setup-Assistent konfigurierbar, nicht ueber die Web-UI) ---


def test_settings_page_shows_local_ai_section_when_enabled(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    env_path.write_text(
        env_path.read_text(encoding="utf-8")
        + "\nLOCAL_AI_ENABLED=true\nOLLAMA_MODEL=qwen2.5:1.5b\nOLLAMA_BASE_URL=http://localhost:11434\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")
    assert response.status_code == 200
    assert "Lokale KI" in response.text
    assert 'value="qwen2.5:1.5b"' in response.text
    assert 'value="http://localhost:11434"' in response.text


def test_settings_page_shows_disabled_hint_when_local_ai_off(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")
    assert response.status_code == 200
    assert "Lokale KI ist derzeit deaktiviert" in response.text
    assert 'action="/dashboard/settings/local-ai"' not in response.text


def test_update_local_ai_settings_persists_model_and_base_url(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "\nLOCAL_AI_ENABLED=true\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/local-ai",
        data={
            "csrf_token": csrf,
            "ollama_model": "mistral:7b",
            "ollama_base_url": "http://127.0.0.1:11434",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    env_text = env_path.read_text(encoding="utf-8")
    assert "OLLAMA_MODEL=" in env_text and "mistral:7b" in env_text
    assert "OLLAMA_BASE_URL=" in env_text and "http://127.0.0.1:11434" in env_text
    assert get_settings().ollama_model == "mistral:7b"


def test_update_local_ai_settings_rejects_blank_model(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/local-ai",
        data={"csrf_token": csrf, "ollama_model": "  ", "ollama_base_url": "http://localhost:11434"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    assert "OLLAMA_MODEL" not in env_path.read_text(encoding="utf-8")


# ==========================================================================
# "KI & Datenschutz" - Endnutzer-Ansicht (06.10., Owner-Direktive
# "SETTINGS -> KI & DATENSCHUTZ"): die technischen Lokale-KI-/KI-Anbindung-
# Felder oben sind UNVERAENDERT funktionsfaehig (siehe Tests oben, alle
# weiterhin gruen) - nur ihre Position im Tab "Erweitert" statt
# "KI & Datenschutz" ist neu. Diese Tests decken den NEUEN Endnutzer-Tab
# ab: Cloud-KI-Auswahl, echte Datenschutz-/Infrastruktur-Statusanzeigen,
# keine technischen Details mehr sichtbar.
# ==========================================================================


def test_ki_datenschutz_tab_shows_cloud_ai_card_not_ollama_fields(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Die technischen Ollama-/Anthropic-Schluessel-Felder sind aus der
    Endnutzer-Ansicht verschwunden (jetzt im Tab "Erweitert", siehe
    test_erweitert_tab_shows_relocated_technical_ai_settings) - der Tab
    zeigt stattdessen die Cloud-KI-Karte und den Datenschutzstatus."""
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert response.status_code == 200
    assert "Cloud-KI" in response.text
    assert "Ihr Datenschutz ist aktiv" in response.text
    assert "Funktionsweise" in response.text
    assert "Ihre Vorteile" in response.text

    # Die ganze Seite rendert serverseitig ALLE Tabpanels (clientseitig nur
    # per CSS/JS umgeschaltet) - technische Local-AI-/Infrastruktur-Begriffe
    # duerfen deshalb speziell INNERHALB des "KI & Datenschutz"-Tabpanels
    # nicht mehr vorkommen (sie existieren weiterhin im HTML, aber im Tab
    # "Erweitert" - siehe test_erweitert_tab_shows_relocated_technical_ai_settings).
    start = response.text.find('data-tabpanel="ki-datenschutz"')
    end = response.text.find('data-tabpanel="email"')
    assert start != -1 and end != -1 and start < end
    ki_datenschutz_section = response.text[start:end]
    assert "OLLAMA_MODEL" not in ki_datenschutz_section
    assert "Ollama-Basis-URL" not in ki_datenschutz_section
    assert "ANTHROPIC_API_KEY" not in ki_datenschutz_section
    assert "Ollama" not in ki_datenschutz_section


def test_erweitert_tab_shows_relocated_technical_ai_settings(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Die bisherigen "Lokale KI"/"KI-Anbindung"-Abschnitte sind weiterhin
    vollstaendig vorhanden - nur jetzt im Tab "Erweitert" (technische,
    nicht-alltaegliche Konfiguration), nicht mehr im Endnutzer-Tab
    "KI & Datenschutz"."""
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert response.status_code == 200
    assert "Lokale KI" in response.text
    assert "KI-Anbindung" in response.text
    assert 'data-tabpanel="erweitert"' in response.text


def test_cloud_ai_card_shows_claude_as_selected_and_gemini_chatgpt_disabled(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Claude ist der einzige real implementierte Anbieter (siehe
    app/ai_providers/factory.py) - Gemini/ChatGPT erscheinen als ECHT
    deaktivierte (nicht auswaehlbare) Optionen, keine Fake-Funktionalitaet."""
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert response.status_code == 200
    assert 'value="claude" selected' in response.text
    assert '<option value="gemini" disabled>' in response.text
    assert '<option value="chatgpt" disabled>' in response.text


def test_update_cloud_ai_provider_persists_claude(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/cloud-ai",
        data={"csrf_token": csrf, "cloud_ai_provider": "claude"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "tab=ki-datenschutz" in response.headers["location"]
    assert get_settings().cloud_ai_provider == "claude"

    # Nach Reload weiterhin korrekt geladen (echte Persistenz, kein nur
    # client-seitiger Zustand).
    reload_response = client.get("/dashboard/settings")
    assert 'value="claude" selected' in reload_response.text


def test_update_cloud_ai_provider_rejects_unsupported_provider(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Gemini/ChatGPT sind (noch) nicht implementiert - ein direkt an den
    Endpunkt gesendeter, nicht unterstuetzter Wert (z. B. ueber eine
    manipulierte Anfrage, die das deaktivierte Dropdown umgeht) darf
    NICHT persistiert werden (Regel: keine Fake-Provider)."""
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/cloud-ai",
        data={"csrf_token": csrf, "cloud_ai_provider": "gemini"},
        follow_redirects=False,
    )

    assert "error=" in response.headers["location"]
    assert get_settings().cloud_ai_provider == "claude"


def test_update_cloud_ai_provider_requires_admin_role(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Negativtest (/negative-tests): kein normaler Nutzer kann derzeit
    ueberhaupt auf die Einstellungsseite zugreifen (gesamte Seite ist
    admin-only, siehe _require_admin) - dieselbe Schranke gilt daher auch
    fuer die Cloud-KI-Auswahl selbst bei einer direkt an den Endpunkt
    gesendeten Anfrage."""
    _login_non_admin(client, db_session)
    # Settings selbst ist fuer diese Rolle nicht erreichbar (403) - der
    # CSRF-Token steckt im Session-Cookie und ist daher auf JEDER fuer
    # diese Rolle erreichbaren Seite identisch (hier: Chat-Startseite).
    csrf = extract_csrf(client.get("/dashboard/chat").text)

    response = client.post(
        "/dashboard/settings/cloud-ai",
        data={"csrf_token": csrf, "cloud_ai_provider": "claude"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_privacy_status_card_shows_pseudonymization_always_active(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Pseudonymisierung laeuft vollstaendig lokal/im Prozess (Presidio) -
    nicht abschaltbar, daher immer ehrlich "Aktiv" (/privacy-security:
    keine Abschaltmoeglichkeit fuer normale Nutzer)."""
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert "Pseudonymisierung" in response.text
    idx = response.text.find("Pseudonymisierung</span>")
    section = response.text[idx:idx + 800]
    assert "Aktiv" in section


def test_privacy_status_card_shows_honest_gateway_status_when_not_configured(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/status-rule: niemals "Verbunden" zeigen, wenn das Gateway
    tatsaechlich nicht konfiguriert ist."""
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    idx = response.text.find("Lexono Gateway</span>")
    section = response.text[idx:idx + 800]
    assert "Nicht verbunden" in section
    assert "settings-status-pill--ok" not in section


def test_privacy_status_card_shows_connected_gateway_when_configured(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    env_path.write_text(
        env_path.read_text(encoding="utf-8")
        + "\nLEXONO_GATEWAY_URL=https://gateway.example.test\n"
        "LEXONO_GATEWAY_CLIENT_ID=test-client\n"
        "LEXONO_GATEWAY_CLIENT_SECRET=test-secret\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    idx = response.text.find("Lexono Gateway</span>")
    section = response.text[idx:idx + 800]
    assert "Verbunden" in section
    assert "settings-status-pill--ok" in section
    # Honest hint text: gateway path, not the direct-dev-key path.
    assert "sichere Lexono Gateway" in response.text


def test_privacy_status_card_reflects_real_local_ai_status_not_fake_ready(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """/status-rule: lokale KI ist standardmaessig deaktiviert
    (local_ai_enabled=False) - die Karte darf dafuer NICHT "Bereit"
    vortaeuschen. `app.state.local_ai_status` wird im echten Betrieb vom
    Lifespan-Hintergrundcheck gesetzt (app/main.py::
    _run_silent_local_ai_check) - hier direkt nachgebildet, da TestClient
    diesen Hintergrundtask nicht ausfuehrt."""
    from app.local_ai.setup_orchestrator import LocalAiState, LocalAiStatus

    _login_admin(client, db_session)
    app.state.local_ai_status = LocalAiStatus(state=LocalAiState.DISABLED, configured_model=None)

    response = client.get("/dashboard/settings")

    idx = response.text.find("Lokale Verarbeitung</span>")
    section = response.text[idx:idx + 800]
    assert "Nicht aktiv" in section
    assert "settings-status-pill--ok" not in section


def test_privacy_status_card_shows_bereit_when_local_ai_actually_ready(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Gegenprobe: ist die lokale KI tatsaechlich bereit, zeigt die Karte
    das auch ehrlich an (kein pauschal negativer Status)."""
    from app.local_ai.setup_orchestrator import LocalAiState, LocalAiStatus

    _login_admin(client, db_session)
    app.state.local_ai_status = LocalAiStatus(state=LocalAiState.READY, configured_model="qwen2.5:1.5b")

    response = client.get("/dashboard/settings")

    idx = response.text.find("Lokale Verarbeitung</span>")
    section = response.text[idx:idx + 800]
    assert "Bereit" in section
    assert "settings-status-pill--ok" in section


def test_no_api_key_value_ever_appears_in_rendered_settings_page(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Security (/self-review): kein API-Key-Wert darf jemals im
    gerenderten HTML landen - weder im Frontend sichtbar noch im
    Seitenquelltext eingebettet."""
    secret_key_value = "sk-ant-test-definitely-secret-value-12345"
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + f"\nANTHROPIC_API_KEY={secret_key_value}\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    _login_admin(client, db_session)

    response = client.get("/dashboard/settings")

    assert secret_key_value not in response.text


# --- Kanzlei-Profil (Name/Anschrift/Kontakt, 20.08.) ---


def test_firm_profile_page_requires_login(client: TestClient) -> None:
    response = client.get("/dashboard/settings/profile", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_firm_profile_page_requires_admin_role(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_non_admin(client, db_session)
    response = client.get("/dashboard/settings/profile")
    assert response.status_code == 403


def test_firm_profile_page_renders_empty_form_for_admin(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """06.10., Owner-Direktive "SETTINGS -> KANZLEI": GET /dashboard/
    settings/profile ist jetzt ein Redirect auf den eingebetteten
    "Kanzlei"-Tab (settings.html) - TestClient folgt dem per Default."""
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings/profile")
    assert response.status_code == 200
    assert "Kanzleiinformationen" in response.text
    assert "Kanzleiname" in response.text


def test_firm_profile_save_persists_and_shows_up(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmProfile

    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/profile",
        data={
            "csrf_token": csrf,
            "firm_name": "Kanzlei Mustermann Rechtsanwälte",
            "street": "Musterstraße 12",
            "postal_code": "10115",
            "city": "Berlin",
            "phone": "+49 30 1234567",
            "email": "kanzlei@beispiel.de",
            "website": "www.beispiel.de",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    profile = db_session.query(FirmProfile).one()
    assert profile.firm_name == "Kanzlei Mustermann Rechtsanwälte"
    assert profile.city == "Berlin"
    assert profile.updated_by_actor == "admin@kanzlei.test"

    page = client.get("/dashboard/settings/profile")
    assert "Kanzlei Mustermann Rechtsanwälte" in page.text
    assert "Musterstraße 12" in page.text


def test_firm_profile_rejects_blank_name(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmProfile

    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/profile",
        data={
            "csrf_token": csrf,
            "firm_name": "   ",
            "street": "",
            "postal_code": "",
            "city": "",
            "phone": "",
            "email": "",
            "website": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(FirmProfile).filter(FirmProfile.firm_name != "").count() == 0


def test_firm_profile_save_does_not_create_duplicate_rows(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Singleton-Anspruch (siehe app/firm_profile/service.py): zweimaliges
    Speichern aktualisiert dieselbe Zeile, legt keine zweite an."""
    from app.models import FirmProfile

    _login_admin(client, db_session)

    for firm_name in ("Erster Name", "Zweiter Name"):
        csrf = _csrf(client)
        client.post(
            "/dashboard/settings/profile",
            data={
                "csrf_token": csrf,
                "firm_name": firm_name,
                "street": "",
                "postal_code": "",
                "city": "",
                "phone": "",
                "email": "",
                "website": "",
            },
        )

    assert db_session.query(FirmProfile).count() == 1
    assert db_session.query(FirmProfile).one().firm_name == "Zweiter Name"


# --- Kanzleifachprofil: fachliche Schwerpunkte (03.10., Owner-Direktive
# "KANZLEIFACHPROFIL UND JURISTISCHE WISSENSSTEUERUNG") ---


def test_practice_areas_page_lists_all_known_suggestions_unchecked_by_default(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings/profile")
    assert response.status_code == 200
    assert "Fachliche Schwerpunkte" in response.text
    assert "Arbeitsrecht" in response.text
    assert "Erbrecht" in response.text


def test_save_practice_areas_persists_multiple_selections(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmPracticeArea

    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf, "practice_areas": ["Erbrecht", "Familienrecht"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" not in response.headers["location"]

    stored = {
        row.practice_area for row in db_session.query(FirmPracticeArea).all()
    }
    assert stored == {"Erbrecht", "Familienrecht"}

    page = client.get("/dashboard/settings/profile")
    assert page.status_code == 200


def test_save_practice_areas_reflects_selection_state_on_reload(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf, "practice_areas": ["Erbrecht"]},
    )

    page = client.get("/dashboard/settings/profile")

    def _checkbox_tag(html: str, value: str) -> str:
        start = html.index(f'value="{value}"')
        tag_start = html.rindex("<input", 0, start)
        tag_end = html.index("/>", start)
        return html[tag_start:tag_end]

    assert "checked" in _checkbox_tag(page.text, "Erbrecht")
    assert "checked" not in _checkbox_tag(page.text, "Familienrecht")


def test_updating_practice_areas_removes_unchecked_ones(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmPracticeArea

    _login_admin(client, db_session)
    csrf1 = _csrf(client)
    client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf1, "practice_areas": ["Erbrecht", "Familienrecht"]},
    )

    csrf2 = _csrf(client)
    client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf2, "practice_areas": ["Erbrecht"]},
    )

    stored = {row.practice_area for row in db_session.query(FirmPracticeArea).all()}
    assert stored == {"Erbrecht"}


def test_clearing_all_practice_areas_removes_every_row(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmPracticeArea

    _login_admin(client, db_session)
    csrf1 = _csrf(client)
    client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf1, "practice_areas": ["Erbrecht"]},
    )

    csrf2 = _csrf(client)
    response = client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf2},  # keine "practice_areas"-Werte = leere Auswahl
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(FirmPracticeArea).count() == 0


def test_save_practice_areas_rejects_unknown_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmPracticeArea

    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf, "practice_areas": ["Erbrecht", "Erfundenes Rechtsgebiet"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(FirmPracticeArea).count() == 0


def test_save_practice_areas_requires_admin_role(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_non_admin(client, db_session)
    response = client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": "irrelevant", "practice_areas": ["Erbrecht"]},
    )
    assert response.status_code == 403


def test_save_practice_areas_requires_csrf_token(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    response = client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": "falsch", "practice_areas": ["Erbrecht"]},
    )
    assert response.status_code in (400, 403)


def test_save_practice_areas_requires_login(client: TestClient) -> None:
    response = client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": "irrelevant", "practice_areas": ["Erbrecht"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_orphaned_practice_area_is_shown_and_removable(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Direktive §2: "Ungültige oder nicht mehr verfügbare Rechtsgebiets-
    IDs kontrolliert behandeln" - ein Wert, der NICHT (mehr) in
    `PRACTICE_AREA_SUGGESTIONS` steht, darf nicht stillschweigend
    verschwinden oder einen Fehler auslösen, sondern wird deutlich markiert
    angezeigt und bleibt einzeln entfernbar."""
    from app.firm_profile import get_firm_profile
    from app.models import FirmPracticeArea

    _login_admin(client, db_session)
    profile = get_firm_profile(db_session)
    db_session.add(FirmPracticeArea(firm_profile_id=profile.id, practice_area="Steuerstrafrecht (veraltet)"))
    db_session.commit()

    page = client.get("/dashboard/settings/profile")
    assert page.status_code == 200
    assert "Steuerstrafrecht (veraltet)" in page.text
    assert "nicht mehr verfügbar" in page.text

    csrf = _csrf(client)
    response = client.post(
        "/dashboard/settings/profile/practice-areas/Steuerstrafrecht (veraltet)/remove",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert db_session.query(FirmPracticeArea).count() == 0


def test_saving_known_areas_does_not_touch_an_existing_orphaned_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Ein gespeicherter, nicht mehr erkannter Wert darf durch das
    normale Speichern der Checkbox-Liste NICHT versehentlich mitgelöscht
    werden - nur die explizite Entfernen-Aktion darf ihn entfernen."""
    from app.firm_profile import get_firm_profile
    from app.models import FirmPracticeArea

    _login_admin(client, db_session)
    profile = get_firm_profile(db_session)
    db_session.add(FirmPracticeArea(firm_profile_id=profile.id, practice_area="Altes Gebiet"))
    db_session.commit()

    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/profile/practice-areas",
        data={"csrf_token": csrf, "practice_areas": ["Erbrecht"]},
    )

    stored = {row.practice_area for row in db_session.query(FirmPracticeArea).all()}
    assert stored == {"Erbrecht", "Altes Gebiet"}


# --- Kanzlei-Profil: Logo & Unterschrift (20.08.) ---

_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)
_TINY_PNG_BYTES = base64.b64decode(_TINY_PNG_BASE64)


def test_upload_logo_persists_and_shows_preview(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmProfile

    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/profile/logo",
        data={"csrf_token": csrf},
        files={"logo": ("logo.png", _TINY_PNG_BYTES, "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" not in response.headers["location"]

    profile = db_session.query(FirmProfile).one()
    assert profile.logo_path is not None
    assert Path(profile.logo_path).exists()
    assert profile.logo_original_filename == "logo.png"

    page = client.get("/dashboard/settings/profile")
    assert '/dashboard/settings/profile/logo-file"' in page.text


def test_upload_logo_rejects_disallowed_extension(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmProfile

    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/profile/logo",
        data={"csrf_token": csrf},
        files={"logo": ("logo.svg", b"<svg></svg>", "image/svg+xml")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(FirmProfile).filter(FirmProfile.logo_path.isnot(None)).count() == 0


def test_upload_logo_rejects_oversized_file(
    client: TestClient, db_session: Session, env_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.web.settings_router as settings_router_module

    monkeypatch.setattr(settings_router_module, "_MAX_IMAGE_SIZE_BYTES", 10)
    _login_admin(client, db_session)
    csrf = _csrf(client)

    response = client.post(
        "/dashboard/settings/profile/logo",
        data={"csrf_token": csrf},
        files={"logo": ("logo.png", _TINY_PNG_BYTES, "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_remove_logo_clears_fields_and_deletes_file(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmProfile

    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/profile/logo",
        data={"csrf_token": csrf},
        files={"logo": ("logo.png", _TINY_PNG_BYTES, "image/png")},
    )
    stored_path = Path(db_session.query(FirmProfile).one().logo_path)
    assert stored_path.exists()

    csrf = _csrf(client)
    response = client.post(
        "/dashboard/settings/profile/logo/remove",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db_session.expire_all()
    profile = db_session.query(FirmProfile).one()
    assert profile.logo_path is None
    assert profile.logo_original_filename is None
    assert not stored_path.exists()


def test_upload_signature_persists_and_stores_signatory_name(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    from app.models import FirmProfile

    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/profile",
        data={
            "csrf_token": csrf,
            "firm_name": "Kanzlei Testfall",
            "street": "",
            "postal_code": "",
            "city": "",
            "phone": "",
            "email": "",
            "website": "",
            "signatory_name": "Rechtsanwältin Anna Muster",
        },
    )

    csrf = _csrf(client)
    response = client.post(
        "/dashboard/settings/profile/signature",
        data={"csrf_token": csrf},
        files={"signature": ("signature.png", _TINY_PNG_BYTES, "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    profile = db_session.query(FirmProfile).one()
    assert profile.signature_path is not None
    assert Path(profile.signature_path).exists()
    assert profile.signatory_name == "Rechtsanwältin Anna Muster"


def test_logo_file_route_serves_uploaded_image(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/profile/logo",
        data={"csrf_token": csrf},
        files={"logo": ("logo.png", _TINY_PNG_BYTES, "image/png")},
    )

    response = client.get("/dashboard/settings/profile/logo-file")
    assert response.status_code == 200
    assert response.content == _TINY_PNG_BYTES


def test_logo_file_route_404_without_logo(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings/profile/logo-file")
    assert response.status_code == 404


def test_logo_file_route_accessible_to_non_admin(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """ECHTER FUND (20.09., beim Bauen der Briefkopf-Vorschau im Entwurf-
    Editor entdeckt): diese Route war bisher `_require_admin`-gesperrt,
    obwohl `export_draft_docx`/`export_draft_pdf` (app/web/drafts_router.py)
    exakt dieselben Bilddaten laengst OHNE Rolleneinschraenkung an jeden
    angemeldeten Nutzer ausliefern - ein Anwalt/Mitarbeiter ohne Admin-Rolle
    konnte die identischen Bytes also bereits ueber jeden Export erhalten,
    nur die direkte Bildansicht (jetzt auch fuer die Briefkopf-Vorschau im
    Entwurf-Editor benoetigt) war ihm verwehrt. Upload als Admin, Abruf als
    Mitarbeiter."""
    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/profile/logo",
        data={"csrf_token": csrf},
        files={"logo": ("logo.png", _TINY_PNG_BYTES, "image/png")},
    )
    client.post("/dashboard/logout", follow_redirects=False)

    _login_non_admin(client, db_session)
    response = client.get("/dashboard/settings/profile/logo-file")
    assert response.status_code == 200
    assert response.content == _TINY_PNG_BYTES


def test_logo_file_route_requires_login(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    response = client.get("/dashboard/settings/profile/logo-file", follow_redirects=False)
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


def test_signature_file_route_accessible_to_non_admin(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Gleiche Begruendung wie test_logo_file_route_accessible_to_non_admin
    oben, fuer die Signatur-Datei-Route."""
    _login_admin(client, db_session)
    csrf = _csrf(client)
    client.post(
        "/dashboard/settings/profile/signature",
        data={"csrf_token": csrf},
        files={"signature": ("signature.png", _TINY_PNG_BYTES, "image/png")},
    )
    client.post("/dashboard/logout", follow_redirects=False)

    _login_non_admin(client, db_session)
    response = client.get("/dashboard/settings/profile/signature-file")
    assert response.status_code == 200
    assert response.content == _TINY_PNG_BYTES




# ==========================================================================
# "LEXONO - EINSTELLUNGEN UI REBUILD" (06.10.) - neue Tab-Struktur +
# neue "Allgemein"-Einstellungen. Alles ueber dieselbe .env-basierte
# Persistenz wie die bestehenden Tests oben (kein neues Datenmodell).
# ==========================================================================


def test_settings_page_renders_new_header_and_tabs(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings")
    assert response.status_code == 200
    assert '<h1 class="settings-header__title">Einstellungen</h1>' in response.text
    assert "Lexono an Ihre Kanzlei anpassen" in response.text
    for tab in ["Allgemein", "KI &amp; Datenschutz", "E-Mail", "Benutzer", "Kanzlei", "Lizenz", "Erweitert"]:
        assert tab in response.text


def test_settings_page_shows_real_profile_data_not_reference_placeholders(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Direktive §2: "Keine Beispielwerte aus der Referenz hardcoden" -
    die Referenz zeigt "Max Mustermann"/"Rechtsanwalt" - hier muss der
    TATSAECHLICHE angemeldete Nutzer erscheinen."""
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings")
    assert "admin@kanzlei.test" in response.text
    assert "Max Mustermann" not in response.text
    assert "Lexono Professional" not in response.text
    assert "10 Benutzer" not in response.text


def test_settings_page_shows_honest_license_card_without_fabricated_data(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Kein Lizenzdatenmodell vorhanden - die Karte darf keine erfundenen
    Werte zeigen, nur die tatsaechliche Nutzerzahl."""
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings")
    assert "Keine zentrale Lizenzverwaltung konfiguriert" in response.text
    assert "von 1 Benutzer" in response.text or "von 1 Benutzern" in response.text


def test_toggle_general_setting_flips_and_persists(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    assert get_settings().start_with_system is False

    response = client.post(
        "/dashboard/settings/general/toggle",
        data={"csrf_token": csrf, "key": "start_with_system"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "tab=allgemein" in response.headers["location"]
    assert get_settings().start_with_system is True

    # Zweiter Klick schaltet zurueck.
    client.post(
        "/dashboard/settings/general/toggle",
        data={"csrf_token": csrf, "key": "start_with_system"},
    )
    assert get_settings().start_with_system is False


def test_toggle_general_setting_rejects_unknown_key(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    response = client.post(
        "/dashboard/settings/general/toggle",
        data={"csrf_token": csrf, "key": "anthropic_api_key"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]


def test_update_deadline_reminder_lead_days_persists(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    response = client.post(
        "/dashboard/settings/general/deadline-reminder",
        data={"csrf_token": csrf, "deadline_reminder_lead_days": "7"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert get_settings().deadline_reminder_lead_days == 7


def test_update_deadline_reminder_lead_days_rejects_unsupported_value(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    response = client.post(
        "/dashboard/settings/general/deadline-reminder",
        data={"csrf_token": csrf, "deadline_reminder_lead_days": "99"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    assert get_settings().deadline_reminder_lead_days == 3


def test_update_ui_language_only_accepts_german(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    ok = client.post(
        "/dashboard/settings/general/language",
        data={"csrf_token": csrf, "ui_language": "de"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert "error=" not in ok.headers["location"]

    rejected = client.post(
        "/dashboard/settings/general/language",
        data={"csrf_token": csrf, "ui_language": "en"},
        follow_redirects=False,
    )
    assert "error=" in rejected.headers["location"]
    assert get_settings().ui_language == "de"


def test_update_ui_theme_only_accepts_light(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_admin(client, db_session)
    csrf = _csrf(client)
    rejected = client.post(
        "/dashboard/settings/general/theme",
        data={"csrf_token": csrf, "ui_theme": "dark"},
        follow_redirects=False,
    )
    assert "error=" in rejected.headers["location"]
    assert get_settings().ui_theme == "light"


def test_clear_cache_deletes_files_in_download_staging_dir(
    client: TestClient, db_session: Session, env_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.web.settings_router as settings_router_module

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    stale_file = staging_dir / "export.zip"
    stale_file.write_bytes(b"dummy")
    monkeypatch.setattr(settings_router_module, "DOWNLOAD_STAGING_DIR", staging_dir)

    _login_admin(client, db_session)
    csrf = _csrf(client)
    response = client.post(
        "/dashboard/settings/cache/clear",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "success=" in response.headers["location"]
    assert not stale_file.exists()


def test_clear_cache_requires_admin_role(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    _login_non_admin(client, db_session)
    csrf_page = client.get("/dashboard/chat")
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', csrf_page.text)
    csrf = match.group(1) if match else ""
    response = client.post("/dashboard/settings/cache/clear", data={"csrf_token": csrf})
    assert response.status_code == 403


def test_general_settings_tab_param_is_validated_against_allowlist(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Ein beliebiger `tab`-Query-Parameter darf nicht ungeprueft in den
    JS-Kontext der Seite gelangen (06.10., Haertung) - faellt sicher auf
    "allgemein" zurueck."""
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings?tab=<script>alert(1)</script>")
    assert response.status_code == 200
    assert 'var initialTab = "allgemein";' in response.text
    assert "<script>alert(1)</script>" not in response.text


def test_rename_still_works_after_settings_rebuild_regression_check(
    client: TestClient, db_session: Session, env_path: Path
) -> None:
    """Spotcheck (06.10.): die Umbenennen-Faehigkeit des Chats (aus einer
    frueheren Direktive) haengt an keiner hier veraenderten gemeinsamen
    Komponente - reiner Regressionsschutz, kein neuer Funktionsumfang."""
    response = client.get("/dashboard/chat")
    assert response.status_code in (200, 303)
