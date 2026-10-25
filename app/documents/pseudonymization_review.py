"""Zentrale Pseudonymisierungs-Pruefansicht eines Dokuments (25.10.2026).

Datengrundlage fuer den EINEN Dialog, den Dokumentanalyse und Schriftsatzerstellung im Chat vor dem Fortsetzen
oeffnen. KEINE eigene Datenschutzlogik: Treffer, Rueckfragen und das pseudonymisierte Ergebnis kommen ausschliesslich
aus der zentralen Pruefung `ClaudePrivacyGateway.prepare_request` - einmal im Modus "ask" (offene Rueckfragen, wie das
Dokument-Gate `release._run_central_check`) und einmal im Modus "mask" (welche Werte ersetzt wuerden, wie die
Pruefseite `document_release_router.review_pseudonymization`). Lokal, kein Cloud-Aufruf.

Positionen im Seitenbild werden bei JEDEM Aufruf frisch aus der gespeicherten Originaldatei ermittelt (PyMuPDF-
Textsuche auf der Seite) - nie aus gespeicherten oder veralteten Zeichenpositionen. Gebunden an den Textstand
(`text_version`); die Bestaetigung prueft ihn serverseitig erneut. Werte, die im Seitenbild nicht eindeutig
auffindbar sind (gescannte Seiten, abweichende Silbentrennung, DOCX-Layout), werden ausdruecklich als "nicht im
Seitenbild markiert" gemeldet statt stillschweigend zu fehlen."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.documents.release import text_version_of
from app.documents.rendering import determine_viewer_mode
from app.models import Document, Matter

#: Hoechstzahl der Seiten, fuer die Markierungen ermittelt werden (Laufzeitgrenze; darueber Hinweis im Dialog).
MAX_MARKED_PAGES = 60

_WORD_EDGE = re.compile(r"^[\W_]+|[\W_]+$")

FORMAT_NOTES = {
    ".pdf": "PDF: Originalseiten als Bild, Markierungen an der Fundstelle im Seitenbild.",
    ".docx": ("Word (DOCX): Seiten werden lokal mit der eingebauten Layout-Engine (PyMuPDF) gesetzt. Schriftarten, "
              "Abstände, Seitenumbrüche sowie Kopf- und Fußzeilen können von der Darstellung in Microsoft Word "
              "abweichen."),
    ".txt": "Textdatei: ohne Seitenlayout; Markierungen nur in der Textansicht.",
}


@dataclass
class ReviewData:
    document_id: str
    filename: str
    text_version: str
    status: str  # "review" (offene Rueckfragen) | "ok" | "blocked" | "no_text"
    blocked_reasons: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    mappings: list[dict] = field(default_factory=list)
    result_text: str = ""
    original_text: str = ""  # nur fuer die lokale Textansicht (Format ohne Seitenbild)
    viewer_kind: str = "unsupported"
    pages: list[dict] = field(default_factory=list)
    not_located: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _gateway_results(db: Session, document: Document, matter: Matter, conversation_id: str | None):
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.privacy.decisions import load_decisions
    from app.privacy.gateway import ClaudePrivacyGateway

    known = RuleBasedLocalAIProvider()._build_known_entities(matter.id, matter, db)  # wie beim Versand
    # Entscheidungen wie beim Fortsetzen im Chat (aktenweit + diese Unterhaltung)
    decisions = load_decisions(db, matter_id=matter.id, conversation_id=conversation_id)
    gateway = ClaudePrivacyGateway()
    common = dict(purpose="formulate_draft", sachverhalt=document.extracted_text or "", known_entities=known,
                  privacy_decisions=decisions)
    return gateway.prepare_request(**common, review_mode="ask"), gateway.prepare_request(**common, review_mode="mask")


def open_candidate_terms(db: Session, document: Document, matter: Matter, conversation_id: str | None) -> list[str]:
    """Die aktuell offenen Rueckfragen dieses Dokumentstands (zentrale Pruefung, Modus "ask")."""
    if not (document.extracted_text or "").strip():
        return []
    ask, _ = _gateway_results(db, document, matter, conversation_id)
    return [c.term for c in ask.review_candidates] if ask.review_required else []


def _normalized(rect, page_rect) -> dict:
    return {"x": round((rect.x0 - page_rect.x0) / page_rect.width, 5),
            "y": round((rect.y0 - page_rect.y0) / page_rect.height, 5),
            "w": round(rect.width / page_rect.width, 5),
            "h": round(rect.height / page_rect.height, 5)}


def _locate(page, value: str, words) -> list:
    """Fundstellen EINES Werts auf einer Seite. Einwortwerte: nur ganze Woerter (kein "Richter" in "Richterin");
    Mehrwortwerte: Phrasensuche der Seite."""
    value = value.strip()
    if not value:
        return []
    if " " not in value:
        return [w[:4] for w in words if _WORD_EDGE.sub("", w[4]) == value]
    return list(page.search_for(value))


def _page_marks(path: Path, values: dict[str, dict]) -> tuple[list[dict], set[str], bool]:
    """Seitenliste mit normierten Markierungsrechtecken. `values`: Wert -> Markierungsattribute."""
    import pymupdf

    pages, found = [], set()
    truncated = False
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= MAX_MARKED_PAGES:
                truncated = True
                pages.append({"number": index + 1, "width": page.rect.width, "height": page.rect.height,
                              "marks": [], "has_text": None})
                continue
            words = page.get_text("words")
            marks = []
            for value, attrs in values.items():
                for rect in _locate(page, value, words):
                    found.add(value)
                    marks.append({**attrs, **_normalized(pymupdf.Rect(rect), page.rect)})
            pages.append({"number": index + 1, "width": page.rect.width, "height": page.rect.height,
                          "marks": marks, "has_text": bool(words)})
    return pages, found, truncated


def _docx_header_footer_gaps(path: Path, pages: list[dict]) -> list[str]:
    """Kopf-/Fusszeilentexte eines DOCX, die in der Seitenansicht NICHT erscheinen (gemessen 25.10.2026: die
    PyMuPDF-Layout-Engine setzt Kopf-/Fusszeilen von Word-Dokumenten nicht). Rueckgabe: fehlende Zeilen."""
    import docx
    import pymupdf

    try:
        document = docx.Document(str(path))
    except Exception:  # noqa: BLE001
        return []
    lines = []
    for section in document.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer):
            for paragraph in getattr(part, "paragraphs", []):
                if paragraph.text.strip() and paragraph.text.strip() not in lines:
                    lines.append(paragraph.text.strip())
    if not lines:
        return []
    with pymupdf.open(path) as doc:
        rendered = " ".join(page.get_text() for page in doc)
    normalized = " ".join(rendered.split())
    return [line for line in lines if " ".join(line.split()) not in normalized]


def build_review(db: Session, document: Document, matter: Matter, conversation_id: str | None) -> ReviewData:
    text = document.extracted_text or ""
    data = ReviewData(document_id=document.id, filename=document.original_filename or "Dokument",
                      text_version=text_version_of(text), status="no_text")
    path = Path(document.file_path or "")
    suffix = path.suffix.lower()
    if suffix in FORMAT_NOTES:
        data.limitations.append(FORMAT_NOTES[suffix])
    if not text.strip():
        data.limitations.append("Das Dokument enthält keinen verwertbaren Text – es kann nicht pseudonymisiert werden.")
        return data

    ask, mask = _gateway_results(db, document, matter, conversation_id)
    if ask.review_required:
        data.status = "review"
        data.candidates = [{"term": c.term, "context_before": c.context_before, "context_after": c.context_after}
                           for c in ask.review_candidates]
    elif ask.allowed:
        data.status = "ok"
    else:
        data.status = "blocked"
        data.blocked_reasons = list(ask.reasons)
    seen = set()
    for m in sorted(mask.mappings, key=lambda m: (m.category, m.placeholder)):
        if (m.placeholder, m.original_value) in seen:
            continue
        seen.add((m.placeholder, m.original_value))
        data.mappings.append({"placeholder": m.placeholder, "category": m.category, "original": m.original_value,
                              "variants": [v for v in m.variants if v != m.original_value]})
    data.result_text = mask.payload.anonymisierter_sachverhalt if mask.allowed and mask.payload else ""
    if not mask.allowed:
        data.blocked_reasons = data.blocked_reasons or list(mask.reasons)

    # Was wird wo markiert: offene Rueckfragen haben Vorrang vor der (vorsichtigen) Maskierung desselben Begriffs.
    values: dict[str, dict] = {}
    for item in data.mappings:
        for value in [item["original"], *item["variants"]]:
            values.setdefault(value, {"kind": "masked", "placeholder": item["placeholder"],
                                      "category": item["category"], "term": item["original"]})
    for candidate in data.candidates:
        values[candidate["term"]] = {"kind": "open", "placeholder": "", "category": "rueckfrage",
                                     "term": candidate["term"]}

    try:
        mode = determine_viewer_mode(path)
    except Exception:  # noqa: BLE001 - Anzeige faellt auf Text zurueck, Pruefung bleibt gueltig
        mode = None
    data.viewer_kind = mode.kind if mode else "unsupported"
    if data.viewer_kind != "pages":
        data.original_text = text
    if data.viewer_kind == "pages":
        try:
            data.pages, found, truncated = _page_marks(path, values)
        except Exception:  # noqa: BLE001
            data.viewer_kind = "text"
            data.original_text = text
            data.limitations.append("Die Seitenansicht konnte nicht erzeugt werden – es wird der extrahierte Text "
                                    "angezeigt.")
            found, truncated = set(), False
        if data.pages:
            located_terms = {values[k]["term"] for k in found}
            data.not_located = sorted({v["term"] for v in values.values()} - located_terms)
            if any(p["has_text"] is False for p in data.pages):
                data.limitations.append("Mindestens eine Seite enthält keine Textebene (Scan). Treffer aus der "
                                        "Texterkennung können dort nicht positionsgenau markiert werden.")
            if suffix == ".docx":
                missing = _docx_header_footer_gaps(path, data.pages)
                if missing:
                    data.limitations.append(
                        f"{len(missing)} Kopf-/Fußzeile(n) dieses Word-Dokuments erscheinen in der Seitenansicht "
                        "nicht. Ihr Inhalt wurde trotzdem geprüft und ist im pseudonymisierten Ergebnis enthalten. "
                        "Tabellenrahmen werden ebenfalls nicht dargestellt.")
            if truncated:
                data.limitations.append(f"Markierungen werden für die ersten {MAX_MARKED_PAGES} Seiten angezeigt.")
            if data.not_located:
                data.limitations.append("Einige Werte wurden im Seitenbild nicht gefunden (z. B. Silbentrennung, "
                                        "Zeilenumbruch, Scan) und sind dort NICHT markiert – sie sind unten "
                                        "aufgeführt und werden trotzdem pseudonymisiert.")
    elif data.viewer_kind == "image":
        data.limitations.append("Bilddatei: Treffer stammen aus der Texterkennung und werden in der Textansicht "
                                "markiert, nicht im Bild.")
    elif data.viewer_kind != "text":
        data.limitations.append("Für dieses Dateiformat gibt es keine originalgetreue Seitenansicht – es wird der "
                                "extrahierte Text angezeigt.")
    return data
