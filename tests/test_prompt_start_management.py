"""Standard-Prompts der Chat-Startseite (28.10.2026, "Standard-Prompts verwalten"): CRUD, Validierung, Persistenz,
Viererlimit, Sortierung, Rollen, Startseite und Uebernahme der bisherigen Startkarten per Migration. Nur synthetische
Daten, isolierte Datenbanken."""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, PromptTemplate
from app.prompt_library import start_prompts as sp
from tests.auth_test_utils import create_test_user, extract_csrf, login, login_as_admin, seed_roles
from tests.test_web_chat import client, db_session  # noqa: F401 - Fixtures

DEFAULT_TITLES = ["Dokument analysieren", "Schriftsatz erstellen", "Zusammenfassen", "Akte öffnen"]


def _items(db: Session) -> list[dict]:
    return [sp.serialize(row) for row in sp.ordered_templates(db)]


def _new(name: str, *, on: bool = False, description: str = "QA-Beschreibung", content: str = "QA-Anweisung") -> dict:
    return {"id": None, "key": "neu-" + name, "name": name, "description": description, "content": content,
            "icon": "bulb", "show_on_start": on}


def _start_titles(db: Session) -> list[str]:
    return [card.title for card in sp.start_cards(db)]


# --- Service -------------------------------------------------------------------------------------------------------


def test_defaults_are_seeded_once_and_shown_in_order(db_session: Session) -> None:
    sp.seed_default_start_prompts(db_session)
    sp.seed_default_start_prompts(db_session)  # idempotent: keine Duplikate
    assert db_session.query(PromptTemplate).count() == 4
    assert _start_titles(db_session) == DEFAULT_TITLES
    actions = {card.title: card.action for card in sp.start_cards(db_session)}
    assert actions == {"Dokument analysieren": "prefill_attach", "Schriftsatz erstellen": "prefill",
                       "Zusammenfassen": "prefill_attach", "Akte öffnen": "open_matter"}


def test_create_edit_delete_and_reorder_in_one_save(db_session: Session) -> None:
    sp.seed_default_start_prompts(db_session)
    items = _items(db_session)
    items[1]["name"] = "Schriftsatz entwerfen"       # bearbeiten
    deleted = [items[2]["id"]]                       # "Zusammenfassen" loeschen
    rest = [items[3], items[1], items[0]]            # umsortieren: Akte, Schriftsatz, Dokument
    payload = {"items": rest + [_new("Rechtliche Bewertung", on=True)], "deleted": deleted}
    sp.apply_management_save(db_session, payload, actor="anwalt@kanzlei.test")
    assert _start_titles(db_session) == ["Akte öffnen", "Schriftsatz entwerfen", "Dokument analysieren",
                                         "Rechtliche Bewertung"]
    assert db_session.query(PromptTemplate).filter_by(name="Zusammenfassen").count() == 0
    edited = db_session.query(PromptTemplate).filter_by(name="Schriftsatz entwerfen").one()
    assert edited.version == 2 and edited.content.startswith("Bitte erstelle einen Schriftsatz-Entwurf")
    events = {e.event_type for e in db_session.query(AuditEvent).all()}
    assert {"prompt_template_created", "prompt_template_updated", "prompt_template_deleted",
            "prompt_start_page_updated"} <= events


def test_only_activated_prompts_appear_not_simply_the_first_four(db_session: Session) -> None:
    sp.seed_default_start_prompts(db_session)
    items = _items(db_session)
    for item in items:
        item["show_on_start"] = False
    items[2]["show_on_start"] = True                                     # nur "Zusammenfassen"
    extra = _new("Recherche", on=True)
    sp.apply_management_save(db_session, {"items": [items[2], extra] + items[:2] + items[3:], "deleted": []},
                             actor="admin@kanzlei.test")
    assert _start_titles(db_session) == ["Zusammenfassen", "Recherche"]
    # deaktivieren loescht nichts
    assert db_session.query(PromptTemplate).count() == 5


def test_a_fifth_activation_is_rejected_and_nothing_changes(db_session: Session) -> None:
    sp.seed_default_start_prompts(db_session)
    before = _items(db_session)
    payload = {"items": before + [_new("Fuenfter", on=True)], "deleted": []}
    with pytest.raises(sp.PromptManagementError) as exc:
        sp.apply_management_save(db_session, payload, actor="admin@kanzlei.test")
    assert "höchstens 4" in str(exc.value) and "deaktivieren" in str(exc.value)
    db_session.rollback()
    assert _items(db_session) == before and db_session.query(PromptTemplate).count() == 4


