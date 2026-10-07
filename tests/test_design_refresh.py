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
_DARK_LOGO_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "web"
    / "static"
    / "img"
    / "lexono-logo-dark.png"
)
_BRANDING_LIGHT_LOGO_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "branding" / "lexono-logo.png"
)
_BRANDING_DARK_LOGO_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "branding" / "Lexono-Logo Dark Mode.png"
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


def test_full_logo_lockup_assets_have_a_real_alpha_channel() -> None:
    """ECHTER FUND behoben (07.10., Owner-Direktive "TAGESABSCHLUSS" /LOGO):
    assets/branding/lexono-logo.png (Light, weisser Hintergrund) und
    assets/branding/Lexono-Logo Dark Mode.png (Dark, schwarzer
    Hintergrund) waren beide opake RGB-PNGs OHNE Alpha-Kanal (per PIL
    verifiziert) - die Dark-Version wurde bisher nur per CSS
    `mix-blend-mode: screen`-Workaround "passend" zum dunklen Seiten-
    hintergrund dargestellt (siehe Git-Historie app.css). Beide Dateien
    (und die davon unveraendert nach app/web/static/img/ kopierte
    lexono-logo-dark.png) haben jetzt einen echten Alpha-Kanal, aus den
    ORIGINALEN Pixeln deterministisch zurueckgerechnet (kein neu
    gezeichnetes/KI-generiertes Logo) - Eckpixel beider Dateien muessen
    vollstaendig transparent sein (Alpha 0)."""
    from PIL import Image

    for path in (
        _BRANDING_LIGHT_LOGO_PATH,
        _BRANDING_DARK_LOGO_PATH,
        _DARK_LOGO_PATH,
    ):
        assert path.exists(), path
        img = Image.open(path)
        assert img.mode == "RGBA", f"{path} hat keinen Alpha-Kanal (mode={img.mode})"
        corner_alpha = img.getpixel((0, 0))[3]
        assert corner_alpha == 0, f"{path} Eckpixel nicht transparent (alpha={corner_alpha})"


#: Spaltenbereich der durchgehend transparenten Luecke zwischen Icon und
#: Wortmarke im Light-Lockup (per Spalten-Alpha-Analyse ermittelt, siehe
#: Git-Historie) - dieselben Grenzen wie beim Erzeugen der Dark-Datei
#: verwendet, hier zur Verifikation erneut herangezogen.
_LOGO_ICON_END_EXCLUSIVE = 498


def test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon() -> None:
    """ECHTER FUND behoben (07.10., Owner-Direktive "LEXONO-LOGO DARK MODE
    EXAKTE KONSTRUKTION"): die vorherige Dark-Datei war zwar bereits
    transparent (siehe test_full_logo_lockup_assets_have_a_real_alpha_
    channel oben), aber ein UNABHAENGIGER Export - per Bounding-Box-
    Messung reproduziert, dass Icon/Wortmarke-Layout zwischen Light und
    Dark um ca. 1-2% abwich (nicht dasselbe Asset, nur ein sehr
    aehnliches). Die Dark-Datei wird jetzt direkt aus der Light-Datei
    erzeugt: die Icon-Spalten (0..498) sind 1:1 (byte-identisch)
    uebernommen, NUR die Wortmark-Spalten (ab 576) wurden auf Weiss
    umgefaerbt (RGB only, Alpha/Buchstabenform unveraendert) - siehe
    Git-Historie fuer das Erzeugungsskript. Dieser Test verankert die
    Byte-Identitaet der Icon-Region dauerhaft gegen eine zukuenftige,
    erneut unabhaengige Neuexportierung der Dark-Datei."""
    from PIL import Image
    import numpy as np

    light = np.asarray(Image.open(_BRANDING_LIGHT_LOGO_PATH).convert("RGBA"))
    dark = np.asarray(Image.open(_BRANDING_DARK_LOGO_PATH).convert("RGBA"))

    assert light.shape == dark.shape, "Light-/Dark-Logo haben unterschiedliche Canvas-Groesse"

    light_icon = light[:, :_LOGO_ICON_END_EXCLUSIVE, :]
    dark_icon = dark[:, :_LOGO_ICON_END_EXCLUSIVE, :]
    assert np.array_equal(light_icon, dark_icon), (
        "Icon-Region weicht zwischen Light- und Dark-Logo ab - muss "
        "byte-identisch sein (dasselbe originale Icon-Asset, nicht neu "
        "exportiert/gezeichnet)."
    )


