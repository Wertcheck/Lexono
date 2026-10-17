"""Konfigurierbare Dokumentfreigabe vor KI-Verarbeitung (§62).

Freigabemodus - gilt KANZLEIWEIT (eine Lexono-Installation = eine Kanzlei, `FirmProfile`), aendern duerfen ihn Admin
und Anwalt (`PERM_PRIVACY_DECISION_MANAGE`, dasselbe Recht wie fuer gespeicherte Datenschutz-Entscheidungen, §52):

- "manual"      (A) Jedes Dokument braucht eine ausdrueckliche Freigabe durch Anwalt/Admin.
- "auto"        (B) Nach erfolgreicher zentraler Datenschutzpruefung ohne gesonderte Freigabe. STANDARD ohne gesetzten
                    Wert - entspricht dem bisherigen, geprueften Verhalten (keine stillschweigende Aenderung bestehender
                    Installationen). Unsichere Begriffe werden wie bisher bei der Anfrage maskiert bzw. erfragt.
- "auto_review" (C) Unauffaellige Dokumente automatisch; bei Auffaelligkeiten (unsichere Begriffe) Entscheidung noetig.

In ALLEN Modi gilt unveraendert: jede Anfrage durchlaeuft die zentrale Pruefung (Pseudonymisierung, Validierung,
Leak-Check, Siegel, fail-closed). Eine fehlgeschlagene Pruefung blockiert das Dokument in jedem Modus - auch eine
manuelle Freigabe uebersteuert sie nie. Freigaben und Pruefergebnisse sind an den Dokumentstand gebunden
(Textstand-Fingerabdruck; Pruefergebnis zusaetzlich an Namensentscheidungen und Beteiligte der Akte): jede Aenderung
fuehrt zur erneuten Pruefung bzw. macht eine manuelle Freigabe wirkungslos.

Die Pruefung selbst ist rein lokal (kein Cloud-Aufruf) - Upload, Import, Oeffnen und Ablegen loesen keine KI aus.
Durchgesetzt wird serverseitig an der einzigen Stelle, an der Dokumenttext in eine Anfrage gelangt
(`RuleBasedLocalAIProvider._build_sachverhalt`), und am Gate fuer ausdruecklich angefragte Dokumente.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, Document, Matter, User
from app.models.document_release import (
    CHECK_FAILED,
    CHECK_PASSED,
    CHECK_UNCERTAIN,
    DocumentPrivacyCheck,
    DocumentRelease,
)

MODE_MANUAL = "manual"
MODE_AUTO = "auto"
MODE_AUTO_REVIEW = "auto_review"
DEFAULT_MODE = MODE_AUTO
MODES = (MODE_MANUAL, MODE_AUTO, MODE_AUTO_REVIEW)
MODE_LABELS = {
    MODE_MANUAL: "Manuelle Freigabe",
    MODE_AUTO: "Automatische Freigabe",
    MODE_AUTO_REVIEW: "Automatische Freigabe mit Entscheidung bei Auffälligkeiten",
}
MODE_DESCRIPTIONS = {
    MODE_MANUAL: "Jedes Dokument muss vor der KI-Verarbeitung ausdrücklich freigegeben werden.",
    MODE_AUTO: "Nach erfolgreicher zentraler Datenschutzprüfung wird das Dokument ohne gesonderte Freigabe "
               "verarbeitet. Unsichere Begriffe werden bei der Anfrage pseudonymisiert bzw. erfragt.",
    MODE_AUTO_REVIEW: "Unauffällige Dokumente werden automatisch freigegeben; bei Unsicherheit wird die "
                      "Verarbeitung blockiert, bis eine Entscheidung getroffen ist.",
}

# Freigabestatus eines Dokuments
STATE_RELEASED_AUTO = "released_auto"
STATE_RELEASED_MANUAL = "released_manual"
STATE_REQUIRED = "required"  # Modus A: Freigabe erforderlich
STATE_DECISION = "decision"  # Modus C: Auffaelligkeit - Entscheidung erforderlich
STATE_BLOCKED = "blocked"  # zentrale Pruefung fehlgeschlagen - in jedem Modus
STATE_NO_TEXT = "no_text"  # (noch) kein verwertbarer Text - Lesbarkeitspruefung (readiness) zustaendig
STATE_LABELS = {
    STATE_RELEASED_AUTO: "Automatisch freigegeben",
    STATE_RELEASED_MANUAL: "Manuell freigegeben",
    STATE_REQUIRED: "Freigabe erforderlich",
    STATE_DECISION: "Entscheidung erforderlich – Auffälligkeit bei der Datenschutzprüfung",
    STATE_BLOCKED: "Verarbeitung blockiert – Datenschutzprüfung fehlgeschlagen",
    STATE_NO_TEXT: "Noch kein verwertbarer Text",
}
RELEASED_STATES = (STATE_RELEASED_AUTO, STATE_RELEASED_MANUAL)


class ReleaseError(Exception):
    """Freigabe nicht moeglich (veralteter Textstand, fehlgeschlagene Pruefung, ungueltig) - Meldung ohne Inhalte."""


@dataclass(frozen=True)
class ReleaseStatus:
    state: str
    mode: str
    text_version: str
    check_outcome: str | None = None
    candidate_count: int = 0

    @property
    def released(self) -> bool:
        return self.state in RELEASED_STATES

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]

    @property
    def mode_label(self) -> str:
        return MODE_LABELS[self.mode]

    @property
    def can_release_manually(self) -> bool:
        """Manuelle Freigabe nur, wo sie noetig ist - und NIE bei fehlgeschlagener Pruefung."""
        return self.state in (STATE_REQUIRED, STATE_DECISION)


# --- Modus und Anzeigepraeferenz -------------------------------------------------------------------------------------


def get_mode(db: Session) -> str:
    from app.firm_profile.service import get_firm_profile

    mode = get_firm_profile(db).document_release_mode
    return mode if mode in MODES else DEFAULT_MODE


def set_mode(db: Session, mode: str, *, actor: str) -> None:
    from app.firm_profile.service import get_firm_profile

    if mode not in MODES:
        raise ReleaseError("Unbekannter Freigabemodus.")
    profile = get_firm_profile(db)
    previous = profile.document_release_mode if profile.document_release_mode in MODES else DEFAULT_MODE
    if previous == mode and profile.document_release_mode == mode:
        return  # unveraendert: kein Audit-Eintrag
    profile.document_release_mode = mode
    db.add(AuditEvent(entity_type="FirmProfile", entity_id=profile.id, event_type="document_release_mode_changed",
                      actor=actor, details=f"Freigabemodus: {previous} -> {mode}"))
    db.commit()


def show_auto_release_notice(user: User | None) -> bool:
    """Nur die INFORMATIVE Erfolgsmeldung - nie Warnungen, Blockaden oder erforderliche Freigaben."""
    return True if user is None else bool(getattr(user, "show_auto_release_notice", True))


def set_auto_release_notice(db: Session, user: User, *, show: bool) -> None:
    user.show_auto_release_notice = show
    db.commit()


# --- Dokumentstand und zentrale Pruefung -------------------------------------------------------------------------------


def text_version_of(text: str | None) -> str:
    """Fingerabdruck des extrahierten Dokumenttexts (dieselbe Formel wie §56 `document_text_version`)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


