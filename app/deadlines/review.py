"""Anwaltliche Pruefung erkannter Fristen (Human-in-the-Loop, ARCHITECTURE.md §73).

Ein KI-Vorschlag (`Deadline.review_status == "unreviewed"`) wird erst durch eine ausdrueckliche, einzelne Entscheidung
einer berechtigten Person (`PERM_DEADLINE_CONFIRM`, Anwalt/Admin) zu einer bestaetigten Frist. Dieses Modul buendelt
die Entscheidung (bestaetigen, verwerfen, zurueckstellen) samt Audit, die Pruefreihenfolge und die rein darstellenden
Ableitungen (Art, Fundstelle, Hinweise). Es berechnet KEINE Fristen und entscheidet nichts selbst.

Zurueckstellen ist bewusst KEIN eigener Pruefstatus: der Vorschlag bleibt "unreviewed" (damit wirken alle bestehenden
Filter - Entwurfskontext, Badge, REST-API - unveraendert), die Zurueckstellung steht ausschliesslich im Audit-Log
("deadline_review_deferred") und wird von dort gelesen. Keine Migration, keine doppelte Datenhaltung."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models import AuditEvent, Deadline

#: Trennzeichen zwischen Originaldatum und Kontext in `Deadline.source_text` (siehe DeadlineAnalysisService).
SOURCE_SEPARATOR = " :: "
#: Vorschlaege mit einem Datum innerhalb dieser Tage gelten als "dringend zu pruefen" - ein Pruefhinweis, keine
#: Aussage ueber den Fristablauf.
URGENT_REVIEW_DAYS = 7
#: Markierung in `Deadline.reasoning` fuer einen bei erneuter Analyse erkannten Konflikt.
CONFLICT_MARKER = "KONFLIKT bei erneuter Analyse:"

DECISIONS = ("confirm", "reject", "defer")
_TARGET_STATUS = {"confirm": "confirmed", "reject": "rejected"}

_KIND_LABELS = {
    "frist": "Mögliche Frist (Fristende genannt)",
    "termin": "Möglicher Termin (kein Fristablauf)",
    "fristbeginn": "Möglicher Fristbeginn (Fristende offen)",
    "fristdauer": "Mögliche Fristdauer (Fristende offen)",
    "manuell": "Manuell erfasst",
}


class ReviewDecisionError(Exception):
    """Ungueltige Entscheidung oder ein Uebergang, der fachlich keinen Sinn ergibt."""


def kind_from_reasoning(reasoning: str | None, *, from_analysis: bool) -> str:
    """Art aus der Begruendung des Extraktors ("Art: ..."); ohne Analysequelle und ohne Angabe "manuell"."""
    reasoning = reasoning or ""
    if "Art: Fristdauer" in reasoning:
        return "fristdauer"
    if "Art: Fristbeginn" in reasoning:
        return "fristbeginn"
    if "Art: Termin" in reasoning:
        return "termin"
    if from_analysis or "Art: Frist" in reasoning:
        return "frist"
    return "manuell"


def deadline_kind(deadline: Deadline) -> str:
    return kind_from_reasoning(deadline.reasoning, from_analysis=bool(deadline.document_id or deadline.message_id))


def kind_label(deadline: Deadline) -> str:
    return _KIND_LABELS[deadline_kind(deadline)]


def split_source_text(source_text: str | None) -> tuple[str | None, str | None]:
    """(Originaldatum/-angabe, Fundstelle) eines KI-Vorschlags; sonst (None, Bezeichnung)."""
    text = source_text or ""
    if SOURCE_SEPARATOR in text:
        raw, context = text.split(SOURCE_SEPARATOR, 1)
        return raw.strip() or None, context.strip() or None
    return None, text.strip() or None


def has_conflict(deadline: Deadline) -> bool:
    return CONFLICT_MARKER in (deadline.reasoning or "")


def review_hint(deadline: Deadline, today: date) -> str | None:
    """Hinweis fuer einen ungeprueften Vorschlag mit nahem oder vergangenem Datum. Behauptet kein Fristversaeumnis:
    das Datum ist nur ein Vorschlag, ob und wann eine Frist ablaeuft, entscheidet die Pruefung."""
    if deadline.review_status != "unreviewed" or deadline.due_date is None:
        return None
    delta = (deadline.due_date - today).days
    if delta < 0:
        return "Datum verstrichen – bitte umgehend prüfen"
    if delta <= URGENT_REVIEW_DAYS:
        return "Dringend prüfen – Datum in Kürze"
    return None


def short_review_hint(deadline: Deadline, today: date) -> str | None:
    """Kurzform fuer die Listenspalte; der volle Hinweis steht im Detailpanel."""
    hint = review_hint(deadline, today)
    if hint is None:
        return None
    return "verstrichen – prüfen" if hint.startswith("Datum verstrichen") else "bald – prüfen"


def deferred_notes(db: Session, deadline_ids: list[str]) -> dict[str, str]:
    """Zurueckgestellte, weiterhin ungepruefte Vorschlaege -> Notiz der letzten Zurueckstellung (ggf. leer)."""
    if not deadline_ids:
        return {}
    rows = (
        db.query(AuditEvent.entity_id, AuditEvent.details, Deadline.review_status)
        .join(Deadline, Deadline.id == AuditEvent.entity_id)
        .filter(
            AuditEvent.entity_type == "Deadline",
            AuditEvent.event_type == "deadline_review_deferred",
            AuditEvent.entity_id.in_(deadline_ids),
        )
        .order_by(AuditEvent.created_at.asc())
        .all()
    )
    notes: dict[str, str] = {}
    for entity_id, details, review_status in rows:
        if review_status == "unreviewed":
            notes[entity_id] = (details or "").partition("Notiz: ")[2]
    return notes


def unreviewed_query(db: Session, *, matter_id: str | None = None):
    query = db.query(Deadline).filter(Deadline.review_status == "unreviewed")
    if matter_id:
        query = query.filter(Deadline.matter_id == matter_id)
    return query


def review_order(deadlines: list[Deadline], deferred: set[str]) -> list[Deadline]:
    """Pruefreihenfolge: nicht zurueckgestellt vor zurueckgestellt, dann nach Datum (ohne Datum zuletzt)."""
    return sorted(
        deadlines,
        key=lambda d: (d.id in deferred, d.due_date is None, d.due_date or date.max, str(d.created_at or "")),
    )


def next_unreviewed(db: Session, *, matter_id: str | None = None, exclude_id: str | None = None) -> Deadline | None:
    """Naechster offener, nicht zurueckgestellter Vorschlag - None, wenn nur noch zurueckgestellte uebrig sind.
    Sonst kaeme ein gerade zurueckgestellter Vorschlag sofort wieder."""
    candidates = [d for d in unreviewed_query(db, matter_id=matter_id).all() if d.id != exclude_id]
    deferred = set(deferred_notes(db, [d.id for d in candidates]))
    ordered = [d for d in review_order(candidates, deferred) if d.id not in deferred]
    return ordered[0] if ordered else None


@dataclass(frozen=True)
class ReviewOutcome:
    deadline: Deadline
    previous_status: str
    decision: str


def decide(db: Session, deadline: Deadline, *, decision: str, actor: str, note: str | None = None) -> ReviewOutcome:
    """Eine einzelne, ausdrueckliche Entscheidung ueber genau eine Frist - mit Audit, in einer Transaktion.

    Die Berechtigung prueft der Aufrufer (Route) serverseitig; dieses Modul kennt keine Sammelentscheidung."""
    if decision not in DECISIONS:
        raise ReviewDecisionError("Ungueltige Pruefentscheidung.")
    note = (note or "").strip()[:500]
    previous = deadline.review_status
    if decision == "defer":
        if previous != "unreviewed":
            raise ReviewDecisionError("Nur ungeprüfte Vorschläge können zurückgestellt werden.")
        event_type = "deadline_review_deferred"
        details = "Pruefung zurueckgestellt (bleibt ungeprueft)"
    else:
        target = _TARGET_STATUS[decision]
        deadline.review_status = target
        event_type = "deadline_review_status_changed"
        details = f"Pruefstatus geaendert: {previous} -> {target}"
    if note:
        details += f"; Notiz: {note}"
    db.add(
        AuditEvent(
            entity_type="Deadline", entity_id=deadline.id, event_type=event_type, actor=actor, details=details
        )
    )
    db.commit()
    return ReviewOutcome(deadline=deadline, previous_status=previous, decision=decision)
