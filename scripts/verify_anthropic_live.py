"""Opt-in-Nachweis des ECHTEN Anthropic-Aufrufs ueber den produktiven Pfad (PRIVACY_REVIEW_ARCHITECTURE.md §21).

Fuehrt NICHTS aus ohne ausdrueckliche Freigabe: erforderlich sind das Argument --authorized-live-call UND ein
ANTHROPIC_API_KEY in der Umgebung. Sendet ausschliesslich einen fest eingebauten, synthetischen Text ueber
Gateway (Pseudonymisierung, Restrisiko-Scan, Siegel) -> Factory (festgelegter offizieller Endpunkt) -> SDK.

Geprueft und ausgegeben (ohne Schluessel, ohne Inhalt der Antwort):
- Ziel-Host und Zertifikat der echten TLS-Verbindung (Aussteller, Gueltigkeit, ausgehandelte TLS-Version),
- dass kein synthetischer Klartextwert in der versendeten Payload steht,
- ob die Antwort ankam (nur Laenge und Platzhalter-Rueckfuehrung).

Aufruf:  python scripts/verify_anthropic_live.py --authorized-live-call
"""

from __future__ import annotations

import os
import socket
import ssl
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SYNTHETIC_TEXT = (
    "Akte: Beispielakte\n[Schreiben] Herr Karl Brenner, Musterweg 12, 12345 Musterstadt, Tel. 030 1234567, "
    "Kennzeichen des Gegners: D-XY 123. Bitte den Sachverhalt in zwei Saetzen zusammenfassen."
)
SYNTHETIC_VALUES = ("Karl Brenner", "Brenner", "Musterweg 12", "1234567", "D-XY 123")


def _tls_evidence(base_url: str) -> dict:
    host = urlparse(base_url).hostname
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((host, 443), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
            issuer = dict(item[0] for item in cert.get("issuer", ()))
            return {
                "host": host,
                "tls_version": tls.version(),
                "issuer": issuer.get("organizationName", "?"),
                "not_after": cert.get("notAfter"),
                "hostname_verified": True,  # wrap_socket haette sonst eine Ausnahme ausgeloest
            }


def main(argv: list[str]) -> int:
    if "--authorized-live-call" not in argv:
        print("Abbruch: kein echter Aufruf ohne --authorized-live-call (ausdrueckliche Freigabe).")
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Abbruch: ANTHROPIC_API_KEY fehlt - echter Aufruf NICHT VERIFIZIERT.")
        return 2

    from app.ai_providers.endpoint_policy import resolve_anthropic_base_url
    from app.ai_providers.factory import build_writing_provider
    from app.config.settings import Settings
    from app.privacy.gateway import ClaudePrivacyGateway

    base_url = resolve_anthropic_base_url()
    if urlparse(base_url).scheme != "https":
        print(f"Abbruch: Ziel ist nicht HTTPS ({urlparse(base_url).hostname}) - kein Live-Nachweis.")
        return 2
    settings = Settings(anthropic_api_key=os.environ["ANTHROPIC_API_KEY"], lexono_gateway_url=None)
    gateway = ClaudePrivacyGateway()
    result = gateway.prepare_request(
        purpose="chat_response", sachverhalt=SYNTHETIC_TEXT, anwaltliche_anmerkungen="Fasse zusammen.", review_mode="mask"
    )
    if not result.allowed:
        print("Gateway hat blockiert (fail-closed):", result.reasons)
        return 1
    payload_text = result.payload.anonymisierter_sachverhalt
    leaked = [value for value in SYNTHETIC_VALUES if value in payload_text]
    print("Klartextwerte in der Payload:", leaked or "keine")
    if leaked:
        return 1

    print("TLS:", _tls_evidence(base_url))
    provider = build_writing_provider(settings)
    response = provider.write(result.payload)
    text = getattr(response, "text", "") or ""
    print("Antwort erhalten:", bool(text), "Laenge:", len(text))
    print("Rueckfuehrung ohne Fehler:", bool(gateway.reconstruct_response(text, result.mappings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