@lru_cache(maxsize=1)
def _check_code_version() -> str:
    """Fingerabdruck des Datenschutz-Codes (app/privacy) - nach einem Update mit geaenderter Erkennung gilt ein
    zwischengespeichertes Pruefergebnis nicht mehr (Review §62)."""
    from app.updater.checker import CURRENT_APP_VERSION

    # App-Version als Rueckfallebene: im installierten Build liegen die Quellen ggf. nicht als .py-Dateien vor
    digest = hashlib.sha256(CURRENT_APP_VERSION.encode())
    for source in sorted((Path(__file__).resolve().parents[1] / "privacy").glob("*.py")):
        digest.update(source.name.encode())
        digest.update(source.read_bytes())
    return digest.hexdigest()[:16]


def _context(db: Session, matter: Matter):
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.privacy.decisions import load_decisions

    known = RuleBasedLocalAIProvider()._build_known_entities(matter.id, matter, db)  # wie beim Versand
    decisions = load_decisions(db, matter_id=matter.id, conversation_id=None)  # aktenweit, wie Schriftsatz-Pfade
    key_source = json.dumps({"code": _check_code_version(), "known": {k: sorted(v) for k, v in sorted(known.items())},
                             "personal": sorted(decisions.personal), "not_personal": sorted(decisions.not_personal)},
                            ensure_ascii=False)
    return known, decisions, hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:32]


