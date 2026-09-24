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

### Achtung: Code-Default ≠ laufende Referenzinstallation (Stand 15.09.)

Bei der Chat-Intelligence-Forensik aufgefallen und hier festgehalten,
damit niemand die falsche Quelle für bare Münze nimmt:

| Quelle | Modell |
|---|---|
| `app/config/settings.py::ollama_model` (Code-Default) | `qwen2.5:1.5b` |
| dieses Dokument / `MODEL_EVALUATION.md` (Entscheidung 31.08.) | `qwen2.5:1.5b` |
| `%PROGRAMDATA%\Lexono\.env` der Referenzmaschine (`OLLAMA_MODEL`) | **`qwen3:8b`** |

Code und Dokumentation stimmen also überein; die tatsächlich laufende
Installation weicht per `.env`-Override ab. Nach ausdrücklicher
Nutzeranweisung (15.09.) ist **`qwen3:8b` zunächst die
Produktionsreferenz**.

Der Code-Default wird bewusst NICHT nachgezogen: das wäre faktisch ein
Modellwechsel für jede Neuinstallation, und derselbe Auftrag verlangt
dafür zuerst einen echten Benchmark auf Zielhardware
(Architektur/Kontext/Prompt/Privacy vorher geklärt). Die Entscheidung
steht also aus – siehe `.agentic/OPEN_ISSUES.md`, Eintrag CHAT-06.

Gemessener Zwischenstand aus der Forensik (nicht als Modellurteil zu
lesen): `qwen3:8b` erzeugt über `/api/chat` mit echtem `messages`-Array
eine natürliche Begrüßung in ~5 s und löst Anschlussfragen korrekt auf
den vorherigen Turn auf – die darin enthaltene **Rechtsauskunft war
jedoch sachlich falsch** (Einspruchsfrist: ein Jahr statt einem Monat,
§ 355 AO). Das stützt die bestehende Rollentrennung: lokales Modell für
Form und Vorverarbeitung, nicht für juristische Substanz.

## Lokale Spracherkennung (STT) - Evaluation, nicht Teil des laufenden Betriebs

Neues Arbeitsfeld (15.09., Nutzerauftrag): perspektivisch echte lokale
(on-device) Spracherkennung fuer den Diktier-Button (Composer/
Anweisungsleiste), ausdruecklich KEINE Cloud-Spracherkennung - siehe
DECISIONS.md (15.09.) zum Grund, warum die native Browser-
`SpeechRecognition` dafuer NICHT infrage kommt (sendet Audio an einen
Cloud-Dienst, bevor die lokale Pseudonymisierung greifen koennte).

Erster Evaluationsdurchlauf: `.agentic/memos/stt_eval/`. Reale Tests von
Vosk und faster-whisper, dokumentierter (nicht gemessener) Ausschluss von
openai-whisper (torch-Abhaengigkeit). Noch KEINE Implementierung, noch
KEINE Modellentscheidung - siehe TASK_MAP §G (STT-EVAL) fuer den
aktuellen Stand und die offenen Schritte vor einer echten Anbindung.

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
