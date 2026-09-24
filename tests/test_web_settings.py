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
from tests.auth_test_utils import create_test_user, login, seed_roles


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
    assert "E-Mail-Postfach" in response.text
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
    _login_admin(client, db_session)
    response = client.get("/dashboard/settings/profile")
    assert response.status_code == 200
    assert "Kanzlei-Profil" in response.text
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


