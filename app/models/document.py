"""Document – Dokument (Scan, PDF, Office-Datei, E-Mail-Anhang ...).

Wichtig (Konzept §7 / Prompt 06): Original, extrahierter Text und
technische Metadaten sind strikt getrennte Felder. `file_path` verweist auf
das Original im Dateisystem - die Datenbank ist nicht die einzige Wahrheit
fuer den Dateiinhalt, sondern fuehrt Beziehungen und Status.
`extracted_text`/`ocr_status` werden hier bereits als Schema vorgesehen,
auch wenn die eigentliche OCR-/Extraktionslogik erst in Prompt 06 entsteht.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    matter_id: Mapped[str | None] = mapped_column(
        ForeignKey("matters.id"), nullable=True, index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True, index=True
    )

    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Pfad im Dateisystem (Intake-/Ablagebereich) - siehe Aktenstruktur,
    # Konzept §7. Die Datei selbst wird nicht in der Datenbank gespeichert.
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Getrennt vom Original: extrahierter/Ocr-Text, erst ab Prompt 06 befuellt.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # not_needed / pending / done / failed / unsupported_format - bewusst
    # freier String, kein DB-Enum, da die endgueltige Zustandslogik erst in
    # Prompt 06 vollstaendig entstand und sich noch erweitern kann.
    ocr_status: Mapped[str] = mapped_column(
        String(32), default="not_needed", nullable=False
    )

    # --- Klassifikation (Prompt 08) ---
    # Bewusst als eigene, nullable Spalten statt eines generischen JSON-
    # Blobs: erlaubt einfaches Filtern/Suchen (z. B. "alle unklassifizierten
    # Dokumente"). `classification_result_json` haelt zusaetzlich das volle
    # strukturierte Ergebnis fuer Nachvollziehbarkeit/Audit.
    classified_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    classification_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    classification_action_required: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    classification_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Loeschen (20.09., Owner-Direktive "WORKSTREAM A — DOKUMENTE LOESCHBAR"):
    # bewusst SOFT-DELETE statt Hard-Delete - ein Dokument ist bei einer
    # Kanzlei potenziell eine aufbewahrungspflichtige Mandantenunterlage
    # (bereits als bewusste Zurueckhaltung dokumentiert, siehe
    # app/web/document_actions_router.py-Moduldocstring vom 18.09.: "ein
    # Dokument endgueltig zu entfernen ist... eine Aufbewahrungs-/
    # Compliance-Frage"). NULL = aktiv (Standardfall, alle bestehenden
    # Zeilen). Gesetzt = "geloescht" aus Anwendersicht (verschwindet aus
    # allen Listen/Viewer/Download), aber wiederherstellbar - identisches
    # Prinzip wie `Client.status` (app/clients/service.py::archive_client),
    # nur als Zeitstempel statt Status-String, da Document kein weiteres
    # Statusfeld hat. Die physische Originaldatei bleibt beim Loeschen
    # unangetastet (nur DB-seitig ausgeblendet) - siehe
    # app/documents/lifecycle.py fuer die Schreiblogik.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    matter: Mapped["Matter | None"] = relationship(back_populates="documents")
    message: Mapped["Message | None"] = relationship(back_populates="documents")
