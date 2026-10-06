"""sanitize_editor_html – reduziert vom Rich-Text-Editor geliefertes HTML
auf eine feste, geprüfte Allowlist (04.10., Dokumenten-Editor).

`Draft.content` ist ab `content_format == "html"` echtes HTML, das
serverseitig gespeichert UND später über `| safe` direkt ins Dokument
eingebettet wird (siehe draft_detail.html/draft_editor.html) - OHNE
Sanitisierung wäre jedes `<script>`/`onerror=`/`javascript:`-Attribut im
gespeicherten Entwurf ein gespeichertes XSS gegen jeden, der die Entwurfs-
oder Editor-Seite später öffnet. Läuft bei JEDEM Speichern (Autosave UND
KI-Bearbeitung, siehe editor_service.py), NIE nur clientseitig (der Editor
selbst nutzt zwar `document.execCommand`, das allein ist kein Schutz gegen
einen manipulierten Request)."""

from __future__ import annotations

import nh3

#: Exakt die Tags, die die Toolbar (draft_editor.html/app_draft_editor.js)
#: tatsächlich erzeugen kann - KEINE generische "alles außer Skripten"-
#: Allowlist, damit auch ein exportierender HTML->PDF/DOCX-Konverter
#: (app/drafting/html_to_flowables.py) nur eine kleine, geschlossene
#: Tag-Menge behandeln muss.
#: "h2" ergänzt (05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS"
#: Phase D) - die Toolbar selbst bietet bereits "Überschrift" (<h2>, siehe
#: draft_editor.html #draft-editor-paragraph-style) an; diese Allowlist
#: strich das Tag aber bisher weg, sobald der Entwurf erneut gespeichert
#: wurde (Autosave/KI-Bearbeitung laufen beide durch sanitize_editor_html).
#: Siehe app/drafting/markdown_to_draft_html.py für den neuen KI-Text-
#: Erstellungsweg, der diese selbe Allowlist jetzt von Anfang an einhält.
_ALLOWED_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u",
    "ul", "ol", "li", "a", "div", "span", "h2",
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
}


def sanitize_editor_html(html: str) -> str:
    """Bereinigt `html` auf die oben definierte Allowlist. `href`-Werte mit
    gefährlichem Schema (z. B. `javascript:`) werden von `nh3` selbst
    bereits serverseitig verworfen (eingebaute URL-Schema-Prüfung,
    unabhängig von der hier übergebenen Attribut-Allowlist)."""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
    )
