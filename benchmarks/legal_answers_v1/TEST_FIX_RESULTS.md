# Testkorrektur: zustandsabhängige `committed_stand`-Tests

Stand 22.10.2026. Branch `feature/feedback-diagnose`, HEAD `8f0b702`. **Nur Teständerungen**, kein Produktionscode
geändert. **Keine Sicherheitsfreigabe.** D1, D4-Signierung und C-14 bleiben **GESPERRT**. Schließt den offenen
Punkt §5 Nr. 4 aus `P3_RESULTS.md`.

## 1 Ausgangslage

Drei Tests erwarteten `owner_sign.committed_stand("HEAD")["sha256"] is None` auf Basis des **damaligen**,
uncommitteten Zustands (§90–§105). Seit dem Checkpoint-Commit `8f0b702` (§110–§112) sind die Fingerabdruck-Dateien
festgeschrieben; der Arbeitsbaum stimmt für alle Fingerabdruck-Dateien mit dem HEAD überein, und `committed_stand`
liefert **korrekt** einen gebundenen Hash (`6414aec…`). Die Tests schlugen damit fehl — nicht wegen eines
Logikfehlers, sondern wegen einer veralteten, zustandsabhängigen Annahme.

Betroffen:
- `tests/test_s96_review_fixes.py` — `test_committed_stand_detects_uncommitted_files`
- `tests/test_s98_review_fixes.py` — `test_real_git_reports_uncommitted_tool_files`
- `tests/test_s105_review_fixes.py` — `test_real_git_reports_uncommitted_tool_files`

## 2 Analyse: ursprünglich abgesicherte Absicht

Alle drei sollten gegen **echtes git** sicherstellen, dass ohne festgeschriebene Fingerabdruck-Dateien **keine
Signaturbasis** entsteht und Signieren damit unmöglich bleibt. Die isolierte, stub-basierte Prüfung dieses
„fehlender Commit → None"-Pfades existiert **unabhängig** bereits in Nachbartests:
- s105 `test_committed_stand_detects_added_untracked_file` (`_git(ls_names=names[:-1])` → None, „nicht committet"),
- s98 `test_commit_boundary_detects_changed_untracked_and_unknown_files` (`_batch(missing=…)` → None),
- s96 `test_committed_stand_matches_worktree_only_if_contents_match` (Inhaltsbindung) + `…refuses_a_worktree_that_differs…`.

Die drei fehlgeschlagenen Tests waren also die **einzigen**, die diese Absicht an den **realen** HEAD koppelten —
genau diese Kopplung ist durch den Commit überholt.

## 3 Korrektur (nur Testfälle + Importe)

Jeder der drei Tests wurde **zustandsunabhängig** umformuliert (umbenannt in
`…committed_stand_binds_commit_but_gate_stays_closed`) und prüft jetzt dreierlei:

1. **Reales Post-Commit-Verhalten:** `committed_stand("HEAD")` liefert entweder einen wohlgeformten, gebundenen
   64-stelligen Hex-Hash (reproduzierbar bei erneutem Aufruf) **oder** — falls der Arbeitsbaum je abweicht — `None`
   **mit** Begründung (`fehlend`), nie still ausgelassen. Damit bleibt der Test in beiden Git-Zuständen gültig.
2. **Isolierte Simulation eines fehlenden Commits:** über den jeweiligen git-Stub des Tests (`_git`, `_batch` bzw.
   ein lokaler ls-tree-Stub) → `sha256 is None` mit Begründung. Die ursprüngliche Sicherheitsabsicht bleibt
   reproduzierbar erhalten.
3. **Gate bleibt geschlossen:** `IsolationGate.from_environment().problems(...)` enthält `owner_keys.ROOT_MISSING`.
   Damit ist ausdrücklich geprüft: **eine gebundene Signaturbasis ist keine Freigabe** — ohne Wurzelschlüssel bleibt
   das Gate zu.

Keine Assertion wurde gelockert; es wurde eine **zusätzliche** Sicherheitsassertion (Gate geschlossen) ergänzt.
Keine Skips. Importe `owner_keys` in s98/s105 ergänzt (s96 hatte ihn bereits). Produktionscode unverändert.

## 4 Ausgeführte Tests

Alle mit `PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider`.

| Lauf | Ergebnis |
|---|---|
| die 3 korrigierten Testfälle einzeln | **3 bestanden** (4,9 s) |
| Regressionssubset `-k "committed/fingerprint/tool_files/gate/ROOT/owner/sign/problems/im_fingerprint"` über s96, s98, s105, s87, s90 | **165 bestanden, 123 deselektiert, 0 fehlgeschlagen** (230 s) |

Klasse: **Unit/lokale git-Lesung** — kein Nachweis realer Isolation. Keine echte Signatur, keine VM, keine
Systemänderung. Die vollständige Isolationssuite wurde nicht erneut gelaufen (nicht erforderlich; die relevanten
`committed_stand`/Gate-Tests sind abgedeckt).

## 5 Verbleibende Risiken / nächste Schritte

- Unverändert offen: reale D1-Prüfung (VM, getrennte Konten, Windows-Rechte), D4-Signatur, V1–V13 — siehe
  `SECURITY_EXIT_PLAN.md` / `D1_PREPARATION_PLAN.md`.
- Nichts committet/gestaged — Freigabe des Commits liegt beim Owner.

**Keine Sicherheitsfreigabe. D1, D4-Signierung und C-14 bleiben GESPERRT.**
