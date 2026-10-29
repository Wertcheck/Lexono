"""Aufbau des ueberarbeiteten Pseudonymisierungs-Pop-ups (26.10.2026): Kopf -> Dokumentleiste -> Vorschau | Pruefpanel
-> Fussleiste, und die im Browser-Skript verankerten Regeln (Bestaetigungssperre, Abbruch ohne Speichern,
Schnellaktionen nur als Vorbelegung, Entscheidungen ueber Dokument-/Ansichtswechsel).

Das Verhalten im Browser (Layout bei 1920x1080, 1280x720, 960x600, Dokumentwechsel, viele Rueckfragen, 70 Seiten)
ist zusaetzlich per Screenshot-QA belegt (benchmarks/legal_answers_v1/qa-evidence/pseudonymization-popup-redesign-
20261026/; Referenzbild-Umsetzung 27.10.2026: .../pseudonymisierung-popup-referenz-20261027/). Hier: serverseitig gerendertes Markup + statische Pruefung der Skriptregeln - keine JS-Laufzeit im Test.
Ausschliesslich synthetische Daten; keine Produktiv-Datenbank."""

from __future__ import annotations

import re
from pathlib import Path

from tests.auth_test_utils import login_as_admin
from tests.test_schriftsatz_release_resume import writer  # noqa: F401 - Fixture
from tests.test_web_chat import client, db_session  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app/web/static/js/pseudonymization_center.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/static/css/app.css").read_text(encoding="utf-8")


def _dialog_markup(client, db_session) -> str:
    login_as_admin(db_session, client)
    page = client.get("/dashboard/chat").text
    start = page.index('<dialog id="pseudonymization-center"')
    return page[start:page.index("</dialog>", start)]


def _function(name: str) -> str:
    """Rumpf einer Funktion `function <name>(...) { ... }` des Skripts (Klammerzaehlung)."""
    start = SCRIPT.index(f"function {name}(")
    depth, index = 0, SCRIPT.index("{", start)
    for position in range(index, len(SCRIPT)):
        depth += {"{": 1, "}": -1}.get(SCRIPT[position], 0)
        if depth == 0:
            return SCRIPT[start:position + 1]
    raise AssertionError(name)


def test_layout_regions_are_in_order_header_docbar_body_footer(client, db_session, writer) -> None:
    html = _dialog_markup(client, db_session)
    order = [html.index(marker) for marker in ('class="pseudo-center__header"', 'class="pseudo-center__docbar"',
                                               'class="pseudo-center__body"', 'class="pseudo-center__footer"')]
    assert order == sorted(order)
    body = html[html.index('class="pseudo-center__body"'):html.index('class="pseudo-center__footer"')]
    # Vorschau links (groesster Bereich), Pruefpanel rechts mit eigenem Scrollbereich
    assert body.index('class="pseudo-center__document"') < body.index('class="pseudo-center__panel"')
    assert "data-pseudo-panel-scroll" in body
    # Fussleiste liegt AUSSERHALB der scrollbaren Bereiche -> immer sichtbar
    footer = html[html.index('class="pseudo-center__footer"'):]
    assert "data-pseudo-center-confirm" in footer and "data-pseudo-center-cancel" in footer
    assert "data-pseudo-footer-status" in footer and "data-pseudo-open-count" in footer


def test_header_has_title_explanation_three_views_and_close(client, db_session, writer) -> None:
    html = _dialog_markup(client, db_session)
    header = html[html.index('class="pseudo-center__header"'):html.index('class="pseudo-center__docbar"')]
    assert "Pseudonymisierung prüfen" in header and 'id="pseudo-center-intro"' in header
    assert "bis dahin wird nichts gesendet" in header
    views = re.findall(r'data-pseudo-view="(\w+)"', header)
    assert views == ["marked", "original", "result"]
    for label in ("Markierte Vorschau", "Original", "Pseudonymisiertes Ergebnis"):
        assert label in header
    # Schliessen = Abbrechen (gleiche Regel, kein eigener Pfad)
    assert re.search(r'class="pseudo-center__close" data-pseudo-center-cancel', header)


