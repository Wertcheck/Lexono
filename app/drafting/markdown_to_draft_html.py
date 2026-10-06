"""render_ai_markdown_to_draft_html – wandelt den von Claude gelieferten,
durchgaengig Markdown-formatierten Entwurfstext (Ueberschriften, Fett,
Listen) in das HTML um, das der Rich-Text-Dokumenten-Editor tatsaechlich
darstellen/erhalten kann (05.10., Owner-Direktive "LONG-RUN PRODUCT
QUALITY PASS", Phase D, live im Dokument-Analyse->Schriftsatz-Workflow
gefundener Bug).

ECHTER FUND (live reproduziert): ein frisch per Chat/Schriftsatz-Generator
erzeugter Entwurf wurde bisher mit `content_format="text"` (Default, siehe
app/drafting/versioning.py) gespeichert - Claudes rohe Markdown-Antwort
landete dadurch UNVERAENDERT im `draft.content`-Klartextfeld. Der Editor
(draft_editor.html) zeigt `content_format=="text"`-Entwuerfe bewusst als
reinen, escapten Text - der Nutzer sah dadurch woertliche Sternchen/Rauten/
Bindestriche statt echter Formatierung, und zwar nicht nur im Chat
(separat behoben, siehe app/chat/markdown_render.py), sondern im
eigentlichen zu bearbeitenden Entwurf selbst.

Bewusst NICHT dieselbe Tag-Menge wie app/chat/markdown_render.py (dort
Chat-Blase, hier das tatsaechliche Entwurfsdokument): Ueberschriften-Ebenen
werden auf die EINE vom Editor-Toolbar unterstuetzte Stufe ("h2", siehe
draft_editor.html #draft-editor-paragraph-style) vereinheitlicht, Code/Pre/
Blockquote (vom Editor nicht unterstuetzt) werden zu normalen Absaetzen
abgeflacht - damit ein Entwurf, der einmal gespeichert wurde, beim naechsten
Autosave/einer KI-Bearbeitung (beide laufen durch sanitize_editor_html mit
EXAKT dieser Allowlist) nicht still Inhalte verliert."""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.drafting.html_sanitizer import sanitize_editor_html

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False, "breaks": True})

#: Markdown-Ueberschriftenebenen (h1-h6) werden alle auf "h2" abgebildet -
#: die einzige Stufe, die draft_editor.html tatsaechlich anbietet/erhaelt.
_HEADING_TAG = "h2"


def render_ai_markdown_to_draft_html(text: str) -> str:
    """Liefert vom Editor darstellbares, sanitisiertes HTML fuer `text`
    (Markdown, untrusted - siehe Sicherheitsherleitung in
    app/chat/markdown_render.py, hier identisches Vorgehen: `html:False`
    + anschliessende nh3-Sanitisierung ueber dieselbe, bereits etablierte
    Editor-Allowlist)."""
    if not text or not text.strip():
        return ""
    tokens = _md.parse(text)
    html = _render_tokens_for_editor(tokens)
    return sanitize_editor_html(html)


def _render_tokens_for_editor(tokens: list[Token]) -> str:
    parts: list[str] = []
    list_stack: list[str] = []  # "ul" | "ol"
    for tok in tokens:
        if tok.type == "heading_open":
            parts.append(f"<{_HEADING_TAG}>")
        elif tok.type == "heading_close":
            parts.append(f"</{_HEADING_TAG}>")
        elif tok.type == "paragraph_open":
            parts.append("<p>")
        elif tok.type == "paragraph_close":
            parts.append("</p>")
        elif tok.type == "bullet_list_open":
            list_stack.append("ul")
            parts.append("<ul>")
        elif tok.type == "bullet_list_close":
            if list_stack:
                list_stack.pop()
            parts.append("</ul>")
        elif tok.type == "ordered_list_open":
            list_stack.append("ol")
            parts.append("<ol>")
        elif tok.type == "ordered_list_close":
            if list_stack:
                list_stack.pop()
            parts.append("</ol>")
        elif tok.type == "list_item_open":
            parts.append("<li>")
        elif tok.type == "list_item_close":
            parts.append("</li>")
        elif tok.type in ("blockquote_open", "blockquote_close"):
            # Vom Editor nicht unterstuetzt - zu einem normalen Absatz
            # abgeflacht statt verworfen (Inhalt bleibt erhalten).
            parts.append("<p>" if tok.type == "blockquote_open" else "</p>")
        elif tok.type == "fence" or tok.type == "code_block":
            # Codebloecke: Inhalt als eigener Absatz erhalten (kein <pre>
            # in der Editor-Allowlist) statt stillschweigend zu verlieren.
            parts.append(f"<p>{_escape(tok.content.rstrip(chr(10)))}</p>")
        elif tok.type == "hr":
            parts.append("<p>---</p>")
        elif tok.type == "inline":
            parts.append(_render_inline(tok.children or []))
    return "".join(parts)


def _render_inline(children: list[Token]) -> str:
    out: list[str] = []
    for child in children:
        if child.type == "text":
            out.append(_escape(child.content))
        elif child.type == "softbreak":
            out.append("<br>")
        elif child.type == "hardbreak":
            out.append("<br>")
        elif child.type == "code_inline":
            out.append(_escape(child.content))
        elif child.type == "strong_open":
            out.append("<strong>")
        elif child.type == "strong_close":
            out.append("</strong>")
        elif child.type == "em_open":
            out.append("<em>")
        elif child.type == "em_close":
            out.append("</em>")
        elif child.type == "link_open":
            href = ""
            for name, value in child.attrs.items() if child.attrs else []:
                if name == "href":
                    href = value or ""
            out.append(f'<a href="{_escape(href)}">')
        elif child.type == "link_close":
            out.append("</a>")
        else:
            # Unbekannter Inline-Tokentyp (z. B. image) - Markup selbst
            # ignorieren, aber enthaltenen Text nicht verlieren.
            if child.content:
                out.append(_escape(child.content))
    return "".join(out)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
