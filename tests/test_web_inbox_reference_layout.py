"""Posteingang nach Referenzbild (26.10.): Lesestatus, Auswahl/Sammelaktion, Kopfbereich-Statistik, letzter Abruf,
"Neue Nachricht", Dark-Mode-Umschalter. Ausschliesslich synthetische Daten (Fixtures aus test_web_inbox.py)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.mail import poll_status
from app.models import Message
from app.web.router import _message_time_label, _sender_parts
from tests.auth_test_utils import extract_csrf
from tests.test_web_inbox import client, db_session, mitarbeiter_client, seeded  # noqa: F401 - pytest-Fixtures


def _csrf(client: TestClient) -> str:
    return extract_csrf(client.get("/dashboard/inbox").text)


def _row_html(html: str, message_id: str) -> str:
    start = html.index(f'data-message-id="{message_id}"')
    return html[html.rindex("<div", 0, start):html.index("</a>", start)]


# --- Lesestatus ---------------------------------------------------------------


def test_new_inbound_messages_are_unread_and_counted(client: TestClient, seeded: dict) -> None:
    html = client.get("/dashboard/inbox").text
    assert "message-row--unread" in _row_html(html, seeded["unmatched_message_id"])
    # ausgehende Nachrichten gelten nie als ungelesen
    assert "message-row--unread" not in _row_html(html, seeded["outbound_message_id"])
    assert client.get("/dashboard/inbox/badge").text.count(">2<") == 1


def test_auto_selected_first_message_is_not_marked_read(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    client.get("/dashboard/inbox")
    db_session.expire_all()
    assert all(m.read_at is None for m in db_session.query(Message).filter_by(direction="inbound"))


@pytest.mark.parametrize("path", ["/dashboard/inbox/{id}", "/dashboard/inbox/{id}/detail"])
def test_explicitly_opening_a_message_marks_it_read(
    client: TestClient, db_session: Session, seeded: dict, path: str
) -> None:
    message_id = seeded["unmatched_message_id"]
    assert client.get(path.format(id=message_id)).status_code == 200
    db_session.expire_all()
    assert db_session.get(Message, message_id).read_at is not None
    assert db_session.get(Message, seeded["matched_message_id"]).read_at is None


def test_badge_is_empty_without_unread_messages(client: TestClient, db_session: Session, seeded: dict) -> None:
    for message in db_session.query(Message).all():
        message.read_at = datetime.now(timezone.utc)
    db_session.commit()
    assert client.get("/dashboard/inbox/badge").text == ""


def test_bulk_mark_read_and_unread(client: TestClient, db_session: Session, seeded: dict) -> None:
    ids = [seeded["matched_message_id"], seeded["unmatched_message_id"], seeded["outbound_message_id"]]
    response = client.post("/dashboard/inbox/mark", data={"csrf_token": _csrf(client), "state": "read",
                                                           "message_ids": ids, "return_to": "/dashboard/inbox?filter=all"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/inbox?filter=all"
    db_session.expire_all()
    assert db_session.get(Message, ids[0]).read_at is not None
    assert db_session.get(Message, ids[1]).read_at is not None
    assert db_session.get(Message, ids[2]).read_at is None  # ausgehend: Lesestatus wird nicht gefuehrt

    client.post("/dashboard/inbox/mark", data={"csrf_token": _csrf(client), "state": "unread", "message_ids": [ids[0]]},
                follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Message, ids[0]).read_at is None
    assert db_session.get(Message, ids[1]).read_at is not None


def test_mark_requires_csrf(client: TestClient, db_session: Session, seeded: dict) -> None:
    response = client.post("/dashboard/inbox/mark", data={"csrf_token": "falsch", "state": "read",
                                                           "message_ids": [seeded["unmatched_message_id"]]},
                           follow_redirects=False)
    assert response.status_code in (302, 303, 400, 403)
    db_session.expire_all()
    assert db_session.get(Message, seeded["unmatched_message_id"]).read_at is None


def test_mark_rejects_unknown_state(client: TestClient, seeded: dict) -> None:
    response = client.post("/dashboard/inbox/mark", data={"csrf_token": _csrf(client), "state": "geloescht",
                                                           "message_ids": [seeded["unmatched_message_id"]]},
                           follow_redirects=False)
    assert response.status_code == 400


@pytest.mark.parametrize("return_to", ["https://example.invalid/", "//example.invalid/x", "/dashboard/settings",
                                       "/dashboard/inbox/{id}", "/dashboard/inbox\\..\\x"])
def test_mark_never_redirects_outside_the_inbox_list(client: TestClient, seeded: dict, return_to: str) -> None:
    """Kein offener Redirect - und keine Einzelnachrichten-URL, deren Aufruf "ungelesen" sofort wieder aufheben
    wuerde."""
    response = client.post("/dashboard/inbox/mark", data={
        "csrf_token": _csrf(client), "state": "unread", "message_ids": [seeded["unmatched_message_id"]],
        "return_to": return_to.format(id=seeded["unmatched_message_id"])}, follow_redirects=False)
    assert response.headers["location"] == "/dashboard/inbox"


def test_list_partial_return_to_is_normalised_to_the_full_page(client: TestClient, seeded: dict) -> None:
    response = client.post("/dashboard/inbox/mark", data={
        "csrf_token": _csrf(client), "state": "read", "message_ids": [seeded["unmatched_message_id"]],
        "return_to": "/dashboard/inbox/list?filter=unmatched"}, follow_redirects=False)
    assert response.headers["location"] == "/dashboard/inbox?filter=unmatched"


# --- Auswahl, Kopfbereich, Zeile, Lesebereich -----------------------------------


def test_rows_have_selection_checkbox_outside_the_link(client: TestClient, seeded: dict) -> None:
    html = client.get("/dashboard/inbox").text
    row = _row_html(html, seeded["unmatched_message_id"])
    checkbox = row.index('name="message_ids"')
    assert 'form="inbox-bulk-form"' in row
    assert checkbox < row.index('class="message-row__link"')  # Kaestchen liegt NICHT im Link
    assert 'id="inbox-bulk-form"' in html and 'action="/dashboard/inbox/mark"' in html
    assert 'id="inbox-select-all"' in html


def test_header_shows_real_statistics_and_poll_state(client: TestClient, seeded: dict) -> None:
    html = client.get("/dashboard/inbox").text
    assert "3 E-Mails (2 ungelesen)" in html
    assert 'data-mail-poll="not_configured"' in html  # Testumgebung ohne Postfach: ehrlich, kein erfundener Abruf


def test_tabs_show_counts_matching_the_filters(client: TestClient, seeded: dict) -> None:
    """Tab-Zahlen (Referenzbild "Alle 42" ...) aus denselben Bedingungen wie die Filter."""
    html = client.get("/dashboard/inbox").text
    expected = {"all": 3, "unmatched": 1, "matched": 2, "with_attachment": 1, "inbound": 2, "outbound": 1}
    for key, count in expected.items():
        tab = html[html.index(f'data-filter-key="{key}"'):]
        assert tab[:tab.index("</a>")].endswith(f'<span class="inbox-tab__count">{count}</span>')
        listed = client.get(f"/dashboard/inbox/list?filter={key}").text.count('<div class="message-row')
        assert listed == count


def test_row_tags_show_practice_area_and_matter_reference(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    from app.models import Matter

    matter = db_session.get(Matter, seeded["matter_id"])
    matter.practice_area = "Steuerrecht"
    db_session.commit()
    html = client.get("/dashboard/inbox").text
    row = _row_html(html, seeded["matched_message_id"])
    assert '<span class="inbox-tag inbox-tag--area">Steuerrecht</span>' in row
    assert "Akte: 2025/0142-ESt" in row
    assert "nicht zugeordnet" in _row_html(html, seeded["unmatched_message_id"])


def test_poll_state_reflects_the_last_run_of_this_instance(client: TestClient, seeded: dict, monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "mail_provider", "imap")
    monkeypatch.setattr(settings, "mail_auto_sync_enabled", True)
    poll_status.reset()
    try:
        assert 'data-mail-poll="pending"' in client.get("/dashboard/inbox").text
        poll_status.record_poll(ok=True, new_messages=2)
        html = client.get("/dashboard/inbox").text
        assert 'data-mail-poll="ok"' in html and "Letzter Abruf: heute," in html
        poll_status.record_poll(ok=False)
        assert "(fehlgeschlagen)" in client.get("/dashboard/inbox").text
        monkeypatch.setattr(settings, "mail_auto_sync_enabled", False)
        assert 'data-mail-poll="disabled"' in client.get("/dashboard/inbox").text
    finally:
        poll_status.reset()


def test_list_partial_pushes_the_full_page_url(client: TestClient, seeded: dict) -> None:
    response = client.get("/dashboard/inbox/list?filter=unmatched&q=Anfrage")
    assert response.headers["HX-Push-Url"] == "/dashboard/inbox?filter=unmatched&q=Anfrage"


def test_detail_shows_sender_block_and_mark_unread_action(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    html = client.get(f"/dashboard/inbox/{seeded['matched_message_id']}").text
    assert 'class="detail-sender"' in html
    assert "j.mueller@steuerkanzlei-test.invalid" in html
    # gerade geoeffnet -> gelesen -> Aktion "Als ungelesen markieren"
    assert 'value="unread"' in html.split('class="detail-toolbar"', 1)[1].split("</div>\n  </div>", 1)[0]
    assert "1 Anhang" in html


def test_inbox_sidebar_offers_new_message_as_chat_draft(client: TestClient, seeded: dict) -> None:
    """Verbindliches Referenzbild: im Posteingang nur "Neue Nachricht"; alle anderen Seiten behalten "Neuen Chat
    starten" (test_web_inbox.py::test_sidebar_has_no_search_trigger_but_has_new_chat_button)."""
    html = client.get("/dashboard/inbox").text
    sidebar = html[html.index('<nav class="sidebar">'):html.index("</nav>", html.index('<nav class="sidebar">'))]
    assert 'href="/dashboard/chat?new=1&amp;compose=email"' in sidebar and "Neue Nachricht" in sidebar
    assert 'href="/dashboard/chat"' in sidebar  # Chat bleibt ueber die Navigation erreichbar
    other = client.get("/dashboard/clients").text
    other_sidebar = other[other.index('<nav class="sidebar">'):]
    assert "Neue Nachricht" not in other_sidebar and "Neuen Chat starten" in other_sidebar


