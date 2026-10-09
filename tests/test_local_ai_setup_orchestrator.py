"""Tests für app/local_ai/setup_orchestrator.py (§68).

Alle Kollaborateure (HardwareDetector, RecommendationEngine, OllamaInstaller,
OllamaLocalLLMProvider) sind gefakt - reine Orchestrierungslogik-Tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_providers.local_llm_provider import LocalAIHealthStatus, LocalLLMUnavailableError
from app.local_ai.hardware_schema import HardwareClass, HardwareProfile
from app.local_ai.model_catalog import ModelCatalogEntry, RelativePerformanceClass
from app.local_ai.ollama_installer import OllamaInstallResult
from app.local_ai.recommendation import ModelEvaluation, ModelRecommendation, RecommendationStatus
from app.local_ai.setup_orchestrator import (
    LocalAiSetupService,
    LocalAiState,
    SetupStage,
)


def _entry(tag: str = "qwen3:4b", download_size_gb: float = 2.5) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        model_name="Qwen3",
        runtime="ollama",
        tag=tag,
        download_size_gb=download_size_gb,
        context_length=256_000,
        min_ram_gb=5.0,
        recommended_ram_gb=8.0,
        min_vram_gb=3.0,
        recommended_vram_gb=4.0,
        cpu_only_supported=True,
        gpu_supported=True,
        capability_profile=("Zusammenfassung",),
        strengths=("läuft lokal",),
        limitations=("ersetzt Claude nicht",),
        expected_performance_class=RelativePerformanceClass.BALANCED,
        recommendation_priority=3,
    )


def _recommendation(primary_status=RecommendationStatus.RECOMMENDED) -> ModelRecommendation:
    evaluation = ModelEvaluation(
        entry=_entry(), status=primary_status, performance_category="ausgewogen", reason="Testgrund"
    )
    return ModelRecommendation(hardware_class=HardwareClass.STANDARD, primary=evaluation, alternatives=[])


class _FakeHardwareDetector:
    def __init__(self, profile: HardwareProfile | None = None) -> None:
        self.profile = profile or HardwareProfile(
            ram_total_gb=16.0, hardware_class=HardwareClass.STANDARD, free_disk_gb=100.0
        )

    def detect(self) -> HardwareProfile:
        return self.profile


class _FakeRecommendationEngine:
    def __init__(self, recommendation: ModelRecommendation | None = None) -> None:
        self.recommendation = recommendation or _recommendation()

    def recommend(self, profile: HardwareProfile) -> ModelRecommendation:
        return self.recommendation


class _FakeOllamaInstaller:
    def __init__(self, *, install_result: OllamaInstallResult | None = None, installed_version="0.32.15") -> None:
        self.install_result = install_result or OllamaInstallResult(
            success=True, already_installed=True, installed_version=installed_version, stage="reused_existing_installation"
        )
        self.installed_version = installed_version

    def ensure_installed(self, *, download_dir: Path) -> OllamaInstallResult:
        return self.install_result

    def detect_installed_version(self) -> str | None:
        return self.installed_version

    def ensure_running(self, **kwargs) -> bool:
        self.ensure_running_calls = getattr(self, "ensure_running_calls", 0) + 1
        self.on_ensure_running(kwargs)
        return True

    def on_ensure_running(self, kwargs) -> None:
        """Hook fuer Tests (Reihenfolge-Pruefung)."""


class _FakeProvider:
    def __init__(self, *, pull_error: Exception | None = None, health: LocalAIHealthStatus | None = None) -> None:
        self.pull_error = pull_error
        self.health = health or LocalAIHealthStatus(reachable=True, model_available=True)
        self.pulled_models: list[str] = []

    def pull_model(self, model: str | None = None) -> None:
        if self.pull_error:
            raise self.pull_error
        self.pulled_models.append(model)

    def check_health(self) -> LocalAIHealthStatus:
        return self.health


def _service(
    *,
    hardware_detector=None,
    recommendation_engine=None,
    ollama_installer=None,
    provider=None,
    env_path: Path,
) -> LocalAiSetupService:
    fake_provider = provider or _FakeProvider()
    return LocalAiSetupService(
        hardware_detector=hardware_detector or _FakeHardwareDetector(),
        recommendation_engine=recommendation_engine or _FakeRecommendationEngine(),
        ollama_installer=ollama_installer or _FakeOllamaInstaller(),
        provider_factory=lambda model, settings: fake_provider,
        env_path=env_path,
    )


class _FakeSettings:
    local_ai_enabled = True
    ollama_model = "qwen3:4b"
    ollama_base_url = "http://localhost:11434"


# --- 9: Modell bereits vorhanden ---


def test_model_already_present_passes_health_check(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    provider = _FakeProvider(health=LocalAIHealthStatus(reachable=True, model_available=True))
    service = _service(env_path=env_path, provider=provider)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is True
    assert result.stage == SetupStage.READY
    assert result.installed_model == "qwen3:4b"


# --- 10/11: Modell fehlt / erfolgreich installiert ---


def test_model_missing_gets_pulled_and_setup_succeeds(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    provider = _FakeProvider()
    service = _service(env_path=env_path, provider=provider)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is True
    assert provider.pulled_models == ["qwen3:4b"]
    content = env_path.read_text(encoding="utf-8")
    assert "LOCAL_AI_ENABLED=true" in content
    assert "OLLAMA_MODEL=" in content and "qwen3:4b" in content


# --- 12: Modell-Download fehlgeschlagen ---


def test_model_download_failure_does_not_mark_setup_successful(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    provider = _FakeProvider(pull_error=LocalLLMUnavailableError("Download fehlgeschlagen"))
    service = _service(env_path=env_path, provider=provider)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is False
    assert result.stage == SetupStage.DOWNLOADING_MODEL
    # .env darf NICHT veraendert worden sein
    assert "LOCAL_AI_ENABLED" not in env_path.read_text(encoding="utf-8")


# --- 13: zu wenig Speicher ---


def test_insufficient_disk_space_blocks_download(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    provider = _FakeProvider()
    hardware = _FakeHardwareDetector(
        HardwareProfile(ram_total_gb=16.0, hardware_class=HardwareClass.STANDARD, free_disk_gb=1.0)
    )
    service = _service(env_path=env_path, provider=provider, hardware_detector=hardware)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is False
    assert result.stage == SetupStage.INSUFFICIENT_DISK_SPACE
    assert provider.pulled_models == []  # Download wurde NIE gestartet


# --- 14/15: Health Check erfolgreich / fehlgeschlagen ---


def test_failed_health_check_after_download_does_not_mark_ready(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    provider = _FakeProvider(health=LocalAIHealthStatus(reachable=True, model_available=False, error="Modell fehlt"))
    service = _service(env_path=env_path, provider=provider)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is False
    assert result.stage == SetupStage.HEALTH_CHECKING
    assert "LOCAL_AI_ENABLED" not in env_path.read_text(encoding="utf-8")


# --- 16: erfolgreicher kompletter Setup-Pfad ---


def test_full_setup_path_succeeds_end_to_end_with_fakes(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    provider = _FakeProvider()
    service = _service(env_path=env_path, provider=provider)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is True
    assert result.stage == SetupStage.READY
    assert result.hardware_profile is not None
    assert result.recommendation is not None


# --- 17: Setup schlägt an definierter Stelle kontrolliert fehl (Ollama-Installation) ---


def test_setup_fails_at_ollama_installation_stage(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    failing_installer = _FakeOllamaInstaller(
        install_result=OllamaInstallResult(
            success=False, already_installed=False, installed_version=None, stage="download_failed", error="Netzwerkfehler"
        )
    )
    service = _service(env_path=env_path, ollama_installer=failing_installer)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is False
    assert result.stage == SetupStage.INSTALLING_RUNTIME
    assert result.error == "Netzwerkfehler"


def test_no_suitable_model_for_hardware_fails_before_any_installation(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    engine = _FakeRecommendationEngine(
        ModelRecommendation(hardware_class=HardwareClass.UNSUPPORTED, primary=None, alternatives=[])
    )
    installer_calls = []
    installer = _FakeOllamaInstaller()
    installer.ensure_installed = lambda **k: installer_calls.append(k) or installer.install_result
    service = _service(env_path=env_path, recommendation_engine=engine, ollama_installer=installer)

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is False
    assert result.stage == SetupStage.NO_SUITABLE_MODEL
    assert installer_calls == []  # Ollama-Installation wurde NIE versucht


def test_caller_cannot_choose_a_model_outside_the_catalog_evaluation(tmp_path: Path) -> None:
    """Vorgabe: "nicht selbst einen anderen Modellnamen erfinden"."""
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    service = _service(env_path=env_path)

    result = service.run_setup(
        model_tag="qwen3:999b-does-not-exist", download_dir=tmp_path / "dl", settings=_FakeSettings()
    )

    assert result.success is False
    assert result.stage == SetupStage.NO_SUITABLE_MODEL


# --- get_status (Grundlage für Auto-Start/Reparatur) ---


def test_status_disabled_when_local_ai_not_enabled(tmp_path: Path) -> None:
    class _Disabled:
        local_ai_enabled = False
        ollama_model = "qwen3:4b"
        ollama_base_url = "http://localhost:11434"

    service = _service(env_path=tmp_path / ".env")
    status = service.get_status(_Disabled())
    assert status.state == LocalAiState.DISABLED


def test_status_ready_when_everything_healthy(tmp_path: Path) -> None:
    provider = _FakeProvider(health=LocalAIHealthStatus(reachable=True, model_available=True))
    service = _service(env_path=tmp_path / ".env", provider=provider)
    status = service.get_status(_FakeSettings())
    assert status.state == LocalAiState.READY


def test_status_runtime_missing_when_not_installed_and_unreachable(tmp_path: Path) -> None:
    provider = _FakeProvider(health=LocalAIHealthStatus(reachable=False, model_available=False, error="nicht erreichbar"))
    installer = _FakeOllamaInstaller(installed_version=None)
    service = _service(env_path=tmp_path / ".env", provider=provider, ollama_installer=installer)
    status = service.get_status(_FakeSettings())
    assert status.state == LocalAiState.RUNTIME_MISSING


def test_status_runtime_unreachable_when_installed_but_not_responding(tmp_path: Path) -> None:
    provider = _FakeProvider(health=LocalAIHealthStatus(reachable=False, model_available=False, error="nicht erreichbar"))
    installer = _FakeOllamaInstaller(installed_version="0.32.15")
    service = _service(env_path=tmp_path / ".env", provider=provider, ollama_installer=installer)
    status = service.get_status(_FakeSettings())
    assert status.state == LocalAiState.RUNTIME_UNREACHABLE


def test_status_model_missing_when_reachable_but_model_absent(tmp_path: Path) -> None:
    provider = _FakeProvider(health=LocalAIHealthStatus(reachable=True, model_available=False, error="Modell fehlt"))
    service = _service(env_path=tmp_path / ".env", provider=provider)
    status = service.get_status(_FakeSettings())
    assert status.state == LocalAiState.MODEL_MISSING


# --- Ollama installiert, aber nicht gestartet (Pilot-Vorbereitung, 09.10.) ---


def test_stopped_ollama_is_started_before_the_model_download(tmp_path: Path) -> None:
    """Real im installierten Build reproduziert: Ollama installiert, aber gestoppt -> der Modell-Download
    scheiterte mit ConnectError, weil `ensure_installed` nur eine vorhandene Installation meldet."""
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ENV=development\n", encoding="utf-8")
    order: list[str] = []

    class _Installer(_FakeOllamaInstaller):
        def on_ensure_running(self, kwargs) -> None:
            order.append("ensure_running")
            assert callable(kwargs["is_reachable"]), "Erreichbarkeit wird ueber den Provider-Health-Check geprueft"

    class _Provider(_FakeProvider):
        def pull_model(self, model: str | None = None) -> None:
            order.append("pull")
            super().pull_model(model)

    installer = _Installer()
    service = _service(env_path=env_path, ollama_installer=installer, provider=_Provider())

    result = service.run_setup(download_dir=tmp_path / "dl", settings=_FakeSettings())

    assert result.success is True
    assert order == ["ensure_running", "pull"]
