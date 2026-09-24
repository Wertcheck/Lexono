"""Tests für app/clients/service.py (Mandantendatenbank, 20.08.).

Reine Service-Ebene (keine HTTP-Schicht) - siehe tests/test_web_clients.py
für die Router-/Berechtigungstests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.service import (
    ClientHasMattersError,
    ClientValidationError,
    archive_client,
    create_client,
    delete_client,
    list_clients,
    reactivate_client,
    update_client,
)
from app.models import AuditEvent, Client, Matter
from app.models.base import Base


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# --- create_client ---


def test_create_client_succeeds_with_required_fields(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Muster GmbH",
        client_number="M-001",
        contact_email="info@muster.test",
        contact_phone="030 1234567",
        practice_area="Vertragsrecht",
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    assert client.id is not None
    assert client.status == "active"
    assert db_session.query(Client).count() == 1


def test_create_client_writes_audit_event(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Muster GmbH",
        client_number="M-001",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    events = db_session.query(AuditEvent).filter_by(entity_id=client.id).all()
    assert len(events) == 1
    assert events[0].event_type == "client_created"
    assert events[0].actor == "anwalt@kanzlei.test"


def test_create_client_rejects_blank_name(db_session: Session) -> None:
    with pytest.raises(ClientValidationError):
        create_client(
            db_session,
            name="   ",
            client_number="M-002",
            contact_email=None,
            contact_phone=None,
            practice_area=None,
            responsible_user_id=None,
            actor="anwalt@kanzlei.test",
        )
    assert db_session.query(Client).count() == 0


def test_create_client_rejects_blank_client_number(db_session: Session) -> None:
    with pytest.raises(ClientValidationError):
        create_client(
            db_session,
            name="Muster GmbH",
            client_number="",
            contact_email=None,
            contact_phone=None,
            practice_area=None,
            responsible_user_id=None,
            actor="anwalt@kanzlei.test",
        )


def test_create_client_rejects_duplicate_client_number(db_session: Session) -> None:
    create_client(
        db_session,
        name="Erster Mandant",
        client_number="DUP-1",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    with pytest.raises(ClientValidationError):
        create_client(
            db_session,
            name="Zweiter Mandant",
            client_number="DUP-1",
            contact_email=None,
            contact_phone=None,
            practice_area=None,
            responsible_user_id=None,
            actor="anwalt@kanzlei.test",
        )
    assert db_session.query(Client).count() == 1


# --- update_client ---


def test_update_client_changes_fields(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Alter Name",
        client_number="U-001",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    update_client(
        db_session,
        client,
        name="Neuer Name",
        client_number="U-001",
        contact_email="neu@muster.test",
        contact_phone="030 999",
        practice_area="Familienrecht",
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    assert client.name == "Neuer Name"
    assert client.contact_email == "neu@muster.test"
    assert client.practice_area == "Familienrecht"


def test_update_client_allows_keeping_own_client_number(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Muster",
        client_number="U-002",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    # Duerfte NICHT als "bereits vergeben" gegen sich selbst fehlschlagen.
    update_client(
        db_session,
        client,
        name="Muster GmbH",
        client_number="U-002",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    assert client.name == "Muster GmbH"


# --- archive/reactivate ---


def test_archive_client_sets_status_and_keeps_record(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Muster",
        client_number="A-001",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    archive_client(db_session, client, actor="anwalt@kanzlei.test")
    assert client.status == "archived"
    assert db_session.query(Client).count() == 1


def test_reactivate_client_sets_status_active(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Muster",
        client_number="A-002",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    archive_client(db_session, client, actor="anwalt@kanzlei.test")
    reactivate_client(db_session, client, actor="anwalt@kanzlei.test")
    assert client.status == "active"


# --- delete_client (Kernanforderung: kein Hard-Delete mit Akten) ---


def test_delete_client_without_matters_succeeds(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Ohne Akte",
        client_number="D-001",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    client_id = client.id
    delete_client(db_session, client, actor="admin@kanzlei.test")
    assert db_session.get(Client, client_id) is None


def test_delete_client_writes_audit_event_before_deletion(db_session: Session) -> None:
    client = create_client(
        db_session,
        name="Ohne Akte",
        client_number="D-002",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    client_id = client.id
    delete_client(db_session, client, actor="admin@kanzlei.test")
    events = db_session.query(AuditEvent).filter_by(entity_id=client_id).all()
    assert any(e.event_type == "client_deleted" for e in events)


def test_delete_client_with_matters_is_blocked(db_session: Session) -> None:
    """Kernanforderung (ausdrueckliche Vorgabe des Anwalts): ein Mandant mit
    verknuepften Akten darf NICHT hart geloescht werden - gesetzliche
    Aufbewahrungspflichten fuer Anwaltsakten."""
    client = create_client(
        db_session,
        name="Mit Akte",
        client_number="D-003",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    matter = Matter(client_id=client.id, title="Bestehende Akte", status="open")
    db_session.add(matter)
    db_session.commit()

    with pytest.raises(ClientHasMattersError):
        delete_client(db_session, client, actor="admin@kanzlei.test")

    # Client UND Matter muessen vollstaendig erhalten bleiben.
    assert db_session.get(Client, client.id) is not None
    assert db_session.query(Matter).filter_by(client_id=client.id).count() == 1


# --- list_clients ---


def test_list_clients_default_shows_only_active(db_session: Session) -> None:
    active = create_client(
        db_session,
        name="Aktiv",
        client_number="L-001",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    archived = create_client(
        db_session,
        name="Archiviert",
        client_number="L-002",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    archive_client(db_session, archived, actor="anwalt@kanzlei.test")

    rows = list_clients(db_session, status="active")
    ids = {row.client.id for row in rows}
    assert active.id in ids
    assert archived.id not in ids


def test_list_clients_status_all_shows_everything(db_session: Session) -> None:
    active = create_client(
        db_session,
        name="Aktiv",
        client_number="L-010",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    archived = create_client(
        db_session,
        name="Archiviert",
        client_number="L-011",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    archive_client(db_session, archived, actor="anwalt@kanzlei.test")

    rows = list_clients(db_session, status="all")
    ids = {row.client.id for row in rows}
    assert {active.id, archived.id} <= ids


def test_list_clients_search_matches_name_and_client_number(db_session: Session) -> None:
    create_client(
        db_session,
        name="Sonnenschein Rechtsanwälte",
        client_number="S-100",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    create_client(
        db_session,
        name="Andere Firma",
        client_number="X-999",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )

    by_name = list_clients(db_session, search="sonnenschein")
    assert len(by_name) == 1
    assert by_name[0].client.name == "Sonnenschein Rechtsanwälte"

    by_number = list_clients(db_session, search="X-999")
    assert len(by_number) == 1
    assert by_number[0].client.client_number == "X-999"


def test_list_clients_filters_by_practice_area(db_session: Session) -> None:
    create_client(
        db_session,
        name="Mieter GmbH",
        client_number="P-001",
        contact_email=None,
        contact_phone=None,
        practice_area="Mietrecht",
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    create_client(
        db_session,
        name="Arbeiter GmbH",
        client_number="P-002",
        contact_email=None,
        contact_phone=None,
        practice_area="Arbeitsrecht",
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )

    rows = list_clients(db_session, practice_area="Mietrecht")
    assert len(rows) == 1
    assert rows[0].client.name == "Mieter GmbH"


def test_list_clients_computes_last_contact_from_messages_across_matters(
    db_session: Session,
) -> None:
    from datetime import datetime, timedelta, timezone

    from app.models import Message

    client = create_client(
        db_session,
        name="Mit Nachrichten",
        client_number="C-500",
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )
    matter = Matter(client_id=client.id, title="Akte", status="open")
    db_session.add(matter)
    db_session.flush()

    older = datetime.now(timezone.utc) - timedelta(days=10)
    newer = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.add_all(
        [
            Message(matter_id=matter.id, direction="inbound", created_at=older),
            Message(matter_id=matter.id, direction="outbound", created_at=newer),
        ]
    )
    db_session.commit()

    rows = list_clients(db_session, search="Mit Nachrichten")
    assert len(rows) == 1
    assert rows[0].last_contact_at is not None
    # SQLite gibt datetime ggf. ohne tzinfo zurueck - nur auf den Tag genau
    # vergleichen, um TZ-Handling-Details nicht mitzutesten.
    assert rows[0].last_contact_at.date() == newer.date()


def _client(db: Session, name: str, number: str) -> Client:
    return create_client(
        db,
        name=name,
        client_number=number,
        contact_email=None,
        contact_phone=None,
        practice_area=None,
        responsible_user_id=None,
        actor="anwalt@kanzlei.test",
    )


def test_list_clients_counts_only_open_matters(db_session: Session) -> None:
    """UI/UX-Audit (14.09., assets/ux-ui/29_mandanten_uebersicht.png): die
    Mandantenliste zeigt jetzt die Anzahl der OFFENEN Akten je Mandant -
    abgeschlossene Akten duerfen NICHT mitgezaehlt werden."""
    client = _client(db_session, "Mit offenen Akten", "C-600")
    db_session.add_all(
        [
            Matter(client_id=client.id, title="Offen 1", status="open"),
            Matter(client_id=client.id, title="Offen 2", status="open"),
            Matter(client_id=client.id, title="Erledigt", status="closed"),
        ]
    )
    db_session.commit()

    rows = list_clients(db_session, search="Mit offenen Akten")

    assert len(rows) == 1
    assert rows[0].open_matter_count == 2


def test_list_clients_reports_zero_open_matters_without_any_matter(
    db_session: Session,
) -> None:
    """Der OUTER JOIN liefert fuer Mandanten ganz ohne Akte NULL - die
    Liste muss daraus eine echte 0 machen (die Referenz zeigt dort
    ebenfalls "0", keinen Platzhalter)."""
    _client(db_session, "Ohne jede Akte", "C-601")
    db_session.commit()

    rows = list_clients(db_session, search="Ohne jede Akte")

    assert len(rows) == 1
    assert rows[0].open_matter_count == 0


def test_list_clients_counts_open_matters_even_without_any_message(
    db_session: Session,
) -> None:
    """ECHTER, beim Bau bewusst mitgedachter Fall (siehe Kommentar in
    list_clients): der Akten-Zaehler haengt an einer EIGENEN Subquery, nicht
    am "letzter Kontakt"-Aggregat - ein Mandant mit offenen Akten, aber ganz
    ohne Nachrichten, muss seinen Zaehler trotzdem behalten (waere bei einem
    gemeinsamen Join ueber Message stillschweigend auf 0 gefallen)."""
    client = _client(db_session, "Akten ohne Nachrichten", "C-602")
    db_session.add(Matter(client_id=client.id, title="Offen", status="open"))
    db_session.commit()

    rows = list_clients(db_session, search="Akten ohne Nachrichten")

    assert len(rows) == 1
    assert rows[0].last_contact_at is None
    assert rows[0].open_matter_count == 1
