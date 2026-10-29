"""Lizenz-Tab der Einstellungen (29.10.2026): Produktuebersicht Lexono / Lexono Finance / Lexono Finance & Tax mit
Preisen je Benutzer und Monat inkl. MwSt., ehrlichem Status und Funktionsvergleich. Reine Anzeige: weder Preise noch
Produktkarten schalten etwas frei; "Ihr Paket" folgt ausschliesslich der zentralen Lizenzpruefung. Synthetische Daten."""

from __future__ import annotations

import re

from tests.auth_test_utils import login_as_admin
from tests.module_license_utils import LicenseFactory
from tests.test_web_chat import client, db_session  # noqa: F401 - Fixtures


def _license_tab(client) -> str:
    html = client.get("/dashboard/settings?tab=lizenz").text
    return html.split('data-tabpanel="lizenz"', 1)[1].split('data-tabpanel="erweitert"', 1)[0]


def test_license_tab_shows_three_products_with_prices_and_status(client, db_session) -> None:
    login_as_admin(db_session, client)
    tab = _license_tab(client)
    products = re.findall(r'data-product="([a-z_]+)" data-product-status="([a-z_]+)"', tab)
    assert products == [("lexono", "verfuegbar"), ("finance", "in_entwicklung"), ("finance_tax", "in_entwicklung")]
    for price in ("99 €", "149 €", "249 €"):
        assert price in tab
    assert tab.count("inkl. MwSt.") >= 3 and "pro Benutzer / Monat" in tab
    assert tab.count("In Entwicklung") == 2 and "Verfügbar" in tab
    assert "Finance &amp; Tax" in tab and "Ihre Vorteile im Überblick" in tab
    # Funktionsvergleich: Kanzlei-Funktionen in allen drei, Belege ab Finance, Steuern nur in Finance & Tax
    row = lambda label: tab.split(f"<th scope=\"row\">{label}</th>", 1)[1].split("</tr>", 1)[0]  # noqa: E731
    assert row("Kanzlei-Workflows").count('aria-label="enthalten"') == 3
    assert row("Mandanten- und Aktenverwaltung").count('aria-label="enthalten"') == 3
    assert row("Digitale Belegverwaltung").count('aria-label="enthalten"') == 2
    assert row("Steuererklärungen und Steuer-Workflows").count('aria-label="enthalten"') == 1
    # keine erfundene Kauf-/Freischaltfunktion auf den Karten, keine Legende "Enthalten/Nicht enthalten/In Entwicklung"
    cards = tab.split('class="license-products"', 1)[1].split("</section>", 1)[0]
    assert "<button" not in cards and "<a " not in cards and "<form" not in cards
    assert "Legende" not in tab


def test_current_package_follows_only_the_real_license_state(client, db_session, monkeypatch) -> None:
    login_as_admin(db_session, client)
    tab = _license_tab(client)
    assert re.search(r'license-product is-current"[^>]*data-product="lexono"', tab)
    assert tab.count("Ihr Paket") == 1
    LicenseFactory(monkeypatch).activate(db_session, ("finanzen", "steuern"))
    tab = _license_tab(client)
    assert re.search(r'license-product is-current"[^>]*data-product="finance_tax"', tab)  # Vollprofil + Lizenz
    # die Lizenzpruefung selbst bleibt sichtbar und unveraendert
    assert 'id="module-licenses"' in tab and "/dashboard/settings/license/import" in tab
