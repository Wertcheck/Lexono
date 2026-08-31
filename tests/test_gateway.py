"""Tests für den Lexono-Gateway (ARCHITECTURE.md §70) - eigener, von der
Kanzlei-Anwendung unabhängiger Deployment-Kontext (siehe gateway/__init__.py).

Deckt ab: Authentifizierung (gültige/ungültige/widerrufene Credential),
Mandantentrennung, Rate-Limiting, Modell-/Token-Allowlist, KEINE
Persistenz von Request-/Response-Inhalten, und dass der Gateway die
einzige Stelle ist, die den echten Anthropic-Key tatsächlich verwendet."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from gateway.config import GatewaySettings, get_gateway_settings
from gateway.db import build_engine
from gateway.main import app, get_db
from gateway.models import Base, Tenant
from gateway.rate_limiter import SlidingWindowRateLimiter
from gateway.security import generate_client_secret, hash_client_secret
from gateway.tenant_admin import create_tenant, revoke_tenant, rotate_tenant_secret


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
def gateway_settings() -> GatewaySettings:
    return GatewaySettings(
        anthropic_api_key="sk-ant-fake-server-side-key",
        allowed_models=["claude-sonnet-5"],
        max_tokens_ceiling=4000,
        default_rate_limit_per_minute=30,
    )


@pytest.fixture()
def client(db_session: Session, gateway_settings: GatewaySettings) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_gateway_settings] = lambda: gateway_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def tenant_credential(db_session: Session) -> tuple[Tenant, str]:
    secret = generate_client_secret()
    tenant = Tenant(
        display_name="Test-Kanzlei",
        secret_hash=hash_client_secret(secret),
        rate_limit_per_minute=30,
    )
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant, secret


def _auth_header(tenant: Tenant, secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.client_id}:{secret}"}


def _relay_body(model: str = "claude-sonnet-5", max_tokens: int = 500) -> dict:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": "System-Anweisung"}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hallo"}]}],
    }


def _mock_anthropic_response(text: str = "Antworttext") -> MagicMock:
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 5
    response.usage = usage
    return response


# --- Datenbank-Verzeichnis (echter Bug, gefunden beim ersten realen
# Gateway-Start: SQLite legt das Verzeichnis der DB-Datei nicht selbst an) ---


def test_build_engine_creates_missing_sqlite_parent_directory(tmp_path) -> None:
    nested_db_path = tmp_path / "does" / "not" / "exist" / "gateway.db"
    settings = GatewaySettings(database_url=f"sqlite:///{nested_db_path}")

    engine = build_engine(settings)
    Base.metadata.create_all(engine)  # darf nicht mit "unable to open database file" scheitern

    assert nested_db_path.parent.exists()
    engine.dispose()


# --- Health ---


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- Authentifizierung ---


def test_relay_without_authorization_header_is_rejected(client: TestClient) -> None:
    response = client.post("/v1/relay/messages", json=_relay_body())
    assert response.status_code == 401


def test_relay_with_malformed_authorization_header_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/relay/messages",
        json=_relay_body(),
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert response.status_code == 401


def test_relay_with_unknown_client_id_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/relay/messages",
        json=_relay_body(),
        headers={"Authorization": "Bearer unknown-client-id:some-secret"},
    )
    assert response.status_code == 401


def test_relay_with_wrong_secret_is_rejected(
    client: TestClient, tenant_credential: tuple[Tenant, str]
) -> None:
    tenant, _ = tenant_credential
    response = client.post(
        "/v1/relay/messages",
        json=_relay_body(),
        headers={"Authorization": f"Bearer {tenant.client_id}:falsches-secret"},
    )
    assert response.status_code == 401


def test_relay_with_revoked_tenant_is_rejected(
    client: TestClient, db_session: Session, tenant_credential: tuple[Tenant, str]
) -> None:
    tenant, secret = tenant_credential
    revoke_tenant(db_session, client_id=tenant.client_id)
    response = client.post(
        "/v1/relay/messages", json=_relay_body(), headers=_auth_header(tenant, secret)
    )
    assert response.status_code == 401


def test_relay_with_valid_credential_and_mocked_anthropic_succeeds(
    client: TestClient, tenant_credential: tuple[Tenant, str]
) -> None:
    tenant, secret = tenant_credential
    with patch("gateway.relay.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_anthropic_response(
            "Synthetische Testantwort"
        )
        response = client.post(
            "/v1/relay/messages", json=_relay_body(), headers=_auth_header(tenant, secret)
        )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Synthetische Testantwort"
    assert body["input_tokens"] == 10
    assert body["output_tokens"] == 5


# --- Mandantentrennung ---


def test_one_tenant_credential_cannot_authenticate_as_another(
    client: TestClient, db_session: Session
) -> None:
    secret_a = generate_client_secret()
    tenant_a = Tenant(
        display_name="Kanzlei A", secret_hash=hash_client_secret(secret_a), rate_limit_per_minute=30
    )
    secret_b = generate_client_secret()
    tenant_b = Tenant(
        display_name="Kanzlei B", secret_hash=hash_client_secret(secret_b), rate_limit_per_minute=30
    )
    db_session.add_all([tenant_a, tenant_b])
    db_session.commit()
    db_session.refresh(tenant_a)
    db_session.refresh(tenant_b)

    # Kanzlei As client_id mit Kanzlei Bs Secret - muss scheitern.
    response = client.post(
        "/v1/relay/messages",
        json=_relay_body(),
        headers={"Authorization": f"Bearer {tenant_a.client_id}:{secret_b}"},
    )
    assert response.status_code == 401


# --- Rate-Limiting ---


def test_rate_limit_blocks_after_configured_number_of_requests(
    client: TestClient, db_session: Session
) -> None:
    secret = generate_client_secret()
    tenant = Tenant(
        display_name="Rate-Limit-Test",
        secret_hash=hash_client_secret(secret),
        rate_limit_per_minute=2,
    )
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    with patch("gateway.relay.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_anthropic_response()
        r1 = client.post(
            "/v1/relay/messages", json=_relay_body(), headers=_auth_header(tenant, secret)
        )
        r2 = client.post(
            "/v1/relay/messages", json=_relay_body(), headers=_auth_header(tenant, secret)
        )
        r3 = client.post(
            "/v1/relay/messages", json=_relay_body(), headers=_auth_header(tenant, secret)
        )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_sliding_window_rate_limiter_unit() -> None:
    limiter = SlidingWindowRateLimiter()
    assert limiter.allow("t1", limit_per_minute=2, now=0.0) is True
    assert limiter.allow("t1", limit_per_minute=2, now=1.0) is True
    assert limiter.allow("t1", limit_per_minute=2, now=2.0) is False
    # Nach Ablauf des 60s-Fensters wieder erlaubt.
    assert limiter.allow("t1", limit_per_minute=2, now=61.5) is True


def test_rate_limiter_tracks_tenants_independently() -> None:
    limiter = SlidingWindowRateLimiter()
    assert limiter.allow("tenant-a", limit_per_minute=1, now=0.0) is True
    assert limiter.allow("tenant-a", limit_per_minute=1, now=0.5) is False
    assert limiter.allow("tenant-b", limit_per_minute=1, now=0.5) is True


# --- Modell-/Token-Allowlist (Kostenkontrolle) ---


def test_relay_rejects_model_not_in_allowlist(
    client: TestClient, tenant_credential: tuple[Tenant, str]
) -> None:
    tenant, secret = tenant_credential
    response = client.post(
        "/v1/relay/messages",
        json=_relay_body(model="claude-opus-4-1-not-allowed"),
        headers=_auth_header(tenant, secret),
    )
    assert response.status_code == 400


def test_relay_rejects_max_tokens_above_ceiling(
    client: TestClient, tenant_credential: tuple[Tenant, str]
) -> None:
    tenant, secret = tenant_credential
    response = client.post(
        "/v1/relay/messages",
        json=_relay_body(max_tokens=999999),
        headers=_auth_header(tenant, secret),
    )
    assert response.status_code == 400


def test_relay_without_configured_api_key_fails_closed(
    client: TestClient, tenant_credential: tuple[Tenant, str]
) -> None:
    app.dependency_overrides[get_gateway_settings] = lambda: GatewaySettings(
        anthropic_api_key=None, allowed_models=["claude-sonnet-5"]
    )
    tenant, secret = tenant_credential
    response = client.post(
        "/v1/relay/messages", json=_relay_body(), headers=_auth_header(tenant, secret)
    )
    assert response.status_code == 503


def test_relay_translates_upstream_error_without_leaking_details(
    client: TestClient, tenant_credential: tuple[Tenant, str]
) -> None:
    import anthropic

    tenant, secret = tenant_credential
    with patch("gateway.relay.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = anthropic.APIError(
            "irrelevant internal detail", request=MagicMock(), body=None
        )
        response = client.post(
            "/v1/relay/messages", json=_relay_body(), headers=_auth_header(tenant, secret)
        )
    assert response.status_code == 502
    assert "irrelevant internal detail" not in response.text


# --- Keine Persistenz von Inhalten ---


def test_gateway_database_has_no_content_related_tables(db_session: Session) -> None:
    """Strukturelle Absicherung: das Gateway-Schema besitzt außer `tenants`
    keine weitere Tabelle - insbesondere keine für Requests/Responses/
    Prompts/Chatverläufe."""
    table_names = set(Base.metadata.tables.keys())
    assert table_names == {"tenants"}


def test_tenant_model_has_no_content_fields() -> None:
    column_names = {column.name for column in Tenant.__table__.columns}
    forbidden_substrings = ("prompt", "message", "content", "payload", "document", "text")
    for column_name in column_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in column_name.lower(), (
                f"Tenant-Spalte {column_name!r} klingt nach Inhaltsspeicherung"
            )


# --- Credential-Lebenszyklus ---


def test_create_tenant_returns_working_credential(db_session: Session) -> None:
    credential = create_tenant(db_session, display_name="Neue Kanzlei", rate_limit_per_minute=10)
    tenant = db_session.query(Tenant).filter(Tenant.client_id == credential.client_id).first()
    assert tenant is not None
    assert tenant.is_active is True
    assert tenant.secret_hash != credential.client_secret  # niemals Klartext gespeichert


def test_revoke_tenant_marks_inactive(db_session: Session) -> None:
    credential = create_tenant(db_session, display_name="Zu widerrufen", rate_limit_per_minute=10)
    assert revoke_tenant(db_session, client_id=credential.client_id) is True
    tenant = db_session.query(Tenant).filter(Tenant.client_id == credential.client_id).first()
    assert tenant.is_active is False
    assert tenant.revoked_at is not None


def test_revoke_unknown_tenant_returns_false(db_session: Session) -> None:
    assert revoke_tenant(db_session, client_id="does-not-exist") is False


def test_rotate_tenant_secret_invalidates_old_secret(
    client: TestClient, db_session: Session
) -> None:
    credential = create_tenant(db_session, display_name="Rotation-Test", rate_limit_per_minute=30)
    tenant = db_session.query(Tenant).filter(Tenant.client_id == credential.client_id).first()

    new_secret = rotate_tenant_secret(db_session, client_id=credential.client_id)
    assert new_secret is not None
    assert new_secret != credential.client_secret

    # Altes Secret funktioniert nicht mehr.
    old_response = client.post(
        "/v1/relay/messages",
        json=_relay_body(),
        headers={"Authorization": f"Bearer {tenant.client_id}:{credential.client_secret}"},
    )
    assert old_response.status_code == 401

    # Neues Secret funktioniert.
    with patch("gateway.relay.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_anthropic_response()
        new_response = client.post(
            "/v1/relay/messages",
            json=_relay_body(),
            headers={"Authorization": f"Bearer {tenant.client_id}:{new_secret}"},
        )
    assert new_response.status_code == 200
