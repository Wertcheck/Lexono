"""Gemeinsame Bausteine fuer "Namen schützen" (PRIVACY_REVIEW_ARCHITECTURE.md §51-§54).

Die Wortlisten (Anrede/Titel/Rolle vor einem Namen, Namenszusaetze) haben EINE Quelle in
`app/privacy/detectors.py`. Jeder Router, der eine Seite mit dem Bedienelement rendert
(`partials/name_protection.html`), traegt sie hier in seine Jinja-Umgebung ein - kein Nachbau im JavaScript.
Pruefen und Speichern einer Markierung: `app/privacy/decisions.parse_manual_terms`/`record_manual_terms`."""

from __future__ import annotations

import html
import re

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.privacy.decisions import InvalidPrivacyDecisionError, parse_manual_terms, record_manual_terms
from app.privacy.detectors import MANUAL_NAME_PREFIX_WORDS, NAME_PARTICLES

_TAG = re.compile(r"<[^>]+>")


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


def protect_manual_terms(db: Session, draft, user_text: str, protected_terms: str | None, user) -> str | None:
    """§54: Markierung fuer eine Anfrage auf einem bestehenden Entwurf (Aenderungsauftrag, Editor). Geprueft gegen
    Auftrag/Auswahl (das geht in die Anfrage) UND den angezeigten Entwurf: ein dort stehender, nicht erkannter Name ist
    ebenso markierbar - die Entscheidung gilt aktenweit fuer jede spaetere Anfrage (gemessen 16.10.2026: der
    Aenderungsauftrag selbst sendet den bisherigen Entwurf nicht mit, nur Aktentitel und Auftrag). Gespeichert VOR der
    Anfrage. Rueckgabe: Fehlermeldung ohne Begriff (dann nichts senden) oder None."""
    try:
        terms = parse_manual_terms(protected_terms, f"{user_text or ''}\n{draft_plain_text(draft)}")
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
