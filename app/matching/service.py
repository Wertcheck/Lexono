"""MatterAssignmentService – wendet ein `MatchResult` tatsächlich an.

Trennt bewusst "Kandidaten finden/bewerten" (matcher.py, reine Logik,
leicht testbar) von "Ergebnis anwenden + protokollieren" (hier, mit
Datenbankzugriff). Ermittelt außerdem `classification_ok` aus den mit der
Nachricht verknüpften Dokumenten (Kopplung an Prompt 08).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.matching.matcher import MatterMatchingService
from app.matching.schema import MatchResult
from app.models import AuditEvent, Message


class MatterAssignmentService:
    def __init__(
        self,
        matcher: MatterMatchingService,
        *,
        classification_low_confidence_threshold: float,
    ) -> None:
        self.matcher = matcher
        self.classification_low_confidence_threshold = (
            classification_low_confidence_threshold
        )

    def suggest_matter(self, message: Message, db: Session) -> MatchResult:
        """Wie `assign_matter`, aber REIN LESEND - wendet nichts an, legt
        kein `AuditEvent` an. Fuer die "Automatische Zuordnung (Vorschlag)"-
        Karte im Posteingang (app/web/router.py): zeigt dem Anwalt den
        aktuellen Kandidaten fuer eine bereits unzugeordnete Nachricht,
        OHNE dass das reine Ansehen der Seite bereits eine Zuordnung
        bewirkt - die tatsaechliche Anwendung geschieht ausschliesslich
        ueber eine explizite "Übernehmen"-Aktion (siehe
        app/web/router.py::accept_matter_suggestion)."""
        classification_ok = self._classification_is_sufficient(message)
        return self.matcher.match_message(message, db, classification_ok=classification_ok)

    def assign_matter(self, message: Message, db: Session) -> MatchResult:
        classification_ok = self._classification_is_sufficient(message)

        result = self.matcher.match_message(
            message, db, classification_ok=classification_ok
        )

        if result.decision == "auto_assigned":
            message.matter_id = result.matter_id
            for document in message.documents:
                document.matter_id = result.matter_id

        db.add(
            AuditEvent(
                entity_type="Message",
                entity_id=message.id,
                event_type=f"matter_match_{result.decision}",
                actor="system",
                details=result.reasoning,
            )
        )
        db.commit()
        db.refresh(message)
        return result

    def _classification_is_sufficient(self, message: Message) -> bool:
        """True, wenn kein zugehöriges Dokument eine niedrige/fehlende
        Klassifikationskonfidenz hat. Dokumente ganz ohne Klassifikation
        (z. B. weil OCR noch aussteht) gelten als NICHT ausreichend -
        sicherer Default statt stillschweigend zu ignorieren."""
        for document in message.documents:
            if document.classification_confidence is None:
                return False
            if (
                document.classification_confidence
                < self.classification_low_confidence_threshold
            ):
                return False
        return True