def test_dark_logo_has_identical_overall_geometry_to_light_logo() -> None:
    """Gegenprobe zum Icon-Byte-Identitaets-Test oben: die GESAMTE
    sichtbare Bounding Box (Icon + Wortmarke zusammen) muss zwischen
    Light und Dark exakt uebereinstimmen - keine unterschiedliche
    Skalierung, keine unterschiedlichen Abstaende/Positionen."""
    from PIL import Image
    import numpy as np

    def bbox(path):
        arr = np.asarray(Image.open(path).convert("RGBA"))
        alpha = arr[:, :, 3]
        ys, xs = np.where(alpha > 20)
        return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

    light_bbox = bbox(_BRANDING_LIGHT_LOGO_PATH)
    dark_bbox = bbox(_BRANDING_DARK_LOGO_PATH)
    assert light_bbox == dark_bbox, (
        f"Bounding Box weicht ab: Light={light_bbox} Dark={dark_bbox} - "
        "Gesamtproportion/Position muss zwischen beiden Modi identisch sein."
    )


def test_dark_logo_wordmark_is_recolored_to_white() -> None:
    """Die Wortmark-Region (ab Spalte 576, nach der transparenten Luecke)
    muss im Dark-Logo tatsaechlich weiss sein - die einzige erlaubte
    Abweichung von der Light-Datei."""
    from PIL import Image
    import numpy as np

    dark = np.asarray(Image.open(_BRANDING_DARK_LOGO_PATH).convert("RGBA"))
    wordmark_start = 576
    wordmark_region = dark[:, wordmark_start:, :]
    opaque_mask = wordmark_region[:, :, 3] > 200
    opaque_pixels = wordmark_region[opaque_mask]
    assert opaque_pixels.size > 0, "Keine (ausreichend) deckenden Wortmark-Pixel gefunden"
    assert (opaque_pixels[:, :3] == 255).all(), (
        "Nicht alle deckenden Wortmark-Pixel im Dark-Logo sind reines Weiss."
    )


def test_sidebar_header_uses_the_same_icon_and_live_wordmark_in_both_themes() -> None:
    """ECHTER FUND behoben (07.10., Owner-Direktive "LEXONO-LOGO DARK MODE
    EXAKTE KONSTRUKTION"): Dark Mode tauschte bisher per CSS auf eine
    SEPARATE, flach exportierte Logo-Datei (Icon+Wortmarke als ein Bild) -
    live nachgemessen (getBoundingClientRect), dass dadurch Icon-Groesse
    (32px statt 38px) UND die Icon/Wortmarke-Relation zwischen den Themes
    tatsaechlich abwichen (zwei unabhaengig exportierte Dateien, keine
    wirklich gemeinsame Konstruktion). Jetzt rendert die Kopfzeile in
    BEIDEN Themes dieselben zwei Elemente (`logo-mark.png`-Icon +
    `.sidebar__brand-text`-Live-Wortmarke) - kein `sidebar__brand-logo--
    dark`-Bildwechsel mehr, keine separate Dark-Logo-Datei im Markup."""
    html = _read_base_html()
    assert "sidebar__brand-logo--dark" not in html
    assert "lexono-logo-dark.png" not in html
    assert html.count('src="/dashboard/static/img/logo-mark.png"') == 1
    assert html.count('class="sidebar__brand-text"') == 1

    css = _read_css()
    assert ":root[data-theme=\"dark\"] .sidebar__brand-logo--dark {" not in css
    assert "sidebar__brand-logo--dark" not in css


def test_sidebar_wordmark_turns_pure_white_in_dark_mode_only() -> None:
    """Gegenprobe zum obigen Fund: die EINZIGE erlaubte Abweichung zwischen
    den Themes ist die Textfarbe der Wortmarke - ein expliziter, reiner
    Weiss-Wert (nicht das allgemeine `--ink-900`-Offwhite-Token), wie von
    der Direktive ausdruecklich verlangt ("Schriftzug wird auf Weiss
    geaendert")."""
    css = _read_css()
    start = css.index(':root[data-theme="dark"] .sidebar__brand-text {')
    end = css.index("}", start)
    block = css[start:end]
    assert "color: #ffffff;" in block