def test_docbar_tabs_with_select_for_many_documents_and_preview_tools(client, db_session, writer) -> None:
    html = _dialog_markup(client, db_session)
    docbar = html[html.index('class="pseudo-center__docbar"'):html.index('class="pseudo-center__body"')]
    # Dokument-Tabs (Typ + Status) nur bei mehreren Dokumenten; Auswahlliste erst bei vielen
    assert "data-pseudo-doc-tabs" in docbar and re.search(r"data-pseudo-doc-row hidden", docbar)
    assert re.search(r"data-pseudo-doc-select-wrap hidden", docbar)
    assert "data-pseudo-file-name" in docbar and "data-pseudo-subtitle" in docbar  # Datei wie in der Referenz
    for tool in ("data-pseudo-page-prev", "data-pseudo-page-info", "data-pseudo-page-next",
                 "data-pseudo-zoom-out", "data-pseudo-zoom-in", "data-pseudo-zoom-fit", "data-pseudo-maximize"):
        assert tool in docbar, tool
    # Seitenleiste mit Miniaturen dauerhaft (kein Ein-/Ausblende-Schalter mehr); bis zum Laden versteckt
    assert "data-pseudo-thumbs-toggle" not in html and re.search(r"data-pseudo-thumbs hidden", html)
    legend = docbar[docbar.index('class="pseudo-center__legend"'):]
    for label in ("Person", "Organisation", "Adresse", "Datum", "Sonstiges", "Rückfrage offen", "bleibt lesbar"):
        assert label in legend[:3000], label


def test_panel_offers_progress_question_navigation_protect_and_quick_actions(client, db_session, writer) -> None:
    html = _dialog_markup(client, db_session)
    panel = html[html.index('class="pseudo-center__panel"'):html.index('class="pseudo-center__footer"')]
    for marker in ("data-pseudo-progress hidden", 'role="progressbar"', "data-pseudo-q-prev", "data-pseudo-q-next",
                   "data-pseudo-questions", "data-pseudo-mappings", "data-pseudo-protect-form", "data-pseudo-quick hidden",
                   "data-pseudo-limits"):
        assert marker in panel, marker
    # Namensschutz bleibt erhalten (aktenweit, serverseitige Pruefung) - nicht kommentarlos entfernt
    assert 'name="protected_terms"' in panel and 'name="text_version"' in panel


def test_confirm_is_disabled_by_default_and_locked_while_questions_are_open() -> None:
    assert re.search(r"data-pseudo-center-confirm disabled", (ROOT / "app/web/templates/partials/"
                                                               "pseudonymization_center.html").read_text(encoding="utf-8"))
    assert 'return "Pseudonymisierung bestätigen und fortfahren"' in _function("confirmLabel")
    footer = _function("renderFooter")
    assert "remaining > 0" in footer and "state.busy" in footer and 'state.mode === "view"' in footer
    assert "!state.canManage" in footer and "blocked" in footer and "loadingNeeded" in footer
    # Auch der Klick prueft die Sperre noch einmal; der Server revalidiert (require_resolved=1)
    assert "if (confirmBtn.disabled) { return; }" in SCRIPT and 'form.append("require_resolved", "1")' in SCRIPT


def test_cancel_discards_decisions_and_sends_nothing() -> None:
    close = _function("close")
    assert "state.decisions = {}" in close
    assert "fetch(" not in close and "submit" not in close
    assert "onCancel" in close  # Chat-Rueckfrage: Abbrechen setzt nur die bestehende Blase zurueck


def test_decisions_survive_document_and_view_switches() -> None:
    for name in ("selectDoc", "setView", "renderDocument", "rerenderKeepingPage", "stepQuestion", "setMaximized"):
        assert "state.decisions = {}" not in _function(name), name
    # neu gesetzt werden Entscheidungen nur beim Oeffnen, beim Abbruch und bei einer NEUEN Server-Rueckfrage
    assert SCRIPT.count("state.decisions = {}") == 3


