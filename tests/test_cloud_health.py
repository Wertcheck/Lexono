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
        _settings(lexono_gateway_url="https://gw.example.test:8443/x", anthropic_api_key="k"),
        probe=lambda *a: seen.append(a) or (True, ""),
    )
    assert seen == [("gw.example.test", 8443, True)]
    assert result.state == ch.REACHABLE


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