def test_new_message_prefills_the_chat_composer_without_sending(client: TestClient, db_session: Session) -> None:
    from app.models import ChatMessage

    html = client.get("/dashboard/chat?new=1&compose=email").text
    assert ">Bitte entwirf eine neue E-Mail an </textarea>" in html
    assert db_session.query(ChatMessage).count() == 0
    assert "Bitte entwirf" not in client.get("/dashboard/chat?new=1").text.split('id="chat-input"', 1)[1][:300]


def test_header_theme_toggle_persists_only_for_admins(
    client: TestClient, mitarbeiter_client: TestClient, seeded: dict
) -> None:
    admin_html = client.get("/dashboard/inbox").text
    assert 'id="theme-toggle-btn"' in admin_html
    assert 'data-persist-url="/dashboard/settings/general/theme"' in admin_html
    staff_html = mitarbeiter_client.get("/dashboard/inbox").text
    assert 'id="theme-toggle-btn"' in staff_html
    assert 'data-persist-url=""' in staff_html and 'data-csrf=""' in staff_html


# --- Hilfsfunktionen -------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [
    ("Amtsgericht Mitte <poststelle@ag.example>", ("Amtsgericht Mitte", "poststelle@ag.example")),
    ('"Krüger, Sabine" <s.k@example.invalid>', ("Krüger, Sabine", "s.k@example.invalid")),
    ("nur@adresse.example", ("nur@adresse.example", "nur@adresse.example")),
    ("Kanzlei ohne Adresse", ("Kanzlei ohne Adresse", "")),
    (None, ("Unbekannter Absender", "")),
])
def test_sender_parts(raw, expected) -> None:
    assert _sender_parts(raw) == expected


