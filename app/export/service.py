"""MatterExportService – strukturierter Export EINER Akte (Prompt 35).

Anders als `BackupService` (vollständige, technische Rohsicherung des
gesamten Systems) exportiert dieser Service GEZIELT alle Daten EINER
einzelnen Akte in ein nachvollziehbares, menschenlesbares Format -
relevant für:
- DSGVO Art. 15 (Auskunftsrecht) / Art. 20 (Datenübertragbarkeit): ein
  Mandant kann verlangen, alle über ihn gespeicherten Daten zu erhalten.
- Aktenschließung/Archivierung: vollständige Dokumentation eines
  abgeschlossenen Falls in einem einzigen, portablen Archiv.

Erzeugt ein ZIP mit:
- `manifest.json`: strukturierte Daten (Akte, Mandant, Nachrichten,
  Entwürfe inkl. aller Versionen, Anmerkungen, Fristen, Postausgang-
  Status, Audit-Trail) - menschen- UND maschinenlesbar.
- `documents/`: Kopien der Original-Dokumentdateien dieser Akte.

WICHTIG: enthält UNPSEUDONYMISIERTE Mandanteninhalte (das ist der Zweck -
ein Auskunftsersuchen verlangt die echten Daten, keine Platzhalter) -
genauso schützenswert wie die Produktionsdatenbank, nur auf eine Akte
begrenzt statt das gesamte System.
"""

from __future__ import annotations

import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.deadlines.review import deadline_kind, kind_label
from app.models import (
    AttorneyInstruction,
    AuditEvent,
    Deadline,
    Document,
    Draft,
    Matter,
    Message,
    OutboxEntry,
)


class MatterNotFoundError(Exception):
    pass


def json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Nicht serialisierbar: {type(value)}")


class MatterExportService:
    def export_matter(self, matter_id: str, db: Session, output_dir: str | Path) -> Path:
        matter = db.get(Matter, matter_id)
        if matter is None:
            raise MatterNotFoundError(f"Akte {matter_id} nicht gefunden")

        manifest = self.build_manifest(matter, db)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_reference = (matter.reference_number or matter.id).replace("/", "-")
        archive_path = output_dir / f"export_{safe_reference}_{timestamp}.zip"

        documents = db.query(Document).filter_by(matter_id=matter_id).all()

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, default=json_default),
            )
            for document in documents:
                source_path = Path(document.file_path)
                if source_path.exists():
                    arcname = f"documents/{document.id}_{source_path.name}"
                    archive.write(source_path, arcname=arcname)
            archive.writestr(
                "EXPORT_INFO.txt",
                (
                    f"Lexono Aktenexport - Akte: {matter.title}\n"
                    f"Erstellt: {timestamp}\n"
                    "Enthaelt vollstaendige, unpseudonymisierte Akteninhalte - "
                    "wie die Produktionsdatenbank selbst zu behandeln.\n"
                ),
            )

        return archive_path

    def build_manifest(self, matter: Matter, db: Session) -> dict[str, Any]:
        """Öffentlich (nicht mehr `_build_manifest`, 20.08.) - wird jetzt
        zusätzlich von `ClientExportService` (app/clients/export_service.py)
        wiederverwendet, um den Mandanten-Datenauszug pro Akte auf exakt
        demselben, bereits für DSGVO Art. 15/20 konzipierten Format
        aufzubauen statt einer zweiten, abweichenden Implementierung."""
        messages = db.query(Message).filter_by(matter_id=matter.id).all()
        documents = db.query(Document).filter_by(matter_id=matter.id).all()
        drafts = (
            db.query(Draft)
            .filter_by(matter_id=matter.id)
            .order_by(Draft.version.asc())
            .all()
        )
        deadlines = db.query(Deadline).filter_by(matter_id=matter.id).all()
        instructions = db.query(AttorneyInstruction).filter_by(matter_id=matter.id).all()
        outbox_entries = db.query(OutboxEntry).filter_by(matter_id=matter.id).all()
        audit_events = (
            db.query(AuditEvent)
            .filter(AuditEvent.entity_id == matter.id)
            .order_by(AuditEvent.created_at.asc())
            .all()
        )

        return {
            "matter": {
                "id": matter.id,
                "title": matter.title,
                "reference_number": matter.reference_number,
                "practice_area": matter.practice_area,
                "status": matter.status,
                "client_name": matter.client.name if matter.client else None,
                "created_at": matter.created_at,
            },
            "messages": [
                {
                    "id": m.id,
                    "direction": m.direction,
                    "sender": m.sender,
                    "recipient": m.recipient,
                    "subject": m.subject,
                    "body_text": m.body_text,
                    "created_at": m.created_at,
                }
                for m in messages
            ],
            "documents": [
                {
                    "id": d.id,
                    "original_filename": d.original_filename,
                    "classified_type": d.classified_type,
                    "extracted_text": d.extracted_text,
                    "created_at": d.created_at,
                }
                for d in documents
            ],
            "drafts": [
                {
                    "id": d.id,
                    "version": d.version,
                    "status": d.status,
                    "content": d.content,
                    "previous_version_id": d.previous_version_id,
                    "created_at": d.created_at,
                }
                for d in drafts
            ],
            "deadlines": [
                {
                    "id": dl.id,
                    "source_text": dl.source_text,
                    "due_date": dl.due_date,
                    "review_status": dl.review_status,
                    # §74: Art (frist/termin/fristbeginn/fristdauer/beendigung/manuell) - ein Beendigungstermin ist
                    # im Export als solcher erkennbar, nicht als Frist
                    "kind": deadline_kind(dl),
                    # §76: nachvollziehbar statt nur Code - Bezeichnung der Art, Begruendung (inkl. Unsicherheit, bei einem
                    # Beendigungstermin "kein Fristende"/keine Wirksamkeitsaussage) und Konfidenz des Vorschlags
                    "kind_label": kind_label(dl),
                    "reasoning": dl.reasoning,
                    "confidence": dl.confidence,
                }
                for dl in deadlines
            ],
            "attorney_instructions": [
                {
                    "id": i.id,
                    "instruction_text": i.instruction_text,
                    "status": i.status,
                    "actor": i.actor,
                    "created_at": i.created_at,
                }
                for i in instructions
            ],
            "outbox_entries": [
                {
                    "id": o.id,
                    "status": o.status,
                    "sent_at": o.sent_at,
                    "sent_by": o.sent_by,
                }
                for o in outbox_entries
            ],
            "audit_trail": [
                {
                    "event_type": e.event_type,
                    "actor": e.actor,
                    "details": e.details,
                    "created_at": e.created_at,
                }
                for e in audit_events
            ],
        }
