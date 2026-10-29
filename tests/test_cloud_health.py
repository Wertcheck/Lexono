"""Cloud-KI-Status: Konfiguration / Erreichbarkeit / letzte Anfrage (Pilot-Vorbereitung)."""

from __future__ import annotations

import socket
from types import SimpleNamespace

from app.ai_providers import cloud_health as ch


def _settings(**kw):
    base = {"lexono_gateway_url": None, "anthropic_api_key": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_not_configured_without_key_or_gateway() -> None:
    called = []
    result = ch.compute_cloud_health(_settings(), probe=lambda *a: called.append(a) or (True, ""))
    assert result.state == ch.NOT_CONFIGURED
    assert called == [], "ohne Konfiguration darf keine Verbindung aufgebaut werden"


def test_gateway_url_has_priority_and_is_probed() -> None:
    seen = []
    result = ch.compute_cloud_health(
        _settings(lexono_gateway_url="https://gw.example.test:8443/x", anthropic_api_key="k",
                  lexono_gateway_client_id="kanzlei-test", lexono_gateway_client_secret="s"),
        probe=lambda *a: seen.append(a) or (True, ""),
    )
    assert seen == [("gw.example.test", 8443, True)]
    assert result.state == ch.REACHABLE
    assert result.route == ch.ROUTE_GATEWAY


def test_gateway_without_credentials_is_not_reported_ready() -> None:
    """§72: die Factory baut ohne Zugangsdaten keinen Gateway-Provider (kein Rueckfall auf direkt) - kein "Bereit"."""
    called = []
    result = ch.compute_cloud_health(
        _settings(lexono_gateway_url="https://gw.example.test/x", anthropic_api_key="k"),
        probe=lambda *a: called.append(a) or (True, ""),
    )
    assert result.state == ch.NOT_CONFIGURED and result.route == "" and called == []


def test_direct_route_to_the_official_address(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    seen = []
    result = ch.compute_cloud_health(_settings(anthropic_api_key="k"), probe=lambda *a: seen.append(a) or (True, ""))
    assert seen == [("api.anthropic.com", 443, True)]
    assert result.state == ch.REACHABLE and result.route == ch.ROUTE_DIRECT


def test_qa_loopback_endpoint_is_named_as_such(monkeypatch) -> None:
    """§71-Befund: direkt an einen lokalen Pruefendpunkt gesendet, angezeigt wurde "Cloud-KI (Gateway)"."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:52604")
    monkeypatch.setenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1")
    result = ch.compute_cloud_health(_settings(anthropic_api_key="k"), probe=lambda *a: (True, ""))
    assert result.route == ch.ROUTE_QA_LOOPBACK and result.state == ch.REACHABLE


def test_route_is_kept_when_unreachable(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    result = ch.compute_cloud_health(_settings(anthropic_api_key="k"), probe=lambda *a: (False, "Zeitüberschreitung"))
    assert result.state == ch.UNREACHABLE and result.route == ch.ROUTE_DIRECT


def test_disallowed_base_url_has_no_route(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://198.51.100.7:8080")
    monkeypatch.delenv("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", raising=False)
    assert ch.cloud_route(_settings(anthropic_api_key="k")) == ""
    assert ch.compute_cloud_health(_settings(anthropic_api_key="k"), probe=lambda *a: (True, "")).state == ch.NOT_CONFIGURED


def test_unreachable_carries_category_only() -> None:
    result = ch.compute_cloud_health(
        _settings(anthropic_api_key="sk-geheim"), probe=lambda *a: (False, "Zeitüberschreitung")
    )
    assert result.state == ch.UNREACHABLE
    assert result.reason == "Zeitüberschreitung"
    assert "sk-geheim" not in repr(result)


def test_probe_closed_local_port_reports_refused() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ok, reason = ch.probe_reachability("127.0.0.1", port, False, timeout=1.0)
    assert ok is False
    assert reason in {"Verbindung abgelehnt", "Netzwerkfehler", "Zeitüberschreitung"}


def test_probe_open_local_port_is_reachable() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        ok, reason = ch.probe_reachability("127.0.0.1", srv.getsockname()[1], False, timeout=1.0)
    assert (ok, reason) == (True, "")


def test_request_failed_when_last_call_errored(monkeypatch) -> None:
    class _DB:
        def close(self) -> None:
            pass

    monkeypatch.setattr(ch, "last_request_failed", lambda db: True)
    result = ch.compute_cloud_health(
        _settings(anthropic_api_key="k"), session_factory=_DB, probe=lambda *a: (True, "")
    )
    assert result.state == ch.REQUEST_FAILED


def test_unreachable_wins_over_request_failed(monkeypatch) -> None:
    monkeypatch.setattr(ch, "last_request_failed", lambda db: True)
    result = ch.compute_cloud_health(
        _settings(anthropic_api_key="k"), session_factory=lambda: None, probe=lambda *a: (False, "Netzwerkfehler")
    )
    assert result.state == ch.UNREACHABLE


def test_log_query_failure_does_not_break_status() -> None:
    class _DB:
        def query(self, *a):
            raise RuntimeError("db down")

        def close(self) -> None:
            pass

    result = ch.compute_cloud_health(
        _settings(anthropic_api_key="k"), session_factory=_DB, probe=lambda *a: (True, "")
    )
    assert result.state == ch.REACHABLE
