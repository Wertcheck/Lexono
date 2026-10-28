"""Tests: Standard-Prompts verwalten (Modal der Chat-Startseite).

Deckt CRUD, Validierung, Persistenz/Wiederherstellung, das Viererlimit, die
Sortierung, die Startseiten-Auswahl (nur aktivierte Prompts, in gespeicherter
Reihenfolge - nicht automatisch die obersten vier), Rechte/CSRF und die
Migration (Übernahme der bisher fest verdrahteten Startseiten-Prompts)."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import AuditEvent, PromptTemplate
from app.models.base import Base
from app.prompt_library import home_service
from app.prompt_library.home import HOME_PROMPT_LIMIT
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles

STATE_URL = "/dashboard/library/prompts/manage/state"


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def roles(db_session: Session):
    return seed_roles(db_session)


def _entry(name: str, *, home: bool = False, pid: str | None = None, **extra) -> dict:
    data = {
        "id": pid, "name": name, "description": f"Beschreibung {name}",
        "content": f"Anweisung {name}", "icon": "document", "show_on_home": home,
        "attach_on_use": False,
    }
    data.update(extra)
    return data


def _login_curator(client: TestClient, db: Session, roles, key: str = "anwalt") -> str:
    create_test_user(db, roles[key], f"{key}@kanzlei.test")
    login(client, f"{key}@kanzlei.test")
    return extract_csrf(client.get("/dashboard/chat?new=1").text)


def _save(client: TestClient, csrf: str, prompts: list[dict], deleted: list[str] | None = None):
    return client.post(
        STATE_URL,
        data={"csrf_token": csrf, "payload": json.dumps({"prompts": prompts, "deleted": deleted or []})},
    )


def _state(client: TestClient) -> dict:
    response = client.get(STATE_URL)
    assert response.status_code == 200
    return response.json()


def _home_titles(html: str) -> list[str]:
    block = html.split('id="chat-home-prompts"', 1)[1].split("</div>\n          </div>", 1)[0]
    return re.findall(r'chat-quick-action__title">([^<]+)<', block)


# --- CRUD ---------------------------------------------------------------

def test_create_edit_delete_roundtrip(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)

    created = _save(client, csrf, [_entry("A"), _entry("B", home=True)])
    assert created.status_code == 200
    prompts = created.json()["prompts"]
    assert [p["name"] for p in prompts] == ["A", "B"]
    a_id, b_id = prompts[0]["id"], prompts[1]["id"]
    assert prompts[1]["show_on_home"] is True and prompts[0]["show_on_home"] is False

    edited = _save(client, csrf, [_entry("A neu", pid=a_id, content="Neuer Text"), _entry("B", home=True, pid=b_id)])
    assert edited.status_code == 200
    row = db_session.get(PromptTemplate, a_id)
    assert (row.name, row.content, row.version) == ("A neu", "Neuer Text", 2)
    assert db_session.get(PromptTemplate, b_id).version == 1  # unverändert -> keine neue Version

    deleted = _save(client, csrf, [_entry("B", home=True, pid=b_id)], deleted=[a_id])
    assert deleted.status_code == 200
    assert db_session.get(PromptTemplate, a_id) is None
    assert db_session.get(PromptTemplate, b_id) is not None
    events = {e.event_type for e in db_session.query(AuditEvent).all()}
    assert {"prompt_template_created", "prompt_template_updated", "prompt_template_deleted", "prompt_home_layout_updated"} <= events


def test_deactivating_does_not_delete(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    pid = _save(client, csrf, [_entry("A", home=True)]).json()["prompts"][0]["id"]
    _save(client, csrf, [_entry("A", home=False, pid=pid)])
    row = db_session.get(PromptTemplate, pid)
    assert row is not None and row.show_on_home is False and row.content == "Anweisung A"
    _save(client, csrf, [_entry("A", home=True, pid=pid)])
    assert db_session.get(PromptTemplate, pid).show_on_home is True


# --- Validierung ---------------------------------------------------------

@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("name", "   ", "Titel"),
        ("name", "x" * 101, "100"),
        ("description", "", "Beschreibung"),
        ("description", "x" * 201, "200"),
        ("content", " \n ", "Anweisung"),
        ("content", "x" * 2001, "2000"),
    ],
)
def test_validation_rejects_and_changes_nothing(client, db_session, roles, field, value, fragment) -> None:
    csrf = _login_curator(client, db_session, roles)
    broken = _entry("Kaputt")
    broken[field] = value
    response = _save(client, csrf, [_entry("Gültig"), broken])
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert errors[0]["index"] == 1 and errors[0]["field"] == field and fragment in errors[0]["message"]
    assert db_session.query(PromptTemplate).count() == 0  # atomar: auch der gültige Eintrag wurde nicht gespeichert


def test_boundary_lengths_are_accepted(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    ok = _entry("n" * 100, description="d" * 200, content="c" * 2000)
    assert _save(client, csrf, [ok]).status_code == 200


def test_unknown_icon_falls_back_to_default(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    saved = _save(client, csrf, [_entry("A", icon="<script>")]).json()["prompts"][0]
    assert saved["icon"] == "document"


# --- Viererlimit ---------------------------------------------------------

def test_limit_allows_four_rejects_five_and_keeps_state(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    four = [_entry(f"P{i}", home=True) for i in range(HOME_PROMPT_LIMIT)]
    assert _save(client, csrf, four).status_code == 200

    current = _state(client)["prompts"]
    five = [*[_entry(p["name"], home=True, pid=p["id"]) for p in current], _entry("Fünfter", home=True)]
    response = _save(client, csrf, five)
    assert response.status_code == 422
    assert "deaktivieren" in response.json()["errors"][0]["message"]
    assert db_session.query(PromptTemplate).count() == 4  # nichts hinzugekommen
    assert sum(p["show_on_home"] for p in _state(client)["prompts"]) == 4


def test_unlimited_prompts_can_be_stored(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    assert _save(client, csrf, [_entry(f"P{i}", home=(i < 4)) for i in range(12)]).status_code == 200
    assert db_session.query(PromptTemplate).count() == 12
    assert len(home_service.home_prompts(db_session)) == 4


# --- Sortierung, Startseite, Wiederherstellung ----------------------------------

def test_order_persists_and_home_shows_only_active_in_saved_order(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    # Aktiv sind bewusst NICHT die obersten vier: Position 1 und 3 inaktiv.
    entries = [_entry("Eins"), _entry("Zwei", home=True), _entry("Drei"), _entry("Vier", home=True), _entry("Fünf", home=True)]
    saved = _save(client, csrf, entries).json()["prompts"]
    assert [p["name"] for p in saved] == ["Eins", "Zwei", "Drei", "Vier", "Fünf"]
    assert _home_titles(client.get("/dashboard/chat?new=1").text) == ["Zwei", "Vier", "Fünf"]

    # Umsortieren: Fünf nach vorn, Zwei ans Ende.
    by_name = {p["name"]: p["id"] for p in saved}
    reordered = [
        _entry("Fünf", home=True, pid=by_name["Fünf"]), _entry("Eins", pid=by_name["Eins"]),
        _entry("Vier", home=True, pid=by_name["Vier"]), _entry("Drei", pid=by_name["Drei"]),
        _entry("Zwei", home=True, pid=by_name["Zwei"]),
    ]
    assert _save(client, csrf, reordered).status_code == 200
    assert [p["name"] for p in _state(client)["prompts"]] == ["Fünf", "Eins", "Vier", "Drei", "Zwei"]
    assert _home_titles(client.get("/dashboard/chat?new=1").text) == ["Fünf", "Vier", "Zwei"]


def test_state_is_restored_from_a_fresh_session(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    _save(client, csrf, [_entry("A", home=True), _entry("B"), _entry("C", home=True)])
    # "Neustart": neue Datenbank-Sitzung auf demselben Speicher liest denselben Zustand
    engine = db_session.get_bind()
    fresh = sessionmaker(bind=engine)()
    try:
        assert [(p.name, p.show_on_home) for p in home_service.list_prompts(fresh)] == [("A", True), ("B", False), ("C", True)]
        assert [p.name for p in home_service.home_prompts(fresh)] == ["A", "C"]
    finally:
        fresh.close()


def test_omitted_prompts_are_kept_not_deleted(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    saved = _save(client, csrf, [_entry("A"), _entry("B")]).json()["prompts"]
    # veralteter Client kennt B nicht: B darf nicht stillschweigend verschwinden
    _save(client, csrf, [_entry("A", pid=saved[0]["id"])])
    assert {p.name for p in db_session.query(PromptTemplate).all()} == {"A", "B"}


def test_stale_id_is_rejected(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    response = _save(client, csrf, [_entry("A", pid="00000000-0000-0000-0000-000000000000")])
    assert response.status_code == 422


def test_empty_home_shows_hint_without_fake_cards(client, db_session, roles) -> None:
    _login_curator(client, db_session, roles)
    html = client.get("/dashboard/chat?new=1").text
    assert "keine Standard-Prompts auf der Startseite aktiviert" in html
    assert _home_titles(html) == []


def test_home_cards_carry_prefill_attach_and_escape_html(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    _save(client, csrf, [_entry("<b>X</b>", home=True, content='Sag "Hallo" <script>alert(1)</script>', attach_on_use=True)])
    html = client.get("/dashboard/chat?new=1").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;b&gt;X&lt;/b&gt;" in html
    assert 'data-attach="true"' in html


# --- Rechte, CSRF ----------------------------------------------------------------

def test_mitarbeiter_cannot_read_or_write_and_sees_no_manage_button(client, db_session, roles) -> None:
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")
    page = client.get("/dashboard/chat?new=1")
    assert "data-prompt-manager-open" not in page.text and 'id="pm-modal"' not in page.text
    assert client.get(STATE_URL).status_code == 403
    assert _save(client, extract_csrf(page.text), [_entry("A")]).status_code == 403
    assert db_session.query(PromptTemplate).count() == 0


def test_curator_sees_manage_button_and_modal(client, db_session, roles) -> None:
    _login_curator(client, db_session, roles, "admin")
    html = client.get("/dashboard/chat?new=1").text
    assert "data-prompt-manager-open" in html and 'id="pm-modal"' in html
    assert "app_prompt_manager.js" in html


def test_save_requires_csrf_and_login(client, db_session, roles) -> None:
    csrf = _login_curator(client, db_session, roles)
    bad = client.post(STATE_URL, data={"csrf_token": "falsch", "payload": json.dumps({"prompts": []})})
    assert bad.status_code in {400, 403}
    anonymous = TestClient(app)
    assert anonymous.get(STATE_URL, follow_redirects=False).status_code in {303, 401}
    assert csrf


# --- Migration -------------------------------------------------------------------

def _cfg(db_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_migration_adopts_existing_home_prompts_without_data_loss(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "schritt3_030")
    con = sqlite3.connect(str(db))
    for pid, name, desc, content in [
        ("1", "Zusammenfassen", "meine Beschreibung", "MEIN EIGENER TEXT"),
        ("2", "Mahnschreiben", "Mahnung", "Text {Mandant}"),
    ]:
        con.execute(
            "insert into prompt_templates(id,name,description,content,version,created_at,updated_at) "
            "values(?,?,?,?,1,'2026-01-01','2026-01-01')", (pid, name, desc, content),
        )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "select name, show_on_home, sort_order, content, description from prompt_templates order by sort_order"
    ).fetchall()
    con.close()
    names = [r[0] for r in rows]
    assert names == ["Dokument analysieren", "Schriftsatz erstellen", "Zusammenfassen", "Mahnschreiben"]
    assert [r[1] for r in rows] == [1, 1, 1, 0]
    by_name = {r[0]: r for r in rows}
    assert by_name["Zusammenfassen"][3] == "MEIN EIGENER TEXT"      # eigener Inhalt bleibt erhalten
    assert by_name["Zusammenfassen"][4] == "meine Beschreibung"
    assert by_name["Mahnschreiben"][3] == "Text {Mandant}"
    assert "analysiere das angehängte Dokument" in by_name["Dokument analysieren"][3]

    command.downgrade(cfg, "schritt3_030")
    command.upgrade(cfg, "head")  # wiederholbar
