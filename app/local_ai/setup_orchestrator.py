"""LocalAiSetupService – verbindet HardwareDetector/RecommendationEngine/
OllamaInstaller/OllamaLocalLLMProvider zu einem einzigen automatisierten
Einrichtungsablauf (§68).

Ablauf (Vorgabe, wörtlich):
    Hardware erkennen -> Modell empfehlen -> bestätigen -> Ollama
    installieren -> Modell herunterladen -> Health Check -> "ready"

Verwendet AUSSCHLIESSLICH bereits bestehende Komponenten - keine
Parallelimplementierung:
- `HardwareDetector`/`RecommendationEngine` (§67)
- `OllamaInstaller` (dieses Paket, §68)
- `OllamaLocalLLMProvider.check_health()`/`.pull_model()` (§65/§68 -
  dieselbe Klasse, die auch den produktiven Ollama-Aufruf in
  `DraftingService` durchführt)
- `app.setup.env_writer.update_env_values` (dieselbe `.env`-Schreiblogik
  wie `app/web/settings_router.py` - KEINE zweite Konfigurationsquelle)
- `app.config.get_settings` (Cache-Invalidierung nach Konfigurations-
  änderung, dasselbe Muster wie überall sonst im Projekt)

Persistiert bei Erfolg GENAU zwei Settings-Felder (`LOCAL_AI_ENABLED`,
`OLLAMA_MODEL`) - beide existieren bereits seit §65, keine neuen
Konfigurationsfelder in der `.env`. Runtime-/Fortschrittszustand
(`SetupStage`) ist bewusst NICHT persistiert - er wird bei jedem Aufruf neu
berechnet (siehe `get_status`), analog zum bestehenden
`app.state.update_check`-Muster (app/main.py) statt eines potenziell
veralteten gespeicherten Flags.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.ai_providers.local_llm_provider import LocalLLMUnavailableError
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider
from app.config import Settings, get_settings
from app.local_ai.hardware_detector import HardwareDetector
from app.local_ai.hardware_schema import HardwareProfile
from app.local_ai.model_catalog import ModelCatalogEntry
from app.local_ai.ollama_installer import OllamaInstaller
from app.local_ai.recommendation import ModelRecommendation, RecommendationEngine
from app.setup.env_writer import update_env_values

logger = logging.getLogger(__name__)

# Sicherheitsmarge oberhalb der reinen Downloadgroesse (Entpacken/temporaere
# Dateien) - dieselbe Grundidee wie "Sicherheits-/Reservebereich" aus der
# Vorgabe, ohne einen bestimmten, unbelegten Prozentsatz als "amtliche"
# Kennzahl auszugeben.
_DISK_SPACE_SAFETY_MARGIN = 1.2


class SetupStage(str, Enum):
    DETECTING_HARDWARE = "detecting_hardware"
    NO_SUITABLE_MODEL = "no_suitable_model"
    INSTALLING_RUNTIME = "installing_runtime"
    INSUFFICIENT_DISK_SPACE = "insufficient_disk_space"
    DOWNLOADING_MODEL = "downloading_model"
    HEALTH_CHECKING = "health_checking"
    READY = "ready"
    FAILED = "failed"


class LocalAiState(str, Enum):
    """Für die spätere Reparatur-Funktion (Vorgabe: "nur die technische
    Grundlage vorsehen") - ein eindeutig unterscheidbarer Zustand pro
    Fehlerursache, keine einzelne generische "kaputt"-Meldung."""

    DISABLED = "disabled"
    RUNTIME_MISSING = "runtime_missing"
    RUNTIME_UNREACHABLE = "runtime_unreachable"
    MODEL_MISSING = "model_missing"
    READY = "ready"


@dataclass
class SetupResult:
    success: bool
    stage: SetupStage
    hardware_profile: HardwareProfile | None
    recommendation: ModelRecommendation | None
    installed_model: str | None
    error: str | None = None


@dataclass
class LocalAiStatus:
    state: LocalAiState
    configured_model: str | None
    detail: str | None = None


class LocalAiSetupService:
    def __init__(
        self,
        *,
        hardware_detector: HardwareDetector | None = None,
        recommendation_engine: RecommendationEngine | None = None,
        ollama_installer: OllamaInstaller | None = None,
        provider_factory: Callable[[str, Settings], OllamaLocalLLMProvider] | None = None,
        env_path: Path | None = None,
    ) -> None:
        self.hardware_detector = hardware_detector or HardwareDetector()
        self.recommendation_engine = recommendation_engine or RecommendationEngine()
        self.ollama_installer = ollama_installer or OllamaInstaller()
        self._provider_factory = provider_factory or self._default_provider_factory
        # None (Standard) -> ".env" im Projekt-/Installationsverzeichnis,
        # analog zu app/web/settings_router.py.
        self.env_path = env_path or Path(".env")

    @staticmethod
    def _default_provider_factory(model: str, settings: Settings) -> OllamaLocalLLMProvider:
        return OllamaLocalLLMProvider(base_url=settings.ollama_base_url, model=model)

    def get_recommendation(self) -> tuple[HardwareProfile, ModelRecommendation]:
        """Schritt 1+2 der Vorgabe: Hardware erkennen, Empfehlung
        berechnen - eigenständig aufrufbar, damit ein Aufrufer (künftige
        UI) die Empfehlung ANZEIGEN kann, bevor der Benutzer die
        Installation bestätigt (Schritt 3 der Vorgabe)."""
        profile = self.hardware_detector.detect()
        recommendation = self.recommendation_engine.recommend(profile)
        return profile, recommendation

    def run_setup(
        self,
        *,
        model_tag: str | None = None,
        download_dir: Path,
        settings: Settings | None = None,
    ) -> SetupResult:
        """Schritt 4 der Vorgabe (nach Bestätigung durch den Benutzer):
        kompletter automatisierter Einrichtungsablauf. `model_tag=None`
        (Standard) übernimmt die Primärempfehlung der `RecommendationEngine`
        - wird explizit ein Tag übergeben (Benutzer wählt eine der
        angezeigten Alternativen), MUSS es ein von der Engine tatsächlich
        bewertetes Katalogmodell sein (Vorgabe wörtlich: "nicht selbst
        einen anderen Modellnamen erfinden")."""
        settings = settings or get_settings()

        profile, recommendation = self.get_recommendation()
        candidates = (
            [recommendation.primary] if recommendation.primary else []
        ) + recommendation.alternatives
        by_tag: dict[str, ModelCatalogEntry] = {c.entry.tag: c.entry for c in candidates}

        chosen_tag = model_tag or (recommendation.primary.entry.tag if recommendation.primary else None)
        if chosen_tag is None or chosen_tag not in by_tag:
            reason = (
                "Kein Katalogmodell ist für diese Hardware geeignet."
                if chosen_tag is None
                else f"'{chosen_tag}' ist kein von der RecommendationEngine bewertetes Modell."
            )
            logger.warning("Lokale-KI-Einrichtung abgebrochen: %s", reason)
            return SetupResult(
                success=False,
                stage=SetupStage.NO_SUITABLE_MODEL,
                hardware_profile=profile,
                recommendation=recommendation,
                installed_model=None,
                error=reason,
            )
        chosen_entry = by_tag[chosen_tag]

        install_result = self.ollama_installer.ensure_installed(download_dir=download_dir)
        if not install_result.success:
            logger.warning("Ollama-Installation fehlgeschlagen: %s", install_result.error)
            return SetupResult(
                success=False,
                stage=SetupStage.INSTALLING_RUNTIME,
                hardware_profile=profile,
                recommendation=recommendation,
                installed_model=None,
                error=install_result.error,
            )

        required_gb = chosen_entry.download_size_gb * _DISK_SPACE_SAFETY_MARGIN
        if profile.free_disk_gb is not None and profile.free_disk_gb < required_gb:
            error = (
                f"Für '{chosen_tag}' ist auf diesem Laufwerk nicht genügend Speicher "
                f"verfügbar (benötigt ca. {required_gb:.1f} GB, verfügbar "
                f"{profile.free_disk_gb:.1f} GB)."
            )
            logger.warning("Lokale-KI-Einrichtung abgebrochen: %s", error)
            return SetupResult(
                success=False,
                stage=SetupStage.INSUFFICIENT_DISK_SPACE,
                hardware_profile=profile,
                recommendation=recommendation,
                installed_model=None,
                error=error,
            )

        provider = self._provider_factory(chosen_tag, settings)

        # ECHTER FUND (Pilot-Vorbereitung, 09.10., Test im installierten Build): ist Ollama installiert,
        # aber gerade nicht gestartet (z. B. nach einem Neustart), scheiterte der Modell-Download mit
        # "ConnectError" - `ensure_installed` meldet eine vorhandene Installation nur als solche, startet
        # sie aber nicht. Der Start der Anwendung nutzt dafuer bereits `ensure_running` (app/main.py);
        # hier jetzt ebenfalls, BEVOR das Modell geladen wird. Best effort: bleibt Ollama unerreichbar,
        # scheitert der Download wie bisher mit der bekannten Meldung.
        self.ollama_installer.ensure_running(
            is_reachable=lambda: provider.check_health().reachable,
            wait_seconds=3.0,
            max_attempts=10,
        )

        try:
            provider.pull_model(chosen_tag)
        except LocalLLMUnavailableError as exc:
            logger.warning("Modell-Download fehlgeschlagen (%s): %s", chosen_tag, exc)
            return SetupResult(
                success=False,
                stage=SetupStage.DOWNLOADING_MODEL,
                hardware_profile=profile,
                recommendation=recommendation,
                installed_model=None,
                error=f"Modell-Download fehlgeschlagen: {exc}",
            )

        health = provider.check_health()
        if not (health.reachable and health.model_available):
            error = health.error or "Health Check nach der Einrichtung fehlgeschlagen."
            logger.warning("Health Check nach Einrichtung fehlgeschlagen: %s", error)
            return SetupResult(
                success=False,
                stage=SetupStage.HEALTH_CHECKING,
                hardware_profile=profile,
                recommendation=recommendation,
                installed_model=None,
                error=error,
            )

        update_env_values(
            self.env_path, {"LOCAL_AI_ENABLED": True, "OLLAMA_MODEL": chosen_tag}
        )
        get_settings.cache_clear()
        logger.info("Lokale KI erfolgreich eingerichtet: Modell '%s' bereit.", chosen_tag)

        return SetupResult(
            success=True,
            stage=SetupStage.READY,
            hardware_profile=profile,
            recommendation=recommendation,
            installed_model=chosen_tag,
        )

    def get_status(self, settings: Settings | None = None) -> LocalAiStatus:
        """Grundlage für den automatischen Verbindungs-/Reparaturcheck
        (Vorgabe: "beim nächsten Lexoron-Start automatisch verbinden" /
        "[ Lokale KI reparieren ]") - berechnet den Zustand IMMER frisch
        (kein gespeichertes, potenziell veraltetes Flag), reine
        Lesefunktion, löst keine Reparatur selbst aus."""
        settings = settings or get_settings()
        if not settings.local_ai_enabled:
            return LocalAiStatus(state=LocalAiState.DISABLED, configured_model=None)

        model = settings.ollama_model
        provider = self._provider_factory(model, settings)
        health = provider.check_health()

        if not health.reachable:
            installed_version = self.ollama_installer.detect_installed_version()
            if installed_version is None:
                return LocalAiStatus(
                    state=LocalAiState.RUNTIME_MISSING,
                    configured_model=model,
                    detail=health.error,
                )
            return LocalAiStatus(
                state=LocalAiState.RUNTIME_UNREACHABLE,
                configured_model=model,
                detail=health.error,
            )

        if not health.model_available:
            return LocalAiStatus(
                state=LocalAiState.MODEL_MISSING, configured_model=model, detail=health.error
            )

        return LocalAiStatus(state=LocalAiState.READY, configured_model=model)
