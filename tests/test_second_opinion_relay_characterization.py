"""§43.10 Phase 2 (nur Feature-Branch): CHARAKTERISIERUNG des heutigen Lexono-Relays (`gateway/`, ARCHITECTURE.md §70)
fuer eine spaetere Cloud-Zweitmeinung - das Relay wird NICHT veraendert, kein echter Anthropic-Aufruf (SDK gepatcht).

Diese Tests halten den IST-Zustand fest, damit eine spaetere, gesondert freigegebene Relay-Aenderung bewusst und
sichtbar an ihnen scheitert: Das Relay kennt keinen Zweck, prueft weder Pseudonymisierung noch Siegel (Vertrauensgrenze
ist der Kanzlei-Client) und reicht kein `thinking` weiter. Logs bleiben inhaltsfrei."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.second_opinion.provider import SECOND_OPINION_SYSTEM_PROMPT
from gateway.config import GatewaySettings, get_gateway_settings
from gateway.main import app, get_db
from gateway.models import Base, Tenant
from gateway.schemas import RelayRequest
from gateway.security import generate_client_secret, hash_client_secret

PSEUDONYMIZED = "Herrn [PERSON_01]\n[ADRESSE_01]\nbitte zahlen Sie bis zum 31.07.2026."
CLEARTEXT = "Herrn Maximilian Probst-Fiktiv\nLindenallee 12, 12345 Musterstadt\nbitte zahlen Sie bis zum 31.07.2026."


@pytest.fixture()
def relay() -> Iterator[tuple[TestClient, dict]]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db: Session = sessionmaker(bind=engine)()
    secret = generate_client_secret()
    tenant = Tenant(display_name="Synthetische Kanzlei", secret_hash=hash_client_secret(secret),
                    rate_limit_per_minute=30)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    settings = GatewaySettings(anthropic_api_key="sk-ant-fake-server-side-key", allowed_models=["claude-sonnet-5"])

    def _db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_gateway_settings] = lambda: settings
    try:
        yield TestClient(app), {"Authorization": f"Bearer {tenant.client_id}:{secret}"}
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def _body(user_text: str, *, extra: dict | None = None) -> dict:
    return {"model": "claude-sonnet-5", "max_tokens": 600,
            "system": [{"type": "text", "text": SECOND_OPINION_SYSTEM_PROMPT}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": user_text}]}], **(extra or {})}


def _mock_response() -> MagicMock:
    block = MagicMock()
    block.type, block.text = "text", '{"typ": "Mahnung", "frist": ["31.07.2026"], "termin": [], "fristbeginn": []}'
    response = MagicMock()
    response.content = [block]
    response.usage.input_tokens, response.usage.output_tokens = 120, 30
    return response


def test_relay_has_no_purpose_field_and_forwards_a_second_opinion_like_any_request(relay) -> None:
    client, headers = relay
    assert "purpose" not in RelayRequest.model_fields and "schreibauftrag" not in RelayRequest.model_fields
    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        anthropic_cls.return_value.messages.create.return_value = _mock_response()
        response = client.post("/v1/relay/messages", json=_body(PSEUDONYMIZED, extra={"purpose": "beliebig"}),
                               headers=headers)
    assert response.status_code == 200  # weder abgelehnt noch zweckgeprueft; Zusatzfeld still ignoriert
    _, create_kwargs = anthropic_cls.return_value.messages.create.call_args
    assert "thinking" not in create_kwargs  # Relay reicht `thinking` nicht weiter (Direkt-Provider: disabled)
    _, client_kwargs = anthropic_cls.call_args
    assert client_kwargs["timeout"] == 60.0 and "max_retries" not in client_kwargs  # SDK-Standard: 2 Wiederholungen


def test_relay_cannot_verify_pseudonymization_or_seal(relay) -> None:
    """Vertrauensgrenze ist der Kanzlei-Client: auch Klartext wird weitergeleitet (das Siegel ist ein
    prozesslokales HMAC und erreicht das Relay nicht)."""
    client, headers = relay
    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        anthropic_cls.return_value.messages.create.return_value = _mock_response()
        response = client.post("/v1/relay/messages", json=_body(CLEARTEXT), headers=headers)
    assert response.status_code == 200
    _, create_kwargs = anthropic_cls.return_value.messages.create.call_args
    assert "Probst-Fiktiv" in str(create_kwargs["messages"])


def test_relay_logs_stay_content_free_for_a_second_opinion(relay, caplog: pytest.LogCaptureFixture) -> None:
    client, headers = relay
    with caplog.at_level("INFO", logger="lexono_gateway.relay"):
        with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
            anthropic_cls.return_value.messages.create.return_value = _mock_response()
            client.post("/v1/relay/messages", json=_body(CLEARTEXT), headers=headers)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "status=200" in log_text and "input_tokens=120" in log_text
    for fragment in ("Probst", "Lindenallee", "31.07.2026", "Mahnung", "Zweitmeinung", "zweite"):
        assert fragment not in log_text


def test_relay_limits_that_already_apply(relay) -> None:
    client, headers = relay
    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        too_many_tokens = client.post("/v1/relay/messages", json={**_body(PSEUDONYMIZED), "max_tokens": 5000},
                                      headers=headers)
        wrong_model = client.post("/v1/relay/messages", json={**_body(PSEUDONYMIZED), "model": "anderes-modell"},
                                  headers=headers)
        too_large = client.post("/v1/relay/messages", json=_body("x" * 210_000), headers=headers)
    assert (too_many_tokens.status_code, wrong_model.status_code, too_large.status_code) == (400, 400, 413)
    anthropic_cls.assert_not_called()


def test_upstream_error_is_a_generic_502_without_content(relay) -> None:
    import anthropic

    client, headers = relay
    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        anthropic_cls.return_value.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        response = client.post("/v1/relay/messages", json=_body(PSEUDONYMIZED), headers=headers)
    assert response.status_code == 502 and "PERSON" not in response.text


def test_writing_path_default_max_tokens_exceeds_the_relay_ceiling(relay) -> None:
    """§43.11 Phase 3, Befund am BESTEHENDEN Schreibpfad (nur festgehalten, nicht geaendert): Code-Standard
    `claude_max_tokens` (4096) liegt ueber der Relay-Obergrenze `max_tokens_ceiling` (4000). Ohne CLAUDE_MAX_TOKENS in
    der .env (die .env.example setzt 2000) lehnt das Relay jede Schreibanfrage mit 400 ab."""
    from app.config.settings import Settings

    client_default = Settings.model_fields["claude_max_tokens"].default
    relay_ceiling = GatewaySettings.model_fields["max_tokens_ceiling"].default
    assert (client_default, relay_ceiling) == (4096, 4000)
    client, headers = relay
    with patch("gateway.relay.anthropic.Anthropic") as anthropic_cls:
        response = client.post("/v1/relay/messages", json={**_body(PSEUDONYMIZED), "max_tokens": client_default},
                               headers=headers)
    assert response.status_code == 400
    anthropic_cls.assert_not_called()