def test_quick_actions_only_prefill_the_chat_input() -> None:
    quick = _function("renderQuick")
    assert "fetch(" not in quick and "requestSubmit" not in quick and ".submit(" not in quick
    assert 'textarea[name=\\"content\\"]' in quick and "input.value =" in quick
    assert 'state.mode === "view"' in quick  # nur in der reinen Pruefansicht ohne offenen Auftrag


def test_panel_shows_exactly_one_question_without_a_list_of_the_others() -> None:
    """27.10.2026 (Referenzbild): immer genau EINE Rueckfrage - Fund, Kontext, Frage, drei Optionen; keine Liste
    weiterer Rueckfragen, kein zweiter Dialog."""
    cards = _function("renderQuestions")
    assert "qs.forEach" not in cards and "is-compact" not in cards
    assert "var q = qs[activeIndex(qs)];" in cards and cards.count('el("article"') == 1
    for marker in ("pseudo-question__found", '"Kontext im Dokument"', '"Was möchten Sie mit diesem Begriff tun?"',
                   '"radiogroup"', '"radio"', "aria-checked", "pseudo-question__options"):
        assert marker in cards, marker
    assert "showModal" not in cards and "<dialog" not in cards
    # Fortschritt "Rueckfrage i von n" + Vorherige / Naechste offene Rueckfrage
    progress = _function("renderProgress")
    assert '"Rückfrage " + (Math.max(index, 0) + 1) + " von " + qs.length' in progress
    assert '"Nächste offene Rückfrage"' in progress
    assert "renderQuestions()" in _function("activateQuestion")


def test_chat_question_context_is_labelled_as_message_context() -> None:
    """Native Pruefung 28.10.2026: bei der Rueckfrage beim Senden stammt der Kontext aus der Chat-Nachricht - auch wenn
    der Begriff zusaetzlich in einem angehaengten Dokument markiert ist."""
    cards = _function("renderQuestions")
    assert 'q.fromChat ? "Kontext in Ihrer Nachricht" : "Kontext im Dokument"' in cards


def test_option_hints_stay_on_one_line_in_low_windows() -> None:
    """Native Pruefung 28.10.2026 (WebView2 mit sichtbarer Bildlaufleiste, 1280x720): ein umbrechender Hinweis schob
    "Unsicher" unter den Panelrand - unter 760 px Hoehe sind die Hinweise einzeilig (voll im Tooltip)."""
    block = CSS[CSS.index("@media (max-height: 760px)", CSS.index("/* --- Zentraler Pseudonymisierungsdialog")):]
    block = block[:block.index("\n}\n")]
    assert ".pseudo-question__option small { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }" in block


def test_question_is_compacted_up_to_900px_window_height() -> None:
    """Native Pruefung 28.10.2026: im maximierten Fenster auf 1920x1080 bei 125 % (Viewport 1536x842) lag "Unsicher"
    unter dem Panelrand. Bis 900 px Hoehe ist die Rueckfrage kompakter; schmal UND niedrig stehen die Ansichten in
    der Titelzeile."""
    block = CSS[CSS.index("/* --- Zentraler Pseudonymisierungsdialog (25./26.10.2026"):]
    tier = block[block.index("@media (max-height: 900px) {"):]
    tier = tier[:tier.index("\n}\n")]
    assert "-webkit-line-clamp: 2" in tier and ".pseudo-question__option small { white-space: nowrap;" in tier
    # die 900-px-Stufe steht VOR der 760-px-Stufe, damit diese sie weiter verdichten kann
    assert block.index("@media (max-height: 900px) {") < block.index("@media (max-height: 760px) {")
    assert "@media (max-width: 1180px) and (max-height: 760px) {" in block


