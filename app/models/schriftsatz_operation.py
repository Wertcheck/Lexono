"""Vorgang im Schriftsatz-Generator (§58): Idempotenz gegen F5, Doppelklick, parallele und wiederholte Requests.

Jede Generator-Seite traegt eine serverseitig ausgestellte, signierte und an den Nutzer gebundene Vorgangs-ID
(app/drafting/schriftsatz_operations.py). Der ERSTE Request mit dieser ID legt die Zeile an - die ID ist
PRIMAERSCHLUESSEL, genau ein Request kann sie anlegen (atomar durch die Datenbank, auch prozessuebergreifend). Jeder
weitere Request mit derselben ID (F5, erneutes Absenden, Doppelklick, Wiederholung nach einer verlorenen Antwort)
legt KEINE weitere Akte, keine Dokumente und keinen Entwurf an und loest keine KI-Anfrage aus, sondern zeigt das
gespeicherte Ergebnis. Bleibt ein Vorgang "processing" (Absturz), ist der Zustand unklar: nichts wird automatisch
wiederholt (fail-closed); ein neuer, bewusster Vorgang ist jederzeit moeglich (neue Seite = neue ID).

Keine Formularinhalte, Dateinamen oder Fehlermeldungen mit Inhalten - nur IDs und Zustaende.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

OPERATION_UPLOAD = "upload"
OPERATION_GENERATE = "generate"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class SchriftsatzOperation(TimestampMixin, Base):
    __tablename__ = "schriftsatz_operations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # Vorgangs-ID (uuid4 hex, aus dem signierten Token)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    matter_id: Mapped[str | None] = mapped_column(ForeignKey("matters.id", ondelete="SET NULL"), nullable=True)
    # JSON-Liste der in diesem Vorgang gespeicherten Dokument-IDs (fortlaufend geschrieben - auch bei Teilfehlern
    # nachvollziehbar, welche Dokumente bereits angelegt sind)
    document_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id", ondelete="SET NULL"), nullable=True)
    # HMAC (Session-Schluessel) ueber Art, Formularfelder und Datei-Hashes - erkennt eine BEWUSST geaenderte Anfrage
    # mit alter Vorgangs-ID (Zurueck-Button): sie wird nicht still auf das alte Ergebnis umgeleitet. Kein Klartext.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # §61 P2: Besitzer des laufenden Vorgangs (wie chat_upload_operations, app/chat/process_owner.py) und Plan mit
    # vorab vergebenen IDs (Schnellakte, je Datei ein Dokument). Ein hart abgebrochener UPLOAD wird bei nachweislich
    # beendetem Besitzer fortgesetzt; eine GENERIERUNG nie (Cloud-Anfrage evtl. erfolgt) - kontrollierter Fehlschlag.
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
