"""create_quick_matter – gemeinsame Hilfsfunktion für die automatische
Akten-/Mandantenanlage, wenn eine Entwurfserstellung ohne Aktenauswahl
angestoßen wird (Schriftsatz-Generator, 20.08.).

Wird von ZWEI Stellen genutzt:
- `DraftingService.create_draft` (app/drafting/service.py), wenn
  `matter_id=None` übergeben wird.
- `app/web/schriftsatz_router.py` direkt, WEIL dort Drag&Drop-Dokumente vor
  dem eigentlichen `create_draft`-Aufruf als `Document` gespeichert werden
  müssen (damit `RuleBasedLocalAIProvider.prepare_draft_context` sie
  überhaupt sieht) - das setzt voraus, dass die Akte zu diesem Zeitpunkt
  bereits existiert. Der Router löst die Akte deshalb selbst VORAB auf und
  übergibt anschließend eine echte `matter_id` an `create_draft` (dessen
  eigener Auto-Create-Zweig bleibt trotzdem bestehen, für alle anderen/
  zukünftigen Aufrufer ohne Aktenauswahl, z. B. eine spätere API).

Eine einzige, gemeinsame Stelle statt zweier fast identischer
Implementierungen - Aktenanlage ist sicherheitsrelevant genug (Aktenisolation,
Audit), um hier keine Kopie driften zu lassen."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import AuditEvent, Client, Matter

#: Sammel-Mandant für Entwürfe, die ohne Aktenauswahl entstanden sind.
#: Bewusst EIN gemeinsamer Datensatz statt eines neuen pro Entwurf - siehe
#: `_resolve_placeholder_client`.
PLACEHOLDER_CLIENT_NAME = "Ohne Mandantenzuordnung"


def _resolve_placeholder_client(db: Session) -> Client:
    """Gibt den EINEN Sammel-Mandanten zurück und legt ihn nur an, falls er
    noch nicht existiert.

    ECHTER FUND auf der synthetischen Kanzlei-Datenbasis (15.09.): in der
    realen Datenbank standen **28 identische Datensätze** namens
    "Ohne Mandantenzuordnung" (alle ohne Mandantennummer) - bei 40
    Mandanten insgesamt. Jeder Schnellentwurf legte einen NEUEN Platzhalter
    an. Für die Kanzlei bedeutete das eine Mandantenliste, die zu rund 70 %
    aus identischem Füllmaterial bestand; genau das Gegenteil von
    "weniger Handarbeit für den Anwalt".

    "Ohne Mandantenzuordnung" ist kein Mandant, sondern ein Zustand - davon
    kann es per Definition nur einen geben. Der älteste vorhandene Datensatz
    wird wiederverwendet (deterministisch, damit wiederholte Läufe stabil
    bleiben).

    Für einen NAMENTLICH genannten Mandanten wird bewusst NICHT
    zusammengeführt: zwei Personen können denselben Namen tragen, und ein
    automatisches Verschmelzen zweier Mandanten wäre ein fachlicher
    Eingriff, der der Kanzlei zusteht - nicht dieser Hilfsfunktion.
    """
    existing = (
        db.query(Client)
        .filter(Client.name == PLACEHOLDER_CLIENT_NAME)
        .order_by(Client.created_at)
        .first()
    )
    if existing is not None:
        return existing
    client = Client(name=PLACEHOLDER_CLIENT_NAME)
    db.add(client)
    db.flush()
    return client


def create_quick_matter(
    db: Session,
    *,
    title: str | None,
    client_name: str | None,
    actor: str,
    client_id: str | None = None,
) -> Matter:
    """Legt eine neue `Matter` (Status "open") an und committet sofort - der
    Aufrufer erhält eine sofort nutzbare `matter.id`. Schreibt ein
    `AuditEvent` (`matter_auto_created`), damit diese automatische, für den
    Anwalt nicht offensichtliche Nebenwirkung nachvollziehbar bleibt (siehe
    CLAUDE.md-Grundregel zu KI-Aktionen).

    Ohne Mandantennamen wird der gemeinsame Sammel-Mandant verwendet statt
    jedes Mal ein neuer Platzhalter angelegt (siehe
    `_resolve_placeholder_client`).

    `client_id` (17.09., Owner-Direktive §5/§6 "verwaiste Beziehungen"):
    ECHTER FUND - der "Akte anlegen"-Einstieg von einer bestehenden
    Mandanten-Detailseite (`client_detail.html`, fuer einen Mandanten OHNE
    Akte) landete bisher auf dem Schriftsatz-Generator OHNE jeden Bezug zum
    bereits bekannten Mandanten. Tippte der Anwalt dort - naheliegend -
    denselben Mandantennamen erneut in `new_client_name` ein, legte
    `client_name` oben JEDES MAL einen NEUEN, DUPLIZIERTEN `Client`-
    Datensatz an, statt den bestehenden wiederzuverwenden - eine der
    Ursachen fuer genau die bereits dokumentierte Mandanten-/Akten-
    Fragmentierung (siehe OPEN_ISSUES.md). `client_id` (eine reine, nicht
    personenbezogene ID - dasselbe bereits etablierte Prinzip wie
    `matter_id` in der URL dieses Formulars) hat Vorrang vor `client_name`:
    ist die ID gueltig, wird der BESTEHENDE Mandant direkt wiederverwendet,
    kein neuer angelegt. Eine ungueltige/fremde ID faellt sicher auf das
    bisherige Verhalten zurueck (client_name/Platzhalter), statt einen
    Fehler zu werfen."""
    if client_id:
        client = db.get(Client, client_id)
    else:
        client = None
    if client is None:
        if client_name:
            client = Client(name=client_name)
            db.add(client)
            db.flush()  # client.id fuer die neue Matter benoetigt
        else:
            client = _resolve_placeholder_client(db)

    matter = Matter(
        client_id=client.id,
        title=title or f"Schnellentwurf {date.today().isoformat()}",
        status="open",
    )
    db.add(matter)
    db.flush()  # matter.id fuer das AuditEvent/den weiteren Ablauf benoetigt

    db.add(
        AuditEvent(
            entity_type="Matter",
            entity_id=matter.id,
            event_type="matter_auto_created",
            actor=actor,
            details=(
                f"Akte automatisch angelegt (ohne Aktenauswahl), "
                f"Mandant: {client.name}"
            ),
        )
    )
    db.commit()
    return matter
