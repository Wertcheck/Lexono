# §106 Behebung A6 (Lückenlosigkeit der Zwischenprüfungen); A5 und A9 nachgeschärft

Stand 21.10.2026. Branch `feature/feedback-diagnose`, HEAD `8e94bc1` (geprüft). Die Änderungen sind **uncommittet**.
**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben **GESPERRT**.

**Grenzen dieser Arbeit:**
- Nur lokale Änderungen an Code, Tests und Dokumentation.
- Keine System-, Hyper-V-, Netz-, DNS-, Firewall-, Konto- oder Rechteänderung. Keine VM, keine echten Schlüssel, keine
  externen Aufrufe, keine Produktivdatenbank.
- Alle Nachweise liefen lokal: tmp-Kopien, simuliertes Hyper-V (`FakeSystem`), synthetische Belege. Unit-Tests und
  Simulation sind **kein Nachweis realer Isolation**.

**Beleg-Klassen:** [Code] gelesen · [Unit] Unit-/Regressionstest · [Sim] gegen simuliertes Hyper-V.

## 1 Geänderte Dateien

| Datei | Befund | Art |
|---|---|---|
| `scripts/legal_bench/isolation_evidence.py` | A6 | `MAX_KONTROLL_LUECKE_S`; `_abdeckung_problem`; `dauer_s` in `KANARIEN_FELDER`; Aufruf in `_eval_kanarien` |
| `scripts/legal_bench/isolation_real.py` | A6 | Zeitstempel in `_fw_kontrolle` |
| `scripts/legal_bench/isolation_acceptance.py` | A6 | synthetische Phasendauer klein |
| `scripts/legal_bench/isolation_host_control.py` | A5 | `startcheck_pflicht`; `_require_startcheck` in allen mutierenden Methoden; CLI-Sonderguard entfernt |
| `tests/test_s106_review_fixes.py` | A5/A6 | **neu**, 16 Tests |
| `tests/test_s98_review_fixes.py`, `test_s102_…`, `test_s104_…` | A5/A9 | Szenario-Konstruktionen mit `startcheck_pflicht=False`; V7-/CLI-Quelltexttests durch Verhaltenstests ersetzt |

## 2 Befunde

### A6 – Lückenlosigkeit der periodischen Zwischenprüfungen → BEHOBEN (Simulation/Unit)

**Ursprung:** AUDIT_S103 §2 A6 (OFFEN). Die periodischen Firewall-/Listener-Prüfungen während der Nachher-Phase wurden
nur auf Fehler bewertet, nicht auf Anzahl/Abstand. Ein hängender oder abgestürzter Prüf-Thread blieb unbemerkt.

**Vorgeschriebenes Intervall [Code]:** `isolation_real.KONTROLL_INTERVALL_S = 15 s` (unverändert, nicht erfunden). Die
`PeriodicCheck` wartet beim Beenden bis zu 30 s. Daraus die höchste zulässige Lücke
`MAX_KONTROLL_LUECKE_S = 3 × 15 = 45 s` (deckt Beendigung plus Jitter).

**Änderung [Code]:**
- `_fw_kontrolle` trägt jetzt einen Zeitstempel (`zeit`); die Listener-Kontrollen haben ihn bereits.
- `dauer_s` (monoton gemessene Phasendauer, vom Haupt-Thread) wird in die bewertbaren Kanarienfelder übernommen.
- Neu `_abdeckung_problem(content)`: Aus `dauer_s` folgt die MINDESTZAHL erwarteter `nachher_waehrend`-Prüfungen je
  Kanal (`int(dauer // 45) − 1`). Zu wenige → BLOCKIERT. Fehlt `dauer_s` → BLOCKIERT. Zwischenprüfungen ohne Zeitstempel
  oder nicht monoton (umsortiert/manipuliert) → BLOCKIERT. Firewall **und** Listener werden getrennt geprüft.
- `_eval_kanarien` ruft die Prüfung nach den bestehenden Phasen-/Kontrollprüfungen auf.

