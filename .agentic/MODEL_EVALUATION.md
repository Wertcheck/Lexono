# MODEL_EVALUATION – Lokale KI: Modell-/Runtime-Bewertung

Nur synthetische Testdaten verwendet, keine echten Mandantendaten.

## Bisher real getestet (CPU-only Entwicklungshardware, Ollama-Runtime)

| Modell         | Größe   | Latenz (typ. Kanzleiaufgabe)     | Ergebnisqualität                          |
|----------------|---------|-----------------------------------|--------------------------------------------|
| `qwen3:4b`     | ~4B     | > 20 Minuten (Thinking-Modus)     | historisch korrekt, aber praktisch unbrauchbar wegen Latenz |
| `llama3.2:1b`  | ~1B     | schnell                           | verweigert/versteht Aufgaben nicht zuverlässig (task-refusing) |
| `qwen2.5:1.5b` | ~1.5B   | ~10–11s warm / ~37s cold          | korrekt, aufgabentreu, keine Halluzinationen – **gewählt** |

Vollständige Herleitung: `ARCHITECTURE.md` §71.

Reale End-zu-End-Zeitmessung (Ollama → Gateway → Claude, echter
Netzwerktest, synthetisches Dokument): 17,7s (lokale KI) + 12,9s
(Cloud-KI) = 33,3s Gesamtlaufzeit.

## Noch NICHT evaluiert (siehe OPEN_ISSUES.md, Kategorie HIGH)

- Alternative Runtimes: llama.cpp (direkt, ohne Ollama-Wrapper) und weitere
  seriöse lokale Inference-Runtimes.
- Weitere Modellfamilien: Mistral, Gemma (nur Qwen/Llama bisher getestet).
- Systematische Scoring-Engine mit mehreren repräsentativen
  Kanzleiaufgaben (Zusammenfassung, Extraktion, Antwortentwurf ans
  Finanzamt, fehlende Informationen erkennen, strukturierte Ausgabe) –
  bisher nur Einzelfall-Stichproben, kein reproduzierbarer Score.

## Entscheidungslogik (aktuell implementiert)

`app/local_ai/hardware_detector.py` + `model_catalog.py` +
`recommendation.py`: Hardware wird klassifiziert (5 Tiers), Modellkatalog
enthält u. a. `qwen2.5:1.5b` (Priorität 0, empfohlen), verschiedene
`qwen3`-Varianten (höhere Priorität, für stärkere Hardware). Empfehlung
erfolgt über `RecommendationEngine.recommend(HardwareProfile)`. Diese
Logik existierte bereits vor dieser Session (Verdrahtung war die Lücke,
siehe DECISIONS.md).
