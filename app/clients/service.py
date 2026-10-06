"""ClientService – CRUD-/Such-/Loeschlogik fuer die Mandantendatenbank
(20.08.).

Loeschlogik (ausdrueckliche Vorgabe des Anwalts, siehe app/models/client.py-
Docstring): `Client.matters` ist mit `cascade="all, delete-orphan"`
definiert - ein direktes `db.delete(client)` bei einem Mandanten mit
bestehenden Akten wuerde deren gesamte Fallhistorie (Nachrichten,
Dokumente, Entwuerfe, Fristen) unwiderruflich mitloeschen, was mit
gesetzlichen Aufbewahrungspflichten fuer Anwaltsakten kollidieren kann.
`delete_client` erzwingt deshalb serverseitig (nicht nur im UI!):
- Mandant OHNE Akten -> echtes Hard-Delete erlaubt.
- Mandant MIT mindestens einer Akte -> `ClientHasMattersError`, das UI
  bietet stattdessen ausschliesslich `archive_client` (Status-Wechsel,
  keine Kaskade, jederzeit umkehrbar) an.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models import AuditEvent, Client, Matter, Message

# Feste Vorschlagsliste fuer das "Rechtsgebiet"-Auswahlfeld im "Mandant
# anlegen"-Modal (app/web/templates/clients_list.html) - bewusst KEINE
# DB-Enum/harte Validierung dagegen (siehe create_client/update_client):
# ein CSV-/Excel-Import darf nicht an abweichender Schreibweise scheitern,
# diese Liste ist reine UI-Bequemlichkeit fuer die manuelle Erfassung.
PRACTICE_AREA_SUGGESTIONS = (
    "Arbeitsrecht",
    "Familienrecht",
    "Mietrecht",
    "Verkehrsrecht",
    "Erbrecht",
    "Strafrecht",
    "Vertragsrecht",
    "Gesellschaftsrecht",
    "Sonstiges",
)

# Vorschlagswerte fuer `Client.client_type` (03.10., Owner-Direktive
# "REFERENZGETREUE MANDANTENUEBERSICHT", Referenzabgleich
# `29_mandanten_uebersicht.png`) - bewusst nur eine Vorschlagsliste wie bei
# `PRACTICE_AREA_SUGGESTIONS` (keine DB-Enum/harte Validierung), dieselbe
# Begruendung: ein CSV-/Excel-Import darf nicht an abweichender
# Schreibweise scheitern.
CLIENT_TYPE_SUGGESTIONS = ("Privatperson", "Unternehmen")

_ALLOWED_CLIENT_SORT_OPTIONS = ("updated_desc", "name_asc", "name_desc")
_ALLOWED_CLIENT_PAGE_SIZES = (10, 20, 50)


class ClientValidationError(Exception):
    """Pflichtfeld fehlt oder Mandantennummer bereits vergeben - wird von
    create_client/update_client UND vom CSV-/Excel-Import
    (app/clients/import_service.py) geworfen, DAMIT beide Wege exakt
    dieselbe Regel durchsetzen (kein zweites, abweichendes Validierungs-
    Set fuer den Import)."""


class ClientHasMattersError(Exception):
    """Ein Hard-Delete wurde fuer einen Mandanten mit mindestens einer
    verknuepften Akte versucht - siehe Moduldocstring."""


@dataclass(frozen=True)
class ClientListRow:
    client: Client
    last_contact_at: datetime | None
    # UI/UX-Audit (14.09., Abgleich gegen assets/ux-ui/29_mandanten_uebersicht.png):
    # die Referenz zeigt je Mandant die Anzahl der OFFENEN Akten - bisher
    # gar nicht vorhanden. Bewusst als weiteres Aggregat in derselben
    # einen Query (siehe list_clients) statt eines Zaehl-Querys pro Zeile,
    # damit das dort dokumentierte "kein N+1"-Versprechen gewahrt bleibt.
    open_matter_count: int = 0


def _validate_required_fields(name: str, client_number: str) -> None:
    if not name or not name.strip():
        raise ClientValidationError("Name ist ein Pflichtfeld.")
    if not client_number or not client_number.strip():
        raise ClientValidationError("Mandantennummer ist ein Pflichtfeld.")


def _check_client_number_unique(
    db: Session, client_number: str, *, exclude_client_id: str | None = None
) -> None:
    query = db.query(Client).filter(Client.client_number == client_number)
    if exclude_client_id is not None:
        query = query.filter(Client.id != exclude_client_id)
    if query.first() is not None:
        raise ClientValidationError(
            f"Mandantennummer '{client_number}' ist bereits vergeben."
        )


def create_client(
    db: Session,
    *,
    name: str,
    client_number: str,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    practice_area: str | None = None,
    client_type: str | None = None,
    city: str | None = None,
    responsible_user_id: str | None = None,
    actor: str,
    commit: bool = True,
) -> Client:
    """Legt einen neuen Mandanten an. Wirft `ClientValidationError` bei
    fehlenden Pflichtfeldern (Name/Mandantennummer) oder bereits
    vergebener Mandantennummer - VOR jedem Schreibzugriff geprueft.

    `commit=False` fuer den Massenimport (app/clients/import_service.py):
    dort committet der Aufrufer selbst gebuendelt am Ende des gesamten
    Imports, nicht nach jeder einzelnen Zeile."""
    name = name.strip()
    client_number = client_number.strip()
    _validate_required_fields(name, client_number)
    _check_client_number_unique(db, client_number)

    client = Client(
        name=name,
        client_number=client_number,
        contact_email=(contact_email or "").strip() or None,
        contact_phone=(contact_phone or "").strip() or None,
        practice_area=(practice_area or "").strip() or None,
        client_type=(client_type or "").strip() or None,
        city=(city or "").strip() or None,
        responsible_user_id=responsible_user_id or None,
        status="active",
    )
    db.add(client)
    db.flush()  # client.id fuer das AuditEvent benoetigt

    db.add(
        AuditEvent(
            entity_type="Client",
            entity_id=client.id,
            event_type="client_created",
            actor=actor,
            details=f"Mandant angelegt: {client.name} ({client.client_number})",
        )
    )
    if commit:
        db.commit()
    return client


def update_client(
    db: Session,
    client: Client,
    *,
    name: str,
    client_number: str,
    contact_email: str | None,
    contact_phone: str | None,
    practice_area: str | None,
    client_type: str | None = None,
    city: str | None = None,
    responsible_user_id: str | None,
    actor: str,
) -> Client:
    name = name.strip()
    client_number = client_number.strip()
    _validate_required_fields(name, client_number)
    _check_client_number_unique(db, client_number, exclude_client_id=client.id)

    client.name = name
    client.client_number = client_number
    client.contact_email = (contact_email or "").strip() or None
    client.contact_phone = (contact_phone or "").strip() or None
    client.practice_area = (practice_area or "").strip() or None
    client.client_type = (client_type or "").strip() or None
    client.city = (city or "").strip() or None
    client.responsible_user_id = responsible_user_id or None

    db.add(
        AuditEvent(
            entity_type="Client",
            entity_id=client.id,
            event_type="client_updated",
            actor=actor,
            details=f"Mandantendaten geaendert: {client.name} ({client.client_number})",
        )
    )
    db.commit()
    return client


def archive_client(db: Session, client: Client, *, actor: str) -> Client:
    client.status = "archived"
    db.add(
        AuditEvent(
            entity_type="Client",
            entity_id=client.id,
            event_type="client_archived",
            actor=actor,
            details=(
                f"Mandant archiviert (Akten bleiben vollstaendig erhalten): "
                f"{client.name}"
            ),
        )
    )
    db.commit()
    return client


def reactivate_client(db: Session, client: Client, *, actor: str) -> Client:
    client.status = "active"
    db.add(
        AuditEvent(
            entity_type="Client",
            entity_id=client.id,
            event_type="client_reactivated",
            actor=actor,
            details=f"Mandant reaktiviert: {client.name}",
        )
    )
    db.commit()
    return client


def delete_client(db: Session, client: Client, *, actor: str) -> None:
    """Hard-Delete - siehe Moduldocstring. `client.matters` wird bewusst
    ueber die bereits geladene ORM-Beziehung geprueft (kein zusaetzliches
    COUNT(*)), da der Aufrufer (app/web/clients_router.py) `client` ohnehin
    per `get_or_404` frisch aus der DB laedt."""
    if len(client.matters) > 0:
        raise ClientHasMattersError(
            f"Mandant '{client.name}' hat noch {len(client.matters)} verknuepfte "
            "Akte(n) - Loeschen ist aus Aufbewahrungsgruenden gesperrt. "
            "Bitte stattdessen archivieren."
        )
    client_id = client.id
    client_name = client.name
    db.add(
        AuditEvent(
            entity_type="Client",
            entity_id=client_id,
            event_type="client_deleted",
            actor=actor,
            details=f"Mandant endgueltig geloescht (keine Akten verknuepft): {client_name}",
        )
    )
    db.delete(client)
    db.commit()


def _build_filtered_client_query(
    db: Session,
    *,
    search: str | None,
    practice_area: str | None,
    client_type: str | None,
    responsible_user_id: str | None,
    status: str,
):
    """Eine einzige gejointe Query statt N+1 (kein separater Query pro
    Zeile fuer "letzter Kontakt") - `last_contact_subq` aggregiert das
    juengste `Message.created_at` je Client UEBER ALLE seine Akten hinweg
    (Aktenisolation ist hier unproblematisch: es wird nur der Zeitstempel
    aggregiert, kein Inhalt vermischt). Gemeinsame Grundlage fuer
    `list_clients` (Seite holen) UND `count_clients` (Gesamtzahl fuer die
    Pagination) - EINE Filterlogik statt zweier abweichender Kopien (03.10.,
    Owner-Direktive "REFERENZGETREUE MANDANTENUEBERSICHT")."""
    last_contact_subq = (
        db.query(
            Matter.client_id.label("client_id"),
            func.max(Message.created_at).label("last_contact_at"),
        )
        .join(Message, Message.matter_id == Matter.id)
        .group_by(Matter.client_id)
        .subquery()
    )

    # Zweites Aggregat in derselben Query (siehe ClientListRow.open_matter_count):
    # Anzahl OFFENER Akten je Mandant, bewusst als eigene Subquery statt eines
    # zusaetzlichen Joins auf `last_contact_subq` - sonst wuerden Mandanten ohne
    # Nachrichten, aber mit offenen Akten, ihren Zaehler verlieren.
    open_matters_subq = (
        db.query(
            Matter.client_id.label("client_id"),
            func.count(Matter.id).label("open_matter_count"),
        )
        .filter(Matter.status == "open")
        .group_by(Matter.client_id)
        .subquery()
    )

    query = (
        db.query(
            Client,
            last_contact_subq.c.last_contact_at,
            open_matters_subq.c.open_matter_count,
        )
        .outerjoin(last_contact_subq, last_contact_subq.c.client_id == Client.id)
        .outerjoin(open_matters_subq, open_matters_subq.c.client_id == Client.id)
        .options(joinedload(Client.responsible_user))
    )

    if status != "all":
        query = query.filter(Client.status == status)
    if practice_area:
        query = query.filter(Client.practice_area == practice_area)
    if client_type:
        query = query.filter(Client.client_type == client_type)
    if responsible_user_id:
        query = query.filter(Client.responsible_user_id == responsible_user_id)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            or_(Client.name.ilike(like), Client.client_number.ilike(like))
        )
    return query, last_contact_subq


def count_clients(
    db: Session,
    *,
    search: str | None = None,
    practice_area: str | None = None,
    client_type: str | None = None,
    responsible_user_id: str | None = None,
    status: str = "active",
) -> int:
    """Gesamtzahl der zu `list_clients` passenden Mandanten (fuer die
    Pagination-Fusszeile, z. B. "10 von 42 Mandanten") - dieselben Filter,
    KEIN Limit/Offset. `.count()` auf dieser Query ist unverfaelscht: beide
    Subquery-Joins sind 1:1-Aggregate (je Client hoechstens eine Zeile je
    Subquery), es entsteht also KEINE Zeilenvervielfachung durch die
    Outer-Joins, die `.count()` verfaelschen koennte."""
    query, _ = _build_filtered_client_query(
        db,
        search=search,
        practice_area=practice_area,
        client_type=client_type,
        responsible_user_id=responsible_user_id,
        status=status,
    )
    return query.count()


def list_clients(
    db: Session,
    *,
    search: str | None = None,
    practice_area: str | None = None,
    client_type: str | None = None,
    responsible_user_id: str | None = None,
    status: str = "active",
    sort: str = "updated_desc",
    page: int = 1,
    page_size: int = 200,
) -> list[ClientListRow]:
    """Liefert GENAU EINE Seite (Standard `page_size=200` entspricht dem
    vorherigen festen `limit=200` dieser Funktion - bestehende Aufrufer
    ohne explizite Pagination erhalten dadurch unveraendertes Verhalten).

    `sort` (03.10., Owner-Direktive "REFERENZGETREUE MANDANTENUEBERSICHT",
    Referenzabgleich `29_mandanten_uebersicht.png"s Sortierungs-Dropdown
    "Zuletzt aktualisiert"): einer von `_ALLOWED_CLIENT_SORT_OPTIONS`.
    "updated_desc" (Standard) sortiert nach dem juengsten bekannten
    Aktivitaetszeitpunkt - `last_contact_at` (letzte Nachricht ueber eine
    Akte) falls vorhanden, sonst der eigene `Client.updated_at`
    (Stammdaten-Aenderung) als ehrlicher Rueckfall, NIEMALS ein erfundener
    Wert."""
    if sort not in _ALLOWED_CLIENT_SORT_OPTIONS:
        sort = "updated_desc"
    if page_size not in _ALLOWED_CLIENT_PAGE_SIZES and page_size != 200:
        page_size = 200
    if page < 1:
        page = 1

    query, last_contact_subq = _build_filtered_client_query(
        db,
        search=search,
        practice_area=practice_area,
        client_type=client_type,
        responsible_user_id=responsible_user_id,
        status=status,
    )

    if sort == "name_asc":
        query = query.order_by(Client.name.asc())
    elif sort == "name_desc":
        query = query.order_by(Client.name.desc())
    else:
        query = query.order_by(
            func.coalesce(last_contact_subq.c.last_contact_at, Client.updated_at).desc()
        )

    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return [
        ClientListRow(
            client=row[0],
            last_contact_at=row[1],
            # Mandanten ohne jede Akte liefern durch den OUTER JOIN NULL -
            # als 0 darstellen, nicht als "–"/None (die Referenz zeigt dort
            # ebenfalls eine echte 0, siehe Zeile "Schulz, Lisa").
            open_matter_count=row[2] or 0,
        )
        for row in rows
    ]
