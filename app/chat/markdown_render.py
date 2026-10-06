"""render_chat_markdown – wandelt Markdown-formatierten Chat-/KI-Text in
sicheres HTML um (05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS",
Phase D, live im Dokument-Analyse-Workflow gefunden).

Root Cause des gefundenen UX-Bugs: `chat.html` gab `message.content` bisher
als reinen, auto-escapten Text aus (`{{ message.content }}`). Claude
produziert aber durchgaengig Markdown-Formatierung (Ueberschriften, Fett,
Listen) - sichtbar fuer den Nutzer als rohe Sternchen/Rauten/Bindestriche
statt echter Formatierung, was gerade bei strukturierten juristischen
Kurzanalysen die Lesbarkeit erheblich verschlechtert.

Sicherheitsmodell: `message.content` ist sowohl bei User- als auch bei
Assistant-Nachrichten "untrusted input" (CLAUDE.md-Grundregel) - ein Nutzer
koennte versuchen, HTML/JS einzuschleusen, und auch ein KI-generierter Text
darf nicht blind als HTML vertraut werden. Zweistufige Absicherung:
1. `markdown_it.MarkdownIt(html=False)` – die rohe Eingabe wird NIE als
   HTML interpretiert, jedes `<`/`>` im Quelltext wird escaped, bevor es
   in einen Textknoten wandert.
2. `nh3.clean(...)` (bereits bestehende, in `app/drafting/html_sanitizer.py`
   etablierte Sanitisierungs-Bibliothek) reduziert das von markdown-it
   selbst erzeugte HTML zusaetzlich auf eine feste Tag-/Attribut-Allowlist -
   Verteidigung in der Tiefe, falls markdown-it jemals abweichendes
   Verhalten zeigen sollte."""

from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

_ALLOWED_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u",
    "ul", "ol", "li",
    "h1", "h2", "h3", "h4",
    "a", "code", "pre", "blockquote", "hr",
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
}

#: `breaks:True` wandelt einzelne Zeilenumbrueche (ohne doppeltes
#: Leerzeichen/Leerzeile) in `<br>` statt sie - wie reines CommonMark es
#: vorschreibt - zu einem Leerzeichen zusammenzufassen. Ohne das wuerden
#: vom Nutzer frei getippte, mehrzeilige Nachrichten (z. B. eine Adresse
#: auf mehreren Zeilen) ihre sichtbaren Umbrueche verlieren - eine echte
#: Regression gegenueber dem vorherigen `white-space:pre-wrap`-Verhalten.
_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False, "breaks": True})


def render_chat_markdown(text: str) -> str:
    """Rendert `text` (Markdown, untrusted) zu sicherem, auf eine feste
    Allowlist reduziertem HTML. Leerer Text ergibt leeren String."""
    if not text:
        return ""
    html = _md.render(text)
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
    )
