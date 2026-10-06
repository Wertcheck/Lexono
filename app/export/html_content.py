"""parse_html_content – wandelt sanitisiertes Editor-HTML
(`Draft.content_format == "html"`, siehe app/drafting/html_sanitizer.py)
in eine einfache, formatneutrale Zwischendarstellung (`ContentBlock`/
`InlineRun`), die SOWOHL der PDF- als auch der DOCX-Export verwenden
(05.10., Owner-Direktive "Vollständiger UX- und Workflow-Audit").

ECHTER FUND (live reproduziert, Phase B des Audits): der PDF-/DOCX-Export
eines im neuen Rich-Text-Editor gespeicherten Entwurfs gab bisher den
rohen HTML-Quelltext aus (`<p>Text</p>` wörtlich als sichtbarer Text im
PDF) - `draft.content.split("\n\n")` ging unverändert von reinem Klartext
aus (siehe app/export/pdf_export_service.py/docx_export_service.py vor
diesem Fix). Betraf AUSSCHLIESSLICH Entwürfe mit `content_format ==
"html"` (seit dem Dokumenten-Editor, 04.10.) - jeder ältere/weiterhin
klartextbasierte Entwurf (`content_format == "text"`) exportierte bereits
vorher korrekt und bleibt über diesen neuen Code-Pfad UNBERÜHRT (siehe
beide Export-Services: der neue Zweig wird nur für "html" betreten).

Bewusst NUR die geschlossene, tatsächlich vom Editor erzeugbare
Tag-Menge unterstützt (identisch zur Sanitisierungs-Allowlist) - kein
allgemeiner HTML-Renderer, keine neue Fremdbibliothek (`html.parser` ist
Python-Standardbibliothek)."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class InlineRun:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    href: str | None = None


@dataclass
class ContentBlock:
    #: "p" (Absatz/Fließtext) | "li_bullet" (Aufzählung) | "li_number"
    #: (nummerierte Liste).
    kind: str = "p"
    runs: list[InlineRun] = field(default_factory=list)
    number: int | None = None


class _EditorHtmlParser(HTMLParser):
    """Einfache, zustandsbehaftete Umwandlung - KEIN generischer HTML/DOM-
    Baum, nur die lineare Blockfolge, die die Export-Renderer brauchen.
    `<br>` wird als hartes Zeilenende INNERHALB des aktuellen Blocks
    behandelt (literales "\\n" im Lauftext, siehe Renderer)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[ContentBlock] = []
        self._current: ContentBlock | None = None
        self._bold = 0
        self._italic = 0
        self._underline = 0
        self._href: str | None = None
        self._list_kind_stack: list[str] = []  # "ul" | "ol"
        self._list_counter_stack: list[int] = []

    def _ensure_block(self, kind: str = "p") -> ContentBlock:
        if self._current is None:
            self._current = ContentBlock(kind=kind)
            self.blocks.append(self._current)
        return self._current

    def _close_block(self) -> None:
        self._current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("p", "div"):
            self._close_block()
            self._ensure_block("p")
        elif tag == "h2":
            # "h2" (05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS"
            # Phase D, siehe html_sanitizer.py-Kommentar) - der PDF-/DOCX-
            # Renderer kennt bisher keine eigene Schriftgroesse pro Block
            # (eine echte Ueberschriftengroesse wuerde die sorgfaeltig
            # austarierte, breitenbasierte Zeilenumbruch-Logik in
            # pdf_export_service.py antasten). Stattdessen wird die
            # Ueberschrift als FETTER Absatz exportiert - nutzt den
            # bereits vollstaendig unterstuetzten Bold-Lauftext-Mechanismus,
            # bleibt optisch erkennbar abgesetzt, ohne die Layout-Engine zu
            # veraendern.
            self._close_block()
            self._ensure_block("p")
            self._bold += 1
        elif tag == "ul":
            self._list_kind_stack.append("ul")
        elif tag == "ol":
            self._list_kind_stack.append("ol")
            self._list_counter_stack.append(0)
        elif tag == "li":
            self._close_block()
            list_kind = self._list_kind_stack[-1] if self._list_kind_stack else "ul"
            if list_kind == "ol":
                self._list_counter_stack[-1] += 1
                block = ContentBlock(kind="li_number", number=self._list_counter_stack[-1])
            else:
                block = ContentBlock(kind="li_bullet")
            self.blocks.append(block)
            self._current = block
        elif tag in ("b", "strong"):
            self._bold += 1
        elif tag in ("i", "em"):
            self._italic += 1
        elif tag == "u":
            self._underline += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            self._href = href
        elif tag == "br":
            block = self._ensure_block()
            block.runs.append(InlineRun(text="\n"))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "div", "li"):
            self._close_block()
        elif tag == "h2":
            self._close_block()
            self._bold = max(0, self._bold - 1)
        elif tag == "ul":
            if self._list_kind_stack and self._list_kind_stack[-1] == "ul":
                self._list_kind_stack.pop()
        elif tag == "ol":
            if self._list_kind_stack and self._list_kind_stack[-1] == "ol":
                self._list_kind_stack.pop()
                self._list_counter_stack.pop()
        elif tag in ("b", "strong"):
            self._bold = max(0, self._bold - 1)
        elif tag in ("i", "em"):
            self._italic = max(0, self._italic - 1)
        elif tag == "u":
            self._underline = max(0, self._underline - 1)
        elif tag == "a":
            self._href = None

    def handle_data(self, data: str) -> None:
        if not data:
            return
        block = self._ensure_block()
        block.runs.append(
            InlineRun(
                text=data,
                bold=self._bold > 0,
                italic=self._italic > 0,
                underline=self._underline > 0,
                href=self._href,
            )
        )


def parse_html_content(html: str) -> list[ContentBlock]:
    """Liefert die Blockfolge für `html` - leere/whitespace-only Blöcke
    werden verworfen (entspricht dem bestehenden `if not block: continue`
    der bisherigen Klartext-Export-Schleife, siehe Moduldocstring)."""
    parser = _EditorHtmlParser()
    parser.feed(html)
    parser.close()
    result = []
    for block in parser.blocks:
        if any(run.text.strip() for run in block.runs):
            result.append(block)
    return result