def _run_central_check(text: str, known, decisions) -> tuple[str, int]:
    """Die ZENTRALE Pruefung (`prepare_request`, Modus "ask") auf den Dokumenttext - lokal, kein Cloud-Aufruf."""
    from app.privacy.gateway import ClaudePrivacyGateway

    result = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt=text, known_entities=known, privacy_decisions=decisions,
        review_mode="ask",
    )
    if result.allowed:
        return CHECK_PASSED, 0
    if result.review_required:
        return CHECK_UNCERTAIN, len(result.review_candidates)
    return CHECK_FAILED, 0


def check_document(db: Session, document: Document, *, context=None) -> tuple[str, int]:
    """Pruefergebnis fuer den AKTUELLEN Dokumentstand (zwischengespeichert je Textstand, Kontext und Code-Version).
    Zwischengespeichert werden nur ECHTE Ergebnisse (unauffaellig/auffaellig). "Fehlgeschlagen" - auch ein
    voruebergehender Fehler, den der Gateway selbst als Blockade meldet - wird bei jedem Aufruf neu geprueft und bleibt
    bis dahin fail-closed (Review §62: sonst dauerhaft blockiert). `context`: einmal je Akte berechnet (Laufzeit)."""
    matter = db.get(Matter, document.matter_id) if document.matter_id else None
    if matter is None or matter.deleted_at is not None:
        return CHECK_FAILED, 0
    version = text_version_of(document.extracted_text)
    try:
        known, decisions, context_key = context if context is not None else _context(db, matter)
    except Exception:  # noqa: BLE001 - fail-closed
        return CHECK_FAILED, 0
    cached = (
        db.query(DocumentPrivacyCheck)
        .filter_by(document_id=document.id, text_version=version, context_key=context_key)
        .first()
    )
    if cached is not None:
        return cached.outcome, cached.candidate_count
    try:
        outcome, count = _run_central_check(document.extracted_text or "", known, decisions)
    except Exception:  # noqa: BLE001 - fail-closed, nicht zwischenspeichern
        return CHECK_FAILED, 0
    if outcome != CHECK_FAILED:
        _store_check(db, document.id, version, context_key, outcome, count)
    return outcome, count


def _store_check(db: Session, document_id: str, version: str, context_key: str, outcome: str, count: int) -> None:
    """Eigene kurze Sitzung (wie app/chat/processing_claims.py): kein Commit/Rollback fremder offener Aenderungen des
    laufenden Requests; ein Schreibfehler (paralleler Eintrag, gesperrte Datenbank) ist unschaedlich - dann wird beim
    naechsten Mal erneut geprueft."""
    session = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)()
    try:
        session.add(DocumentPrivacyCheck(document_id=document_id, text_version=version, context_key=context_key,
                                         outcome=outcome, candidate_count=count))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
    finally:
        session.close()


def _valid_manual_release(db: Session, document: Document, version: str) -> DocumentRelease | None:
    return db.query(DocumentRelease).filter_by(document_id=document.id, text_version=version).first()


def release_status(db: Session, document: Document, *, mode: str | None = None) -> ReleaseStatus:
    """Freigabestatus des aktuellen Dokumentstands im (gegebenen oder eingestellten) Modus."""
    mode = mode or get_mode(db)
    version = text_version_of(document.extracted_text)
    if not (document.extracted_text or "").strip():
        return ReleaseStatus(STATE_NO_TEXT, mode, version)
    outcome, count = check_document(db, document)
    if outcome == CHECK_FAILED:  # in JEDEM Modus, auch mit manueller Freigabe
        return ReleaseStatus(STATE_BLOCKED, mode, version, outcome, count)
    if _valid_manual_release(db, document, version) is not None:
        return ReleaseStatus(STATE_RELEASED_MANUAL, mode, version, outcome, count)
    if mode == MODE_MANUAL:
        return ReleaseStatus(STATE_REQUIRED, mode, version, outcome, count)
    if mode == MODE_AUTO_REVIEW and outcome == CHECK_UNCERTAIN:
        return ReleaseStatus(STATE_DECISION, mode, version, outcome, count)
    return ReleaseStatus(STATE_RELEASED_AUTO, mode, version, outcome, count)


