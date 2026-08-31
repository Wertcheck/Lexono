# Skill: Model Evaluation (Benchmark neuer Modelle/Runtimes)

## Zweck

Neue lokale Modelle oder Runtimes (llama.cpp, weitere Open-Weight-Modelle)
seriös und reproduzierbar bewerten.

## Voraussetzungen

Nur synthetische Testdaten. Keine echten Mandantendaten für Benchmarks
(CLAUDE.md, nicht verhandelbar).

## Vorgehensweise

1. Repräsentative Kanzleiaufgaben definieren (Zusammenfassung, Extraktion,
   Antwortentwurf ans Finanzamt, fehlende Informationen erkennen,
   strukturierte Ausgabe) – synthetische Beispiele aus
   `app/synthetic_data/generator.py` nutzen, sofern passend.
2. Für jedes Modell: Latenz (cold/warm), RAM/VRAM-Bedarf, Ergebnis
   manuell auf Halluzinationen/Aufgabentreue prüfen.
3. Ergebnisse tabellarisch in `.agentic/MODEL_EVALUATION.md` festhalten.
4. Bei klarer Überlegenheit: `model_catalog.py` erweitern und Empfehlung
   anpassen (siehe `skills/local_ai/SKILL.md`).

## Erwartetes Ergebnis

Eine belastbare Aussage der Form „Für Hardware-Tier X ist Modell Y aktuell
die beste Wahl“ – NICHT nur „dieses Modell funktioniert“.

## Relevante Dateien

`.agentic/MODEL_EVALUATION.md`, `app/local_ai/model_catalog.py`,
`app/local_ai/recommendation.py`
