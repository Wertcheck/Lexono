"""Dokument-Lifecycle-Aktionen (Loeschen/Wiederherstellen) - 20.09., Owner-
Direktive "WORKSTREAM A — DOKUMENTE LOESCHBAR".

WICHTIG (Architekturentscheidung, per CLAUDE.md Punkt 9 selbst begruendet
und dokumentiert - siehe DECISIONS.md): SOFT-DELETE, kein Hard-Delete. Ein
Dokument ist bei einer Kanzlei potenziell eine aufbewahrungspflichtige
Mandantenunterlage (bereits 18.09. als bewusste Zurueckhaltung dokumentiert,
siehe app/web/document_actions_router.py-Moduldocstring: "ein Dokument
endgueltig zu entfernen ist... eine Aufbewahrungs-/Compliance-Frage").
Identisches Prinzip wie die bereits bestehende Client-Archivierung
(app/clients/service.py::archive_client/delete_client - dort Status-String
+ durch Akten-Verknuepfung geschuetzter Hard-Delete), hier als Zeitstempel
(`Document.deleted_at`) statt Status-String, da Document kein weiteres
Statusfeld hat: NULL = aktiv, gesetzt = geloescht aus Anwendersicht.

Ein geloeschtes Dokument:
- verschwindet aus allen Listen (Akte-Dokumente-Tab, Mandant-Dokumente-Tab)
- ist ueber Viewer/Download NICHT mehr erreichbar (404, identisches
  Verhalten wie ein tatsaechlich geloeschter Datensatz)
- bleibt aber als Datensatz UND als physische Originaldatei vollstaendig
  erhalten und ueber `restore_document` wiederherstellbar.

Die physische Datei (`document.file_path`) wird beim Loeschen NIE
angefasst - nur die DB-Sichtbarkeit aendert sich."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AuditEvent, Document


def soft_delete_document(db: Session, document: Document, *, actor: str) -> Document:
    document.deleted_at = datetime.now(timezone.utc)
    db.add(
        AuditEvent(
            entity_type="Document",
            entity_id=document.id,
            event_type="document_deleted",
            actor=actor,
            details=(
                f"Dokument geloescht (Originaldatei bleibt erhalten, "
                f"wiederherstellbar): {document.original_filename or document.id}"
            ),
        )
    )
    db.commit()
    return document


def restore_document(db: Session, document: Document, *, actor: str) -> Document:
    document.deleted_at = None
    db.add(
        AuditEvent(
            entity_type="Document",
            entity_id=document.id,
            event_type="document_restored",
            actor=actor,
            details=f"Dokument wiederhergestellt: {document.original_filename or document.id}",
        )
    )
    db.commit()
    return document