def _marks_css() -> str:
    start = CSS.index("/* Markierungen (Seitenbild-Overlay, Textansicht, Legende) - Annotationsdesign 28.10.2026")
    return CSS[start:CSS.index(".pseudo-mark-inline.is-active", start) + 200]


def test_marks_are_subtle_fills_with_fine_underlines_not_frames() -> None:
    """Annotationsdesign 28.10.2026: Toenung + 1,5-px-Unterstreichung in der Kategoriefarbe, kleine Rundung, kein
    Rahmen; Art der Fundstelle zusaetzlich ueber den Linienstil (nicht nur Farbe)."""
    css = _marks_css()
    assert "border-radius: 3px; border: 0;" in css and "background-size: 100% 1.5px" in css  # durchgezogen
    for group in ("person", "organisation", "adresse", "datum", "sonstiges"):
        rule = re.search(r"\.pseudo-mark--cat-" + group + r", \.pseudo-center__swatch--" + group + r" \{([^}]*)\}", css)
        assert rule and "--mk-line" in rule.group(1) and "--mk-fill:" in rule.group(1), group
        # zurueckhaltende Toenung (Alpha <= 0,12) - Text bleibt lesbar
        alpha = float(re.search(r"--mk-fill: rgba\([^)]*, (0\.\d+)\)", rule.group(1)).group(1))
        assert alpha <= 0.12, (group, alpha)
    open_rule = re.search(r"\.pseudo-mark--open, \.pseudo-center__swatch--open \{([^}]*)\}", css).group(1)
    kept_rule = re.search(r"\.pseudo-mark--kept, \.pseudo-center__swatch--kept \{([^}]*)\}", css).group(1)
    assert "transparent 0" in open_rule and "background-size: 6px 1.5px" in open_rule  # gestrichelt = pruefpflichtig
    assert "--mk-fill: transparent" in kept_rule and "background-size: 3px 1.5px" in kept_rule  # gepunktet, ohne Toenung
    # keine groben Rahmen, keine Animationen mehr an den Markierungen
    assert "dashed" not in css and "dotted" not in css and "@keyframes" not in css and "animation" not in css
    assert not re.search(r"\.pseudo-mark[^{]*\{[^}]*border: (1|2)", CSS[CSS.index("/* --- Zentraler Pseudonymisierungsdialog"):])


def test_active_question_and_current_occurrence_are_graded() -> None:
    css = _marks_css()
    active = re.search(r"\.pseudo-mark\.is-active \{([^}]*)\}", css).group(1)
    current = re.search(r"\.pseudo-mark\.is-current \{([^}]*)\}", css).group(1)
    assert "0 0 0 1px" in active  # alle Fundstellen der angezeigten Rueckfrage: feine Kontur
    assert "0 0 0 1.5px" in current and "0 0 0 5px" in current  # aktuelle Fundstelle: Kontur + weicher Halo
    assert ".pseudo-mark:focus-visible" in css and ".pseudo-mark:hover" in css


def test_marks_hug_the_text_and_follow_every_zoom_level() -> None:
    build = _function("buildMark")
    assert 'm.style.left = "calc(" + (mark.x * 100) + "% - 1.5px)"' in build
    assert "(mark.y + mark.h * 0.06) * 100" in build and "mark.h * 0.88 * 100" in build
    assert "style.top = " in build and "px\"" not in build.split("style.top")[1].split(";")[0]  # Prozent, nicht px


def test_multi_part_findings_merge_and_contained_duplicates_are_dropped() -> None:
    drawable = _function("drawableMarks")
    # gleiche Kategorie, gleiche Zeile, kleiner Abstand -> EINE Markierung (z. B. "Thomas" | "Berger")
    assert "categoryGroup(prev.category) === categoryGroup(m.category)" in drawable and "sameLine" in drawable
    assert "prev.parts = prev.parts.concat(m.parts)" in drawable
    # vollstaendig enthaltene maskierte Funde (z. B. "Bauer" in "Max Bauer") werden nicht doppelt getoent
    assert "inside(a, b)" in drawable
    # offene Rueckfragen werden nie verbunden/entfernt und liegen oben (jede Fundstelle einzeln anwaehlbar)
    assert 'merged.concat(page.marks.filter(function (m) { return m.kind === "open"; }))' in drawable
    assert "drawableMarks(page).forEach" in _function("renderDocument")


