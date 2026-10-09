"""create_new_draft_version – die EINZIGE Stelle im Projekt, die eine neue
`Draft`-Zeile anlegt.

GRUNDREGEL (Vorgabe des Anwalts, wörtlich): "Ein bestehender Entwurf darf
bei einer Neugenerierung nicht überschrieben werden" - und dasselbe gilt
für manuelle Änderungen. Diese Regel wird hier an EINER Stelle technisch
durchgesetzt, statt sie in drei verschiedenen Services (KI-Neugenerierung
in `app/drafting/service.py`, anwaltliche Bearbeitung in
`app/feedback/service.py`, zukünftige eigenständige Bearbeitungsaktion im
Dashboard) jeweils neu zu implementieren - jede Duplikation wäre ein
Risiko, dass eine Stelle versehentlich doch mutiert statt eine neue Zeile
anzulegen.

Jeder Aufruf:
1. Legt eine NEUE `Draft`-Zeile an (nie ein UPDATE auf eine bestehende).
2. Verkettet sie über `previous_version_id` (None nur bei der allerersten
   Version einer Entwurfslinie).
3. Erhöht `version` fortlaufend relativ zur Vorgängerversion.
4. Verändert die VORGÄNGER-Zeile an keiner Stelle - deren `content` und
   `status` bleiben eingefroren (Nachvollziehbarkeit der Historie).
5. Schreibt IMMER ein Audit-Event für die neue Version selbst
   (`event_type` vom Aufrufer vorgegeben, z. B. "draft_created" für v1,
   "draft_version_created" für alle Folgeversionen) - der Aufrufer kann
   zusätzlich eigene, spezifischere Audit-Events schreiben (z. B.
   "attorney_instruction_applied", "draft_manual_edit").
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditEvent, Draft

#: Einziger neuer, zusaetzlich erlaubter `Draft.status`-Wert (04.10.,
#: Dokumenten-Editor) - siehe `discard_ai_suggestion` unten und
#: app/models/draft.py-Moduldocstring "ERWEITERUNG (04.10.)".
AI_SUGGESTION_DISCARDED_STATUS = "ai_suggestion_discarded"


def create_new_draft_version(
    db: Session,
    *,
    matter_id: str,
    content: str,
    status: str = "draft",
    previous_draft: Draft | None = None,
    message_id: str | None = None,
    actor: str,
    event_type: str,
    details: str | None = None,
    subject: str | None = None,
    recipient: str | None = None,
    content_format: str | None = None,
) -> Draft:
    """Legt eine neue, eigenständige Draft-Version an.

    `previous_draft=None` => allererste Version (v1) einer Entwurfslinie.
    `previous_draft=<Draft>` => Folgeversion, verkettet über
    `previous_version_id`; `message_id` wird dabei automatisch vom
    Vorgänger übernommen, falls nicht explizit angegeben.

    `subject`/`recipient`/`content_format` (04.10., Dokumenten-Editor):
    wie `message_id` automatisch vom Vorgänger übernommen, falls nicht
    explizit angegeben - eine neue Version (z. B. durch KI-Neugenerierung
    ohne Editor-Kontext) verliert dadurch NIE die im Editor gepflegten
    Betreff-/Empfänger-/Format-Angaben. `None` bei `previous_draft=None`
    (erste Version) => ehrlicher Leerzustand/"text"-Standard, siehe
    app/models/draft.py.
    """
    if previous_draft is not None:
        version = previous_draft.version + 1
        previous_version_id = previous_draft.id
        if message_id is None:
            message_id = previous_draft.message_id
        if subject is None:
            subject = previous_draft.subject
        if recipient is None:
            recipient = previous_draft.recipient
        if content_format is None:
            content_format = previous_draft.content_format
    else:
        version = 1
        previous_version_id = None
        if content_format is None:
            content_format = "text"

    draft = Draft(
        matter_id=matter_id,
        message_id=message_id,
        content=content,
        version=version,
        status=status,
        previous_version_id=previous_version_id,
        subject=subject,
        recipient=recipient,
        content_format=content_format,
    )
    db.add(draft)
    db.flush()

    db.add(
        AuditEvent(
            entity_type="Draft",
            entity_id=draft.id,
            event_type=event_type,
            actor=actor,
            details=details,
        )
    )
    db.commit()
    db.refresh(draft)
    return draft


def create_manual_edit_version(
    db: Session,
    *,
    previous_draft: Draft,
    new_content: str,
    status: str = "draft",
    actor: str,
    details: str | None = None,
    subject: str | None = None,
    recipient: str | None = None,
    content_format: str | None = None,
) -> Draft:
    """Gemeinsamer Weg für JEDE manuelle Bearbeitung eines Entwurfs -
    egal ob über `DraftFeedbackService` (Bearbeitung im Rahmen einer
    Freigabe/"approved_with_edits") oder über eine eigenständige
    Dashboard-Bearbeitungsaktion (Prompt 23/24, ohne Freigabeentscheidung).

    Beide Aufrufer sollen exakt dieselbe Versionierungs- und Audit-Logik
    durchlaufen (zwei Audit-Events: das generische "draft_version_created"
    aus `create_new_draft_version` PLUS das spezifischere
    "draft_manual_edit" hier) - Zentralisierung verhindert, dass eine
    Stelle versehentlich abweicht (z. B. das spezifische Event vergisst).
    """
    new_draft = create_new_draft_version(
        db,
        matter_id=previous_draft.matter_id,
        content=new_content,
        status=status,
        previous_draft=previous_draft,
        actor=actor,
        event_type="draft_version_created",
        details=details,
        subject=subject,
        recipient=recipient,
        content_format=content_format,
    )
    db.add(
        AuditEvent(
            entity_type="Draft",
            entity_id=new_draft.id,
            event_type="draft_manual_edit",
            actor=actor,
            details=details,
        )
    )
    db.commit()
    db.refresh(new_draft)
    return new_draft


def discard_ai_suggestion(db: Session, *, draft: Draft, actor: str) -> Draft:
    """Verwirft einen KI-Bearbeitungsvorschlag (neuer Rich-Text-Editor,
    04.10.) - der Vorschlag wurde bereits ueber `create_new_draft_version`
    als eigene, eingefrorene Zeile angelegt (siehe
    app/drafting/editor_service.py::apply_ai_suggestion - KEINE zweite,
    parallele Persistenzlogik). "Verwerfen" aendert NUR den `status`
    dieser EINEN Zeile auf `AI_SUGGESTION_DISCARDED_STATUS` - exakt die
    bereits bestehende Ausnahme "Status-Update ohne Versionssprung"
    (siehe app/models/draft.py-Moduldocstring, dort bisher nur für
    Freigabe/Ablehnung genutzt). Die Zeile selbst bleibt unveraendert in
    der Historie erhalten (Nachvollziehbarkeit, CLAUDE.md) - nichts wird
    geloescht, siehe `resolve_visible_draft` dafuer, wie sie aus der
    standardmaessig angezeigten Kettenspitze herausgehalten wird."""
    draft.status = AI_SUGGESTION_DISCARDED_STATUS
    db.add(
        AuditEvent(
            entity_type="Draft",
            entity_id=draft.id,
            event_type="draft_ai_suggestion_discarded",
            actor=actor,
            details=f"KI-Vorschlag v{draft.version} verworfen, Vorgaengerversion bleibt aktiv",
        )
    )
    db.commit()
    db.refresh(draft)
    return draft


def resolve_visible_draft(draft: Draft, by_id: dict[str, Draft]) -> Draft:
    """Löst einen verworfenen KI-Vorschlag (`status ==
    AI_SUGGESTION_DISCARDED_STATUS`) auf die zuletzt tatsächlich aktive
    Vorgängerversion auf - genutzt überall dort, wo bisher einfach "die
    letzte/die per `previous_version_id` referenzierte Zeile" als aktiver
    Stand einer Entwurfslinie galt (`drafts_list.html`-Leaf-Auswahl,
    Editor-Öffnen-Link), BEVOR verworfene KI-Vorschläge existierten.

    `by_id`: alle Versionen DERSELBEN Entwurfslinie, nach `id` indiziert
    (z. B. `{d.id: d for d in all_matter_drafts}`, siehe
    app/web/drafts_router.py::_load_version_chain).

    Ehrlicher Rückfallfall (CLAUDE.md: "Unsicherheit explizit markieren,
    nicht verschweigen"): ist JEDE Version der Kette verworfen (z. B.
    jede bisherige KI-Anfrage wurde abgelehnt), wird `draft` selbst
    zurückgegeben statt eine nicht existierende "gültige" Version zu
    erfinden - besser sichtbar verworfen als fälschlich verschwiegen."""
    current = draft
    while (
        current.status == AI_SUGGESTION_DISCARDED_STATUS
        and current.previous_version_id is not None
        and current.previous_version_id in by_id
    ):
        current = by_id[current.previous_version_id]
    return current


def find_latest_version(db: Session, draft: Draft) -> Draft:
    """Aktueller Kettenkopf einer Entwurfslinie ab `draft` (folgt `previous_version_id`
    vorwaerts; verworfene KI-Vorschlaege zaehlen nicht als Nachfolger, siehe
    `discard_ai_suggestion`). Gibt `draft` selbst zurueck, wenn es keinen Nachfolger gibt.
    Dient dem Chat dazu, den Editor-Link immer auf die AKTUELLE Fassung zu setzen und eine
    Chat-Ueberarbeitung als Folgeversion an die bestehende Kette zu haengen."""
    current = draft
    seen = {current.id}
    while True:
        successor = (
            db.query(Draft)
            .filter(
                Draft.previous_version_id == current.id,
                Draft.status != AI_SUGGESTION_DISCARDED_STATUS,
            )
            .order_by(Draft.version.desc(), Draft.created_at.desc())
            .first()
        )
        if successor is None or successor.id in seen:
            return current
        seen.add(successor.id)
        current = successor