**Eigenschaften (geprüft):**
- Fehlende Zwischenprüfungen werden durch Start-/Endcheck **nicht** kompensiert (die Mindestzahl ist von Start/Ende
  unabhängig) — [Unit] `test_end_check_does_not_compensate_missing_interior_checks`.
- Umsortierte/doppelte/zeitstempellose Prüfungen fallen auf — [Unit] `test_reordered_interior_checks_block`,
  `test_interior_check_without_timestamp_blocks`.
- Eine Lücke bei nur **einem** der beiden Kanäle blockiert — [Unit] `test_listener_gap_blocks_even_if_firewall_is_complete`.
- Kurze Phasen (≤ 45 s) brauchen keine Zwischenprüfungen — [Unit] `test_short_phase_needs_no_interior_checks`.
- Ende-zu-Ende: ein langer Nachher-Phasenlauf ohne Zwischenprüfungen → [Sim] BLOCKIERT
  (`test_evaluation_blocks_long_phase_without_interior_checks`); der synthetische Kurzlauf bleibt PASS.

**Grenze/Modell:** `dauer_s` stammt vom Host (E1). Ein kompromittierter Host könnte `dauer_s` zu klein melden und so
die Mindestzahl senken — das liegt außerhalb des Modells (AUDIT_S95 „Grenzen des Modells“). Für den ehrlichen
Betriebsfall (hängender Thread) ist die Erkennung belastbar. **Reales Timing NICHT VERIFIZIERT.**

### A5 – Startcheck unabhängig vom Aufrufweg → BEHOBEN (Methodenebene), mit dokumentiertem Restrisiko

**Ursprung:** AUDIT_S105 A5. In §105 saß die Erzwingung nur in der CLI; ein direkter Methodenaufruf umging sie.

**Analyse [Code]:** Produktiv entstehen reale Änderungen nur über `acl_setzen`/`fw_setzen`/`image_zuruecksetzen`. Die CLI
konstruiert `HostControl` **immer mit** Änderungsjournal; `host_lauf` konstruiert `ausfuehren=False` und mutiert nie.

**Änderung [Code]:**
- Die Erzwingung wanderte aus der CLI in die drei Methoden (`_require_startcheck`). Sie greift, sobald ein Journal
  geführt wird — also bei jedem realen Aenderungspfad, **unabhängig** davon, ob der Aufruf über die CLI oder direkt
  erfolgt.
- `startcheck_pflicht` (Default True) erlaubt Tests, Absturz-/Rückbau-Szenarien ohne vorherigen Startcheck aufzubauen;
  Produktion nutzt den Default.
- Der frühere CLI-Sonderguard entfiel (keine zweite, getrennt pflegbare Prüfstelle mehr) — CLI und Methode teilen
  dieselbe Erzwingung ([Sim] `test_cli_and_method_share_the_same_enforcement`).

**Belege:**
- [Sim] `test_direct_method_call_requires_startcheck` (acl/fw/image): der **direkte** Aufruf ohne Startcheck → BLOCKIERT.
- [Sim] `test_method_allows_mutation_after_ok_startcheck`, `test_stale_startcheck_does_not_authorize` (12 h).

**Restrisiko (dokumentiert, nicht behoben):** Ein mutierender Aufruf **ohne** Journal wird nicht erzwungen
(Unit-/Ad-hoc-Kontext). Das ist kein Produktionspfad (die CLI führt immer ein Journal). [Unit]
`test_without_journal_enforcement_is_not_applied_unit_context` hält das Verhalten fest. Die 12-h-Frist bleibt eine
Heuristik. Alles nur simuliert.

### A9 – Testqualität → NACHGESCHÄRFT

**Ursprung:** AUDIT_S105 A9 (TEILWEISE). Es blieben zwei Quelltextprüfungen (`inspect.getsource`).

**Änderung:**
- V7: `test_v7_acceptance_requires_a_resolver_and_a_real_query` (Quelltextprüfung) → ersetzt durch
  `test_v7_dns_probe_result_is_meaningful_not_a_blind_block` (Verhaltens-/Negativtest: nur eine echte
  Zeitüberschreitung sperrt; Kopfzeilen bzw. leere Ausgabe ergeben „fehler“, nicht „blockiert“).
