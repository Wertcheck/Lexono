"""Konfiguration des Lexono-Gateway (ARCHITECTURE.md §70).

Eigener `.env.gateway`-Namespace - bewusst NICHT `.env` (das ist die
Kanzlei-Client-Konfigurationsdatei). Ein Gateway-Betrieb und eine
Kanzlei-Installation laufen niemals im selben Prozess/Verzeichnis, daher
gibt es strukturell keine Möglichkeit, dass eine Kanzlei versehentlich die
Gateway-`.env` (mit dem echten Anthropic-Key) einliest oder umgekehrt.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.gateway",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Der einzige Ort im gesamten Projekt, an dem dieser Wert eine
    # tatsächliche Produktionsbedeutung hat - siehe ARCHITECTURE.md §70.
    anthropic_api_key: SecretStr | None = None
    # Modell-Allowlist statt eines vom Client frei waehlbaren Strings -
    # verhindert, dass eine kompromittierte Kanzlei-Credential teurere/
    # andere Modelle als vorgesehen anfordert (Kostenkontrolle, Auftrag §32).
    allowed_models: list[str] = Field(
        default_factory=lambda: ["claude-sonnet-5", "claude-opus-4-8"]
    )
    # Harte Obergrenze unabhaengig vom Client-Wunsch - zweite
    # Kostenkontroll-Schranke neben dem Rate-Limit.
    max_tokens_ceiling: int = 4000

    database_url: str = "sqlite:///./gateway_data/gateway.db"

    # Sliding-Window-Rate-Limit pro Kanzlei-Credential (siehe
    # gateway/rate_limiter.py). Bewusst grosszuegig fuer interaktive
    # Chat-Nutzung einer einzelnen Kanzlei, nicht fuer Batch-Verarbeitung.
    default_rate_limit_per_minute: int = 30

    host: str = "127.0.0.1"
    port: int = 8700

    log_level: str = "INFO"


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
