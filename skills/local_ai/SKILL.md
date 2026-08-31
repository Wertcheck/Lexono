# Skill: Local-AI-Änderung (Modell/Runtime/Hardware-Logik)

## Zweck

Änderungen an der lokalen KI-Orchestrierung datenbasiert und ohne
Regression auf schwacher Hardware vornehmen.

## Vorgehensweise

1. Vor jeder Modellempfehlungsänderung: echten Benchmark auf
   repräsentativer Kanzleiaufgabe durchführen (Latenz UND
   Ergebnisqualität), niemals nur nach Modellgröße entscheiden.
2. Ergebnis in `.agentic/MODEL_EVALUATION.md` eintragen (Tabelle
   erweitern).
3. Entscheidung + Begründung in `.agentic/DECISIONS.md`.
4. `app/local_ai/model_catalog.py` erweitern (nicht bestehende Einträge
   überschreiben, neue mit passender `recommendation_priority` ergänzen).
5. Settings-Default (`app/config/settings.py::ollama_model`) nur ändern,
   wenn der Benchmark eindeutig eine bessere Wahl zeigt.

## Prüfungen

- Timeout-Werte (`OllamaLocalLLMProvider.timeout_seconds`) bleiben UI-
  verträglich (aktuell 120s Obergrenze, health-check separat mit 5s).
- Fail-closed: `LocalLLMUnavailableError` darf niemals still verschluckt
  werden.

## Typische Fehler

Local AI bei einem langsamen Testgerät vorschnell deaktivieren statt eine
Hardware-Tiering-Lösung zu suchen (ausdrücklich verboten, Masterprompt
§33).

## Relevante Dateien

`app/local_ai/*`, `app/ai_providers/ollama_provider.py`,
`app/config/settings.py`, `.agentic/MODEL_EVALUATION.md`
