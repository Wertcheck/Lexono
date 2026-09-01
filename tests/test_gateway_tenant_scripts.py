"""Tests fuer die duennen CLI-Wrapper-Skripte rund um Kanzlei-Credentials
(ARCHITECTURE.md §70, Umsetzungsplan Punkt 5). `scripts/create_gateway_tenant.py`
ist absichtlich unveraendert und hat bewusst kein eigenes Testmodul bekommen -
diese Tests folgen demselben Muster fuer die beiden neuen Skripte
(`revoke_gateway_tenant.py`, `rotate_gateway_tenant_secret.py`), die
ausschliesslich bereits getestete Funktionen aus `gateway/tenant_admin.py`
aufrufen - keine neue Credential-Logik hier."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.revoke_gateway_tenant as revoke_script
import scripts.rotate_gateway_tenant_secret as rotate_script
from gateway.models import Base, Tenant
from gateway.tenant_admin import create_tenant


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


def _patch_script_db(monkeypatch: pytest.MonkeyPatch, module, db_session: Session) -> None:
    """Beide Skripte bauen sich intern eine eigene Engine/Session ueber
    `build_engine`/`build_session_factory`/`init_db` - fuer den Test wird
    stattdessen direkt die In-Memory-Testsession zurueckgegeben, ohne die
    Skriptlogik selbst zu veraendern."""
    monkeypatch.setattr(module, "get_gateway_settings", lambda: object())
    monkeypatch.setattr(module, "build_engine", lambda settings: object())
    monkeypatch.setattr(module, "init_db", lambda engine: None)
    monkeypatch.setattr(module, "build_session_factory", lambda engine: (lambda: db_session))


def test_revoke_script_requires_client_id_env_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("GATEWAY_TENANT_CLIENT_ID", raising=False)
    exit_code = revoke_script.main()
    assert exit_code == 1
    assert "GATEWAY_TENANT_CLIENT_ID" in capsys.readouterr().err


def test_revoke_script_revokes_existing_tenant(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, capsys: pytest.CaptureFixture
) -> None:
    credential = create_tenant(db_session, display_name="Zu widerrufen", rate_limit_per_minute=10)
    _patch_script_db(monkeypatch, revoke_script, db_session)
    monkeypatch.setenv("GATEWAY_TENANT_CLIENT_ID", credential.client_id)

    exit_code = revoke_script.main()

    assert exit_code == 0
    tenant = db_session.query(Tenant).filter(Tenant.client_id == credential.client_id).first()
    assert tenant.is_active is False
    assert "widerrufen" in capsys.readouterr().out.lower()


def test_revoke_script_reports_unknown_client_id(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, capsys: pytest.CaptureFixture
) -> None:
    _patch_script_db(monkeypatch, revoke_script, db_session)
    monkeypatch.setenv("GATEWAY_TENANT_CLIENT_ID", "does-not-exist")

    exit_code = revoke_script.main()

    assert exit_code == 1
    assert "FEHLER" in capsys.readouterr().err


def test_rotate_script_requires_client_id_env_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("GATEWAY_TENANT_CLIENT_ID", raising=False)
    exit_code = rotate_script.main()
    assert exit_code == 1
    assert "GATEWAY_TENANT_CLIENT_ID" in capsys.readouterr().err


def test_rotate_script_rotates_secret_and_invalidates_old_one(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, capsys: pytest.CaptureFixture
) -> None:
    credential = create_tenant(db_session, display_name="Rotation-Test", rate_limit_per_minute=10)
    _patch_script_db(monkeypatch, rotate_script, db_session)
    monkeypatch.setenv("GATEWAY_TENANT_CLIENT_ID", credential.client_id)

    exit_code = rotate_script.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert credential.client_secret not in output  # altes Secret nirgendwo mehr ausgegeben

    tenant = db_session.query(Tenant).filter(Tenant.client_id == credential.client_id).first()
    assert tenant.secret_hash != credential.client_secret


def test_rotate_script_reports_unknown_client_id(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, capsys: pytest.CaptureFixture
) -> None:
    _patch_script_db(monkeypatch, rotate_script, db_session)
    monkeypatch.setenv("GATEWAY_TENANT_CLIENT_ID", "does-not-exist")

    exit_code = rotate_script.main()

    assert exit_code == 1
    assert "FEHLER" in capsys.readouterr().err
