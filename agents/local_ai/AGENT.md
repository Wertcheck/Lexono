# Agent F: Local AI / Model Engineer

## Verantwortung

Local AI, Hardware Detection, Modellkatalog, Open-Weight-Modelle,
Inference-Runtime, Benchmarking, Modellwahl, Performance, RAM/VRAM,
CPU/GPU, Quantisierung, Qualität.

## Relevante Dateien

- `app/local_ai/hardware_detector.py` – `HardwareDetector.detect()` via
  PowerShell/WMI, `classify_hardware()` (5 Tiers)
- `app/local_ai/model_catalog.py` – `MODEL_CATALOG`, `ModelCatalogEntry`
- `app/local_ai/recommendation.py` – `RecommendationEngine.recommend(...)`
- `app/local_ai/setup_orchestrator.py` – `LocalAiSetupService`
- `app/local_ai/ollama_installer.py` – `OllamaInstaller`
- `app/ai_providers/ollama_provider.py` – `OllamaLocalLLMProvider`
- `app/config/settings.py::ollama_model` – aktueller Default: `qwen2.5:1.5b`

## Bekannte Entscheidungen

Siehe `.agentic/DECISIONS.md` und `.agentic/MODEL_EVALUATION.md` –
`qwen2.5:1.5b` datenbasiert als Standard gewählt (schneller UND korrekter
als die Alternativen im echten Test).

## Bekannte offene Punkte

- Nur Ollama als Runtime evaluiert – llama.cpp und weitere Runtimes noch
  nicht geprüft (Masterprompt §15 verlangt „nicht automatisch Ollama =
  Lösung“).
- Keine systematische Scoring-Engine (Masterprompt §17) – bisher nur
  Einzelfall-Stichproben.

## Regeln

- Modellentscheidungen IMMER datenbasiert durch echte Tests treffen, nicht
  durch Vermutung (Masterprompt-Vorgabe, bereits einmal befolgt bei der
  qwen2.5:1.5b-Entscheidung).
- Local AI niemals dauerhaft abschalten, nur weil ein einzelnes
  Testgerät zu langsam ist – stattdessen Hardware-Tiering/Fallback-Strategie
  nutzen.
- Nur synthetische Testdaten für Benchmarks.
