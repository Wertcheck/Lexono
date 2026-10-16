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
from app.privacy.review import (
    MAX_TERM_LENGTH,
    PrivacyDecisions,
    ReviewCandidate,
    MANUAL_NAME_PREFIX_WORDS,
    is_identifier_shaped_term,
    is_name_shaped_term,
    is_plate_shaped_term,
)


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


def _validate(term: str, decision: str, *, allow_names: bool = False) -> str:
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
    # Ebenso kennnummernfoermige Kandidaten (§22, review.is_identifier_shaped_term).
    # §52: NUR fuer die manuelle Markierung (`allow_names`) zusaetzlich mehrteilige Personennamen (review.is_name_shaped_term:
    # nur Buchstaben, innere Bindestriche, Leerzeichen, Namenszusaetze). Die Rueckfrage bleibt bei Einzelwoertern (Audit oben).
    if (not term.isalpha() and not (allow_names and is_name_shaped_term(term)) and not is_plate_shaped_term(term)
            and not is_identifier_shaped_term(term)):
        raise InvalidPrivacyDecisionError("Ungültiger Begriff.")
    return term


MAX_MANUAL_TERMS = 20


def validate_manual_terms(terms: object, message_text: str) -> list[str]:
    """§51 (16.10.2026): vom Anwalt im Eingabefeld markierte Namen, die die automatische Erkennung uebersieht ("Der
    Richter Zweigelt"). Gleiche Form wie eine Rueckfrage-Entscheidung "Als Person behandeln" (ein Wort, siehe `_validate`)
    und - gegen veraltete Markierungen nach einer Textaenderung - nur, wenn der Begriff in der Nachricht vorkommt.
    Fehler werden NICHT uebergangen: der Aufrufer sendet dann nichts (keine stillschweigend verlorene Markierung)."""
    from app.privacy.review import detect_decision_terms

    if not isinstance(terms, list) or len(terms) > MAX_MANUAL_TERMS or not all(isinstance(t, str) for t in terms):
        raise InvalidPrivacyDecisionError("Ungültige Namensmarkierung.")
    result: list[str] = []
    for raw in terms:
        term = _validate(" ".join(raw.split()), DECISION_PERSONAL, allow_names=True)
        if not is_name_shaped_term(term):
            raise InvalidPrivacyDecisionError("Bitte nur Namen markieren (Buchstaben, Bindestrich, Namenszusatz).")
        if term.split(" ")[0].lower().rstrip(".:") in MANUAL_NAME_PREFIX_WORDS:
            # Rolle/Anrede/Titel gehoert nicht in den Platzhalter (sonst verschwindet "Richter" mit dem Namen)
            raise InvalidPrivacyDecisionError("Bitte nur den Namen ohne Anrede, Titel oder Rolle markieren.")
        if not detect_decision_terms(message_text, {term}):
            # Ohne den Begriff: die Meldung landet im klassischen Formularpfad in der Redirect-URL (?error=...) und
            # damit im Zugriffsprotokoll/Browserverlauf. Der Browser nennt den Namen vorher lokal (chat.html).
            raise InvalidPrivacyDecisionError(
                "Ein markierter Name kommt in der Nachricht nicht mehr vor. Bitte Markierung prüfen."
            )
        if term not in result:
            result.append(term)
    return result


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
    manual: bool = False,
) -> PrivacyDecision:
    """Speichert (bzw. aktualisiert) eine Entscheidung und zaehlt sie klartextfrei in der Lern-Statistik.
    Audit ohne Begriff (nur Entscheidungsart) - der Begriff kann ein Name sein. `manual`: vom Anwalt selbst markiert
    (§51), nicht von Lexono gefragt - nur fuer den Audit-Text."""
    term = _validate(term, decision, allow_names=manual)
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
            details=(f"Name manuell zur Pseudonymisierung markiert: {decision}" if manual
                     else f"Datenschutz-Rückfrage entschieden: {decision}"),
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


def list_for_matter(db: Session, matter_id: str) -> list[PrivacyDecision]:
    """§52: alle Entscheidungen EINER Akte (aktenweite zuerst, dann unterhaltungsbezogene; je alphabetisch)."""
    rows = db.query(PrivacyDecision).filter(PrivacyDecision.matter_id == matter_id).all()
    return sorted(rows, key=lambda r: (r.conversation_id is not None, r.term.lower()))


def revoke_decision(db: Session, *, matter_id: str, decision_id: str, actor: str) -> PrivacyDecision:
    """§52: Entscheidung gezielt aufheben (Zeile loeschen). Gilt ab der NAECHSTEN Pruefung jedes Pfads - die Entscheidungen
    werden bei jeder Anfrage frisch geladen (`load_decisions`), es gibt keinen Zwischenspeicher. Die automatische Erkennung
    bleibt unberuehrt. Nur innerhalb DERSELBEN Akte (Aktenisolation); Audit ohne Begriff."""
    row = _row_of_matter(db, matter_id, decision_id)
    db.add(AuditEvent(entity_type="Matter", entity_id=matter_id, event_type="privacy_decision_revoked", actor=actor,
                      details=f"Datenschutz-Entscheidung aufgehoben: {row.decision}"))
    db.delete(row)
    db.flush()
    return row


def confirm_unsure_decision(db: Session, *, matter_id: str, decision_id: str, user_id: str | None, actor: str) -> PrivacyDecision:
    """§52: "Unsicher" als "Als Person behandeln" bestaetigen (beides maskiert; die Bestaetigung ist eine Korrektur der
    Aktenlage, keine Schutzaenderung)."""
    row = _row_of_matter(db, matter_id, decision_id)
    if row.decision != DECISION_UNSURE:
        raise InvalidPrivacyDecisionError("Nur „Unsicher“ kann bestätigt werden.")
    row.decision = DECISION_PERSONAL
    row.decided_by_user_id = user_id
    db.add(AuditEvent(entity_type="Matter", entity_id=matter_id, event_type="privacy_decision_confirmed", actor=actor,
                      details="Datenschutz-Entscheidung bestätigt: unsure -> personal"))
    db.flush()
    return row


def _row_of_matter(db: Session, matter_id: str, decision_id: str) -> PrivacyDecision:
    row = db.query(PrivacyDecision).filter(PrivacyDecision.id == decision_id, PrivacyDecision.matter_id == matter_id).first()
    if row is None:
        raise InvalidPrivacyDecisionError("Diese Entscheidung gehört nicht zu dieser Akte.")
    return row


def delete_for_conversation(db: Session, conversation_id: str) -> int:
    """Loescht die unterhaltungsbezogenen Entscheidungen (SQLite erzwingt keine Fremdschluessel-Kaskade)."""
    return (
        db.query(PrivacyDecision)
        .filter(PrivacyDecision.conversation_id == conversation_id)
        .delete(synchronize_session=False)
    )
