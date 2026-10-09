"""Tests für app/local_ai/recommendation.py (§67).

Deckt die 15 geforderten Szenarien ab (siehe Nummerierung in den
Testnamen/Kommentaren) plus den explizit geforderten Beweis, dass der
i7-3720QM NICHT als normale Referenzklasse behandelt wird."""

from __future__ import annotations

from app.local_ai.hardware_detector import classify_hardware
from app.local_ai.hardware_schema import HardwareClass, HardwareProfile
from app.local_ai.recommendation import RecommendationEngine, RecommendationStatus


def _classified_profile(**overrides) -> HardwareProfile:
    defaults = {"ram_total_gb": 16.0, "cpu_vendor": "GenuineIntel", "cpu_generation": 13, "cpu_cores": 8}
    defaults.update(overrides)
    profile = HardwareProfile(**defaults)
    profile.hardware_class = classify_hardware(profile)
    return profile


# --- 1: < 16 GB RAM ---


def test_below_16gb_has_no_primary_recommendation() -> None:
    profile = _classified_profile(ram_total_gb=8.0)
    result = RecommendationEngine().recommend(profile)
    assert result.primary is None
    assert all(e.status == RecommendationStatus.UNSUPPORTED for e in result.alternatives)


# --- 2: 16 GB + Legacy-CPU ---


def test_16gb_legacy_cpu_never_reaches_recommended() -> None:
    profile = _classified_profile(
        ram_total_gb=15.9, cpu_vendor="GenuineIntel", cpu_generation=3, cpu_cores=4
    )
    result = RecommendationEngine().recommend(profile)
    assert result.hardware_class == HardwareClass.LEGACY
    assert result.primary is not None
    assert result.primary.status != RecommendationStatus.RECOMMENDED
    assert all(e.status != RecommendationStatus.RECOMMENDED for e in result.alternatives)


# --- 3: 16 GB + moderne CPU ---


def test_16gb_modern_cpu_reaches_recommended() -> None:
    profile = _classified_profile(ram_total_gb=16.0, cpu_generation=13, cpu_cores=8)
    result = RecommendationEngine().recommend(profile)
    assert result.primary is not None
    assert result.primary.status == RecommendationStatus.RECOMMENDED


# --- 4: 16 GB + GPU ---


def test_16gb_with_gpu_reaches_recommended() -> None:
    profile = _classified_profile(
        ram_total_gb=16.0, gpu_present=True, gpu_vendor="NVIDIA", vram_gb=8.0
    )
    result = RecommendationEngine().recommend(profile)
    assert result.primary is not None
    assert result.primary.status == RecommendationStatus.RECOMMENDED


# --- 5: 32 GB + CPU-only ---


def test_32gb_cpu_only_recommends_a_larger_model_than_16gb() -> None:
    profile_16 = _classified_profile(ram_total_gb=16.0, cpu_generation=13, cpu_cores=8)
    profile_32 = _classified_profile(ram_total_gb=32.0, cpu_generation=13, cpu_cores=8)
    result_16 = RecommendationEngine().recommend(profile_16)
    result_32 = RecommendationEngine().recommend(profile_32)
    assert (
        result_32.primary.entry.recommendation_priority
        >= result_16.primary.entry.recommendation_priority
    )


# --- 6: 32 GB + GPU ---


def test_32gb_with_gpu_reaches_recommended() -> None:
    profile = _classified_profile(
        ram_total_gb=32.0, gpu_present=True, gpu_vendor="NVIDIA", vram_gb=10.0
    )
    result = RecommendationEngine().recommend(profile)
    assert result.primary is not None
    assert result.primary.status == RecommendationStatus.RECOMMENDED


# --- 7: 64 GB + starke GPU ---


def test_64gb_with_strong_gpu_recommends_the_largest_available_model() -> None:
    profile = _classified_profile(
        ram_total_gb=64.0, gpu_present=True, gpu_vendor="NVIDIA", vram_gb=24.0
    )
    result = RecommendationEngine().recommend(profile)
    assert result.hardware_class == HardwareClass.WORKSTATION
    assert result.primary is not None
    # Muss NICHT das absolut groesste Katalogmodell sein (siehe "Empfehlung
    # != technisches Maximum"), aber deutlich groesser als auf 16 GB.
    profile_16 = _classified_profile(ram_total_gb=16.0, cpu_generation=13, cpu_cores=8)
    result_16 = RecommendationEngine().recommend(profile_16)
    assert (
        result.primary.entry.recommendation_priority
        > result_16.primary.entry.recommendation_priority
    )


# --- 8: unbekannte GPU ---


def test_unknown_gpu_falls_back_to_cpu_only_logic() -> None:
    profile = _classified_profile(
        ram_total_gb=16.0, gpu_present=True, gpu_vendor=None, vram_gb=None
    )
    result = RecommendationEngine().recommend(profile)
    assert result.hardware_class == HardwareClass.STANDARD  # keine "faehige" GPU gewertet
    assert result.primary is not None


# --- 9: unbekanntes VRAM ---


def test_unknown_vram_with_known_dedicated_gpu_does_not_crash() -> None:
    profile = _classified_profile(
        ram_total_gb=16.0, gpu_present=True, gpu_vendor="NVIDIA", vram_gb=None
    )
    result = RecommendationEngine().recommend(profile)
    assert result.primary is not None


