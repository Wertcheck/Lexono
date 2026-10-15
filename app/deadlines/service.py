"""DeadlineAnalysisService – erzeugt `Deadline`-Datensätze aus einem Dokument
oder einer Posteingang-Nachricht.

Setzt auf `Document.extracted_text` (Prompt 06) UND `Document.matter_id`
(Prompt 09) auf - eine Frist kann nur einer Akte zugeordnet werden, wenn
das Dokument bereits einer Akte zugeordnet ist (`Deadline.matter_id` ist
nicht nullable, siehe app/models/deadline.py). Ohne Text oder ohne
Aktenzuordnung wird die Analyse übersprungen und protokolliert, statt
Annahmen zu treffen. `analyze_message` (20.09., Posteingang-Fristen-
erkennung, ECHTER FUND: eine erkannte Frist im blossen E-Mail-Text einer
Nachricht - z. B. "bitte antworten Sie bis zum 15.03.2027" - wurde bisher
NIE erkannt, nur Anhaenge/Dokumente liefen durch die Fristenanalyse;
ausserdem liefen bereits VOR der Aktenzuordnung verarbeitete Anhaenge einer
Nachricht nie ein zweites Mal durch die Analyse, obwohl `analyze_document`
beim ersten Versuch mangels Aktenzuordnung nur uebersprungen, nicht mit
leeren Ergebnis "erledigt" markiert wurde - siehe app/web/router.py::
accept_matter_suggestion, wo beides jetzt bei der Aktenzuordnung nachgeholt
wird) folgt exakt demselben Prinzip fuer `Message.body_text` UND
`Message.matter_id`.

`review_status` wird NIE von diesem Service gesetzt/verändert - der
Deadline-Modell-Default "unreviewed" (Prompt 04) bleibt für jede hier
erzeugte Frist bestehen, bis ein Mensch sie bestätigt oder verwirft.

Seit der Anbindung an `DocumentProcessingService` (§64, automatischer
Aufruf nach erfolgreich abgeschlossener Textextraktion/OCR): `analyze_document`
ist bewusst IDEMPOTENT - existieren für ein `Document` bereits `Deadline`-
Datensätze, wird KEINE erneute Erkennung durchgeführt und es entstehen
KEINE Duplikate, sondern die bereits vorhandenen Fristen werden unverändert
zurückgegeben. Notwendig, weil `process_document` (z. B. über den
Retry-Pfad bei einem vorherigen OCR-Fehlschlag) für dasselbe Dokument
mehrfach aufgerufen werden kann. `analyze_message` ist aus demselben Grund
ebenfalls idempotent (ueber `Deadline.message_id`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.deadlines.extractor import DeadlineExtractor, ExtractedDeadline
from app.deadlines.review import CONFLICT_MARKER, deadline_kind, kind_from_reasoning, split_source_text
from app.models import AuditEvent, Deadline, Document, Message


@dataclass
class ReanalysisResult:
    """Ergebnis einer ausdruecklich ausgeloesten erneuten Analyse (ARCHITECTURE.md §73). Bestehende Fristen werden nie veraendert."""

    skipped_reason: str | None = None
    created: list[Deadline] = field(default_factory=list)
    already_known: int = 0
    conflicts: int = 0
    confirmed_not_found: int = 0


def _candidate_kind(candidate: ExtractedDeadline) -> str:
    return kind_from_reasoning(candidate.reasoning, from_analysis=True)


def finding_keys(findings: list[tuple[str, str | None]]) -> list[str]:
    """Stabile Fund-Schluessel (ARCHITECTURE.md §73.1) fuer (Art, Originalangabe) in Textreihenfolge:
    "art|angabe|n" mit n = n-tes Vorkommen derselben Art und Angabe. Bewusst NICHT das (korrigierbare) Datum und
    nicht der Kontext: zwei Fristen mit gleichem Datum bleiben zwei Funde, und eine geaenderte Umgebung (neue
    Texterkennung) macht aus demselben Fund keinen neuen."""
    counts: dict[str, int] = {}
    keys: list[str] = []
    for kind, raw in findings:
        base = f"{kind}|{' '.join((raw or '').split()).lower()}"[:240]
        counts[base] = counts.get(base, 0) + 1
        keys.append(f"{base}|{counts[base]}")
    return keys


def _candidate_keys(candidates: list[ExtractedDeadline]) -> list[str]:
    return finding_keys([(_candidate_kind(c), c.raw_date_text) for c in candidates])


def _existing_keys(existing: list[Deadline]) -> dict[str, str]:
    """Deadline-ID -> Fund-Schluessel. Gespeicherter Schluessel, sonst (Altdaten) zur Laufzeit aus dem aktuellen
    `source_text` abgeleitet - nichts wird zurueckgeschrieben. Altdaten ohne Originalangabe bleiben ohne Schluessel."""
    keys = {d.id: d.finding_key for d in existing if d.finding_key}
    legacy = [d for d in existing if not d.finding_key and split_source_text(d.source_text)[0]]
    derived = finding_keys([(deadline_kind(d), split_source_text(d.source_text)[0]) for d in legacy])
    keys.update({d.id: key for d, key in zip(legacy, derived)})
    return keys


class DeadlineAnalysisService:
    def __init__(self, extractor: DeadlineExtractor) -> None:
        self.extractor = extractor

    def analyze_document(self, document: Document, db: Session) -> list[Deadline]:
        if not document.extracted_text or not document.extracted_text.strip():
            db.add(
                AuditEvent(
                    entity_type="Document",
                    entity_id=document.id,
                    event_type="deadline_analysis_skipped",
                    actor="system",
                    details="Kein extrahierter Text vorhanden",
                )
            )
            db.commit()
            return []

        if not document.matter_id:
            db.add(
                AuditEvent(
                    entity_type="Document",
                    entity_id=document.id,
                    event_type="deadline_analysis_skipped",
                    actor="system",
                    details=(
                        "Dokument noch keiner Akte zugeordnet - "
                        "Fristenanalyse erfordert eine Aktenzuordnung"
                    ),
                )
            )
            db.commit()
            return []

        already_analyzed = (
            db.query(Deadline).filter(Deadline.document_id == document.id).all()
        )
        if already_analyzed:
            # Idempotenz (siehe Moduldocstring): keine erneute Erkennung,
            # keine Duplikate - dieselben, bereits vorhandenen Fristen
            # werden unveraendert zurueckgegeben.
            db.add(
                AuditEvent(
                    entity_type="Document",
                    entity_id=document.id,
                    event_type="deadline_analysis_already_done",
                    actor="system",
                    details=(
                        f"Fristenanalyse bereits zuvor durchgefuehrt "
                        f"({len(already_analyzed)} bestehende Frist(en)) - "
                        "keine erneute Erkennung, keine Duplikate"
                    ),
                )
            )
            db.commit()
            return already_analyzed

        extracted = self.extractor.extract(document.extracted_text)
        created_deadlines: list[Deadline] = []

        for candidate, key in zip(extracted, _candidate_keys(extracted)):
            # review_status wird bewusst NICHT gesetzt - Modell-Default
            # "unreviewed" greift, siehe Moduldocstring.
            source_text = f"{candidate.raw_date_text} :: {candidate.source_text}"
            deadline = Deadline(
                matter_id=document.matter_id,
                document_id=document.id,
                source_text=source_text,
                due_date=candidate.due_date,
                confidence=candidate.confidence,
                reasoning=candidate.reasoning,
                finding_key=key,
                original_source_text=source_text,
            )
            db.add(deadline)
            created_deadlines.append(deadline)

        db.add(
            AuditEvent(
                entity_type="Document",
                entity_id=document.id,
                event_type="deadline_analysis_completed",
                actor="system",
                details=(
                    f"{len(created_deadlines)} möglicher Frist(en) gefunden, "
                    "alle mit Status 'unreviewed' - manuelle Prüfung erforderlich"
                ),
            )
        )
        db.commit()
        for deadline in created_deadlines:
            db.refresh(deadline)
        return created_deadlines

    def analyze_message(self, message: Message, db: Session) -> list[Deadline]:
        """Wie `analyze_document`, aber fuer den Text einer Posteingang-
        Nachricht (`Message.body_text`) statt eines Dokuments - siehe
        Moduldocstring fuer den vollen Befund/Kontext."""
        if not message.body_text or not message.body_text.strip():
            db.add(
                AuditEvent(
                    entity_type="Message",
                    entity_id=message.id,
                    event_type="deadline_analysis_skipped",
                    actor="system",
                    details="Kein Nachrichtentext vorhanden",
                )
            )
            db.commit()
            return []

        if not message.matter_id:
            db.add(
                AuditEvent(
                    entity_type="Message",
                    entity_id=message.id,
                    event_type="deadline_analysis_skipped",
                    actor="system",
                    details=(
                        "Nachricht noch keiner Akte zugeordnet - "
                        "Fristenanalyse erfordert eine Aktenzuordnung"
                    ),
                )
            )
            db.commit()
            return []

        already_analyzed = (
            db.query(Deadline).filter(Deadline.message_id == message.id).all()
        )
        if already_analyzed:
            db.add(
                AuditEvent(
                    entity_type="Message",
                    entity_id=message.id,
                    event_type="deadline_analysis_already_done",
                    actor="system",
                    details=(
                        f"Fristenanalyse bereits zuvor durchgefuehrt "
                        f"({len(already_analyzed)} bestehende Frist(en)) - "
                        "keine erneute Erkennung, keine Duplikate"
                    ),
                )
            )
            db.commit()
            return already_analyzed

        extracted = self.extractor.extract(message.body_text)
        created_deadlines: list[Deadline] = []

        for candidate, key in zip(extracted, _candidate_keys(extracted)):
            # review_status wird bewusst NICHT gesetzt - Modell-Default
            # "unreviewed" greift, siehe Moduldocstring.
            source_text = f"{candidate.raw_date_text} :: {candidate.source_text}"
            deadline = Deadline(
                matter_id=message.matter_id,
                message_id=message.id,
                source_text=source_text,
                due_date=candidate.due_date,
                confidence=candidate.confidence,
                reasoning=candidate.reasoning,
                finding_key=key,
                original_source_text=source_text,
            )
            db.add(deadline)
            created_deadlines.append(deadline)

        db.add(
            AuditEvent(
                entity_type="Message",
                entity_id=message.id,
                event_type="deadline_analysis_completed",
                actor="system",
                details=(
                    f"{len(created_deadlines)} möglicher Frist(en) gefunden, "
                    "alle mit Status 'unreviewed' - manuelle Prüfung erforderlich"
                ),
            )
        )
        db.commit()
        for deadline in created_deadlines:
            db.refresh(deadline)
        return created_deadlines

    def reanalyze_document(self, document: Document, db: Session, *, actor: str) -> ReanalysisResult:
        """Erneute Analyse auf ausdrueckliche Anforderung (ARCHITECTURE.md §73) - z. B. nach neuer Texterkennung.

        Bestehende Fristen werden NIE geaendert, geloescht oder zurueckgesetzt - auch keine verworfenen oder
        korrigierten. Abgleich ueber den stabilen Fund-Schluessel (`finding_keys`, §73.1), nicht ueber das Datum: ein
        korrigierter Vorschlag wird wiedererkannt, zwei Funde mit gleichem Datum bleiben zwei. Neue Funde entstehen als
        ungepruefte Vorschlaege. Weicht ein neuer Fund von einer bestaetigten Frist gleicher Art ab, traegt er einen sichtbaren
        Konflikthinweis; entschieden wird nichts. Bestaetigte Fristen, die nicht mehr gefunden werden, bleiben
        unveraendert und werden nur gezaehlt (Audit)."""
        result = ReanalysisResult()
        if not document.extracted_text or not document.extracted_text.strip():
            result.skipped_reason = "Kein extrahierter Text vorhanden"
        elif not document.matter_id:
            result.skipped_reason = "Dokument noch keiner Akte zugeordnet"
        if result.skipped_reason:
            db.add(AuditEvent(entity_type="Document", entity_id=document.id, event_type="deadline_reanalysis_skipped",
                              actor=actor, details=result.skipped_reason))
            db.commit()
            return result

        existing = (
            db.query(Deadline)
            .filter(Deadline.document_id == document.id)
            .order_by(Deadline.created_at.asc())
            .all()
        )
        existing_keys = _existing_keys(existing)
        known = set(existing_keys.values())
        confirmed = [d for d in existing if d.review_status == "confirmed"]

        candidates = self.extractor.extract(document.extracted_text)
        candidate_keys = _candidate_keys(candidates)
        for candidate, key in zip(candidates, candidate_keys):
            kind = _candidate_kind(candidate)
            if key in known:
                result.already_known += 1
                continue
            reasoning = candidate.reasoning
            differing = sorted(
                d.due_date.strftime("%d.%m.%Y") for d in confirmed
                if candidate.due_date is not None and d.due_date is not None
                and deadline_kind(d) == kind and d.due_date != candidate.due_date
            )
            if differing:
                result.conflicts += 1
                reasoning = (
                    f"{CONFLICT_MARKER} In diesem Dokument ist bereits eine Frist gleicher Art bestätigt "
                    f"({', '.join(differing)}); neuer Fund: {candidate.raw_date_text}. Ob es eine Änderung oder eine "
                    f"weitere Frist ist, muss geprüft werden - keine automatische Entscheidung. {reasoning}"
                )
            # review_status bewusst nicht gesetzt - Modell-Default "unreviewed".
            source_text = f"{candidate.raw_date_text} :: {candidate.source_text}"
            deadline = Deadline(
                matter_id=document.matter_id,
                document_id=document.id,
                source_text=source_text,
                due_date=candidate.due_date,
                confidence=candidate.confidence,
                reasoning=reasoning,
                finding_key=key,
                original_source_text=source_text,
            )
            db.add(deadline)
            result.created.append(deadline)

        # Nur Fristen mit Fund-Schluessel sind vergleichbar; bestaetigte Altdaten ohne Originalangabe zaehlen nicht.
        found = set(candidate_keys)
        result.confirmed_not_found = sum(
            1 for d in confirmed if d.id in existing_keys and existing_keys[d.id] not in found
        )
        db.add(
            AuditEvent(
                entity_type="Document",
                entity_id=document.id,
                event_type="deadline_reanalysis_completed",
                actor=actor,
                details=(
                    f"Erneute Fristenanalyse: {len(result.created)} neue(r) Vorschlag/Vorschlaege (ungeprueft), "
                    f"{result.already_known} bereits bekannt, {result.conflicts} Konflikt(e) mit bestaetigten "
                    f"Fristen, {result.confirmed_not_found} bestaetigte Frist(en) nicht mehr gefunden "
                    "(unveraendert). Bestehende Fristen wurden nicht veraendert."
                ),
            )
        )
        db.commit()
        for deadline in result.created:
            db.refresh(deadline)
        return result
