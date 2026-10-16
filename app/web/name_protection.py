"""Gemeinsame Bausteine fuer "Namen schützen" (PRIVACY_REVIEW_ARCHITECTURE.md §51-§54).

Die Wortlisten (Anrede/Titel/Rolle vor einem Namen, Namenszusaetze) haben EINE Quelle in
`app/privacy/detectors.py`. Jeder Router, der eine Seite mit dem Bedienelement rendert
(`partials/name_protection.html`), traegt sie hier in seine Jinja-Umgebung ein - kein Nachbau im JavaScript.
Pruefen und Speichern einer Markierung: `app/privacy/decisions.parse_manual_terms`/`record_manual_terms`."""

from __future__ import annotations

import hashlib
import html
import re

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.privacy.decisions import InvalidPrivacyDecisionError, parse_manual_terms, record_manual_terms
from app.privacy.detectors import MANUAL_NAME_PREFIX_WORDS, NAME_PARTICLES

_TAG = re.compile(r"<[^>]+>")
# §55: Obergrenze fuer den mitgeschickten aktuellen Editortext (nur Pruefgrundlage, wird nie gespeichert/gesendet)
MAX_CURRENT_TEXT_CHARS = 200_000


def install_name_protection(templates: Jinja2Templates) -> Jinja2Templates:
    templates.env.globals["manual_name_prefix_words"] = sorted(MANUAL_NAME_PREFIX_WORDS)
    templates.env.globals["manual_name_particles"] = sorted(NAME_PARTICLES)
    return templates


def draft_plain_text(draft) -> str:
    """Lesbarer Text eines Entwurfs (Rich-Text-Entwuerfe ohne Tags) - nur fuer die Pruefung, ob ein markierter Name
    dort vorkommt."""
    content = draft.content or ""
    if getattr(draft, "content_format", None) == "html":
        content = html.unescape(_TAG.sub(" ", content))
    return content


def document_text_version(document) -> str:
    """§56: Fingerabdruck des extrahierten Dokumenttexts - bindet eine Markierung an GENAU den Textstand, der beim
    Markieren angezeigt wurde. Aendert sich der Text (z. B. erneute Texterkennung), passt der Fingerabdruck nicht mehr
    und die Markierung wird abgewiesen statt auf einen anderen Text angewendet zu werden. Kein Klartext."""
    return hashlib.sha256((document.extracted_text or "").encode("utf-8")).hexdigest()[:32]


def protect_document_terms(db: Session, document, protected_terms: str | None, text_version: str | None, user) -> str | None:
    """§56: Markierung in einem hochgeladenen Dokument. Dieselbe Pruefung und Speicherung wie Chat/Formulare
    (`parse_manual_terms`/`record_manual_terms`), geprueft gegen den extrahierten Dokumenttext; zusaetzlich muss der
    Textstand (`document_text_version`) dem angezeigten entsprechen. Aktenweite Entscheidung, gespeichert VOR jeder
    Anfrage. Rueckgabe: Fehlermeldung ohne Begriff (dann nichts senden) oder None."""
    if not (protected_terms or "").strip():
        return None
    if not document.matter_id:
        return "Das Dokument ist noch keiner Akte zugeordnet - bitte zuerst zuordnen. Es wurde nichts gespeichert."
    if not document.extracted_text:
        return "Das Dokument hat keinen ausgelesenen Text - es kann nichts markiert werden."
    if (text_version or "") != document_text_version(document):
        return ("Der Dokumenttext hat sich seit dem Öffnen geändert. Bitte die Ansicht neu laden und erneut markieren - "
                "es wurde nichts gespeichert oder gesendet.")
    try:
        terms = parse_manual_terms(protected_terms, document.extracted_text)
    except InvalidPrivacyDecisionError as exc:
        return str(exc).replace("in der Nachricht", "im Dokumenttext")
    try:
        record_manual_terms(db, matter_id=document.matter_id, conversation_id=None, terms=terms, user_id=user.id,
                            actor=user.email)
    except Exception:  # noqa: BLE001 - fail-closed: ohne gespeicherte Markierung keine Anfrage
        return "Die Namensmarkierung konnte nicht gespeichert werden - es wurde nichts gesendet."
    return None


def protect_manual_terms(
    db: Session, draft, user_text: str, protected_terms: str | None, user, *, current_text: str | None = None
) -> str | None:
    """§54: Markierung fuer eine Anfrage auf einem bestehenden Entwurf (Aenderungsauftrag, Editor). Geprueft gegen
    Auftrag/Auswahl (das geht in die Anfrage) UND den angezeigten Entwurf: ein dort stehender, nicht erkannter Name ist
    ebenso markierbar - die Entscheidung gilt aktenweit fuer jede spaetere Anfrage (gemessen 16.10.2026: der
    Aenderungsauftrag selbst sendet den bisherigen Entwurf nicht mit, nur Aktentitel und Auftrag). Gespeichert VOR der
    Anfrage. Rueckgabe: Fehlermeldung ohne Begriff (dann nichts senden) oder None.

    §55 `current_text`: der AKTUELLE, ggf. noch nicht gespeicherte Editorinhalt, mit derselben Anfrage geschickt, aus
    dem der Browser die Markierung abgeleitet hat - damit ist die Markierung an genau die Textversion gebunden, die die
    Person sieht. Er ist nur Pruefgrundlage: er wird weder gespeichert noch in die Cloud-Anfrage uebernommen (die
    enthaelt weiterhin nur Anweisung und Auswahl) und nie protokolliert. Kein automatisches Speichern des Entwurfs."""
    if current_text is not None and len(current_text) > MAX_CURRENT_TEXT_CHARS:
        return "Der Editortext ist für die Namensprüfung zu lang - es wurde nichts gesendet."
    basis = "\n".join((user_text or "", current_text or "", draft_plain_text(draft)))
    try:
        terms = parse_manual_terms(protected_terms, basis)
    except InvalidPrivacyDecisionError as exc:
        return str(exc)
    if not terms:
        return None
    if not draft.matter_id:
        return "Ohne Akte kann kein Name geschützt werden - es wurde nichts gesendet."
    try:
        record_manual_terms(db, matter_id=draft.matter_id, conversation_id=None, terms=terms, user_id=user.id,
                            actor=user.email)
    except Exception:  # noqa: BLE001 - fail-closed: ohne gespeicherte Markierung keine Anfrage
        return "Die Namensmarkierung konnte nicht gespeichert werden - es wurde nichts gesendet."
    return None