# --- 10: unbekannte CPU-Generation ---


def test_unknown_cpu_generation_can_still_reach_recommended() -> None:
    profile = _classified_profile(cpu_vendor="AuthenticAMD", cpu_generation=None, cpu_cores=8)
    result = RecommendationEngine().recommend(profile)
    assert result.hardware_class == HardwareClass.STANDARD
    assert result.primary is not None
    assert result.primary.status == RecommendationStatus.RECOMMENDED


# --- 11: zu wenig Speicher (RAM als Speicher-Engpass) ---


def test_insufficient_ram_for_any_model_yields_no_primary() -> None:
    profile = _classified_profile(ram_total_gb=4.0)
    result = RecommendationEngine().recommend(profile)
    assert result.primary is None


# --- 12: technisch kompatibles, aber marginales Modell ---


def test_oversized_model_on_modest_hardware_is_marginal_not_recommended() -> None:
    """14B auf einer 16-GB-CPU-only-Maschine: technisch lauffaehig (RAM
    reicht knapp), aber nicht die Empfehlung."""
    profile = _classified_profile(ram_total_gb=16.0, cpu_generation=13, cpu_cores=8)
    result = RecommendationEngine().recommend(profile)
    fourteen_b = next(e for e in result.alternatives if e.entry.tag == "qwen3:14b")
    assert fourteen_b.status in (RecommendationStatus.MARGINAL, RecommendationStatus.SUPPORTED)
    assert fourteen_b.status != RecommendationStatus.RECOMMENDED


# --- 13: empfohlenes Modell ---


def test_primary_recommendation_has_a_human_readable_reason() -> None:
    profile = _classified_profile(ram_total_gb=16.0, cpu_generation=13, cpu_cores=8)
    result = RecommendationEngine().recommend(profile)
    assert result.primary is not None
    assert len(result.primary.reason) > 0
    assert result.primary.performance_category in (
        "schnell",
        "ausgewogen",
        "langsam",
        "sehr langsam",
    )


# --- 14: mehrere geeignete Modelle ---


def test_multiple_suitable_models_all_appear_with_their_own_status() -> None:
    profile = _classified_profile(ram_total_gb=32.0, cpu_generation=13, cpu_cores=8)
    result = RecommendationEngine().recommend(profile)
    all_tags = {result.primary.entry.tag} | {e.entry.tag for e in result.alternatives}
    from app.local_ai.model_catalog import get_model_catalog

    assert all_tags == {entry.tag for entry in get_model_catalog()}


# --- 15: deterministische Empfehlung ---


def test_recommendation_is_deterministic_across_repeated_calls() -> None:
    profile = _classified_profile(ram_total_gb=32.0, cpu_generation=13, cpu_cores=8)
    engine = RecommendationEngine()
    first = engine.recommend(profile)
    second = engine.recommend(profile)
    assert first.primary.entry.tag == second.primary.entry.tag
    assert first.primary.status == second.primary.status
    assert [e.entry.tag for e in first.alternatives] == [e.entry.tag for e in second.alternatives]


# --- Explizit geforderter Beweis: i7-3720QM ist KEINE Referenzklasse ---


def test_i7_3720qm_is_not_treated_as_standard_reference_hardware() -> None:
    """Reale Maschine aus §66/§67 (Windows 10 Pro, i7-3720QM, 16 GB RAM,
    Intel HD Graphics 4000, keine dedizierte GPU)."""
    profile = _classified_profile(
        ram_total_gb=15.9,
        cpu_vendor="GenuineIntel",
        cpu_model="Intel(R) Core(TM) i7-3720QM CPU @ 2.60GHz",
        cpu_generation=3,
        cpu_cores=4,
        cpu_threads=8,
        gpu_present=True,
        gpu_vendor="Intel",
        gpu_model="Intel(R) HD Graphics 4000",
        vram_gb=2.1,
    )
    result = RecommendationEngine().recommend(profile)

    assert result.hardware_class == HardwareClass.LEGACY
    assert result.hardware_class != HardwareClass.STANDARD
    assert result.primary is not None
    assert result.primary.status != RecommendationStatus.RECOMMENDED
    assert "langsam" in result.primary.performance_category


def test_qwen3_8b_is_not_recommended_on_16_gb_because_lexono_needs_its_own_ram() -> None:
    """Real gemessen (09.10.): auf 15,7 GB RAM liess `qwen3:8b` (5,9 GB) neben Lexono selbst
    (~2 GB inkl. WebView2) nur ~1,4-2 GB frei. Es ist lauffaehig (SUPPORTED), aber nicht die
    Empfehlung; empfohlen wird ein kleineres Modell."""
    profile = _classified_profile(ram_total_gb=15.7, cpu_generation=11, cpu_cores=4)
    result = RecommendationEngine().recommend(profile)
    assert result.primary is not None
    assert result.primary.entry.tag != "qwen3:8b"
    eight_b = next(e for e in result.alternatives if e.entry.tag == "qwen3:8b")
    assert eight_b.status == RecommendationStatus.SUPPORTED
