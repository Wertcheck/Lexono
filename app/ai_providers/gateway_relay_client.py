"""HTTP-Client für den Lexono-Gateway (ARCHITECTURE.md §70) - der einzige
Kommunikationsweg der Kanzlei-Anwendung zu Anthropic im Produktionsmodus.

Verwendet `httpx` (bereits Kernabhängigkeit, siehe app/updater/checker.py),
NIEMALS `anthropic.Anthropic` - dieser Client kennt keinen Anthropic-Key
und kann keinen kennen. Er sendet ausschließlich das, was der Aufrufer
(GatewayRelayWritingProvider/-ReviewProvider) bereits aus einer bereits
pseudonymisierten, Final-Payload-Gate-geprüften `ClaudeRequestPayload`
gebaut hat - keine Kenntnis von Mandantendaten in diesem Modul."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class GatewayRelayError(Exception):
    """Fehler bei der Kommunikation mit dem Lexono-Gateway (Netzwerk,
    Authentifizierung, Rate-Limit, serverseitiger Anthropic-Fehler) - wird
    vom Aufrufer wie jeder andere Provider-Fehler behandelt (siehe
    DraftingService: kontrolliert abbrechen, niemals stillschweigend einen
    alternativen direkten Cloud-Pfad versuchen).

    `status_code` (§44): HTTP-Status der Relay-Antwort (None bei Netzwerkfehler/Timeout) - damit
    `drafting.service._log_provider_exception` ihn inhaltsfrei protokolliert (z. B. 400 bei ueberschrittener
    Token-Obergrenze statt eines nicht zuordenbaren "Interner Fehler")."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class GatewayRelayResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    # §44: vom Relay durchgereicht ("max_tokens" = Antwort abgeschnitten); None bei aelterem Relay
    stop_reason: str | None = None


def call_gateway_messages(
    *,
    base_url: str,
    client_id: str,
    client_secret: str,
    model: str,
    max_tokens: int,
    system_blocks: list[dict],
    message_blocks: list[dict],
    timeout_seconds: float = 60.0,
    thinking: dict | None = None,
) -> GatewayRelayResult:
    """`thinking` (§44): optional, nur `{"type": "disabled"}` - wird nur mitgeschickt, wenn gesetzt (der
    Schreibpfad setzt es wie `AnthropicClaudeWritingProvider`; der Review-Pfad bleibt unveraendert ohne). Ein aelteres
    Relay ignoriert das Feld. KEINE Wiederholung hier: ein Fehler oder Timeout bricht kontrolliert ab."""
    url = base_url.rstrip("/") + "/v1/relay/messages"
    headers = {"Authorization": f"Bearer {client_id}:{client_secret}"}
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [{"role": "user", "content": message_blocks}],
    }
    if thinking is not None:
        body["thinking"] = thinking
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise GatewayRelayError(f"Gateway nicht erreichbar: {exc}") from exc

    if response.status_code != 200:
        # Bewusst KEINE Weitergabe von response.text an den Aufrufer -
        # koennte (theoretisch) interne Gateway-Details enthalten. Nur der
        # Statuscode wird in der Fehlermeldung genannt.
        raise GatewayRelayError(
            f"Gateway antwortete mit Status {response.status_code}", status_code=response.status_code
        )

    data = response.json()
    return GatewayRelayResult(
        text=data["text"],
        input_tokens=data.get("input_tokens"),
        output_tokens=data.get("output_tokens"),
        stop_reason=data.get("stop_reason"),
    )
