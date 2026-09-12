"""Regressionsschutz für das 'Premium Legal Tech'-Design-Refresh
(Schritt 3): Farb-/Typografie-Token, Layout-Breitenbegrenzung, Logo,
entfernter Entwickler-Text. Die eigentliche Wirkung wurde während der
Umsetzung live im Browser über getComputedStyle() verifiziert (Schrift,
Hintergrundfarbe, Sidebar-Farbe, Button-Farbe, Panel-Schatten/-Radius,
Formular-Maximalbreite, Logo-Ladeerfolg) - diese Tests verankern die
zugrunde liegenden Werte dauerhaft gegen versehentliche Regressionen."""

from __future__ import annotations

from pathlib import Path

_CSS_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "css" / "app.css"
)
_BASE_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "base.html"
)
_LOGO_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "img" / "logo-mark.png"
)


def _read_css() -> str:
    return _CSS_PATH.read_text(encoding="utf-8")


def _read_base_html() -> str:
    return _BASE_HTML_PATH.read_text(encoding="utf-8")


def test_background_is_off_white_f8fafc() -> None:
    assert "--paper-100: #f8fafc;" in _read_css()


def test_sidebar_is_light_not_dark() -> None:
    """Ueberholt durch die Lexono-UI-Ueberarbeitung (kein dunkles Sidebar-Panel
    mehr, komplett helles Layout, Trennung nur per 1px-Linie) - siehe
    ARCHITECTURE.md."""
    css = _read_css()
    assert "--sidebar-bg" not in css
    assert "background: var(--paper-100);" in css
    assert "border-right: 1px solid var(--paper-line);" in css


def test_active_sidebar_link_uses_slate_100_and_slate_900() -> None:
    css = _read_css()
    assert "--paper-200: #f1f5f9;" in css
    assert "background: var(--paper-200);" in css
    assert "color: var(--ink-900);" in css


def test_no_serif_font_anywhere_in_tokens() -> None:
    css = _read_css()
    assert "serif" not in css.lower() or "sans-serif" in css.lower()
    assert "Source Serif" not in css
    assert "Georgia" not in css


def test_font_display_equals_font_body_both_sans_serif() -> None:
    css = _read_css()
    assert "--font-display: var(--font-body);" in css
    assert "'Inter'" in css


def test_content_containers_have_max_width_constraint() -> None:
    css = _read_css()
    assert "--content-max-width: 1280px;" in css
    assert "max-width: var(--content-max-width);" in css


def test_standalone_forms_are_narrower_than_page_width() -> None:
    css = _read_css()
    assert "max-width: 640px;" in css


def test_tags_are_pill_shaped() -> None:
    css = _read_css()
    assert "--radius-pill: 999px;" in css
    assert "border-radius: var(--radius-pill);" in css


def test_cards_have_subtle_shadow_and_rounded_corners() -> None:
    css = _read_css()
    assert "--shadow-sm:" in css
    assert "box-shadow: var(--shadow-sm);" in css
    assert "--radius-md: 10px;" in css


def test_prototype_footer_text_removed() -> None:
    assert "Interner Prototyp" not in _read_base_html()


def test_logo_is_embedded_in_sidebar() -> None:
    html = _read_base_html()
    assert 'src="/dashboard/static/img/logo-mark.png"' in html
    assert "sidebar__brand-logo" in html


def test_logo_file_exists_and_is_valid_png() -> None:
    # Verbindliches Lexono-Logo (Dokument+Schild+Kette) als PNG mit
    # transparentem Hintergrund - siehe assets/branding/lexono-logo.png
    # (Quelle) bzw. app/web/static/img/logo-mark.png (auf die Icon-Marke
    # zugeschnittener, unveraendert uebernommener Ausschnitt).
    assert _LOGO_PATH.exists()
    with _LOGO_PATH.open("rb") as f:
        signature = f.read(8)
    assert signature == b"\x89PNG\r\n\x1a\n"


def test_scrollbars_are_thin_and_use_design_tokens() -> None:
    """20.08.: schlanke, "Apple-Pro"-Scrollbars global auf html/body sowie
    als Utility fuer Scroll-Container (.overflow-y-auto/.table-container) -
    Firefox (scrollbar-width/-color) und WebKit (::-webkit-scrollbar-*)
    jeweils mit denselben Design-Tokens (--scrollbar-thumb Daumen,
    --paper-line Track, --scrollbar-thumb-hover Hover). Seit dem UI-
    Feinschliff vom 20.08. ist --scrollbar-thumb an die verbindliche
    CI-Primaerfarbe #101828 gebunden (siehe --seal-green), nicht mehr an
    einen eigenstaendigen Grauton."""
    css = _read_css()
    assert "scrollbar-width: thin;" in css
    assert "scrollbar-color: var(--scrollbar-thumb) var(--paper-line);" in css
    assert ".overflow-y-auto" in css
    assert ".table-container" in css
    assert "::-webkit-scrollbar {" in css
    assert "width: 7px;" in css
    assert "height: 7px;" in css
    assert "::-webkit-scrollbar-track" in css
    assert "::-webkit-scrollbar-thumb" in css
    assert "::-webkit-scrollbar-thumb:hover" in css


def test_scrollbar_thumb_uses_primary_ink_token() -> None:
    css = _read_css()
    # Beide Scrollbar-Tokens sind transluzente Abstufungen von rgb(16, 24, 40)
    # (== #101828, die verbindliche CI-Primaerfarbe), nicht eigenstaendige
    # Grautoene - strikte Farbpalettendurchsetzung (UI-Feinschliff 20.08.).
    assert "--scrollbar-thumb: rgba(16, 24, 40, 0.28);" in css
    assert "--scrollbar-thumb-hover: rgba(16, 24, 40, 0.48);" in css

    thumb_block_start = css.index("*::-webkit-scrollbar-thumb {")
    thumb_block_end = css.index("}", thumb_block_start)
    thumb_block = css[thumb_block_start:thumb_block_end]
    assert "background-color: var(--scrollbar-thumb);" in thumb_block
    assert "border-radius: var(--radius-pill);" in thumb_block

    hover_block_start = css.index("*::-webkit-scrollbar-thumb:hover {")
    hover_block_end = css.index("}", hover_block_start)
    hover_block = css[hover_block_start:hover_block_end]
    assert "background-color: var(--scrollbar-thumb-hover);" in hover_block