def test_sidebar_collapse_icon_is_a_plain_chevron_not_a_k_shape() -> None:
    """ECHTER FUND behoben (07.10., Owner-Direktive "SIDEBAR-COLLAPSE-
    ICON"): das vorherige Icon kombinierte einen Rahmen-Rect, eine
    vertikale Linie UND einen Pfeil direkt daneben - wirkte dadurch wie
    ein Buchstabe "K" (live im Dark-Mode-Header neben dem Logo
    reproduziert). Jetzt nur noch ein einzelner Chevron-Pfad, kein
    `<rect>`, keine zusaetzliche vertikale Linie."""
    import re as _re

    icons_path = (
        Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "_icons.html"
    )
    html = icons_path.read_text(encoding="utf-8")
    start = html.index("{% macro collapse(")
    end = html.index("{% endmacro %}", start)
    block = html[start:end]
    assert "<rect" not in block
    assert "M9 4v16" not in block
    assert _re.search(r"<path d=\"M15\.5 5\.5 9 12l6\.5 6\.5\"", block)


def test_sidebar_collapse_icon_rotates_to_point_the_other_way_when_collapsed() -> None:
    """Ein einziges SVG statt zwei Varianten: app.css dreht den Chevron per
    `transform: rotate(180deg)`, sobald `html.sidebar-collapsed` aktiv
    ist - zeigt dadurch ausgeklappt nach links ("einklappen") und
    eingeklappt nach rechts ("ausklappen")."""
    css = _read_css()
    start = css.index("html.sidebar-collapsed .sidebar-edge-toggle .icon {")
    end = css.index("}", start)
    block = css[start:end]
    assert "transform: rotate(180deg);" in block


def test_sidebar_edge_toggle_sits_at_sidebar_right_edge_not_next_to_logo() -> None:
    """ECHTER FUND behoben (07.10., Owner-Direktive "SIDEBAR-COLLAPSE-
    CONTROL POSITIONIERUNG"): das Handle sass zuvor innerhalb von
    `.global-header__brand` direkt neben dem Logo und wirkte dadurch wie
    ein Bestandteil des Logos. Jetzt ein eigenstaendiges Geschwister-
    element von `.sidebar`/`.main`, markup-seitig NICHT mehr innerhalb
    von `.global-header__brand` - und seine Position ist an dieselbe
    `--sidebar-width`-Variable gekoppelt, die `.sidebar` fuer seine
    eigene Breite nutzt (kein fester Pixel-Abstand vom Logo), bzw. im
    eingeklappten Zustand an denselben Wert wie `.sidebar`s eigene
    eingeklappte Breite (68px)."""
    html = _read_base_html()
    brand_start = html.index('<div class="global-header__brand">')
    brand_end = html.index("</div>", brand_start)
    assert "sidebar-collapse-toggle" not in html[brand_start:brand_end]
    assert 'id="sidebar-collapse-toggle" class="sidebar-edge-toggle"' in html

    css = _read_css()
    start = css.index(".sidebar-edge-toggle {")
    end = css.index("}", start)
    block = css[start:end]
    assert "left: var(--sidebar-width);" in block
    assert "position: absolute;" in block

    collapsed_start = css.index("html.sidebar-collapsed .sidebar-edge-toggle {")
    collapsed_end = css.index("}", collapsed_start)
    collapsed_block = css[collapsed_start:collapsed_end]
    assert "left: 68px;" in collapsed_block


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


def test_chat_dropzone_hint_respects_hidden_attribute() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): `.chat-dropzone-hint {
    display: flex; }` galt bedingungslos und ueberschrieb damit das
    `hidden`-Attribut, das chat.html per JS beim Verlassen einer echten
    Drag-Aktion wieder setzt (siehe dragenter/dragleave-Handler dort) -
    die Box "Dateien hier ablegen" blieb dadurch PERMANENT sichtbar statt
    nur waehrend eines echten Datei-Drags. Der bedingungslose Basis-
    Selektor muss auf "display: none" stehen, die sichtbare Variante nur
    unter ":not([hidden])" gelten."""
    css = _read_css()
    base_start = css.index(".chat-dropzone-hint {")
    base_end = css.index("}", base_start)
    base_block = css[base_start:base_end]
    assert "display: none;" in base_block

    visible_start = css.index(".chat-dropzone-hint:not([hidden]) {")
    visible_end = css.index("}", visible_start)
    visible_block = css[visible_start:visible_end]
    assert "display: flex;" in visible_block