def test_time_label_today_yesterday_and_date() -> None:
    now = datetime.now(timezone.utc).astimezone().replace(hour=12, minute=0)
    assert _message_time_label(now.replace(hour=9, minute=5), now) == "09:05"
    assert _message_time_label(now - timedelta(days=1), now) == "Gestern"
    assert _message_time_label(now - timedelta(days=5), now) == (now - timedelta(days=5)).strftime("%d.%m.%Y")


# --- Migration --------------------------------------------------------------------


@pytest.mark.usefixtures("restore_logging_after_alembic")
def test_migration_marks_existing_messages_read_and_new_ones_stay_unread(tmp_path) -> None:
    from alembic import command

    from tests.test_migrations import _alembic_config_for

    db_path = tmp_path / "bestand.db"
    command.upgrade(_alembic_config_for(db_path), "schritt3_037")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO messages (id, created_at, updated_at, direction, subject) "
                "VALUES ('m1', '2026-10-01 08:00:00', '2026-10-01 08:00:00', 'inbound', 'Bestand (synthetisch)')")
    con.commit()
    con.close()
    command.upgrade(_alembic_config_for(db_path), "head")
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT read_at FROM messages WHERE id='m1'").fetchone()[0] == "2026-10-01 08:00:00"
    con.execute("INSERT INTO messages (id, created_at, updated_at, direction) "
                "VALUES ('m2', '2026-10-26 08:00:00', '2026-10-26 08:00:00', 'inbound')")
    assert con.execute("SELECT read_at FROM messages WHERE id='m2'").fetchone()[0] is None
    con.close()
