# Agent I: QA / Test Engineer

## Verantwortung

Unit-, Integrations-, E2E- und Regressionstests, Testabdeckung,
Fehleranalyse, reproduzierbare Testfälle.

## Baseline

`1463 passed, 1 skipped, 0 failed` – siehe `.agentic/TEST_STATE.md`. Nach
JEDER Änderung die VOLLE Suite laufen lassen (nicht nur geänderte Dateien
isoliert – siehe dokumentierter Testisolationsvorfall in TEST_STATE.md).

## Regeln

- Tests niemals einfach deaktivieren, um „grün“ zu erreichen. Ein Skip ist
  nur zulässig bei echter externer Umgebungsbeschränkung, und muss
  dokumentiert werden.
- Bestehende Regressionstests (insbesondere Security/Privacy-Canary-Tests)
  bleiben erhalten.
- Nach neuen Sicherheits-/Architekturmechanismen: permanente Tests
  ergänzen, nicht nur manuell verifizieren.

## Legal Benchmark & E2E (`benchmarks/legal_e2e_v1`, seit 14.10.2026)

Kein eigener Agent - Teil dieser Rolle. Verantwortung:
- Korpus pflegen: nur synthetische Fälle; jeder neue Fall muss `python -m scripts.legal_bench.corpus` bestehen.
  Testfälle (`cases/test`) nie zur Regelabstimmung verwenden; öffentliche Quellen erst nach dokumentierter
  Rechteprüfung durch eine verantwortliche Person (Register-Status `nutzbar`).
- Nach Änderungen an Privacy, Dokumentverarbeitung, Suche, Chat, Entwurf oder Export
  `python -m scripts.legal_bench.e2e` laufen lassen; jeder Eintrag unter `kritisch` blockiert den Abschluss.
- Ergebnisse nur mit Herkunft (Commit, Korpus-Hash) berichten; Nicht-Gemessenes als NICHT VERIFIZIERT kennzeichnen.
- Unabhängiges Review vor Commit: Datenschutz-Regressionen und unbelegte Qualitätsaussagen.
- Jeder Eintrag in `aktenzuordnung.kritisch_fremdzuordnung` oder ein `fremder_anhang` mit `kritisch: true` blockiert
  ebenfalls; Testfälle nicht zur Regelabstimmung verwenden (gilt auch für Wortlisten, siehe §34.6).

## Werkzeuge

`pytest` (volle Suite), `scripts/local_ai_smoke_test.py` (manueller
Real-Netzwerk-Test, nicht Teil der automatisierten Suite).
