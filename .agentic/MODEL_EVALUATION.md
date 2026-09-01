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

## Vierter Modellvergleich: gemma2:2b (01.09., real getestet)

Auf expliziten Nutzerauftrag ("Model Evaluation" als offener Punkt
weiterverfolgen, sofern ohne unverhältnismäßigen Aufwand möglich) wurde
ein zusätzliches, bisher nicht getestetes Modell derselben Gewichtsklasse
real benchmarkt (`ollama pull gemma2:2b`, ~1,6GB, kein neuer Runtime -
weiterhin Ollama, daher kein unverhältnismäßiger Aufwand). Identischer
synthetischer Prompt wie beim ursprünglichen 3-Modell-Vergleich, echte
lokale Aufrufe, keine Mocks.

| Lauf | Gesamtzeit | Anmerkung |
|---|---|---|
| gemma2:2b cold | 42,1s | vergleichbar mit qwen2.5:1.5b cold (~37s) |
| gemma2:2b warm | **18,2s** | deutlich langsamer als qwen2.5:1.5b warm (10-11s) |

**Qualität: disqualifizierender Befund.** Platzhalter-Erhaltung
(Pflichtkriterium, siehe Masterprompt §17 "korrekte Platzhalter") wurde
in BEIDEN Läufen verletzt:
- Cold-Run: `[MANDANT_01]` wurde zu `**Mandant[01]**` umgeschrieben
  (Markdown-Fettung mit dem Platzhalter verschmolzen, Klammerposition
  verändert) - der spätere Rücktausch (`Pseudonymizer.reconstruct()`,
  exakter String-Ersatz) würde diesen veränderten Platzhalter NICHT mehr
  finden und ihn unverändert im finalen Dokument stehen lassen.
- Warm-Run: `[GEGNER_01]` wurde komplett durch ein erfundenes Wort
  ("Steuerbescheidgeber", kein reales deutsches Wort) ERSETZT statt
  erhalten - echte Halluzination UND vollständiger Platzhalterverlust,
  nicht nur eine Formatierungsabweichung.

**Ergebnis: `qwen2.5:1.5b` bleibt die datenbasiert beste Wahl.**
gemma2:2b ist sowohl langsamer (warm ~65% mehr Zeit) als auch bei einem
Pflichtkriterium (Platzhaltererhaltung) unzuverlässig - `app/config/
settings.py::ollama_model` bewusst NICHT geändert. Ein negatives, aber
werthaltiges Ergebnis: bestätigt die bestehende Wahl erneut, statt sie in
Frage zu stellen.

## Runtime-Erweiterbarkeit (verifiziert, 01.09., Masterprompt V2 Task #63)

Konkreter Code-Befund, KEINE Vermutung: die Architektur ist bereits auf
mehrere Runtimes vorbereitet, aber es existiert bisher nur eine konkrete
Implementierung (Ollama):

- `app/ai_providers/local_llm_provider.py::LocalLLMProvider` ist ein
  `typing.Protocol` (`process`/`check_health`/`generate_structured`) - ein
  neuer Runtime-Provider (z. B. llama.cpp) müsste nur dieses Protocol
  erfüllen, `DraftingService` kennt nie eine konkrete Implementierung.
- `Settings.local_ai_runtime: str = "ollama"` (app/config/settings.py:174)
  existiert bereits als eigenes Konfigurationsfeld mit einem
  Validator (`local_ai_runtime_must_be_supported`, Zeile 188-195), der
  aktuell nur `{"ollama"}` als unterstützten Wert akzeptiert - das Feld
  selbst ist also bereits für weitere Werte angelegt.
- `ModelCatalogEntry.runtime` (app/local_ai/model_catalog.py) trägt
  bereits ein `runtime`-Feld pro Katalogeintrag (aktuell ausschließlich
  `"ollama"` befüllt).
- `app/ai_providers/factory.py::build_local_llm_provider` baut aktuell
  IMMER `OllamaLocalLLMProvider` (Zeile 123-126, kein Runtime-Dispatch) -
  das ist die einzige Stelle, die für eine zweite Runtime erweitert werden
  müsste (Dispatch auf `settings.local_ai_runtime`).

**Bewusst NICHT umgesetzt in dieser Session**: eine echte llama.cpp-
Integration (Installation, Modell-Download, Provider-Implementierung,
Benchmark) wurde NICHT begonnen - das wäre ein eigener, mehrstündiger
Workstream (Kompilierung/Installation, GB-große Modell-Downloads, echte
Vergleichsmessungen) und stand in dieser Iteration nicht im Verhältnis zu
den übrigen bearbeiteten Punkten. Der obige Befund ist die konkrete,
code-basierte Grundlage für diesen Workstream, kein Ersatz dafür - siehe
`.agentic/OPEN_ISSUES.md` (HIGH) für den nächsten konkreten Schritt.

## Entscheidungslogik (aktuell implementiert)

`app/local_ai/hardware_detector.py` + `model_catalog.py` +
`recommendation.py`: Hardware wird klassifiziert (5 Tiers), Modellkatalog
enthält u. a. `qwen2.5:1.5b` (Priorität 0, empfohlen), verschiedene
`qwen3`-Varianten (höhere Priorität, für stärkere Hardware). Empfehlung
erfolgt über `RecommendationEngine.recommend(HardwareProfile)`. Diese
Logik existierte bereits vor dieser Session (Verdrahtung war die Lücke,
siehe DECISIONS.md).
