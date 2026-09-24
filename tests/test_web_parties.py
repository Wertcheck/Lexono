"""Tests für app/web/parties_router.py (17.09., Owner-Direktive
§5/§6/§10 "technisch lösbare Lücke statt Produktentscheidung").

ECHTER FUND: `app.models.Party` wird bereits produktiv gelesen
(`RuleBasedLocalAIProvider._build_known_entities` - Pseudonymisierung +
CHAT-04-Fast-Path), hatte aber projektweit keinen einzigen Schreibpfad.
Siehe app/web/parties_router.py-Moduldocstring für die volle Begründung.

Gleiches Testmuster wie tests/test_web_matters.py: In-Memory-SQLite über
app.dependency_overrides."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import AuditEvent, Client, Matter, Party, User
from app.models.base import Base
from tests.auth_test_utils import extract_csrf, login_as_admin


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


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _matter(db: Session, *, title: str = "Testakte") -> Matter:
    client_row = Client(name="Max Mustermann", client_number=f"K-{uuid4().hex[:8]}")
    matter = Matter(client=client_row, title=title)
    db.add_all([client_row, matter])
    db.commit()
    db.refresh(matter)
    return matter


def _csrf(client: TestClient, matter_id: str) -> str:
    page = client.get(f"/dashboard/matters/{matter_id}")
    return extract_csrf(page.text)


def test_matter_detail_page_shows_parties_and_add_form(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    db_session.add(
        Party(matter_id=matter.id, name="Vermieter Schmidt GmbH", role="Gegner")
    )
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert response.status_code == 200
    assert "Vermieter Schmidt GmbH" in response.text
    assert "Gegner" in response.text
    assert 'name="name"' in response.text


def test_create_party_persists_it_with_matter_isolation(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/parties",
        data={
            "csrf_token": csrf,
            "name": "Rechtsanwältin Müller",
            "role": "gegnerische Anwältin",
            "email": "mueller@example-testdomain.invalid",
            "phone": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/matters/{matter.id}"
    party = db_session.query(Party).filter_by(matter_id=matter.id).first()
    assert party is not None
    assert party.name == "Rechtsanwältin Müller"
    assert party.role == "gegnerische Anwältin"
    assert party.email == "mueller@example-testdomain.invalid"
    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Party", entity_id=party.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "party_added"


def test_create_party_is_actually_used_by_known_entity_detection(
    client: TestClient, db_session: Session
) -> None:
    """Schliesst den Kreis zum eigentlichen Fund: eine ueber diesen neuen
    Weg angelegte Partei muss real von der bereits bestehenden
    `_build_known_entities`-Logik als "gegner" erkannt werden - nicht nur
    in der DB landen."""
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider

    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)
    client.post(
        f"/dashboard/matters/{matter.id}/parties",
        data={
            "csrf_token": csrf,
            "name": "Vermieter Schmidt GmbH",
            "role": "Gegner",
            "email": "",
            "phone": "",
        },
    )
    db_session.refresh(matter)

    known = RuleBasedLocalAIProvider()._build_known_entities(matter.id, matter, db_session)

    assert "Vermieter Schmidt GmbH" in known["gegner"]


def test_create_party_with_blank_name_is_rejected_without_persisting(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)

    client.post(
        f"/dashboard/matters/{matter.id}/parties",
        data={"csrf_token": csrf, "name": "   ", "role": "", "email": "", "phone": ""},
    )

    assert db_session.query(Party).filter_by(matter_id=matter.id).count() == 0


def test_delete_party_removes_it(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    party = Party(matter_id=matter.id, name="Zu entfernende Partei")
    db_session.add(party)
    db_session.commit()
    csrf = _csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/parties/{party.id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db_session.query(Party).filter_by(id=party.id).first() is None


def test_delete_party_respects_matter_isolation(
    client: TestClient, db_session: Session
) -> None:
    """Eine Partei einer FREMDEN Akte darf ueber die URL einer anderen
    Akte nicht loeschbar sein - Aktenisolation (CLAUDE.md)."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B")
    party_of_b = Party(matter_id=matter_b.id, name="Partei der Akte B")
    db_session.add(party_of_b)
    db_session.commit()
    csrf = _csrf(client, matter_a.id)

    client.post(
        f"/dashboard/matters/{matter_a.id}/parties/{party_of_b.id}/delete",
        data={"csrf_token": csrf},
    )

    assert db_session.query(Party).filter_by(id=party_of_b.id).first() is not None


def test_create_party_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)

    response = client.post(
        f"/dashboard/matters/{matter.id}/parties",
        data={"csrf_token": "invalid", "name": "Sollte nicht angelegt werden", "role": "", "email": "", "phone": ""},
    )

    assert response.status_code == 403
    assert db_session.query(Party).filter_by(matter_id=matter.id).count() == 0


def test_create_party_404s_for_unknown_matter(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    csrf = _csrf(client, matter.id)

    response = client.post(
        "/dashboard/matters/does-not-exist/parties",
        data={"csrf_token": csrf, "name": "Egal", "role": "", "email": "", "phone": ""},
    )

    assert response.status_code == 404
