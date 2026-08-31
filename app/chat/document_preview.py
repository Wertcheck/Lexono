"""Pseudonymisierungs-Vorschau für den Dokument-Workspace im Chat
(Masterprompt V2, Task #62).

WICHTIG (Datenschutz/Architektur): Dieses Modul führt KEINE eigene
Erkennung durch - es ruft exakt dieselben Detektoren auf, die auch
`app/privacy/pseudonymizer.py::Pseudonymizer` für den tatsächlichen
Cloud-Anfrage-Pfad verwendet (`detect_all` + `detect_presidio_entities`).
Es gibt hier also KEINE zweite, abweichende Erkennungslogik - was hier als
"erkannt" markiert wird, ist strukturell identisch zu dem, was vor jedem
Cloud-Aufruf tatsächlich pseudonymisiert würde (Masterprompt-Vorgabe:
"nur reale erkannte Kategorien verwenden, keine Fake-Datenlogik").

Dieses Modul selbst sendet nichts, speichert nichts dauerhaft und wird bei
jedem Seitenaufruf frisch berechnet (kein neues persistentes Feld auf
`Document`) - Text bleibt vollständig in-memory für die Dauer des
Requests, siehe Datenschutz-Grundsatz "keine unnötige dauerhafte
Speicherung abgeleiteter sensibler Daten"."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from app.privacy.detectors import detect_all
from app.privacy.presidio_ner import detect_presidio_entities

_CATEGORY_LABELS = {
    "mandant": "Mandant",
    "gegner": "Gegenpartei",
    "anwalt": "Anwalt/Anwältin",
    "gericht": "Gericht",
    "aktenzeichen": "Aktenzeichen",
    "adresse": "Anschrift",
    "datum": "Datum",
    "betrag": "Betrag",
    "vertrag": "Vertrag",
    "email": "E-Mail",
    "telefon": "Telefon",
    "iban": "IBAN",
    "steuer_id": "Steuernummer",
    "kundennummer": "Kundennummer",
    "person": "Person",
    "ort": "Ort",
    "organisation": "Organisation",
}


@dataclass(frozen=True)
class DocumentPreviewEntity:
    category: str
    label: str
    value: str
    count: int


@dataclass(frozen=True)
class DocumentPreview:
    #: Bereits HTML-escapter Text mit eingebetteten <mark>-Hervorhebungen -
    #: fertig zum direkten `| safe`-Rendern im Template (siehe chat.html).
    highlighted_html: str
    entities: list[DocumentPreviewEntity]


def build_document_preview(text: str | None) -> DocumentPreview:
    """Erkennt PII im übergebenen (bereits lokal extrahierten) Dokumenttext
    und liefert sowohl hervorgehobenes HTML als auch eine gruppierte
    Kategorienliste für die rechte Kontextleiste.

    Bewusst OHNE `known_entities` (Mandant/Gegner/Anwalt/Gericht-Rollen) -
    diese kämen aus der Aktenstammdaten-Zuordnung, die eine per Chat schnell
    angelegte Konversation nicht zwingend hat (siehe
    `app/drafting/quick_matter.py`). Presidio-Treffer ohne bekannte Rolle
    bekommen dieselbe rollenneutrale Kategorie ("person"/"ort"/
    "organisation"), die sie auch im echten Pseudonymisierungslauf hätten -
    keine erfundene Rollenzuordnung."""
    if not text:
        return DocumentPreview(highlighted_html="", entities=[])

    spans = detect_all(text, None, ner_detector=detect_presidio_entities)
    spans = sorted(spans, key=lambda span: span.start)

    # detect_all() loest Ueberlappungen bereits selbst auf - dieser Filter
    # ist defensive Doppelsicherung (guenstig, verhindert im Fehlerfall
    # kaputtes HTML durch verschachtelte <mark>-Tags), kein Ersatz dafuer.
    non_overlapping = []
    last_end = -1
    for span in spans:
        if span.start < last_end:
            continue
        non_overlapping.append(span)
        last_end = span.end

    html_parts: list[str] = []
    cursor = 0
    for span in non_overlapping:
        html_parts.append(escape(text[cursor : span.start]))
        label = _CATEGORY_LABELS.get(span.category, span.category)
        html_parts.append(
            f'<mark class="pii-highlight pii-highlight--{escape(span.category)}" '
            f'title="{escape(label)}">{escape(text[span.start : span.end])}</mark>'
        )
        cursor = span.end
    html_parts.append(escape(text[cursor:]))

    entities_by_key: dict[tuple[str, str], DocumentPreviewEntity] = {}
    for span in non_overlapping:
        key = (span.category, span.value.lower())
        existing = entities_by_key.get(key)
        if existing is None:
            entities_by_key[key] = DocumentPreviewEntity(
                category=span.category,
                label=_CATEGORY_LABELS.get(span.category, span.category),
                value=span.value,
                count=1,
            )
        else:
            entities_by_key[key] = DocumentPreviewEntity(
                category=existing.category,
                label=existing.label,
                value=existing.value,
                count=existing.count + 1,
            )

    return DocumentPreview(
        highlighted_html="".join(html_parts),
        entities=list(entities_by_key.values()),
    )