@pytest.mark.parametrize(("field", "value", "message"), [
    ("name", "", "Titel"),
    ("description", "", "Beschreibung"),
    ("content", "   ", "Prompt-Anweisung"),
    ("name", "x" * 101, "höchstens 100"),
    ("description", "x" * 201, "höchstens 200"),
    ("content", "x" * 2001, "höchstens 2000"),
])
def test_required_fields_and_limits_are_validated(db_session: Session, field: str, value: str, message: str) -> None:
    sp.seed_default_start_prompts(db_session)
    item = _new("Neu")
    item[field] = value
    with pytest.raises(sp.PromptManagementError) as exc:
        sp.apply_management_save(db_session, {"items": _items(db_session) + [item], "deleted": []}, actor="a")
    assert message in str(exc.value) and exc.value.field == field and exc.value.key == "neu-Neu"
    db_session.rollback()
    assert db_session.query(PromptTemplate).count() == 4


def test_unknown_icon_and_stale_ids_are_rejected(db_session: Session) -> None:
    sp.seed_default_start_prompts(db_session)
    item = _new("Neu")
    item["icon"] = "<script>"
    with pytest.raises(sp.PromptManagementError):
        sp.apply_management_save(db_session, {"items": [item], "deleted": []}, actor="a")
    with pytest.raises(sp.PromptManagementError):
        sp.apply_management_save(db_session, {"items": [{**_new("X"), "id": "gibt-es-nicht"}], "deleted": []},
                                 actor="a")


def test_existing_long_texts_stay_untouched_and_do_not_block_activation(db_session: Session) -> None:
    long = PromptTemplate(name="Alt " + "x" * 150, description="", content="y" * 5000, version=3)
    db_session.add(long)
    db_session.commit()
    items = _items(db_session)
    items[0]["show_on_start"] = True
    sp.apply_management_save(db_session, {"items": items, "deleted": []}, actor="a")
    db_session.refresh(long)
    assert long.show_on_start and long.version == 3 and len(long.content) == 5000


def test_prompts_not_in_the_payload_are_kept(db_session: Session) -> None:
    """Parallel angelegte Prompts (nicht in der gespeicherten Liste) bleiben erhalten und reihen sich hinten ein."""
    sp.seed_default_start_prompts(db_session)
    items = _items(db_session)
    db_session.add(PromptTemplate(name="Parallel", description="d", content="c", version=1))
    db_session.commit()
    sp.apply_management_save(db_session, {"items": items, "deleted": []}, actor="a")
    assert [i["name"] for i in _items(db_session)][-1] == "Parallel"


# --- Web -----------------------------------------------------------------------------------------------------------


def _save(client: TestClient, payload: dict) -> object:
    csrf = extract_csrf(client.get("/dashboard/chat?new=1").text)
    return client.post("/dashboard/library/prompts/manage", data={"csrf_token": csrf, "payload": json.dumps(payload)})


