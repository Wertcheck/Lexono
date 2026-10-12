"""Zulaessige Netzwerkziele fuer KI-Aufrufe (Sicherheitspruefung 13.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md §21).

1. Cloud (Anthropic, direkter Pfad): Das Anthropic-SDK uebernimmt still die Umgebungsvariable `ANTHROPIC_BASE_URL`. Ist sie
   gesetzt (IT-Skript, Schadsoftware, Testrest), gingen pseudonymisierte Anfragen UND der API-Schluessel (`x-api-key`) an
   diesen Rechner - auch unverschluesselt. Zulaessig ist deshalb nur die offizielle HTTPS-Adresse oder ein Pruefendpunkt
   auf DIESEM Rechner (lokale QA-Erfassung, nur mit QA_LOOPBACK_FLAG); jedes andere Ziel ist ein Konfigurationsfehler
   (fail-closed, kein Versand).
2. Lokale KI (Ollama): Standard ist ein Endpunkt auf diesem Rechner. Ein anderer Rechner nur mit ausdruecklicher Freigabe
   (`LOCAL_AI_ALLOW_REMOTE_ENDPOINT=true`) - Pseudonymisierung allein ist kein Vertrauensnachweis fuer fremde Rechner."""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

OFFICIAL_ANTHROPIC_BASE_URL = "https://api.anthropic.com"


class CloudEndpointNotAllowedError(ValueError):
    """`ANTHROPIC_BASE_URL` zeigt auf ein nicht zulaessiges Ziel."""


def is_loopback_url(url: str) -> bool:
    """True nur, wenn der Host dieser Rechner ist ("localhost", 127.0.0.0/8, ::1)."""
    host = (urlsplit(url.strip()).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


#: Review 13.10.2026: ein einzelnes ANTHROPIC_BASE_URL auf diesen Rechner haette einem lokalen Prozess still JEDE Anfrage
#: zugeleitet. Ein Pruefendpunkt auf diesem Rechner braucht deshalb zusaetzlich diese ausdrueckliche QA-Freigabe ("1").
QA_LOOPBACK_FLAG = "LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT"


def resolve_anthropic_base_url(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    override = env.get("ANTHROPIC_BASE_URL", "").strip()
    if not override:
        return OFFICIAL_ANTHROPIC_BASE_URL
    if override.rstrip("/") == OFFICIAL_ANTHROPIC_BASE_URL:
        return override
    if is_loopback_url(override) and env.get(QA_LOOPBACK_FLAG, "").strip() == "1":
        return override
    host = urlsplit(override).hostname or override
    raise CloudEndpointNotAllowedError(
        f"ANTHROPIC_BASE_URL zeigt auf '{host}' - zulaessig sind nur {OFFICIAL_ANTHROPIC_BASE_URL} oder ein lokaler "
        f"Pruefendpunkt auf diesem Rechner mit {QA_LOOPBACK_FLAG}=1. Kein Cloud-Versand."
    )
