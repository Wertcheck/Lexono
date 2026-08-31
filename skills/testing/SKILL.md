# Skill: Testing / Regressionslauf

## Zweck

Sicherstellen, dass jede Änderung die Test-Baseline
(`.agentic/TEST_STATE.md`) nicht verschlechtert.

## Vorgehensweise

1. Relevante Testdatei(en) lesen, bestehendes Testmuster übernehmen.
2. Neuen/geänderten Test schreiben.
3. Zunächst die betroffene(n) Testdatei(en) isoliert laufen lassen
   (schnelles Feedback).
4. IMMER anschließend die VOLLE Suite laufen lassen (`pytest`) – manche
   Fehler treten nur ordnungsabhängig auf (siehe dokumentierter
   `app.state`-Testisolationsvorfall in `.agentic/TEST_STATE.md`).
5. `.agentic/TEST_STATE.md` mit dem neuen Ist-Stand aktualisieren.

## Erwartetes Ergebnis

Mindestens die bisherige Anzahl bestandener Tests, keine neuen Fehler,
keine neu deaktivierten Tests ohne dokumentierten externen Grund.

## Typische Fehler

Nur die geänderte Datei testen und die volle Suite überspringen –
verdeckt ordnungsabhängige Fehler.

## Relevante Dateien

`.agentic/TEST_STATE.md`
