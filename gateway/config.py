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
    # §44 (15.10.2026): Upstream-Timeout und KEINE automatischen SDK-Wiederholungen (vorher SDK-Standard: 60 s und
    # 2 Wiederholungen, also bis ~180 s und drei kostenpflichtige Versuche). Der Client gibt nach
    # LEXONO_GATEWAY_TIMEOUT_SECONDS (Standard 60 s) auf - danach weiterlaufende Versuche haetten keinen Abnehmer.
    # Regel: (anthropic_max_retries + 1) * anthropic_timeout_seconds < Client-Timeout.
    anthropic_timeout_seconds: float = Field(default=55.0, gt=0)
    anthropic_max_retries: int = Field(default=0, ge=0, le=2)
    # §44.1: Timeout des Kanzlei-Clients (dort LEXONO_GATEWAY_TIMEOUT_SECONDS, Standard 60 s). Das Relay kann ihn nicht
    # erfragen - er wird hier von Hand gespiegelt (kein Abgleich; kuerzester Wert aller angebundenen Kanzleien), damit
    # die Startpruefung `retry_budget_fits_client_timeout` greift. Ein kuerzerer echter Client-Timeout bleibt unbemerkt.
    client_timeout_seconds: float = Field(default=60.0, gt=0)

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

    @model_validator(mode="after")
    def retry_budget_fits_client_timeout(self) -> "GatewaySettings":
        """§44.1 (15.10.2026, mit dem echten Anthropic-SDK 1.5.0 gegen einen lokalen Fake nachgewiesen): das SDK
        wiederholt bei 408/409/429/5xx, Verbindungsfehlern und Timeouts und wartet dazwischen so lange, wie ein
        `retry-after`/`retry-after-ms`-Header verlangt - bis zu 60 s (sonst Backoff 0,5 s, 1 s, ... hoechstens 8 s). Mit
        Wiederholungen liefen weitere kostenpflichtige Versuche nach dem Client-Abbruch ohne Abnehmer.

        Startpruefung als NOTWENDIGE Bedingung, nicht als Obergrenze: (Wiederholungen + 1) x Timeout + Wiederholungen x
        60 s muss UNTER dem Client-Timeout liegen. Sie weist Konfigurationen ab, die schon im Regelfall (jeder Versuch
        endet mit einem Lese-Timeout, jede Wartezeit ist ein maximales retry-after) laenger laufen als der Client wartet.
        Sie GARANTIERT KEINE Hoechstdauer: der Timeout gilt je Verbindungs-, Schreib-, Lese- und Pool-Vorgang (httpx2),
        ein einzelner Versuch kann daher laenger dauern - eine stueckweise Antwort wurde mit 6,02 s bei 2 s Timeout
        gemessen. Eine harte Gesamtfrist gibt es im Relay nicht (PRIVACY_REVIEW_ARCHITECTURE.md §44.1)."""
        retry_after_max = 60.0  # hoechste vom SDK befolgte retry-after-Wartezeit (anthropic/_base_client.py)
        planned = (self.anthropic_max_retries + 1) * self.anthropic_timeout_seconds + (
            self.anthropic_max_retries * retry_after_max)
        if planned >= self.client_timeout_seconds:
            raise ValueError(
                "Relay-Laufzeit passt schon im Regelfall nicht in den Client-Timeout: (ANTHROPIC_MAX_RETRIES + 1) x "
                "ANTHROPIC_TIMEOUT_SECONDS + ANTHROPIC_MAX_RETRIES x 60 s (retry-after) = "
                f"{planned:.0f} s, CLIENT_TIMEOUT_SECONDS = {self.client_timeout_seconds:.0f} s. "
                "Wiederholungen oder Timeout senken - oder Client- und Relay-Timeout gemeinsam erhoehen. "
                "(Notwendige Bedingung, keine Hoechstdauer - siehe PRIVACY_REVIEW_ARCHITECTURE.md §44.1.)"
            )
        return self


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
