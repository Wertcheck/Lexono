"""Tests für app/drafting/quick_matter.py - die automatische Akten-/
Mandantenanlage bei Entwürfen ohne Aktenauswahl.

Anlass (15.09.): auf der echten synthetischen Kanzlei-Datenbasis standen
**28 identische** Mandanten namens "Ohne Mandantenzuordnung" bei 40
Mandanten insgesamt - jeder Schnellentwurf hatte einen neuen Platzhalter
angelegt.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.drafting.quick_matter import PLACEHOLDER_CLIENT_NAME, create_quick_matter
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
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_repeated_quick_matters_reuse_one_placeholder_client(db_session: Session) -> None:
    """Der eigentliche Fehler: "Ohne Mandantenzuordnung" ist kein Mandant,
    sondern ein Zustand - davon kann es nur einen geben. Vorher wuchs die
    Mandantenliste der Kanzlei mit jedem Schnellentwurf um eine weitere
    identische Zeile."""
    for _ in range(5):
        create_quick_matter(db_session, title=None, client_name=None, actor="anwalt@kanzlei.test")

    placeholders = db_session.query(Client).filter(Client.name == PLACEHOLDER_CLIENT_NAME).all()
    assert len(placeholders) == 1
    assert db_session.query(Client).count() == 1


def test_repeated_quick_matters_still_create_separate_matters(db_session: Session) -> None:
    """Der gemeinsame Sammel-Mandant darf die AKTEN nicht zusammenlegen -
    Aktenisolation gilt je Akte, nicht je Mandant."""
    first = create_quick_matter(
        db_session, title=None, client_name=None, actor="anwalt@kanzlei.test"
    )
    second = create_quick_matter(
        db_session, title=None, client_name=None, actor="anwalt@kanzlei.test"
    )

    assert first.id != second.id
    assert db_session.query(Matter).count() == 2
    assert first.client_id == second.client_id


def test_named_client_is_not_merged_into_an_existing_one(db_session: Session) -> None:
    """Bewusste Grenze des Fixes: zwei Personen koennen denselben Namen
    tragen. Ein automatisches Verschmelzen zweier NAMENTLICHER Mandanten
    waere ein fachlicher Eingriff, der der Kanzlei zusteht."""
    create_quick_matter(
        db_session, title=None, client_name="Muster, Anna", actor="anwalt@kanzlei.test"
    )
    create_quick_matter(
        db_session, title=None, client_name="Muster, Anna", actor="anwalt@kanzlei.test"
    )

    assert db_session.query(Client).filter(Client.name == "Muster, Anna").count() == 2


def test_client_id_reuses_the_existing_client_instead_of_creating_a_duplicate(
    db_session: Session,
) -> None:
    """ECHTER FUND (17.09., Owner-Direktive §5/§6 "verwaiste Beziehungen"):
    im Unterschied zu `client_name` (siehe `test_named_client_is_not_merged_
    into_an_existing_one` oben - bewusst NIE automatisch nach Namen
    zusammengefuehrt) ist `client_id` eine explizite, vom Aufrufer bereits
    aufgeloeste Referenz auf EINEN konkreten, bekannten Mandanten (z. B.
    "Akte anlegen" von dessen eigener Detailseite) - hier MUSS wiederverwendet
    werden, sonst entsteht bei jedem Klick ein neuer Duplikat-Mandant."""
    existing_client = Client(name="Bereits bekannter Mandant GmbH")
    db_session.add(existing_client)
    db_session.commit()

    matter = create_quick_matter(
        db_session,
        title="Neue Akte",
        client_name="",
        client_id=existing_client.id,
        actor="anwalt@kanzlei.test",
    )

    assert matter.client_id == existing_client.id
    assert db_session.query(Client).count() == 1


def test_unknown_client_id_falls_back_to_client_name(db_session: Session) -> None:
    """Sicherer Fehlerfall: eine ungueltige/fremde `client_id` darf keinen
    Fehler werfen, sondern faellt auf das bisherige Verhalten zurueck."""
    matter = create_quick_matter(
        db_session,
        title="Neue Akte",
        client_name="Neuer Mandant GmbH",
        client_id="does-not-exist",
        actor="anwalt@kanzlei.test",
    )

    client = db_session.get(Client, matter.client_id)
    assert client.name == "Neuer Mandant GmbH"


def test_named_client_does_not_use_the_placeholder(db_session: Session) -> None:
    matter = create_quick_matter(
        db_session, title=None, client_name="Beispiel GmbH", actor="anwalt@kanzlei.test"
    )

    client = db_session.get(Client, matter.client_id)
    assert client.name == "Beispiel GmbH"
    assert db_session.query(Client).filter(Client.name == PLACEHOLDER_CLIENT_NAME).count() == 0


def test_placeholder_reuse_still_writes_an_audit_event_per_matter(db_session: Session) -> None:
    """Die automatische Aktenanlage bleibt eine fuer den Anwalt nicht
    offensichtliche Nebenwirkung - sie muss je Akte nachvollziehbar
    bleiben, auch wenn der Mandant wiederverwendet wird."""
    create_quick_matter(db_session, title=None, client_name=None, actor="anwalt@kanzlei.test")
    create_quick_matter(db_session, title=None, client_name=None, actor="anwalt@kanzlei.test")

    events = db_session.query(AuditEvent).filter_by(event_type="matter_auto_created").all()
    assert len(events) == 2
    assert {e.entity_id for e in events} == {m.id for m in db_session.query(Matter).all()}


def test_existing_placeholder_from_earlier_runs_is_reused(db_session: Session) -> None:
    """Auf einer bestehenden Installation existiert der Platzhalter bereits.
    Er muss wiederverwendet werden, statt einen 29. anzulegen."""
    existing = Client(name=PLACEHOLDER_CLIENT_NAME)
    db_session.add(existing)
    db_session.commit()

    matter = create_quick_matter(
        db_session, title=None, client_name=None, actor="anwalt@kanzlei.test"
    )

    assert matter.client_id == existing.id
    assert db_session.query(Client).count() == 1
