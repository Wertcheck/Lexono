"""Build-Profile "kanzlei-pilot" / "full" (28.10.2026): Finance ist im Kanzlei-Pilotpaket nicht enthalten und nicht
erreichbar, Kanzleifunktionen und Lizenzpruefung bleiben intakt, Migrationen 040-042 bleiben (bestehende Datenbanken auf
Revision 042 starten weiter), das Vollprofil stellt Finance weiter bereit. Nur synthetische Daten."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules import profile

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh_profile_cache():
    profile.current_profile.cache_clear()
    yield
    profile.current_profile.cache_clear()


# --- Profilermittlung -----------------------------------------------------------------------------------------------


def test_source_default_is_full_and_env_selects_pilot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(profile.ENV_VAR, raising=False)
    assert profile.current_profile() == "full" and profile.module_available("finanzen")
    profile.current_profile.cache_clear()
    monkeypatch.setenv(profile.ENV_VAR, "kanzlei-pilot")
    assert profile.available_modules() == {"kanzlei"} and not profile.module_available("finanzen")
    profile.current_profile.cache_clear()
    monkeypatch.setenv(profile.ENV_VAR, "unbekannt")
    with pytest.raises(RuntimeError):
        profile.current_profile()


@pytest.mark.parametrize("content,expected", [(None, "kanzlei-pilot"), ("full", "full"), ("kanzlei-pilot", "kanzlei-pilot"),
                                              ("irgendwas", "kanzlei-pilot")])
def test_installed_exe_reads_only_the_bundled_file_fail_closed(monkeypatch, tmp_path: Path, content, expected) -> None:
    if content is not None:
        (tmp_path / profile.PROFILE_FILE_NAME).write_text(content, encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv(profile.ENV_VAR, "full")          # Umgebungsvariable darf ein Paket NICHT erweitern
    assert profile.current_profile() == expected


def test_valid_finance_license_unlocks_nothing_in_pilot(monkeypatch, db_session) -> None:  # noqa: F811
    from app.modules import licensing
    from tests.module_license_utils import LicenseFactory

    LicenseFactory(monkeypatch).activate(db_session, ("finanzen", "steuern"))
    assert licensing.enabled_modules(db_session) == {"kanzlei", "finanzen", "steuern"}     # Vollprofil
    profile.current_profile.cache_clear()
    monkeypatch.setenv(profile.ENV_VAR, "kanzlei-pilot")
    assert licensing.enabled_modules(db_session) == {"kanzlei"}
    assert not licensing.is_enabled(db_session, "finanzen")


from tests.test_web_chat import db_session  # noqa: E402,F401 - Fixture

# --- Pilot-App in eigenem Prozess (Router-Registrierung passiert beim Import von app.main) -------------------------

_PROBE = r'''
import json, sys
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest
from app.db.session import get_db
from app.main import app
from app.models import Base, Client
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles
from tests.module_license_utils import LicenseFactory

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine)()
app.dependency_overrides[get_db] = lambda: (yield db)
mp = pytest.MonkeyPatch()
LicenseFactory(mp).activate(db, ("finanzen", "steuern"))      # gueltige Lizenz liegt vor
roles = seed_roles(db)
create_test_user(db, roles["admin"], "admin@kanzlei.test")
c = Client(name="QA Muster GmbH"); db.add(c); db.commit()
client = TestClient(app)
login(client, "admin@kanzlei.test")
token = extract_csrf(client.get("/dashboard/chat").text)
out = {
    "chat": client.get("/dashboard/chat").status_code,
    "clients": client.get("/dashboard/clients").status_code,
    "matters": client.get("/dashboard/matters").status_code,
    "nav_fibu": 'href="/dashboard/fibu"' in client.get("/dashboard/chat").text,
    "fibu_get": client.get(f"/dashboard/fibu?client={c.id}").status_code,
    "fibu_post": client.post("/dashboard/fibu/accounts/seed", data={"csrf_token": token, "client_id": c.id}, follow_redirects=False).status_code,
    "fibu_export": client.get(f"/dashboard/fibu/export/datev-entwurf.csv?client={c.id}").status_code,
    "fibu_receipt": client.get(f"/dashboard/fibu/receipts/x/file?client={c.id}").status_code,
    "settings_has_finance": 'data-module="finanzen"' in client.get("/dashboard/settings?tab=lizenz").text,
    "settings_has_kanzlei": 'data-module="kanzlei"' in client.get("/dashboard/settings?tab=lizenz").text,
    "fibu_code_loaded": "app.fibu.service" in sys.modules or "app.web.fibu_router" in sys.modules,
}
print("PROBE" + json.dumps(out))
'''


def _probe(profile_name: str) -> dict:
    env = dict(os.environ, LEXONO_BUILD_PROFILE=profile_name, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", _PROBE], cwd=str(ROOT), env=env, capture_output=True, text=True,
                       timeout=300)
    line = next((l for l in r.stdout.splitlines() if l.startswith("PROBE")), None)
    assert line, r.stdout[-2000:] + r.stderr[-3000:]
    return json.loads(line[5:])


def test_pilot_app_has_no_finance_even_with_valid_license() -> None:
    out = _probe("kanzlei-pilot")
    assert (out["chat"], out["clients"], out["matters"]) == (200, 200, 200)          # Kanzlei intakt
    assert not out["nav_fibu"] and not out["settings_has_finance"] and out["settings_has_kanzlei"]
    assert (out["fibu_get"], out["fibu_post"], out["fibu_export"], out["fibu_receipt"]) == (404, 404, 404, 404)
    assert out["fibu_code_loaded"] is False


def test_full_app_still_provides_finance() -> None:
    out = _probe("full")
    assert out["nav_fibu"] and out["settings_has_finance"] and out["fibu_get"] == 200 and out["fibu_post"] == 303
    assert out["fibu_code_loaded"] is True


# --- Migrationen und Paketdefinition ------------------------------------------------------------------------------------


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_db_at_042_upgrades_to_head_without_error(tmp_path: Path, monkeypatch) -> None:
    from alembic import command
    from alembic.script import ScriptDirectory

    from tests.test_migrations import _alembic_config_for

    monkeypatch.setenv(profile.ENV_VAR, "kanzlei-pilot")       # Migrationen sind profilunabhaengig
    cfg = _alembic_config_for(tmp_path / "m.db")
    command.upgrade(cfg, "schritt3_042")
    command.upgrade(cfg, "head")                                  # wie beim Programmstart (run.py)
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["schritt3_042"]
    import sqlite3
    con = sqlite3.connect(tmp_path / "m.db")
    assert con.execute("select version_num from alembic_version").fetchone()[0] == "schritt3_042"
    con.close()


def test_spec_keeps_migrations_and_excludes_only_finance_runtime_in_pilot() -> None:
    spec = (ROOT / "windows" / "lexono.spec").read_text(encoding="utf-8")
    assert '(str(PROJECT_ROOT / "migrations"), "migrations")' in spec          # alle Migrationen im Bundle
    assert '_FINANCE_EXCLUDES = ["app.fibu", "app.fibu.service", "app.web.fibu_router"] if _PILOT else []' in spec
    assert '"app/web/templates/fibu.html", "app/web/static/css/fibu.css"' in spec
    assert 'LEXONO_BUILD_PROFILE", PROFILE_PILOT' in spec                   # Installer-Standard: Pilot
    assert "app.models" not in spec.split("_FINANCE_EXCLUDES")[1].split("\n")[0]   # Modelle bleiben
    for rev in ("schritt3_040", "schritt3_041", "schritt3_042"):
        assert list((ROOT / "migrations" / "versions").glob(f"{rev}_*.py"))


def test_finance_css_is_separate_and_only_loaded_by_finance_page() -> None:
    app_css = (ROOT / "app/web/static/css/app.css").read_text(encoding="utf-8")
    fibu_css = (ROOT / "app/web/static/css/fibu.css").read_text(encoding="utf-8")
    assert ".fibu-kpi" not in app_css and ".fibu-kpi" in fibu_css and ".sidebar__new-badge" in app_css
    assert "/dashboard/static/css/fibu.css" in (ROOT / "app/web/templates/fibu.html").read_text(encoding="utf-8")
    assert "fibu.css" not in (ROOT / "app/web/templates/base.html").read_text(encoding="utf-8")
