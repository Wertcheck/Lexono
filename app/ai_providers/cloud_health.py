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
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.ai_providers.endpoint_policy import CloudEndpointNotAllowedError, resolve_anthropic_base_url

logger = logging.getLogger("lexono.cloud_health")

NOT_CONFIGURED = "not_configured"
CHECKING = "checking"
REACHABLE = "reachable"
UNREACHABLE = "unreachable"
REQUEST_FAILED = "request_failed"

DEFAULT_API_BASE_URL = "https://api.anthropic.com"
PROBE_TIMEOUT_SECONDS = 3.0

# §72 (App-Test §71: "Cloud-KI (Gateway) Bereit", obwohl die Gateway-Zeile deaktiviert war und direkt an einen lokalen
# Pruefendpunkt gesendet wurde): die Sidebar nannte den Weg fest als "Gateway". Jetzt wird der TATSAECHLICH genutzte Weg
# mit derselben Regel wie `factory.build_writing_provider` bestimmt (Gateway-URL hat Vorrang, sonst direkt).
ROUTE_GATEWAY = "gateway"
ROUTE_DIRECT = "direct"
ROUTE_QA_LOOPBACK = "qa_loopback"


@dataclass(frozen=True)
class CloudHealth:
    state: str
    #: Feste, inhaltsfreie Kategorie (z. B. "Zeitüberschreitung") - nie Schluessel, URLs mit Zugangsdaten
    #: oder Anfrageinhalte.
    reason: str = ""
    checked_at: datetime | None = None
    #: Genutzter Weg (ROUTE_*) oder "" ohne vollstaendige Konfiguration - Konfiguration, kein Erreichbarkeitsnachweis.
    route: str = ""


def _gateway_credentials_present(settings) -> bool:
    secret = getattr(settings, "lexono_gateway_client_secret", None)
    secret_value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    return bool(getattr(settings, "lexono_gateway_client_id", None) and secret_value)


def cloud_route(settings) -> str:
    """Weg, den die Factory fuer Cloud-Anfragen nimmt: Gateway (URL + Zugangsdaten), direkt zur offiziellen Adresse,
    lokaler QA-Pruefendpunkt - oder "" (nicht nutzbar konfiguriert, die Factory wuerde keinen Provider bauen)."""
    if getattr(settings, "lexono_gateway_url", None):
        # Ohne Zugangsdaten verweigert die Factory den Gateway-Provider (kein Rueckfall auf direkt) - nicht nutzbar.
        return ROUTE_GATEWAY if _gateway_credentials_present(settings) else ""
    if getattr(settings, "anthropic_api_key", None) is None:
        return ""
    try:
        url = resolve_anthropic_base_url()
    except CloudEndpointNotAllowedError:
        return ""
    return ROUTE_DIRECT if url.rstrip("/") == DEFAULT_API_BASE_URL else ROUTE_QA_LOOPBACK


def probe_target(settings) -> tuple[str, int, bool] | None:
    """(Host, Port, TLS?) der konfigurierten Cloud-Anbindung oder None, wenn nichts nutzbar konfiguriert ist.

    Gateway-URL hat Vorrang; sonst der direkte Pfad - wie das SDK mit `ANTHROPIC_BASE_URL`
    (Umgebungsvariable) bzw. der Standard-API-Adresse."""
    gateway_url = getattr(settings, "lexono_gateway_url", None)
    api_key = getattr(settings, "anthropic_api_key", None)
    if gateway_url:
        if not _gateway_credentials_present(settings):
            return None  # §72: Factory baut ohne Zugangsdaten keinen Gateway-Provider - nicht "Bereit" melden
        url = str(gateway_url)
    elif api_key is not None:
        # Dieselbe Zielpruefung wie die Factory (§21): ein nicht zulaessiges ANTHROPIC_BASE_URL gilt dort als "nicht
        # konfiguriert" - hier ebenso, statt den fremden Host anzusprechen und "online" zu melden.
        try:
            url = resolve_anthropic_base_url()
        except CloudEndpointNotAllowedError:
            return None
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
    route = cloud_route(settings)
    reachable, reason = probe(*target)
    if not reachable:
        return CloudHealth(UNREACHABLE, reason, now, route)
    if session_factory is not None:
        db = session_factory()
        try:
            if last_request_failed(db):
                return CloudHealth(REQUEST_FAILED, "letzte Anfrage fehlgeschlagen", now, route)
        except Exception:  # noqa: BLE001 - Status darf nie an der Protokoll-Abfrage scheitern
            logger.debug("Aufruf-Protokoll fuer den Cloud-Status nicht lesbar", exc_info=True)
        finally:
            db.close()
    return CloudHealth(REACHABLE, "", now, route)