def release_manually(db: Session, document: Document, *, text_version: str, user: User) -> ReleaseStatus:
    """Manuelle Freigabe des ANGEZEIGTEN Textstands. Abgewiesen bei veraltetem Stand oder fehlgeschlagener Pruefung."""
    current = text_version_of(document.extracted_text)
    if not text_version or text_version != current:
        raise ReleaseError("Der Dokumenttext hat sich seit der Anzeige geändert. Bitte erneut prüfen und freigeben.")
    status = release_status(db, document)
    if status.state == STATE_BLOCKED:
        raise ReleaseError("Die Datenschutzprüfung ist fehlgeschlagen – eine Freigabe ist nicht möglich.")
    if status.state == STATE_NO_TEXT:
        raise ReleaseError("Das Dokument enthält keinen verwertbaren Text.")
    if status.state in RELEASED_STATES:
        return status
    db.add(DocumentRelease(document_id=document.id, text_version=current, released_by_user_id=user.id,
                           mode_at_release=status.mode))
    db.add(AuditEvent(entity_type="Document", entity_id=document.id, event_type="document_released_manually",
                      actor=user.email, details=f"Dokument manuell für die KI-Verarbeitung freigegeben "
                                                f"(Modus {status.mode}, Prüfung {status.check_outcome})"))
    try:
        db.commit()
    except IntegrityError:  # Doppelklick: dieselbe Freigabe existiert bereits
        db.rollback()
    return release_status(db, document)


def processing_state(db: Session, document: Document, *, mode: str | None = None, context=None) -> str:
    """Freigabezustand fuer die DURCHSETZUNG (Kontextaufbau, Dokument-Gate) - ohne unnoetige zweite Pruefung:

    - Modus B: freigegeben. Eine fehlgeschlagene Pruefung blockiert ohnehin die GESAMTE Anfrage (die zentrale Pruefung
      laeuft bei jedem Versand ueber den vollstaendigen Inhalt, fail-closed) - eine zusaetzliche Dokumentpruefung je
      Anfrage verdoppelte nur die Laufzeit.
    - Modus A: nur mit gueltiger manueller Freigabe des aktuellen Textstands (bei der Freigabe wurde geprueft; ein
      fehlgeschlagener Stand ist nicht freigebbar, und die zentrale Pruefung bei der Anfrage bleibt).
    - Modus C: manuell freigegeben ODER die zentrale Pruefung des Dokuments war unauffaellig (zwischengespeichert).
    Die Statusanzeige nutzt dagegen immer `release_status` (vollstaendige Pruefung)."""
    mode = mode or get_mode(db)
    if mode == MODE_AUTO:
        return STATE_RELEASED_AUTO
    version = text_version_of(document.extracted_text)
    if _valid_manual_release(db, document, version) is not None:
        # wie die Anzeige: eine inzwischen fehlgeschlagene Pruefung blockiert auch ein freigegebenes Dokument
        if check_document(db, document, context=context)[0] == CHECK_FAILED:
            return STATE_BLOCKED
        return STATE_RELEASED_MANUAL
    if mode == MODE_MANUAL:
        return STATE_REQUIRED
    outcome, _ = check_document(db, document, context=context)
    if outcome == CHECK_FAILED:
        return STATE_BLOCKED
    return STATE_DECISION if outcome == CHECK_UNCERTAIN else STATE_RELEASED_AUTO


def released_document_ids(db: Session, documents) -> set[str]:
    """IDs der aktuell zur Verarbeitung freigegebenen Dokumente (serverseitige Durchsetzung beim Kontextaufbau)."""
    mode = get_mode(db)
    if mode == MODE_AUTO:
        return {document.id for document in documents}
    contexts: dict[str, object] = {}  # Kontext einmal je Akte (Beteiligte, Entscheidungen) statt je Dokument

    def context_of(document):
        if document.matter_id not in contexts:
            matter = db.get(Matter, document.matter_id) if document.matter_id else None
            try:
                contexts[document.matter_id] = _context(db, matter) if matter is not None else None
            except Exception:  # noqa: BLE001 - fail-closed: check_document meldet dann "fehlgeschlagen"
                contexts[document.matter_id] = None
        return contexts[document.matter_id]

    return {document.id for document in documents
            if processing_state(db, document, mode=mode, context=context_of(document)) in RELEASED_STATES}
