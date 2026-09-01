"""Datensparsames Request-Logging (Auftrag §33, §16 des Ursprungsauftrags).

`log_relay_request` nimmt bewusst NUR die hier aufgelisteten, typisierten
Felder entgegen - kein `**kwargs`, kein Freitext-/Body-Parameter. Das
macht ein versehentliches Content-Logging nicht nur unterlassen, sondern
strukturell erschwert: wer hier Inhalte mitloggen wollte, müsste diese
Funktionssignatur bewusst und sichtbar erweitern, nicht nur einen
zusätzlichen Parameter beim Aufruf übergeben."""

from __future__ import annotations

import logging

_logger = logging.getLogger("lexono_gateway.relay")


def log_relay_request(
    *,
    request_id: str,
    tenant_id: str | None,
    duration_ms: float,
    status: int,
    error_category: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """`input_tokens`/`output_tokens` (Umsetzungsplan Punkt 4, Nutzungsbasis
    fuer spaetere Auswertung/Abrechnung) sind wie alle anderen Felder hier
    ausschliesslich Zahlen - kein Prompt-/Antworttext, kein Dokumentinhalt.
    Optional (`None` bei Fehlerfaellen vor dem eigentlichen Anthropic-
    Aufruf, z. B. Rate-Limit/Auth/Validierungsfehler - dort gibt es noch
    keine Tokenzahlen)."""
    _logger.info(
        "request_id=%s tenant_id=%s duration_ms=%.1f status=%s error_category=%s "
        "input_tokens=%s output_tokens=%s",
        request_id,
        tenant_id or "-",
        duration_ms,
        status,
        error_category or "-",
        input_tokens if input_tokens is not None else "-",
        output_tokens if output_tokens is not None else "-",
    )
