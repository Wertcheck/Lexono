"""Speichern und Laden von Datenschutz-Entscheidungen (Human in the Loop, 12.10.2026).

Geltungsbereiche (PRIVACY_REVIEW_ARCHITECTURE.md M5):
- "personal" und "unsure" gelten fuer die GANZE Akte (sichere Richtung: mehr Pseudonymisierung) und bleiben auch nach
  dem Loeschen der Unterhaltung erhalten (conversation_id = NULL, Aktendatum).
- "not_personal" gilt NUR in der Unterhaltung, in der entschieden wurde (unsichere Richtung bleibt eng); wird mit der
  Unterhaltung geloescht.
- Widerspruch (derselbe Begriff ist in der Akte "personal"/"unsure" und in der Unterhaltung "not_personal"):
  "personal" gewinnt (fail-closed).

Globales Lernen (M6): `PrivacyFeedbackStat` zaehlt Entscheidungen je HMAC des Begriffs - kein Klartext, kein Kontext,
keine Akte, kein Nutzer. Einzelnes Feedback aendert keine Regel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models.privacy_decision import (
    DECISION_NOT_PERSONAL,
    DECISION_PERSONAL,
    DECISION_UNSURE,
    VALID_PRIVACY_DECISIONS,
    PrivacyDecision,
    PrivacyFeedbackStat,
)
from app.privacy.review import MAX_TERM_LENGTH, PrivacyDecisions, ReviewCandidate, is_plate_shaped_term


class InvalidPrivacyDecisionError(ValueError):
    pass


def _stat_key() -> bytes:
    from app.config.settings import get_settings

    secret = get_settings().resolved_session_secret_key
    return hashlib.sha256(("lexono-privacy-feedback:" + secret).encode("utf-8")).digest()


def term_hmac(term: str) -> str:
    """Nicht umkehrbarer, kanzleilokaler Schluessel eines Begriffs (gross-/kleinschreibungsunabhaengig)."""
    return hmac.new(_stat_key(), term.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()


def load_decisions(db: Session, *, matter_id: str, conversation_id: str | None) -> PrivacyDecisions:
    rows = db.query(PrivacyDecision).filter(PrivacyDecision.matter_id == matter_id).all()
    personal = {r.term for r in rows if r.decision in (DECISION_PERSONAL, DECISION_UNSURE)}
    not_personal = {
        r.term
        for r in rows
        if r.decision == DECISION_NOT_PERSONAL and conversation_id is not None and r.conversation_id == conversation_id
    }
    return PrivacyDecisions(personal=frozenset(personal), not_personal=frozenset(not_personal - personal))


def _validate(term: str, decision: str) -> str:
    term = (term or "").strip()
    if decision not in VALID_PRIVACY_DECISIONS:
        raise InvalidPrivacyDecisionError("Unbekannte Entscheidung.")
    if not term or len(term) > MAX_TERM_LENGTH or "\n" in term or "@@" in term or "[" in term:
        raise InvalidPrivacyDecisionError("Ungültiger Begriff.")
    # Nur dieselbe Form wie Rueckfrage-Kandidaten (ein alphabetisches Wort): ein eingeschleuster Mehrwort-Begriff koennte in
    # der Ueberlappungsaufloesung einen laengeren Treffer anderer Kategorie (z. B. E-Mail) verdraengen (Audit 12.10.2026).
    # Ausnahme (13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §21.3): kennzeichenfoermige Rueckfrage-Kandidaten ("D-XY 123").
    # Ohne sie wurde die Antwort des Anwalts verworfen und Lexono fragte endlos erneut (installierter Build). Die Form ist
    # eng (Buchstaben, Bindestrich, Leerzeichen, Ziffern) und wird immer als "kennung" maskiert.
    if not term.isalpha() and not is_plate_shaped_term(term):
        raise InvalidPrivacyDecisionError("Ungültiger Begriff.")
    return term


def record_decision(
    db: Session,
    *,
    matter_id: str,
    conversation_id: str,
    term: str,
    decision: str,
    user_id: str | None,
    actor: str,
    candidate: ReviewCandidate | None = None,
) -> PrivacyDecision:
    """Speichert (bzw. aktualisiert) eine Entscheidung und zaehlt sie klartextfrei in der Lern-Statistik.
    Audit ohne Begriff (nur Entscheidungsart) - der Begriff kann ein Name sein."""
    term = _validate(term, decision)
    scope_conversation = conversation_id if decision == DECISION_NOT_PERSONAL else None
    row = (
        db.query(PrivacyDecision)
        .filter(
            PrivacyDecision.matter_id == matter_id,
            PrivacyDecision.term == term,
            PrivacyDecision.conversation_id.is_(None)
            if scope_conversation is None
            else PrivacyDecision.conversation_id == scope_conversation,
        )
        .first()
    )
    if row is None:
        row = PrivacyDecision(
            matter_id=matter_id, conversation_id=scope_conversation, term=term, decision=decision,
            decided_by_user_id=user_id,
        )
        db.add(row)
    else:
        row.decision = decision
        row.decided_by_user_id = user_id
    _count_feedback(db, term=term, decision=decision, candidate=candidate)
    db.add(
        AuditEvent(
            entity_type="Matter",
            entity_id=matter_id,
            event_type="privacy_decision_recorded",
            actor=actor,
            details=f"Datenschutz-Rückfrage entschieden: {decision}",
        )
    )
    db.flush()
    return row


def _count_feedback(db: Session, *, term: str, decision: str, candidate: ReviewCandidate | None) -> None:
    key = term_hmac(term)
    stat = (
        db.query(PrivacyFeedbackStat)
        .filter(PrivacyFeedbackStat.term_hmac == key, PrivacyFeedbackStat.decision == decision)
        .first()
    )
    if stat is None:
        stat = PrivacyFeedbackStat(term_hmac=key, decision=decision, count=0)
        db.add(stat)
    stat.count = (stat.count or 0) + 1
    stat.last_seen_at = datetime.now(timezone.utc)
    if candidate is not None:
        stat.features_json = json.dumps({"roles": list(candidate.roles), "probe": candidate.probe_score})


def delete_for_conversation(db: Session, conversation_id: str) -> int:
    """Loescht die unterhaltungsbezogenen Entscheidungen (SQLite erzwingt keine Fremdschluessel-Kaskade)."""
    return (
        db.query(PrivacyDecision)
        .filter(PrivacyDecision.conversation_id == conversation_id)
        .delete(synchronize_session=False)
    )
