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


def _rule_blocks(selector: str) -> list[str]:
    blocks, start = [], 0
    while (i := CSS.find(selector + " {", start)) != -1:
        blocks.append(CSS[i:CSS.index("}", i)])
        start = i + 1
    return blocks


def test_info_card_never_scrolls_internally() -> None:
    """Rechte Info-Leiste (27.10.): kein eigenes Scrollen mehr - die zuletzt geltende Regel setzt overflow:visible
    und hebt die Hoehenbegrenzung auf (vorher max-height:100% + overflow-y:auto)."""
    blocks = _rule_blocks(".knowledge-page .knowledge-laws-layout .law-info-card")
    assert any("max-height: none" in b and "overflow: visible" in b for b in blocks)
    # nach der Umstellung fuehrt keine spaetere Regel das interne Scrollen wieder ein
    tail = CSS[CSS.index("rechte Info-Leiste ohne eigenes Scrollen"):]
    for block in (b for b in blocks if b in tail):
        assert "overflow-y: auto" not in block and "max-height: 100%" not in block


def test_info_card_is_not_hidden_on_narrow_windows() -> None:
    """Unter 1100 px stand frueher `display: none` - jetzt ein Band unter der Tabelle (Grid), nie ausgeblendet."""
    for block in _rule_blocks("  .knowledge-page .law-info-card") + _rule_blocks(
            "  .knowledge-page .knowledge-laws-layout .law-info-card"):
        assert "display: none" not in block
    assert "grid-template-areas" in CSS[CSS.index("Schmale Fenster: Karte als Band"):]


def test_info_card_title_keeps_a_space_when_the_line_break_is_hidden(client: TestClient, db_session: Session) -> None:
    login_as_admin(db_session, client)
    html = client.get("/dashboard/knowledge?category=laws").text
    assert "Aktuelles Recht. <br />Lokal verfügbar." in html


def test_info_card_shows_real_team_and_local_content_counts(client: TestClient, db_session: Session) -> None:
    """Info-Leiste (27.10.): Kanzlei-Team aus der Benutzerverwaltung (aktive Konten je Rolle) und lokal aktive
    Gesetze/Paragraphen - echte Zahlen, deaktivierte Konten und deaktivierte Gesetze zaehlen nicht mit."""
    from datetime import date

    from app.models import Law, LawSection
    from tests.auth_test_utils import create_test_user, seed_roles

    login_as_admin(db_session, client)
    roles = seed_roles(db_session)
    create_test_user(db_session, roles["anwalt"], "anwalt@kanzlei.test")
    inactive = create_test_user(db_session, roles["anwalt"], "ehemalig@kanzlei.test")
    inactive.is_active = False
    db_session.add_all([Law(code="QAA", title="QA-Gesetz A", is_active=True),
                        Law(code="QAB", title="QA-Gesetz B", is_active=False)])
    db_session.flush()
    db_session.add_all([LawSection(law_code=code, section_number=str(n), title="QA", text_content="QA",
                                   last_updated=date(2026, 1, 1)) for code in ("QAA", "QAB") for n in (1, 2, 3)])
    db_session.commit()

    html = client.get("/dashboard/knowledge?category=laws").text
    stats = html[html.index('class="law-info-card__stats"'):html.index("</dl>", html.index('class="law-info-card__stats"'))]
    assert "<dt>Kanzlei-Team</dt>" in stats
    assert "2 aktive Konten" in stats and "1 Admin" in stats and "1 Anwalt/Anwältin" in stats
    assert "1 Gesetz · 3 Paragraphen" in stats


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
