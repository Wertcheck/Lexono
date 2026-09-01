"""Konfiguration des Lexono-Gateway (ARCHITECTURE.md §70).

Eigener `.env.gateway`-Namespace - bewusst NICHT `.env` (das ist die
Kanzlei-Client-Konfigurationsdatei). Ein Gateway-Betrieb und eine
Kanzlei-Installation laufen niemals im selben Prozess/Verzeichnis, daher
gibt es strukturell keine Möglichkeit, dass eine Kanzlei versehentlich die
Gateway-`.env` (mit dem echten Anthropic-Key) einliest oder umgekehrt.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
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
    # Zentrale Modellsteuerung (Nutzerauftrag, Umsetzungsplan Punkt 1): das
    # vom Client im RelayRequest gesendete `model`-Feld wird weiterhin gegen
    # `allowed_models` geprueft (unveraendertes Verhalten, bestehende Tests
    # bleiben gueltig), bestimmt aber NICHT mehr das tatsaechlich an
    # Anthropic gesendete Modell - das ist ausschliesslich `default_model`.
    # Ein Modellwechsel ist damit eine reine `.env.gateway`-Aenderung +
    # Prozess-Neustart, ohne dass irgendeine Kanzlei-Installation neu
    # gebaut werden muss (siehe gateway/main.py::relay_messages).
    default_model: str = "claude-sonnet-5"
    # Harte Obergrenze unabhaengig vom Client-Wunsch - zweite
    # Kostenkontroll-Schranke neben dem Rate-Limit.
    max_tokens_ceiling: int = 4000
    # Größenlimit fuer den Eingabe-Payload (system+messages, serialisiert),
    # geprueft in gateway/main.py::relay_messages VOR dem Anthropic-Aufruf -
    # zweite, grobkoernigere Schutzschicht ist das Reverse-Proxy-Bodylimit
    # (siehe deploy/Caddyfile). 200 KB ist fuer einen einzelnen
    # pseudonymisierten Text-Prompt (keine Anhaenge, keine Bilder) grosszuegig
    # bemessen.
    max_request_bytes: int = 200_000

    database_url: str = "sqlite:///./gateway_data/gateway.db"

    # Sliding-Window-Rate-Limit pro Kanzlei-Credential (siehe
    # gateway/rate_limiter.py). Bewusst grosszuegig fuer interaktive
    # Chat-Nutzung einer einzelnen Kanzlei, nicht fuer Batch-Verarbeitung.
    default_rate_limit_per_minute: int = 30

    host: str = "127.0.0.1"
    port: int = 8700

    log_level: str = "INFO"

    @model_validator(mode="after")
    def default_model_must_be_allowed(self) -> "GatewaySettings":
        """Verhindert eine in sich widerspruechliche Konfiguration (z. B.
        ein Tippfehler in `.env.gateway`), bei der das zentral gesetzte
        `default_model` selbst gar nicht in `allowed_models` steht - das
        wuerde sonst erst beim ersten echten Request auffallen, nicht schon
        beim Start."""
        if self.default_model not in self.allowed_models:
            raise ValueError(
                "GATEWAY_DEFAULT_MODEL muss in ALLOWED_MODELS enthalten sein "
                f"(default_model={self.default_model!r}, allowed_models={self.allowed_models!r})"
            )
        return self


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
