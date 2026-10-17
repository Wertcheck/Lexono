"""Gemeinsame Hilfsfunktionen für Tests, die eine angemeldete Session
brauchen (Prompt 26). Wird von Testdateien importiert, die VOR Prompt 26
geschrieben wurden und jetzt Login-Pflicht berücksichtigen müssen -
zentraler Ort, um Duplikation über mehrere Testdateien zu vermeiden.

Bewusst KEINE pytest-Fixture-Datei (kein conftest.py) - jede Testdatei
entscheidet selbst, ob/wie sie diese Hilfsfunktionen einsetzt, um deren
bestehende Fixture-Struktur nicht zu verändern.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.models import Role, User

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

DEFAULT_TEST_PASSWORD = "TestPasswort123"


def extract_csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None, "Kein csrf_token-Feld im HTML gefunden"
    return match.group(1)


_OPERATION_RE = re.compile(r'name="operation_token" value="([^"]+)"')


_UPLOAD_RE = re.compile(r'name="upload_token" value="([^"]+)"')


def extract_upload_token(html: str) -> str:
    """§59: Upload-Vorgangs-ID des Chat-Eingabefelds (serverseitig ausgestellt, je Seite neu)."""
    match = _UPLOAD_RE.search(html)
    assert match is not None, "Kein upload_token-Feld im HTML gefunden"
    return match.group(1)


def extract_operation_token(html: str) -> str:
    """§58: Vorgangs-ID des Schriftsatz-Generators (serverseitig ausgestellt, je Seite neu)."""
    match = _OPERATION_RE.search(html)
    assert match is not None, "Kein operation_token-Feld im HTML gefunden"
    return match.group(1)


def seed_roles(db: Session) -> dict[str, Role]:
    """Idempotent: legt die drei Rollen an, falls noch nicht vorhanden."""
    roles = {}
    for name in ("Admin", "Anwalt", "Mitarbeiter"):
        role = db.query(Role).filter_by(name=name).first()
        if role is None:
            role = Role(name=name)
            db.add(role)
            db.flush()
        roles[name.lower()] = role
    db.commit()
    return roles


def create_test_user(
    db: Session, role: Role, email: str, password: str = DEFAULT_TEST_PASSWORD
) -> User:
    user = User(
        email=email,
        role_id=role.id,
        is_active=True,
        password_hash=hash_password(password),
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    return user


def login(client: TestClient, email: str, password: str = DEFAULT_TEST_PASSWORD) -> None:
    response = client.post(
        "/dashboard/login",
        data={"email": email, "password": password, "next": "/dashboard/inbox"},
        follow_redirects=False,
    )
    assert response.status_code == 303, f"Login fehlgeschlagen: {response.headers}"


def login_as_admin(db: Session, client: TestClient, email: str = "admin@kanzlei.test") -> User:
    """Bequemlichkeitsfunktion für Altbestand-Tests (Prompts 21-25), die
    NICHT die neue Rollenlogik selbst testen (das übernimmt
    tests/test_auth_web.py gründlich) - Admin hat alle Berechtigungen,
    verändert die ursprünglich getestete Fachlogik also nicht."""
    roles = seed_roles(db)
    user = create_test_user(db, roles["admin"], email)
    login(client, email)
    return user
