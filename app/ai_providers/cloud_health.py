"""Cloud-KI-Status: Konfiguration, tatsaechliche Erreichbarkeit und letzte Anfrage.

Die Sidebar zeigte "Bereit", sobald ein API-Schluessel bzw. eine Gateway-URL konfiguriert war - ohne
nachzuweisen, dass die Cloud ueberhaupt erreichbar ist. Jetzt werden drei Dinge getrennt:

1. Konfiguration vorhanden? (Schluessel/Gateway - nur das BOOLEAN, nie der Wert)
2. Verbindung erreichbar? Rein netzwerkseitige Pruefung (TCP und, bei https, TLS-Handshake mit
   Zertifikatspruefung) zum Host der Cloud-Anbindung, mit kurzem Timeout. Es wird KEINE API-Anfrage
   gesendet, KEIN Schluessel benutzt und kein Inhalt uebertragen - kein Kostenrisiko und keine
   Beruehrung des Datenschutzpfads.
3. Letzte echte Anfrage fehlgeschlagen? Aus dem bestehenden Aufruf-Protokoll (`ApiCallLog`): die
   juengste Anfrage endete mit einem Provider-Fehler (z. B. ungueltiger Schluessel, kein Guthaben).

"Bereit" wird nur gezeigt, wenn die Verbindung nachweislich erreichbar ist und die letzte Anfrage nicht
fehlgeschlagen ist. Die bestehende Fehlerbehandlung und die Fail-Closed-Logik des Datenschutzpfads
bleiben unberuehrt."""

from __future__ import annotations

import logging
import os
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

logger = logging.getLogger("lexono.cloud_health")

NOT_CONFIGURED = "not_configured"
CHECKING = "checking"
REACHABLE = "reachable"
UNREACHABLE = "unreachable"
REQUEST_FAILED = "request_failed"

DEFAULT_API_BASE_URL = "https://api.anthropic.com"
PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class CloudHealth:
    state: str
    #: Feste, inhaltsfreie Kategorie (z. B. "Zeitüberschreitung") - nie Schluessel, URLs mit Zugangsdaten
    #: oder Anfrageinhalte.
    reason: str = ""
    checked_at: datetime | None = None


def probe_target(settings) -> tuple[str, int, bool] | None:
    """(Host, Port, TLS?) der konfigurierten Cloud-Anbindung oder None, wenn nichts konfiguriert ist.

    Gateway-URL hat Vorrang; sonst der direkte Pfad - wie das SDK mit `ANTHROPIC_BASE_URL`
    (Umgebungsvariable) bzw. der Standard-API-Adresse."""
    gateway_url = getattr(settings, "lexono_gateway_url", None)
    api_key = getattr(settings, "anthropic_api_key", None)
    if gateway_url:
        url = str(gateway_url)
    elif api_key is not None:
        url = os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_API_BASE_URL
    else:
        return None
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    tls = parsed.scheme != "http"
    port = parsed.port or (443 if tls else 80)
    return host, port, tls


def probe_reachability(host: str, port: int, tls: bool, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Verbindung (TCP, bei TLS inkl. Handshake und Zertifikatspruefung) mit Timeout - ohne Inhalt."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if tls:
                context = ssl.create_default_context()
                with context.wrap_socket(sock, server_hostname=host):
                    pass
        return True, ""
    except socket.timeout:
        return False, "Zeitüberschreitung"
    except socket.gaierror:
        return False, "Adresse nicht auflösbar"
    except ConnectionRefusedError:
        return False, "Verbindung abgelehnt"
    except ssl.SSLError:
        return False, "TLS-/Zertifikatsfehler"
    except OSError:
        return False, "Netzwerkfehler"


def last_request_failed(db: Session) -> bool:
    """Endete die juengste protokollierte Cloud-Anfrage mit einem Provider-Fehler?"""
    from app.models import ApiCallLog

    latest = db.query(ApiCallLog).order_by(ApiCallLog.created_at.desc()).first()
    return bool(latest is not None and latest.result_status == "error" and latest.error_status == "writing_provider_exception")


def compute_cloud_health(
    settings,
    *,
    session_factory: Callable[[], Session] | None = None,
    probe: Callable[..., tuple[bool, str]] = probe_reachability,
) -> CloudHealth:
    now = datetime.now(timezone.utc)
    target = probe_target(settings)
    if target is None:
        return CloudHealth(NOT_CONFIGURED, "", now)
    reachable, reason = probe(*target)
    if not reachable:
        return CloudHealth(UNREACHABLE, reason, now)
    if session_factory is not None:
        db = session_factory()
        try:
            if last_request_failed(db):
                return CloudHealth(REQUEST_FAILED, "letzte Anfrage fehlgeschlagen", now)
        except Exception:  # noqa: BLE001 - Status darf nie an der Protokoll-Abfrage scheitern
            logger.debug("Aufruf-Protokoll fuer den Cloud-Status nicht lesbar", exc_info=True)
        finally:
            db.close()
    return CloudHealth(REACHABLE, "", now)