def test_document_and_question_stay_synchronised_both_ways() -> None:
    """Native/Browser-QA 28.10.2026: Klick auf die ZWEITE Fundstelle zeigte "Fundstelle 1 von 2"."""
    build = _function("buildMark")
    assert 'm.setAttribute("data-occ", String(occ))' in build and "selectOccurrence(mark.term, occ)" in build
    assert 'evt.key === "Enter"' in build and 'm.setAttribute("role", "button")' in build and "m.tabIndex = 0" in build
    select = _function("selectOccurrence")
    assert "state.occurrence = occ" in select and "renderQuestions()" in select and "markCurrent()" in select
    current = _function("markCurrent")
    assert '[data-occ=\\"" + state.occurrence + "\\"]' in current and 'classList.add("is-current")' in current
    # Panel -> Dokument: jede Aktivierung/Neuzeichnung setzt die aktuelle Fundstelle neu
    for name in ("activateQuestion", "refreshMarks", "renderDocument"):
        assert "markCurrent()" in _function(name), name
    # Zaehlung im Dokument = Zaehlung im Panel (dokumentuebergreifend)
    assert "occOffset(mark.term)" in _function("renderDocument") and "occurrences(q)" in _function("occOffset")


def test_three_options_explain_the_real_server_behaviour() -> None:
    options = SCRIPT[SCRIPT.index("var OPTIONS = ["):SCRIPT.index("];", SCRIPT.index("var OPTIONS = ["))]
    assert re.findall(r'decision: "(\w+)"', options) == ["personal", "not_personal", "unsure"]
    for title in ("Pseudonymisieren", "Nicht pseudonymisieren", "Unsicher"):
        assert f'title: "{title}"' in options
    # "Unsicher" wird serverseitig wie "personal" pseudonymisiert - die Erklaerung darf nichts anderes versprechen
    assert 'hint: "Wird vorsichtshalber pseudonymisiert."' in options
    assert "Zur weiteren Prüfung" not in SCRIPT


def test_choosing_an_option_only_selects_and_apply_steps_to_the_next_open_question() -> None:
    cards = _function("renderQuestions")
    click = cards[cards.index('b.addEventListener("click"'):]
    click = click[:click.index("});") + 3]
    assert "state.decisions[q.term] = option.decision" in click
    assert "goToQuestion" not in click and "stepQuestion" not in click and "fetch(" not in click
    apply = SCRIPT[SCRIPT.index('applyBtn.addEventListener("click"'):]
    apply = apply[:apply.index("});") + 3]
    assert "if (applyBtn.disabled) { return; }" in apply and "stepQuestion(1)" in apply
    assert "fetch(" not in apply and "submit" not in apply and "state.decisions" not in apply
    footer = _function("renderFooter")
    # waehrend Rueckfragen offen sind: "Uebernehmen und naechste Rueckfrage" (erst nach Auswahl aktiv) statt Bestaetigen
    assert "applyBtn.hidden = !stepping" in footer and "confirmBtn.hidden = true" in footer
    assert "applyBtn.disabled = state.busy || !chosen" in footer


def test_marks_and_legend_use_the_reference_categories() -> None:
    assert "function categoryGroup(" in SCRIPT and "function questionGroup(" in SCRIPT
    for group in ("person", "organisation", "adresse", "datum", "sonstiges"):
        assert f".pseudo-mark--cat-{group}" in CSS and f".pseudo-center__swatch--{group}" in CSS
    # Rueckfrage ohne erkannte Kategorie: "Unsicherer Begriff" statt eines erfundenen Typs
    assert 'unsicher: "Unsicherer Begriff"' in SCRIPT and 'return raw ? categoryGroup(raw) : "unsicher";' in SCRIPT
    # Entscheidungen aendern nur Klassen (Seitenbilder werden nicht neu geladen)
    assert "renderDocument" not in _function("refreshMarks")


