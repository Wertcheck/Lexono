# Freigabe-Checkliste für einen späteren Lauf (`legal_answers_v1`)

Stand §77, 18.10.2026. **Kein Punkt ist freigegeben.** Jeder OFFENE Punkt verhindert einen Lauf.
`python -m scripts.legal_bench.answer_run check --config <run_config>` prüft die maschinell erfassbaren Punkte. Es meldet
„LAUF NICHT FREIGEGEBEN“, solange ein Feld `null` ist, `approved` nicht `true` ist oder eine Datenschutzvorgabe
abgeschwächt wurde. Eine Ausführungsfunktion gibt es bewusst nicht; sie ist Teil der späteren Freigabe (C-14).

| # | Entscheidung | Feld in `run_config` | Zuständig | Status |
|---|---|---|---|---|
| C-1 | Modell-Anbieter, Modell-ID und genaue Modellversion | `model.provider`, `model.model_id`, `model.model_version` | Owner | **OFFEN** |
| C-2 | Parameter: Temperatur, max_tokens | `model.temperature`, `model.max_tokens` | Owner | **OFFEN** |
| C-3 | Lexono-Commit und Prompt-Version, die den Payload erzeugen | `pipeline.lexono_commit`, `pipeline.prompt_version` | Owner/Technik | **OFFEN** |
| C-4 | Fälle und Splits; Reihenfolge erst dev, dann test | `scope.specs`, `scope.splits` | Owner | Vorlage: LA-D005, LA-D006, LA-T004 – **OFFEN bis Freigabe** |
| C-5 | Wiederholungen je Aufgabe; daraus ergibt sich die Aufrufzahl (derzeit 6 × Wiederholungen) | `scope.repetitions` | Owner | **OFFEN** |
| C-6 | Höchstzahl der Aufrufe | `limits.max_calls` | Owner | **OFFEN** |
| C-7 | Maximaler Kostenrahmen in EUR und Preisannahme je Aufruf | `limits.max_cost_eur`, `limits.price_per_call_eur_estimate` | Owner | **OFFEN** |
| C-8 | Abbruch nach fehlgeschlagenen Aufrufen | `limits.abort_after_failed_calls` | Owner | **OFFEN** |
| C-9 | Abbruch beim ersten Datenschutzbefund | `limits.abort_on_first_privacy_finding` | – | fest **true** (nicht abschwächbar) |
| C-10 | Datenschutz: nur über den pseudonymisierten Lexono-Pfad, Payload-Erfassung, Leak-Check, Fail-Closed, keine echten Mandantendaten | `pipeline.path`, `privacy.*` | Datenschutz | Pflichtwerte fest; Freigabe `approvals.datenschutz_freigabe` **OFFEN** |
| C-11 | Ablage der Rohantworten und Payload-Hashes (lokal) und Aufbewahrungsdauer | `storage.results_dir`, `storage.retention` | Owner/Datenschutz | **OFFEN** |
| C-12 | Juristische Freigabe der Spezifikationen: Referenzlösungen, Varianten, Alternativergebnisse (Audit J1–J3) | `approvals.juristische_freigabe_spezifikationen` | qualifizierte juristische Person | **OFFEN** |
| C-13 | Gewichte und PASS/FAIL-Schwellen je Dimension (Audit J4); bis dahin gibt es nur FAIL_KRITISCH oder OFFEN, nie PASS | `rubric.aggregation`, `approvals.juristische_freigabe_schwellen_und_gewichte` | qualifizierte juristische Person | **OFFEN** |
| C-14 | Ausführungsschritt: Umsetzung über den regulären Lexono-Chat- und Schriftsatzpfad mit Payload-Erfassung; erneute Payload-Prüfung vor dem ersten Aufruf | – (nicht implementiert) | Technik + Owner | **OFFEN** |
| C-15 | Bewertende Personen, blinde Bewertung, Zweitprüfung bei kritischen Fehlern (Audit J5) | `evaluation.reviewers`, `evaluation.second_reviewer_for_critical` | Owner/juristisch | **OFFEN** |
| C-16 | Kritische Fehler: Definition je Fall (`dangerous_errors`); bestätigter kritischer Fehler ergibt FAIL_KRITISCH ohne Ausgleich | Spezifikationen, `rubric.aggregation` | qualifizierte juristische Person | Regel fest, Inhalte **OFFEN** (J2) |
| C-17 | Ausdrückliche Gesamtfreigabe des Laufs | `approved`, `approvals.owner_freigabe_lauf_und_kosten`, `run_id` | Owner | **OFFEN** |

**Vor dem Lauf (technisch, Pflicht):**

- Spezifikationen gültig und Sperre unverändert (`python -m scripts.legal_bench.answer_specs`).
- Aufrufplan erzeugt und archiviert (`python -m scripts.legal_bench.answer_run plan --config <run_config>`).
- `check` meldet „LAUF FREIGEGEBEN“.
- Nach Freigabe der Schwellen bewusst neu sperren (`--update-lock --reason …`). Danach gilt für den Test-Split: keine
  Änderung mehr, solange Ergebnisse ausgewertet werden.
