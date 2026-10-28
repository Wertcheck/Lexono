"""Chat-Startseite nach Referenzbild (28.10.2026): Begruessung, vier kompakte Prompt-Karten ("Dokument analysieren",
"Schriftsatz erstellen", "Zusammenfassen", "Akte öffnen"), "Standard-Prompts verwalten" (bestehende Verwaltung), breite
Eingabe. Alle neuen Regeln nur unter `.chat-panel--start` (laufende Chats bleiben unveraendert). Synthetische Daten."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Client, Matter
from tests.auth_test_utils import login_as_admin
from tests.test_web_chat import client, db_session  # noqa: F401 - Fixtures

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/web/static/css/app.css").read_text(encoding="utf-8")


def _start_block(html: str) -> str:
    return html[html.index('<div class="chat-empty-state">'):html.index('class="chat-composer"')]


def test_start_page_has_four_compact_prompt_cards_in_reference_order(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/chat?new=1").text
    assert 'class="chat-panel chat-panel--start"' in html
    block = _start_block(html)
    titles = re.findall(r'<span class="chat-quick-action__title">([^<]+)</span>', block)
    assert titles == ["Dokument analysieren", "Schriftsatz erstellen", "Zusammenfassen", "Akte öffnen"]
    assert 'id="chat-greeting"' in block and "Wie kann ich Sie heute unterstützen?" in block
    # Verwaltung der Standard-Prompts: bestehende Funktion, kein neuer Pfad
    assert re.search(r'<a href="/dashboard/library/prompts" class="chat-empty-state__edit-prompts"', block)
    assert "Standard-Prompts verwalten" in block
    # keine erfundenen Bedienelemente (Drei-Punkte-Menue) und keine Pfeil-Icons auf den Karten
    assert "dots_vertical" not in block and "chevron" not in block and "Weitere Prompts" not in block
    # bestehende Schnellaktionen fuellen weiterhin nur das Eingabefeld vor
    assert block.count("data-prefill=") == 3


def test_akte_oeffnen_uses_the_existing_chat_with_matter_entry(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    owner = Client(name="QA-SYNTH Muster GmbH")
    db_session.add(owner)
    db_session.flush()
    matter = Matter(client_id=owner.id, title="QA-SYNTH Einspruch", reference_number="QA-1")
    db_session.add(matter)
    db_session.commit()
    block = _start_block(client.get("/dashboard/chat?new=1").text)
    assert "data-matter-picker" in block
    form = block[block.index('<form method="get" action="/dashboard/chat"'):]
    form = form[:form.index("</form>")]
    assert 'name="new" value="1"' in form and 'name="matter"' in form and matter.id in form
    # der Zielpfad merkt die Akte fuer die erste Nachricht vor (bestehende Funktion)
    target = client.get(f"/dashboard/chat?new=1&matter={matter.id}").text
    assert f'name="matter_id" value="{matter.id}"' in target


def test_akte_oeffnen_without_matters_links_to_the_matter_list(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    block = _start_block(client.get("/dashboard/chat?new=1").text)
    assert "data-matter-picker" not in block
    assert re.search(r'<a href="/dashboard/matters" class="chat-quick-action chat-quick-action--card', block)


def test_prefill_handler_only_binds_prompt_cards(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/chat?new=1").text
    assert 'document.querySelectorAll(".chat-quick-action[data-prefill]")' in html
    assert 'document.querySelector("[data-matter-picker]")' in html and 'evt.key === "Escape"' in html


def test_start_styles_are_scoped_to_the_start_panel() -> None:
    start = CSS.index("Chat-Startseite nach Referenzbild (28.10.2026)")
    block = CSS[start:CSS.index("\n/* ====", start)]
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.endswith("{") and not stripped.startswith("@media"):
            for selector in stripped[:-1].split(","):
                assert ".chat-panel--start" in selector, selector
    # Karten waagerecht und kompakt, Eingabe mit rundem Plus und rundem Senden-Button
    assert "flex-direction: row;" in block and "grid-template-columns: repeat(4, minmax(0, 1fr));" in block
    attach = re.search(r"\.chat-panel--start \.chat-composer__attach-btn \{([^}]*)\}", block).group(1)
    send = re.search(r"\.chat-panel--start \.chat-composer__send-btn \{([^}]*)\}", block).group(1)
    assert "border-radius: 50%" in attach and "border-radius: 50%" in send
    # schmale Fenster: Platz fuer den schwebenden Datenschutz-Button (lag bei 960x600 ueber dem Senden-Button)
    assert "padding-right: 76px" in block


def test_running_conversation_does_not_get_the_start_layout(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/chat?new=1").text
    assert "chat-panel--start" in html
    # Dokumentansicht/aktive Unterhaltung: Bedingung im Template
    template = (ROOT / "app/web/templates/chat.html").read_text(encoding="utf-8")
    assert '{% if not active_conversation and not viewing_document %} chat-panel--start{% endif %}' in template