def test_preview_size_follows_the_available_width_and_screen_scaling() -> None:
    render = _function("renderDocument")
    assert "docPane.clientWidth" in render and "devicePixelRatio" in render and "maxWidth" in render
    assert '".png?dpi=" + dpi' in render
    # Sprung zur Fundstelle beginnt am Seitenanfang statt die Markierung zu zentrieren (fast leere Vorseite)
    assert "pageTop" in _function("scrollToMark") and 'block: "center"' not in _function("scrollToMark")


def test_initial_focus_is_the_dialog_title_not_the_first_view_tab(client, db_session, writer) -> None:
    """Native Desktopprüfung 26.10.2026: showModal() fokussierte "Markierte Vorschau" (schwarzer Rahmen)."""
    html = _dialog_markup(client, db_session)
    title = re.search(r"<h2[^>]*id=\"pseudo-center-title\"[^>]*>", html).group(0)
    assert "autofocus" in title and 'tabindex="-1"' in title
    assert not re.search(r"<button[^>]*autofocus", html)
    assert ".pseudo-center__title:focus { outline: none; }" in CSS
    assert "button:focus-visible" in CSS[CSS.index("/* --- Zentraler Pseudonymisierungsdialog (25./26.10.2026"):]


def test_privacy_corner_tooltip_does_not_overflow_the_main_area() -> None:
    """Native Desktopprüfung 26.10.2026: der zentrierte (unsichtbare) Tooltip ragte 57 px ueber `.main` hinaus und
    erzeugte auf jeder Chatseite eine horizontale Scrollleiste - in der Ecke ist er rechtsbuendig."""
    rule = re.search(r"\.chat-privacy-corner \.chat-composer__privacy-btn::after \{([^}]*)\}", CSS)
    assert rule, "Eck-Variante des Tooltips fehlt"
    body = rule.group(1)
    assert "right: 0" in body and "left: auto" in body and "translateX(-50%)" not in body
    hover = re.search(r"\.chat-privacy-corner \.chat-composer__privacy-btn:hover::after \{([^}]*)\}", CSS).group(1)
    assert "translateX" not in hover


def test_document_content_is_rendered_as_text_only() -> None:
    assert "innerHTML" not in SCRIPT and "insertAdjacentHTML" not in SCRIPT


def test_css_keeps_hidden_rule_and_responsive_fallbacks() -> None:
    block = CSS[CSS.index("/* --- Zentraler Pseudonymisierungsdialog (25./26.10.2026"):]
    assert ".pseudo-center [hidden] { display: none !important; }" in block
    assert "grid-template-rows: auto auto minmax(0, 1fr) auto" in block  # Fussleiste nie herausgeschoben
    assert "@media (max-width: 900px)" in block and "@media (max-height: 760px)" in block
    assert ".modal.pseudo-center.is-maximized .pseudo-center__panel { display: none; }" in block


def test_css_follows_the_app_tokens_found_in_the_reference_comparison() -> None:
    """Abgleich mit Screenshots der laufenden App (26.10.2026): Buttons im Pop-up erbten die Schrift nicht (Arial statt
    Inter), Radien wichen von --radius-sm/--radius-md ab."""
    block = CSS[CSS.index("/* --- Zentraler Pseudonymisierungsdialog (25./26.10.2026"):]
    assert ".pseudo-center button, .pseudo-center select, .pseudo-center input { font-family: var(--font-body); }" in block
    assert not re.search(r"border-radius:\s*(7|8|10|14)px", block)
    assert "border-radius: var(--radius-md)" in block and "border-radius: var(--radius-sm)" in block
