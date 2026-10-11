"""Verarbeitungsstand eines Dokuments aus Sicht einer KI-Anfrage (13.10.2026).

ECHTER FUND (installierter Build, OCR_ENABLED=false): ein gescanntes PDF blieb `ocr_status="pending"` ohne Text. Die
lokale Kontextvorbereitung uebersprang es stillschweigend, die Chat-Frage ging trotzdem an die Cloud und wurde ohne
jeden Hinweis beantwortet, als waere das Dokument ausgewertet worden.

Dieses Modul leitet aus den BESTEHENDEN Feldern (`ocr_status`, `extracted_text`) und dem Verarbeitungs-Audit einen
Zustand ab - kein neues Datenmodell. Nur `READY` erlaubt, Dokumentinhalt als ausgewertet zu behandeln. Die Pruefung
liest immer frisch aus der Datenbank (serverseitig, unmittelbar vor der Verarbeitung); eine Anzeige im Browser ist
keine Sicherheitskontrolle.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import AuditEvent, Document

READY = "ready"  # Text erfolgreich extrahiert bzw. per OCR erkannt
PROCESSING = "processing"  # Verarbeitung noch nicht abgeschlossen
NO_TEXT = "no_text"  # verarbeitet, aber kein verwertbarer Text
OCR_DISABLED = "ocr_disabled"  # Texterkennung noetig, war bei der Verarbeitung deaktiviert
FAILED = "failed"  # Extraktion/Texterkennung fehlgeschlagen
UNSUPPORTED = "unsupported"  # Dateiformat nicht verarbeitbar
DELETED = "deleted"  # Dokument wurde inzwischen geloescht
OTHER_MATTER = "other_matter"  # Unterhaltung wurde einer anderen Akte zugeordnet (Aktenisolation)
NOT_INCLUDED = "not_included"  # lesbar, aber nicht in den lokal vorbereiteten Kontext uebernommen

#: Audit-Ereignisse, die `DocumentProcessingService.process_document` am ENDE jeder Verarbeitung schreibt. Fehlt jedes
#: davon, ist die Verarbeitung noch nicht abgeschlossen (oder wurde hart abgebrochen) - nie "fertig ohne Text".
_PROCESSING_FINISHED_EVENTS = (
    "document_text_extracted",
    "document_ocr_pending",
    "document_ocr_completed",
    "document_ocr_failed",
    "document_format_unsupported",
)


@dataclass(frozen=True)
class DocumentReadiness:
    document_id: str
    filename: str
    state: str
    #: Nur bei FAILED: technische Fehlerangabe aus dem Audit (enthaelt per Konstruktion nie Pfade oder Inhalte, siehe
    #: app/documents/service.py und app/documents/ocr.py).
    error: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == READY


def _latest_event(db: Session, document_id: str, event_types: Sequence[str]) -> AuditEvent | None:
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "Document",
            AuditEvent.entity_id == document_id,
            AuditEvent.event_type.in_(tuple(event_types)),
        )
        .order_by(AuditEvent.created_at.desc())
        .first()
    )


def assess_document(db: Session, document: Document) -> DocumentReadiness:
    filename = document.original_filename or "Dokument"
    status = document.ocr_status
    has_text = bool((document.extracted_text or "").strip())
    if document.deleted_at is not None:
        return DocumentReadiness(document.id, filename, DELETED)
    if status in ("not_needed", "done"):
        if has_text:
            return DocumentReadiness(document.id, filename, READY)
        if status == "not_needed" and _latest_event(db, document.id, _PROCESSING_FINISHED_EVENTS) is None:
            # Standardwert der Spalte ist "not_needed": ohne Abschluss-Ereignis laeuft die Verarbeitung noch.
            return DocumentReadiness(document.id, filename, PROCESSING)
        return DocumentReadiness(document.id, filename, NO_TEXT)
    if status == "pending":
        return DocumentReadiness(document.id, filename, OCR_DISABLED)
    if status == "failed":
        event = _latest_event(db, document.id, ("document_ocr_failed",))
        return DocumentReadiness(document.id, filename, FAILED, error=event.details if event else None)
    if status == "unsupported_format":
        return DocumentReadiness(document.id, filename, UNSUPPORTED)
    # Unbekannter Zustand: fail-closed, nie als ausgewertet behandeln.
    return DocumentReadiness(document.id, filename, PROCESSING)


def assess_documents(
    db: Session, document_ids: Iterable[str], *, matter_id: str | None = None
) -> list[DocumentReadiness]:
    """Frisch aus der Datenbank (kein Objekt aus einer frueheren Abfrage/Identity Map): eine Statusaenderung durch
    einen parallelen Request (Neuverarbeitung, Loeschen) wird damit unmittelbar vor der Verarbeitung sichtbar."""
    ids = list(dict.fromkeys(document_ids))
    if not ids:
        return []
    documents = {d.id: d for d in db.query(Document).filter(Document.id.in_(ids)).populate_existing().all()}
    result = []
    for document_id in ids:
        document = documents.get(document_id)
        if document is None:
            result.append(DocumentReadiness(document_id, "Dokument", DELETED))
        elif matter_id is not None and document.matter_id != matter_id:
            result.append(DocumentReadiness(document_id, document.original_filename or "Dokument", OTHER_MATTER))
        else:
            result.append(assess_document(db, document))
    return result


def _state_sentence(item: DocumentReadiness, *, ocr_enabled: bool) -> str:
    name = f"„{item.filename}“"
    if item.state == PROCESSING:
        return f"{name} wird noch verarbeitet; der Inhalt liegt noch nicht vor."
    if item.state == NO_TEXT:
        return f"{name} wurde verarbeitet, enthält aber keinen verwertbaren Text (z. B. leere Seiten oder nur Bilder)."
    if item.state == OCR_DISABLED:
        if ocr_enabled:
            return (
                f"{name} ist ein Scan bzw. Bild ohne Textebene. Beim Hochladen war die Texterkennung (OCR) "
                "deaktiviert, der Inhalt wurde daher nicht gelesen. Die Texterkennung ist inzwischen aktiv: bitte das "
                "Dokument erneut hochladen."
            )
        return (
            f"{name} ist ein Scan bzw. Bild ohne (vollständige) Textebene. Die Texterkennung (OCR) ist in dieser "
            "Installation deaktiviert, der Inhalt wurde daher nicht gelesen. Möglichkeiten: eine Fassung mit "
            "Textebene (PDF mit Text oder DOCX) hochladen, oder die Administration bitten, die Texterkennung zu "
            "aktivieren, und das Dokument danach erneut hochladen."
        )
    if item.state == FAILED:
        detail = f" ({item.error})" if item.error else ""
        return f"Bei der Verarbeitung von {name} ist ein Fehler aufgetreten{detail}; der Inhalt liegt nicht vor."
    if item.state == UNSUPPORTED:
        return f"{name} hat ein Dateiformat, das nicht ausgelesen werden kann."
    if item.state == DELETED:
        return f"{name} wurde inzwischen gelöscht."
    if item.state == OTHER_MATTER:
        return f"{name} gehört zu einer anderen Akte und wird in dieser Unterhaltung nicht mehr verwendet."
    if item.state == NOT_INCLUDED:
        return f"{name} konnte nicht in den vorbereiteten Kontext übernommen werden; bitte erneut senden."
    return f"{name} ist nicht verarbeitet."


def not_ready_notice(items: Sequence[DocumentReadiness], *, ocr_enabled: bool) -> str:
    """Lokale Antwort statt eines Cloud-Aufrufs: welches Dokument in welchem Zustand ist und wie es weitergeht. Behauptet
    nie eine Auswertung; die Anfrage wurde NICHT an die Cloud-KI gesendet."""
    blocked = [item for item in items if not item.ready]
    ready = [item for item in items if item.ready]
    lines = [
        "Die Anfrage bezieht sich auf ein Dokument, dessen Inhalt noch nicht ausgewertet werden kann. Sie wurde "
        "deshalb nicht an die Cloud-KI gesendet, und es wurde nichts ausgewertet.",
        "",
    ]
    lines += [f"- {_state_sentence(item, ocr_enabled=ocr_enabled)}" for item in blocked]
    if ready:
        names = ", ".join(f"„{item.filename}“" for item in ready)
        lines += ["", f"Bereits lesbar: {names}. Eine Teilauswertung wird nicht als vollständige Auswertung ausgegeben."]
    if any(item.state == PROCESSING for item in blocked):
        lines += ["", "Bitte die Frage erneut senden, sobald die Verarbeitung abgeschlossen ist."]
    lines += ["", "Fragen ohne Bezug auf dieses Dokument können Sie weiterhin stellen."]
    return "\n".join(lines)


def unevaluated_note(items: Sequence[DocumentReadiness]) -> str:
    """Hinweis unter einer Antwort, wenn Dokumente der Akte (nicht dieser Unterhaltung) nicht ausgewertet wurden."""
    names = ", ".join(f"„{item.filename}“" for item in items)
    return f"Nicht ausgewertet, da ohne verarbeiteten Text: {names}."
