"""DocumentProcessingService – Orchestrierung von Textextraktion und OCR.

Ablauf pro `Document` (Konzept §3, Schritt 3 "Extraktion"):
1. Versuche direkte Textextraktion (PDF/DOCX/TXT).
2. Falls kein/zu wenig Text gefunden wurde UND `settings.ocr_enabled=True`
   ist: OCR ausführen.
3. Falls kein Text gefunden wurde UND OCR deaktiviert ist: Status bleibt
   "pending" (wartet auf manuelle Aktivierung/spätere Verarbeitung) -
   niemals stillschweigend als erledigt markieren.
4. `extracted_text`/`ocr_status` werden aktualisiert - `file_path`
   (Original) bleibt unverändert (siehe app/models/document.py).

Enthält bewusst KEINE juristische Interpretation - reine technische
Extraktion.

Seit Prompt 31: ein OCR-Fehlschlag wird zusätzlich im Fehler-/Retry-
System (app/errors/) protokolliert - vorher blieb `ocr_status="failed"`
ein Endzustand ohne jeden Weg, es erneut zu versuchen, außer den
Datensatz manuell zu löschen und neu anzulegen. `RetryService` wird
bewusst als DEFAULT-Parameter injiziert (nicht hart codiert), damit
Aufrufer (z. B. Tests) eine eigene Instanz übergeben können.

Seit §64: nach ERFOLGREICH abgeschlossener Extraktion/OCR (`ocr_status`
"not_needed" oder "done" - also tatsächlich verwertbarer Text vorhanden,
NICHT bei "failed"/"unsupported_format"/"pending") UND bereits erfolgter
Aktenzuordnung (`document.matter_id` gesetzt) wird automatisch die
bestehende, regelbasierte `DeadlineAnalysisService.analyze_document`
aufgerufen (ebenfalls per DEFAULT-Parameter injiziert, gleiches Muster wie
`retry_service`). Keine LLM-Fristenerkennung, keine Änderung der Privacy-/
Claude-Pipeline - reine Verkettung zweier bereits bestehender, unabhängig
getesteter Services. `analyze_document` ist selbst idempotent (siehe
app/deadlines/service.py) - ein erneuter `process_document`-Aufruf für
dasselbe Dokument (z. B. über den Retry-Pfad) erzeugt keine doppelten
`Deadline`-Datensätze. Fehlt `matter_id` (noch keine Aktenzuordnung
erfolgt), wird `analyze_document` bewusst gar nicht erst aufgerufen -
würde ohnehin nur no-op + Audit-Rauschen erzeugen (siehe dortiger
eigener Guard).

ECHTER FUND (14.09., Reassess nach dem SGB-/Mail-Ingestion-Zyklus):
`ClassificationService` (Prompt 08) war GENAU wie `DeadlineAnalysisService`
vollstaendig implementiert und isoliert getestet, wurde aber von KEINEM
der drei Upload-Pfade (Mail/Chat/Schriftsatz) je aufgerufen -
`Document.classification_confidence` blieb dadurch projektweit IMMER
`None`, wodurch `MatterAssignmentService._classification_is_sufficient`
jede Nachricht MIT Anhang als "nicht ausreichend klassifiziert" behandelte
und eine automatische Zuordnung dafuer strukturell nie moeglich war. Fix:
`classify_document` wird jetzt - genau wie `analyze_document` - hier
ZENTRAL EINMAL nach erfolgreicher Extraktion aufgerufen (gilt damit
automatisch fuer alle drei Upload-Pfade). Anders als die Fristenerkennung
ist `matter_id` dafuer NICHT erforderlich (Klassifikation bewertet nur
den Dokumentinhalt) - laeuft daher auch fuer noch unzugeordnete
Dokumente, genau der Fall, der fuer `_classification_is_sufficient`
relevant ist, BEVOR eine Zuordnung ueberhaupt entschieden wird.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.classification.classifier import PlaceholderDocumentClassifier
from app.classification.service import ClassificationService
from app.deadlines.extractor import PlaceholderDeadlineExtractor
from app.deadlines.service import DeadlineAnalysisService
from app.documents.extraction import extract_text
from app.documents.ocr import OcrError, configure_tesseract, run_ocr
from app.errors.service import RetryService
from app.models import AuditEvent, Document

_TEXT_READY_OCR_STATUSES = ("not_needed", "done")

# Identisch mit `Settings.classification_low_confidence_threshold`s
# Standardwert (app/config/settings.py) - hier bewusst dupliziert statt
# importiert, da diese Klasse (wie `RetryService`/`DeadlineAnalysisService`)
# keine Abhaengigkeit auf das Settings-Objekt haben soll, nur auf
# explizit uebergebene Werte (siehe bestehende Konstruktor-Parameter).
_DEFAULT_CLASSIFICATION_LOW_CONFIDENCE_THRESHOLD = 0.6


class DocumentProcessingService:
    def __init__(
        self,
        *,
        ocr_enabled: bool,
        ocr_languages: str = "deu+eng",
        min_extracted_text_length: int = 20,
        tesseract_cmd: str | None = None,
        retry_service: RetryService | None = None,
        deadline_service: DeadlineAnalysisService | None = None,
        classification_service: ClassificationService | None = None,
        classification_low_confidence_threshold: float = (
            _DEFAULT_CLASSIFICATION_LOW_CONFIDENCE_THRESHOLD
        ),
    ) -> None:
        self.ocr_enabled = ocr_enabled
        self.ocr_languages = ocr_languages
        self.min_extracted_text_length = min_extracted_text_length
        self.retry_service = retry_service or RetryService()
        self.deadline_service = deadline_service or DeadlineAnalysisService(
            PlaceholderDeadlineExtractor()
        )
        self.classification_service = classification_service or ClassificationService(
            PlaceholderDocumentClassifier(),
            low_confidence_threshold=classification_low_confidence_threshold,
        )
        configure_tesseract(tesseract_cmd)

    def process_document(
        self, document: Document, db: Session, *, actor: str = "system"
    ) -> Document:
        """Verarbeitet ein einzelnes Document: Extraktion + ggf. OCR.

        Aktualisiert `document.extracted_text` und `document.ocr_status`
        in-place, schreibt ein begleitendes `AuditEvent` und committet die
        Änderung. Gibt das aktualisierte Document zurück.

        `actor` (Prompt 31): erlaubt, einen manuell ausgelösten Retry
        (z. B. über das Dashboard) im Audit-Log dem tatsächlich
        handelnden Nutzer zuzuordnen, statt pauschal "system" - Standard
        bleibt "system" für die automatische Erstverarbeitung.
        """
        path = Path(document.file_path)
        try:
            result = extract_text(path, min_text_length=self.min_extracted_text_length)
        except Exception as exc:  # noqa: BLE001 - kontrolliert ins Fehler-/Retry-System überführen
            # SICHERHEITSKRITISCH + ROBUSTHEIT (Prompt 31, gefunden bei der
            # Absicherung des Fehler-/Retry-Systems): `extract_text` selbst
            # war NICHT gegen eine fehlende/unlesbare/beschädigte Datei
            # abgesichert - ein `FileNotFoundError` o. Ä. hätte die gesamte
            # Dokumentverarbeitung unkontrolliert abstürzen lassen, statt
            # dem gerade gebauten Fehler-/Retry-System übergeben zu werden.
            # Wie beim OCR-Fehlerpfad: NUR der Exception-TYP, niemals die
            # Original-Nachricht (kann den Dateipfad/-namen enthalten).
            document.ocr_status = "failed"
            event_type = "document_ocr_failed"
            details = f"Textextraktion fehlgeschlagen: {type(exc).__name__}"
            self.retry_service.record_failure(
                db,
                entity_type="Document",
                entity_id=document.id,
                operation="ocr",
                error_category="transient",
                error_message=details,
                actor=actor,
            )
            db.add(
                AuditEvent(
                    entity_type="Document",
                    entity_id=document.id,
                    event_type=event_type,
                    actor=actor,
                    details=details,
                )
            )
            db.commit()
            db.refresh(document)
            return document

        if result.unsupported_format:
            document.ocr_status = "unsupported_format"
            event_type = "document_format_unsupported"
            details = f"Dateiformat nicht unterstützt: {path.suffix}"
        elif not result.needs_ocr:
            document.extracted_text = result.text
            document.ocr_status = "not_needed"
            event_type = "document_text_extracted"
            details = "Text direkt extrahiert, kein OCR erforderlich"
            self.retry_service.record_success(
                db, entity_type="Document", entity_id=document.id, operation="ocr", actor=actor
            )
        elif not self.ocr_enabled:
            document.ocr_status = "pending"
            event_type = "document_ocr_pending"
            details = "OCR erforderlich, aber OCR ist deaktiviert (OCR_ENABLED=false)"
        else:
            try:
                ocr_text = run_ocr(path, languages=self.ocr_languages)
            except OcrError as exc:
                document.ocr_status = "failed"
                event_type = "document_ocr_failed"
                # Bewusst NUR die technische Fehlermeldung - niemals
                # Dokumentinhalt (siehe app/errors/models.py Grundregel).
                details = str(exc)
                self.retry_service.record_failure(
                    db,
                    entity_type="Document",
                    entity_id=document.id,
                    operation="ocr",
                    # OCR-Fehlschläge sind meist Umgebungsprobleme
                    # (Tesseract nicht erreichbar, temporäre Ressourcen-
                    # Engpässe) - als "transient" eingestuft, damit ein
                    # automatischer Wiederholungsversuch sinnvoll ist.
                    error_category="transient",
                    error_message=details,
                    actor=actor,
                )
            else:
                document.extracted_text = ocr_text
                document.ocr_status = "done"
                event_type = "document_ocr_completed"
                details = f"OCR erfolgreich ({self.ocr_languages})"
                self.retry_service.record_success(
                    db,
                    entity_type="Document",
                    entity_id=document.id,
                    operation="ocr",
                    actor=actor,
                )

        db.add(
            AuditEvent(
                entity_type="Document",
                entity_id=document.id,
                event_type=event_type,
                actor=actor,
                details=details,
            )
        )
        db.commit()
        db.refresh(document)

        # Automatische Klassifikation NUR nach tatsaechlich erfolgreicher
        # Extraktion/OCR (siehe Moduldocstring, ECHTER FUND 14.09.) - im
        # Unterschied zur Fristenerkennung unten bewusst OHNE
        # matter_id-Bedingung, da Klassifikation nur den Dokumentinhalt
        # bewertet und genau der Fall (noch unzugeordnetes Dokument mit
        # Anhang) fuer eine spaetere automatische Aktenzuordnung relevant
        # ist.
        if document.ocr_status in _TEXT_READY_OCR_STATUSES:
            self.classification_service.classify_document(document, db)

        # Automatische Fristenerkennung NUR nach tatsaechlich erfolgreicher
        # Extraktion/OCR und NUR mit bereits erfolgter Aktenzuordnung (siehe
        # Moduldocstring) - kein Aufruf bei "failed"/"unsupported_format"/
        # "pending" oder ohne matter_id.
        if document.ocr_status in _TEXT_READY_OCR_STATUSES and document.matter_id:
            self.deadline_service.analyze_document(document, db)

        return document