def test_manage_endpoints_load_save_and_persist_for_the_start_page(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    sp.seed_default_start_prompts(db_session)
    data = client.get("/dashboard/library/prompts/manage.json").json()
    assert data["max_start"] == 4 and data["can_edit"] is True
    assert data["limits"] == {"name": 100, "description": 200, "content": 2000}
    items = data["items"]
    items[0]["show_on_start"] = False
    payload = {"items": [items[3], items[1], items[2], items[0], _new("Recherche", on=True)], "deleted": []}
    response = _save(client, payload)
    assert response.status_code == 200 and response.json()["ok"] is True
    # Persistenz: neuer Aufruf liefert gespeicherte Auswahl/Reihenfolge, Startseite zeigt nur die aktivierten
    again = client.get("/dashboard/library/prompts/manage.json").json()["items"]
    assert [i["name"] for i in again if i["show_on_start"]] == ["Akte öffnen", "Schriftsatz erstellen",
                                                                 "Zusammenfassen", "Recherche"]
    html = client.get("/dashboard/chat?new=1").text
    block = html[html.index('<div class="chat-empty-state">'):html.index('class="chat-composer"')]
    titles = re.findall(r'<span class="chat-quick-action__title">([^<]+)</span>', block)
    assert titles == ["Akte öffnen", "Schriftsatz erstellen", "Zusammenfassen", "Recherche"]
    assert "Dokument analysieren" not in block


def test_save_errors_return_400_and_change_nothing(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    sp.seed_default_start_prompts(db_session)
    items = client.get("/dashboard/library/prompts/manage.json").json()["items"]
    response = _save(client, {"items": items + [_new("Fuenfter", on=True)], "deleted": []})
    assert response.status_code == 400 and "deaktivieren" in response.json()["error"]
    assert client.get("/dashboard/library/prompts/manage.json").json()["items"] == items
    bad = client.post("/dashboard/library/prompts/manage",
                      data={"csrf_token": extract_csrf(client.get("/dashboard/chat?new=1").text), "payload": "{kaputt"})
    assert bad.status_code == 400


def test_staff_can_view_but_not_change(client: TestClient, db_session: Session) -> None:
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["mitarbeiter"], "mitarbeiter@kanzlei.test")
    login(client, "mitarbeiter@kanzlei.test")
    sp.seed_default_start_prompts(db_session)
    data = client.get("/dashboard/library/prompts/manage.json").json()
    assert data["can_edit"] is False and len(data["items"]) == 4
    response = _save(client, {"items": [], "deleted": [data["items"][0]["id"]]})
    assert response.status_code in (302, 303, 403)
    assert db_session.query(PromptTemplate).count() == 4


def test_csrf_is_required(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    sp.seed_default_start_prompts(db_session)
    response = client.post("/dashboard/library/prompts/manage", data={"csrf_token": "falsch", "payload": "{}"})
    assert response.status_code in (400, 403)


def test_start_page_without_active_prompts_shows_a_hint(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/chat?new=1").text
    assert "chat-empty-state__no-prompts" in html and 'data-prompt-manager-open' in html
    assert 'id="prompt-manager"' in html and "/dashboard/static/js/prompt_manager.js" in html


def test_modal_markup_matches_both_reference_states(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/chat?new=1").text
    modal = html[html.index('<dialog id="prompt-manager"'):html.index("</dialog>", html.index('<dialog id="prompt-manager"'))]
    for marker in ("Standard-Prompts verwalten", "Alle Prompts", "data-pm-count", "Neuen Prompt hinzufügen",
                   "Prompt auswählen oder erstellen", "Bis zu 4 Prompts auf der Chat-Startseite anzeigen",
                   "Neuen Prompt erstellen", "Icon", "Auf der Chat-Startseite anzeigen", "Titel", "Beschreibung",
                   "Prompt-Anweisung", "data-pm-cancel", "data-pm-save", "data-pm-confirm"):
        assert marker in modal, marker
    # keine Weiterleitung in die Einstellungen; alle Inhalte per textContent
    script = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app/web/static/js/prompt_manager.js").read_text(
        encoding="utf-8")
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
    assert "state.selected = null;  // beim Oeffnen ist kein Prompt ausgewaehlt" in script
    assert "Ungespeicherte Änderungen verwerfen?" in script


# --- Migration: Uebernahme der bisherigen Startkarten ---------------------------------------------------------------


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_migration_adopts_the_previous_start_cards_without_losing_data(tmp_path) -> None:
    from alembic import command

    from tests.test_migrations import _alembic_config_for

    db_path = tmp_path / "bestand.db"
    command.upgrade(_alembic_config_for(db_path), "schritt3_038")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO prompt_templates (id, created_at, updated_at, name, description, content, version) "
                "VALUES ('p1', '2026-01-01', '2026-01-01', 'Fristverlängerung', 'Eigene Vorlage', "
                "'Sehr geehrte/r {Mandant} ...', 4)")
    con.execute("INSERT INTO prompt_templates (id, created_at, updated_at, name, description, content, version) "
                "VALUES ('p2', '2026-01-01', '2026-01-01', 'Zusammenfassen', 'Kanzleieigene Fassung', "
                "'Eigene Anweisung', 2)")
    con.commit()
    con.close()
    command.upgrade(_alembic_config_for(db_path), "head")
    db = sessionmaker(bind=create_engine(f"sqlite:///{db_path}"))()
    assert _start_titles(db) == DEFAULT_TITLES
    own = db.query(PromptTemplate).filter_by(name="Fristverlängerung").one()
    assert own.content == "Sehr geehrte/r {Mandant} ..." and own.version == 4 and own.show_on_start is False
    # gleichnamige Vorlage wird aktiviert, ihr Text bleibt unveraendert (keine Dublette)
    same = db.query(PromptTemplate).filter_by(name="Zusammenfassen").one()
    assert same.content == "Eigene Anweisung" and same.show_on_start is True
    assert db.query(PromptTemplate).count() == 5
    db.close()
