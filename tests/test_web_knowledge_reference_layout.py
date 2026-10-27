"""Kanzleiwissen nach Referenz 43_Kanzleiwissen_Gesetze.png (27.10.): eigener, buendiger Seitenkopf statt der
zentrierten `.topbar`, unveraenderte OOB-Swap-Ziele, Info-Karte mit Aktualisierungs-Icon, CSS nur seitenbezogen."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.auth_test_utils import login_as_admin
from tests.test_web_knowledge import (  # noqa: F401 - pytest-Fixtures
    _clear_install_progress,
    _use_test_session_local_for_background_install,
    client,
    db_session,
)

CSS = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_page_uses_own_flush_header_with_existing_oob_targets(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/knowledge").text
    head = html[html.index('<header class="knowledge-head">'):html.index("</header>", html.index('class="knowledge-head"'))]
    assert '<h1 class="knowledge-head__title">Kanzleiwissen</h1>' in head
    # Untertitel/Beschreibung bleiben die OOB-Swap-Ziele des Kategoriewechsels
    assert 'id="knowledge-subtitle"' in head and 'id="knowledge-description"' in head
    # nicht mehr die zentrierte, max. 1280 px breite Topbar
    assert 'class="topbar"' not in html


def test_panel_switch_still_swaps_subtitle_and_description(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    response = client.get("/dashboard/knowledge/panel?category=expertise")
    assert 'id="knowledge-subtitle" hx-swap-oob="true"' in response.text
    assert 'id="knowledge-description" hx-swap-oob="true"' in response.text
    assert "Fachwissen" in response.text.split('id="knowledge-subtitle"', 1)[1][:200]


def test_info_card_uses_refresh_icon_for_updates(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/knowledge?category=laws").text
    card = html[html.index('class="law-info-card"'):]
    feature = card[card.index("Flexibel"):card.index("Immer aktuell")]
    # Kreispfeile (icons.refresh, Pfad "M20 4v4.5h-4.5") wie in der Referenz, nicht mehr die Uhr
    assert 'class="law-info-card__feature-icon"' in feature and "M20 4v4.5h-4.5" in feature
    assert "law-info-card__illustration" in card


def test_reference_styles_are_scoped_to_the_knowledge_page() -> None:
    """Die Angleichung (Satzschrift statt Mono/Versalien, Status-Punkte) darf `.draft-table`/`.topbar` anderer Seiten
    nicht veraendern - alle neuen Regeln haengen an `.knowledge-head`/`.knowledge-page`."""
    block = CSS[CSS.index("Kanzleiwissen: Referenzabgleich 43_Kanzleiwissen_Gesetze.png (27.10.)"):]
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.endswith("{") and not stripped.startswith("@media"):
            selector = stripped[:-1].strip()
            assert ".knowledge-head" in selector or ".knowledge-page" in selector or selector.startswith(
                ".knowledge-head") or selector.startswith(":root"), selector
            if selector.startswith(":root"):
                assert ".knowledge-page" in selector or ".knowledge-head" in selector, selector
