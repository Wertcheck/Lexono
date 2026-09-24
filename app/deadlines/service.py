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

from sqlalchemy.orm import Session

from app.deadlines.extractor import DeadlineExtractor
from app.models import AuditEvent, Deadline, Document, Message


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

        for candidate in extracted:
            # review_status wird bewusst NICHT gesetzt - Modell-Default
            # "unreviewed" greift, siehe Moduldocstring.
            deadline = Deadline(
                matter_id=document.matter_id,
                document_id=document.id,
                source_text=f"{candidate.raw_date_text} :: {candidate.source_text}",
                due_date=candidate.due_date,
                confidence=candidate.confidence,
                reasoning=candidate.reasoning,
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

        for candidate in extracted:
            # review_status wird bewusst NICHT gesetzt - Modell-Default
            # "unreviewed" greift, siehe Moduldocstring.
            deadline = Deadline(
                matter_id=message.matter_id,
                message_id=message.id,
                source_text=f"{candidate.raw_date_text} :: {candidate.source_text}",
                due_date=candidate.due_date,
                confidence=candidate.confidence,
                reasoning=candidate.reasoning,
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
