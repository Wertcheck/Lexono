"""AuditLogService – lesender Zugriff auf das bereits bestehende
Audit-Log (`AuditEvent`, Prompt 04).

Wichtigste Methode: `list_events_for_matter` - da `AuditEvent` generisch
per `entity_type`/`entity_id` funktioniert (nicht direkt per `matter_id`),
muss der Service dafuer erst alle zu einer Akte gehoerenden Entitaeten
(Dokumente, Nachrichten, Fristen, Entwuerfe, Aufgaben, Workflow-Laeufe)
ermitteln und dann deren Ereignisse zusammenfuehren. Rein lesend - erzeugt
selbst niemals neue Audit-Events.

WICHTIG (Aktenisolation): `list_events_for_matter` fragt ausschliesslich
Entitaeten ab, die tatsaechlich zu der uebergebenen `matter_id` gehoeren -
exakt dasselbe Muster wie ueberall sonst im Projekt (z. B.
`search_within_matter`, `PromptContextBuilder`).

Bewusst NICHT eingeschlossen: `KnowledgeItem`, `Source`, `Policy` - das
sind kanzleiweite, nicht aktenbezogene Ressourcen (siehe Konzept §5/§6),
ihre Audit-Events gehoeren folgerichtig nicht in eine Akten-Historie.
"""

from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import (
    AttorneyInstruction,
    AuditEvent,
    ChatConversation,
    Deadline,
    Document,
    Draft,
    GeneratedDocument,
    Message,
    Note,
    OutboxEntry,
    Party,
    Task,
    WorkflowRun,
)

# (SQLAlchemy-Modell, entity_type-String) - der entity_type-String muss
# exakt dem entsprechen, was die jeweiligen Services beim Schreiben von
# AuditEvents verwenden (z. B. entity_type="Document").
_MATTER_SCOPED_MODELS: tuple[tuple[type, str], ...] = (
    (Document, "Document"),
    (Message, "Message"),
    (Deadline, "Deadline"),
    (Draft, "Draft"),
    (Task, "Task"),
    (WorkflowRun, "WorkflowRun"),
    # Ergaenzt Prompt 24: AttorneyInstruction (Prompt 23) hatte bislang
    # KEINEN Eintrag hier - ihre Audit-Events (attorney_instruction_created/
    # _applied) waren dadurch bei einer aktenweiten Abfrage unsichtbar,
    # obwohl das Modell bereits matter_id trägt. Echte Luecke, jetzt
    # geschlossen.
    (AttorneyInstruction, "AttorneyInstruction"),
    # Ergaenzt 18.09. (Owner-Direktive "WEITERARBEITEN" Fortsetzung, beim
    # Anbinden einer Akte-Verlaufsansicht gefunden): `Party` (17.09. dieser
    # Sitzung angelegt, siehe app/web/parties_router.py) schreibt bereits
    # echte AuditEvents (party_added/party_removed) und traegt laengst
    # `matter_id` - fehlte hier aber, dieselbe Art Luecke wie oben bei
    # AttorneyInstruction. Ohne diesen Eintrag waeren Beteiligte-Aenderungen
    # bei einer aktenweiten Verlaufsabfrage unsichtbar geblieben.
    (Party, "Party"),
    # Ergaenzt 19.09. (UI/UX-Referenzabgleich, neuer "Notizen"-Tab, siehe
    # app/web/note_actions_router.py): dieselbe Art Luecke wie bei Party
    # oben - direkt beim Anlegen mitbehoben statt sie erneut entstehen zu
    # lassen.
    (Note, "Note"),
    # Ergaenzt 20.09. (Overnight-Autonomielauf, beim Live-Verifizieren des
    # Postausgang-Workflows gefunden): EXAKT dieselbe Art Luecke wie bei
    # Party/AttorneyInstruction/Note oben - `OutboxEntry` schreibt bereits
    # echte AuditEvents (draft_added_to_outbox/draft_marked_sent, siehe
    # app/outbox/service.py) und traegt bereits eine direkte `matter_id`-
    # Spalte (extra dafuer angelegt, siehe dortiger Modell-Kommentar
    # "ermöglicht Aktenisolations-Abfragen ohne Join") - fehlte hier aber
    # ebenfalls. Freigabe/Versand-Bestaetigung eines Entwurfs war dadurch
    # in der Akte-Verlaufsansicht unsichtbar, obwohl beides fachlich zu den
    # wichtigsten nachvollziehbaren Aktionen einer Akte gehoert (CLAUDE.md:
    # "Jede wichtige KI-Aktion muss nachvollziehbar sein").
    (OutboxEntry, "OutboxEntry"),
    # Ergaenzt 20.09. (systematische Suche nach ALLEN Modellen mit
    # `matter_id`, ausgeloest durch den OutboxEntry-Fund oben) - ZWEI
    # weitere Instanzen DERSELBEN Luecke, unabhaengig voneinander
    # gefunden:
    # - `ChatConversation` schreibt echte AuditEvents (u. a.
    #   "chat_relinked_to_matter", wenn ein Anwalt eine Unterhaltung
    #   nachtraeglich einer anderen Akte zuordnet - app/web/
    #   chat_router.py) und traegt bereits `matter_id`.
    # - `GeneratedDocument` schreibt echte AuditEvents ("document_
    #   generated"/"document_edited", app/document_generator/
    #   service.py) und traegt bereits `matter_id` (dort sogar explizit
    #   als "Pflicht" dokumentiert).
    # Beide fehlten hier - ihre Ereignisse waren in der Akte-
    # Verlaufsansicht unsichtbar, obwohl beide Modelle strukturell
    # exakt in dasselbe Muster wie Document/Draft/Party/Note/OutboxEntry
    # passen.
    (ChatConversation, "ChatConversation"),
    (GeneratedDocument, "GeneratedDocument"),
)


class AuditLogService:
    def list_events_for_entity(
        self, entity_type: str, entity_id: str, db: Session
    ) -> list[AuditEvent]:
        return (
            db.query(AuditEvent)
            .filter_by(entity_type=entity_type, entity_id=entity_id)
            .order_by(AuditEvent.created_at)
            .all()
        )

    def list_events_for_matter(self, matter_id: str, db: Session) -> list[AuditEvent]:
        if not matter_id:
            raise ValueError(
                "matter_id ist erforderlich - Audit-Abfrage ohne Aktenbezug "
                "ist nicht erlaubt"
            )

        # Die Akte selbst kann ebenfalls direktes Ziel eines AuditEvents
        # sein (z. B. "legal_research_performed", entity_type="Matter").
        entity_pairs: list[tuple[str, str]] = [("Matter", matter_id)]

        for model, type_name in _MATTER_SCOPED_MODELS:
            ids = (
                db.query(model.id)
                .filter(model.matter_id == matter_id)
                .all()
            )
            entity_pairs.extend((type_name, row[0]) for row in ids)

        if not entity_pairs:
            return []

        conditions = [
            and_(AuditEvent.entity_type == t, AuditEvent.entity_id == i)
            for t, i in entity_pairs
        ]
        return (
            db.query(AuditEvent)
            .filter(or_(*conditions))
            .order_by(AuditEvent.created_at)
            .all()
        )
