"""Tests fuer app/chat/markdown_render.py (05.10., Owner-Direktive
"LONG-RUN PRODUCT QUALITY PASS" Phase D) - live im Dokument-Analyse-
Workflow gefundener UX-Bug: Chat-Antworten zeigten rohe Markdown-Syntax
(**fett**, ## Ueberschrift, - Liste) statt echter Formatierung an."""

from __future__ import annotations

from app.chat.markdown_render import render_chat_markdown


def test_empty_text_renders_empty_string() -> None:
    assert render_chat_markdown("") == ""


def test_bold_becomes_strong_tag() -> None:
    html = render_chat_markdown("Das ist **wichtig**.")
    assert "<strong>wichtig</strong>" in html
    assert "**" not in html


def test_heading_becomes_heading_tag() -> None:
    html = render_chat_markdown("## Zusammenfassung")
    assert "<h2>Zusammenfassung</h2>" in html


def test_list_becomes_list_tags() -> None:
    html = render_chat_markdown("- Erster Punkt\n- Zweiter Punkt")
    assert "<ul>" in html
    assert "<li>Erster Punkt</li>" in html
    assert "<li>Zweiter Punkt</li>" in html


def test_single_newline_becomes_br_not_collapsed_space() -> None:
    # Ohne `breaks:True` wuerde CommonMark einen einzelnen Zeilenumbruch
    # zu einem Leerzeichen zusammenfassen - eine Regression gegenueber dem
    # vorherigen `white-space:pre-wrap`-Verhalten fuer frei getippte,
    # mehrzeilige Nutzernachrichten (z. B. eine Adresse).
    html = render_chat_markdown("Zeile eins\nZeile zwei")
    assert "<br>" in html


def test_script_tag_in_source_is_neutralized() -> None:
    # message.content ist untrusted input (sowohl User- als auch
    # Assistant-Nachrichten) - ein eingeschleustes <script> darf niemals
    # als ausfuehrbares HTML-Element im DOM landen. markdown-it (html:False)
    # escaped die rohe Eingabe bereits vollstaendig zu Text - das Ergebnis
    # ist ein inerter Textknoten, kein echtes <script>-Tag.
    html = render_chat_markdown("<script>alert('xss')</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_onerror_attribute_is_stripped() -> None:
    # Dasselbe Prinzip: die rohe Eingabe wird als Text escaped, nie als
    # echtes <img onerror=...>-Element geparst.
    html = render_chat_markdown('<img src="x" onerror="alert(1)">')
    assert "<img" not in html
    assert "&lt;img" in html


def test_javascript_link_scheme_is_dropped() -> None:
    # markdown-it validiert Link-Schemata selbst: ein gefaehrliches Schema
    # wird gar nicht erst zu einem <a href="...">-Link geparst, sondern
    # bleibt als reiner (inerter) Text stehen - kein klickbarer Link.
    html = render_chat_markdown("[Klick mich](javascript:alert(1))")
    assert "<a " not in html
    assert 'href="javascript:' not in html


def test_plain_paragraph_text_preserved() -> None:
    html = render_chat_markdown("Einfache Antwort ohne Formatierung.")
    assert "Einfache Antwort ohne Formatierung." in html


def test_legitimate_https_link_is_rendered_as_real_link() -> None:
    html = render_chat_markdown("[Mietspiegel](https://www.example-testdomain.invalid/mietspiegel)")
    assert '<a href="https://www.example-testdomain.invalid/mietspiegel"' in html
    assert 'rel="noopener noreferrer"' in html