- CLI-Bedienweg: `test_cli_offers_journal_acknowledgement` (Quelltextprüfung) → ersetzt durch
  `test_journal_quittieren_refuses_while_residue_present` (Verhaltenstest: Quittieren verweigert bei Resten).

**Grenze:** Unit-Tests und Simulation beweisen weiterhin **keine reale Isolation**.

## 3 Tests

| Lauf | Ergebnis | Status |
|---|---|---|
| `tests/test_s106_review_fixes.py` (neu) | 16 bestanden | **PASS** |
| Isolationssuiten Teil 1 (§86, §87, §90, §92, §102, §104, §105, §106) | 316 bestanden, 8 übersprungen | **PASS** |
| Isolationssuiten Teil 2 (§91, §94, §96, §98) | 198 bestanden, 1 übersprungen | **PASS** |
| Syntaxprüfung der geänderten Dateien | ok | **PASS** |
| Übrige Benchmark-Tests §77–§85 | nicht erneut (Logik nicht berührt) | NICHT DURCHGEFÜHRT |
| Gesamtsuite, Lint (ruff fehlt) | — | NICHT DURCHGEFÜHRT |
| Reale VM-Abnahme V1–V13 | — | NICHT DURCHGEFÜHRT |

Alle Läufe einzeln im Vordergrund mit Speicherwächter (Start ≥ 4 GB frei, Stopp < 1,5 GB), `PYTHONDONTWRITEBYTECODE=1`;
der Stopp wurde nie ausgelöst, es wurde kein Bytecode geschrieben. Zwischenfehler durch die neue Erzwingung
(journallose Unit-Konstruktionen, fehlendes `dauer_s` im abgeleiteten Beleg) wurden korrigiert: Erzwingung nur bei
geführtem Journal; `dauer_s` in `KANARIEN_FELDER`.

## 4 Bewertung A5, A6, A9 und übrige Befunde

| # | Stand nach §106 |
|---|---|
| A5 | **behoben** (Erzwingung in den Methoden, CL-/Direktpfad gleich); Restrisiko: journalloser Aufruf (kein Produktionspfad), 12-h-Heuristik, nur simuliert |
| A6 | **behoben** (Mindestzahl/Monotonie der Zwischenprüfungen, Simulation/Unit); Restrisiko: `dauer_s` vom Host (Modellgrenze), reales Timing NICHT VERIFIZIERT |
| A9 | **behoben** (verbliebene Quelltextprüfungen durch Verhaltenstests ersetzt); Grenze: beweist keine reale Isolation |
| A7, A8, A10 | behoben (Code) wie AUDIT_S104/S105; Windows-/git-Semantik bzw. echte Signatur NICHT VERIFIZIERT |
| A1–A4, A11–A17 | wie AUDIT_S104/S105 (behoben bzw. teilweise); Paketinhalt, Pause-Verhalten, reale Hyper-V-Semantik bleiben Restrisiken |
| Grenzen des Modells | unverändert: Administrator in der VM, kompromittierter Host, Absenderfälschung |

**Alle in AUDIT_S100/S103 gelisteten Befunde A1–A17 sind nun behoben oder als ausdrückliches Restrisiko dokumentiert.**
Das ändert nichts daran, dass keine reale Abnahme stattfand.

## 5 Voraussetzungen (unverändert)

- **Commit** nur als Zwischenstand, **NICHT SICHERHEITSABGENOMMEN**.
- **D1 / reale VM-Abnahme:** identische venv Host/VM; Python systemweit; Isolationsvariablen gesetzt; Zähler-,
  Hyper-V-ACL-, Firewall- und Timing-Semantik real prüfen; Startcheck-Bedienablauf real durchspielen.
- **Sicherheitsfreigabe:** erst nach realer Abnahme, echter Owner-Signatur (D4) und erneutem unabhängigem Review.

**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben GESPERRT.
