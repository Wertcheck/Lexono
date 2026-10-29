# §104 Behebung der Befunde A11, A12, A13, A14, A17 aus AUDIT_S103 (A5/A7 geprüft)

Stand 21.10.2026. Branch `feature/feedback-diagnose`, HEAD `8e94bc1` (geprüft). Die Änderungen sind **uncommittet**.
**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben **GESPERRT**.

**Grenzen dieser Arbeit:**
- Nur lokale Änderungen an Code, Tests und Dokumentation.
- Keine System-, Hyper-V-, Firewall-, Netz-, Konto- oder Rechteänderung am Host. Keine VM, keine echten Schlüssel,
  keine externen Aufrufe, keine Produktivdatenbank.
- Alle Nachweise liefen lokal: tmp-Kopien, Kindprozesse, simuliertes Hyper-V (`FakeSystem`) und echte WinAPI-Proben
  gegen **tmp-Dateien mit Read-only-Attribut** (keine Systemdateien, nicht die echte venv).
- Eine reale VM-Abnahme (V1–V13) fand **nicht** statt. Grüne Tests sind kein Nachweis realer Isolation.

**Beleg-Klassen:** [Code] gelesen · [Unit] Unit-/Regressionstest · [Sim] gegen das simulierte Hyper-V · [Real-lokal]
echte WinAPI/FS-Messung im tmp-Verzeichnis · [Annahme] unbewiesen.

## 1 Geänderte Dateien

| Datei | Befund | Art |
|---|---|---|
| `scripts/legal_bench/isolation_host_control.py` | A11, A7 | `aktiver_acl_besitzer`; `acl_entfernen` blockiert fremde/mehrdeutige aktive ACL; `_fw_remove_command` gruppenweise |
| `scripts/legal_bench/isolation_codepfad.py` | A12, A14, A15 | WinAPI-Schreibprobe (`_win_create`, `_classify`, `_probe_file`, `_probe_dir`); `_importable_excluded`; case-insensitive Endungen |
| `scripts/legal_bench/isolation_gate.py` | A13 | Belegalter beim Gebrauch zusätzlich über den Zeitanker (strengerer Nutzungszeitpunkt) |
| `scripts/legal_bench/e2e.py` | A17 | Fake-Embedding-Fallback ohne `sys.path`-Änderung (importlib) |
| `tests/test_s104_review_fixes.py` | alle | **neu**, 20 Tests |
| `tests/test_s102_review_fixes.py` | A12/A9 | fiktiven `winerror`-Test durch `_classify`-Unittest ersetzt |

## 2 Befunde

### A11 – ACL-Lauf-Zuordnung (HOCH) → BEHOBEN (simuliert)

**Ursprung:** AUDIT_S103 §3 A11 / R5. `acl-entfernen --lauf <alt>` entfernte die aktive ACL eines anderen Laufs, weil
ACL-Einträge keine Laufkennung tragen und jede Abnahme dasselbe Soll hat.

**Änderung [Code]:**
- Neu `aktiver_acl_besitzer(journal)`: Da `acl_setzen` nur bei **leerer** Ist-ACL setzt, hält zu jedem Zeitpunkt
  höchstens ein Lauf eine ACL. Der Besitzer ergibt sich aus dem Journal (letzter nicht zurückgebauter `acl-setzen`);
  ein abgebrochener Lauf bleibt Besitzer bis zum bestätigten Rückbau.
- `acl_entfernen` prüft **vor** jeder Löschung:
  - gehört die aktive ACL laut Journal einem **anderen** Lauf als dem Ziel → **BLOCKIERT**, nichts wird entfernt;
  - ohne `--lauf`: aktive Einträge, die **kein** eigener `acl-setzen`-Beleg erklärt → **BLOCKIERT** (Herkunft unklar);
  - fremde Einträge (nicht im Soll) → wie bisher Abbruch.

**Belege:**
- [Unit] `test_aktiver_acl_besitzer_tracks_set_and_removal` (Setzen, Entfernen, Absturz, Abschluss).
- [Sim] `test_old_run_cannot_remove_active_acl_of_a_new_run`: lauf-a setzt+entfernt, lauf-b setzt; `--lauf lauf-a` und
  auch ohne `--lauf` (lauf-c) → BLOCKIERT, die ACL von lauf-b bleibt unangetastet.
- [Sim] `test_active_acl_without_journal_is_blocked`, `test_owner_run_removes_its_own_active_acl`,
  `test_removal_is_idempotent_after_recovery`.
- Alle §98/§102-Recovery-Tests (16 Abbruchfälle u. a.) weiterhin grün.

**Restrisiko:** Nur simuliert. Die Zuordnung stützt sich auf das Journal; geht es verloren, verweigert `--lauf`
(Herkunft nicht belegbar) und die menschliche Entscheidung bleibt nötig. Kein Schutz gegen einen kompromittierten Host.

### A12 – Windows-Schreibprüfung (HOCH) → BEHOBEN (real-lokal)

**Ursprung:** AUDIT_S103 §3 A12 / R9. `os.open` liefert unter Windows bei ACL-geschützten Dateien `winerror None`; der
Code verlangte aber `winerror 5`, sodass jede geschützte Datei „nicht_pruefbar“ ergab → A4 und die A1-Isolationsprobe
wären real immer BLOCKIERT. Der §102-Test belegte das Gegenteil nur mit künstlichem `winerror`.

**Ursache [Code]:** Die C-Laufzeit bildet `ERROR_ACCESS_DENIED` auf `errno 13` (`EACCES`) ab; `OSError.winerror`
bleibt dabei leer. Nur die WinAPI (`GetLastError`) liefert den echten Code.

**Änderung [Code]:**
- Neu `_win_create` über `CreateFileW` mit `WinDLL(use_last_error=True)` und `ctypes.get_last_error()`.
- `_probe_file`: Datei zum Schreiben **öffnen** (nichts schreiben). Erfolg → beschreibbar; `ERROR_ACCESS_DENIED (5)` →
  geschuetzt; alles andere (u. a. `SHARING_VIOLATION 32`, `FILE_NOT_FOUND`) → nicht_pruefbar.
- `_probe_dir`: Datei-Anlage (`CREATE_NEW` + `FILE_FLAG_DELETE_ON_CLOSE`, entfernt sich selbst) **und** Unterordner-
  Anlage (`CreateDirectoryW`/`RemoveDirectoryW`) prüfen; geschuetzt nur, wenn **beide** `ACCESS_DENIED` ergeben
  (schließt zugleich A16/R8 „Ordner-Anlage“ ein).
- Nicht-Windows: unveränderte `os.open`-Logik als Rückfall.
- `_classify` bildet nur `ACCESS_DENIED` auf „geschuetzt“ ab.

**Belege:**
- [Real-lokal, eigener Nachweis] beschreibbare Datei → beschreibbar; **Read-only-Attribut-Datei → geschuetzt**
  (echtes `ACCESS_DENIED`); beschreibbarer Ordner → beschreibbar; fehlende Datei → nicht_pruefbar; **echte deny-ACL auf
  einem tmp-Ordner (icacls, danach zurückgesetzt) → geschuetzt**; laufende beschreibbare EXE → nicht_pruefbar
  (Freigabeverletzung, kein falscher Schutz).
- [Unit/Real-lokal] `test_probe_classify_maps_only_access_denied_to_protected`,
  `test_probe_file_detects_real_write_and_readonly`, `test_probe_dir_detects_real_writability`,
  `test_running_writable_file_is_not_falsely_protected`, `test_write_access_uses_real_probe_and_flags_writable`,
  `test_a1_isolation_probe_uses_the_real_write_check`.

**Folge:** Ein korrekt geschützter Pfad in einer echten VM ergibt jetzt „geschuetzt“. A4 und die A1-Isolationsprobe
sind damit real bestehbar (nicht mehr unvermeidbar BLOCKIERT).

**Restrisiko:** Die Probe prüft Schreibbarkeit durch **Öffnen/Anlegen**, nicht die ACL-Rechte `WRITE_DAC`,
`WRITE_OWNER`, `DELETE` (§97 M1 bleibt offen). Freigabeverletzungen bei laufenden Dateien bleiben „nicht_pruefbar“
(fail-closed). **Auf der echten venv/Basis-Installation nicht ausgeführt** (keine Probedateien dort) – für die VM
vorgesehen (NICHT DURCHGEFÜHRT).

### A13 – Belegalter beim Gebrauch (MITTEL) → BEHOBEN (mit Kopplung an A3)

**Ursprung:** AUDIT_S103 §3 A13 / R6. Das 24-h-Belegalter beim Gebrauch (`MAX_EVIDENCE_AGE_AT_USE`) wurde nur gegen die
VM-Uhr geprüft.

**Änderung [Code]:** Das Gate leitet aus dem Host-Zeitanker und dem monotonen VM-Zähler einen Nutzungszeitpunkt
`anker_now` ab. Ist er **später** als die VM-Uhr (zurückgestellte Uhr), bewertet das Gate die Belege mit diesem
strengeren Zeitpunkt neu. Die bestehende Prüfung gegen die VM-Uhr bleibt zusätzlich.

**Belege:**
- [Unit] `test_evidence_age_at_use_blocks_with_rolled_forward_now`: dieselben Belege, Nutzung 30 h später → BLOCKIERT.
- [Unit] `test_age_at_use_is_checked_against_anchor_not_only_vm_clock`: VM-Uhr scheinbar frisch, Zähler zeigt 26 h →
  Gate geschlossen.

**Restrisiko / ehrliche Einordnung:** Da die Attestierungsgültigkeit ohnehin auf ≤ 24 h begrenzt ist, überschneidet
sich A13 praktisch vollständig mit A3: Jede über den Anker erkannte Zeitüberschreitung schließt das Gate bereits wegen
abgelaufener Bestätigung. Der eigenständige Gewinn ist gering. Der Anker selbst hängt am monotonen Zähler, dessen
Verhalten bei **VM-Pause/Snapshot/Speichern** weiter **NICHT VERIFIZIERT** ist (A3-Restrisiko, unverändert).

### A14 / A15 – importierbare ausgeschlossene Verzeichnisse, Endungs-Groß/Kleinschreibung (MITTEL/NIEDRIG) → BEHOBEN (lokal)

**Ursprung:** AUDIT_S103 §3 A14/A15 / R2/R3. `app/build/` war als `app.build` importierbar, aber gitignoriert und nicht
erfasst; der Bytecode-Scan war case-sensitiv (`.PYC`/`.PYD`).

**Änderung [Code]:**
- Neu `_importable_excluded`: meldet ein ausgeschlossenes Verzeichnis als Problem, sobald es importierbar ist (Paket
  mit `__init__` unter einem Paket oder direkt unter der Wurzel) und Code enthält. Flach und schnell (steigt nicht in
  ausgeschlossene Bäume ab).
- Bytecode-/Erweiterungs-Endungen und die Wurzel-Modulprüfung sind case-insensitiv.

**Belege:**
- [Unit] `test_importable_excluded_subpackage_is_flagged` (`app/build/`),
  `test_excluded_dir_without_package_parent_is_not_flagged`, `test_bytecode_extension_scan_is_case_insensitive`.
- [Real-lokal] Das echte Repository meldet in einem frischen, isolierten Prozess **keine** Codequellen-Probleme außer
  der fehlenden Bytecode-Isolation (`test_real_repo_has_no_code_source_false_positives`, Subprozess; 0 Fehlalarme,
  ~0,2 s).

**Restrisiko:** Namespace-Pakete ohne `__init__` sind weiter nur über die geladenen Module erfasst, nicht statisch.
Der Inhalt von venv/site-packages bleibt ungehasht (A1-Restrisiko).

### A17 – e2e-Fallback mutiert den Suchpfad (NIEDRIG) → BEHOBEN

**Ursprung:** AUDIT_S103 §3 A17. Ohne Embedding-Modell hängte `e2e._embedding_provider` `tests/` an `sys.path` → die
Codequellenprüfung meldete einen unbekannten Suchpfadeintrag (Gate-Fehlalarm).

**Änderung [Code]:** Der Fake-Provider wird über `importlib.util.spec_from_file_location` geladen, ohne `sys.path` zu
verändern.

**Belege:** [Unit] `test_e2e_embedding_fallback_does_not_mutate_sys_path`; [Sim] `test_s80_benchmark_driver.py`
(30 bestanden) – der Treiberpfad bleibt funktionsfähig.

**Restrisiko:** In einer isolierten VM ohne Modell-Cache ist der Fake-Provider aktiv; die Retrieval-Qualität ist dann
ausdrücklich „NICHT VERIFIZIERT“ (unverändert). Für eine echte Abnahme gehört das Produktionsmodell ins Golden Image.

### A5 – Startcheck nicht erzwungen (NIEDRIG) → GEPRÜFT, TEILWEISE

**Befund [Code]:** `acl_setzen` und `fw_setzen` setzen weiterhin nur bei **leerer** Ist-Ressource (ACL bzw. eigene
Firewall-Gruppe); Reste eines früheren Laufs verhindern einen neuen Lauf also technisch
(`test_acl_setzen_refuses_over_residue`). Der **vollständige** `startcheck` (VM aus, Kinddatenträger, Protokolle,
offene Journalvorgänge) wird von den Setzbefehlen **nicht** erzwungen; er bleibt ein eigener, empfohlener Schritt.

**Bewusste Entscheidung:** Eine harte Erzwingung in den Setzbefehlen hätte den legitimen Testfall „mehrere getrennt
abgestürzte Läufe“ gebrochen und ist für die reale Bedienung in `host_lauf`/CLI besser aufgehoben. **Urteil: OFFEN**
(Teilschutz über die Ressourcen-Leerprüfung vorhanden).

### A7 – Entfernen über Anzeigenamen (NIEDRIG) → BEHOBEN (Code), real nicht verifiziert

**Änderung [Code]:** `_fw_remove_command` begrenzt das Entfernen mit `-Group 'Lexono-Abnahme'`; ein nicht eindeutiger
`DisplayName` trifft so keine Regeln außerhalb der eigenen Gruppe. Innerhalb der Gruppe sind die Namen durch die
`run_id` eindeutig.

**Beleg:** [Unit] `test_firewall_remove_is_scoped_to_the_group`.

**Restrisiko:** Die tatsächliche Windows-Semantik von `Remove-NetFirewallRule -DisplayName -Group` ist **NICHT
VERIFIZIERT**; das simulierte System modelliert nur den Namen.

## 3 Tests

| Lauf | Ergebnis | Status |
|---|---|---|
| `tests/test_s104_review_fixes.py` (neu) | 20 bestanden | **PASS** |
| Isolationssuites Teil 1 (§86, §87, §90, §92, §102, §104) | 280 bestanden, 8 übersprungen | **PASS** |
| Isolationssuites Teil 2 (§91, §94, §96, §98) | 353 bestanden, 2 übersprungen (zwei Läufe) | **PASS** |
| Benchmark-Treiber §80 (prüft den e2e-Pfad) | 30 bestanden | **PASS** |
| Syntaxprüfung der geänderten Dateien | ok | **PASS** |
| Übrige Benchmark-Tests §77–§79, §81–§85 | nicht erneut (unverändert seit §102/§99) | NICHT DURCHGEFÜHRT |
| Gesamtsuite, Lint (ruff fehlt) | — | NICHT DURCHGEFÜHRT |
| Schreibtest auf echter venv/Basis-Installation | bewusst nicht (Probedateien) | NICHT DURCHGEFÜHRT |
| Reale Abnahme V1–V13 | — | NICHT DURCHGEFÜHRT |

Alle Läufe einzeln im Vordergrund mit Speicherwächter (Start ≥ 4 GB frei, Stopp < 1,5 GB), `PYTHONDONTWRITEBYTECODE=1`;
der Stopp wurde nie ausgelöst, es wurde kein Bytecode geschrieben.

## 4 Restlücken und offene Befunde

| # | Stand nach §104 |
|---|---|
| A11 | behoben (simuliert); Journalverlust bleibt menschliche Entscheidung |
| A12 | behoben (real-lokal gegen tmp); ACL-Rechte (WRITE_DAC u. a.) weiter ungeprüft; echte venv nicht getestet |
| A13 | behoben, praktisch durch A3 abgedeckt; Zählerverhalten bei Pause/Snapshot NICHT VERIFIZIERT |
| A14/A15 | behoben (lokal); Namespace-Pakete nur über geladene Module; Paketinhalt ungehasht |
| A17 | behoben |
| A5 | OFFEN (Teilschutz über Ressourcen-Leerprüfung) |
| A7 | behoben im Code; Windows-Semantik NICHT VERIFIZIERT |
| A6, A8, A9, A10, A16 | unverändert OFFEN bzw. teilweise (A16 Ordner-Anlage jetzt geprüft, ACL-Rechte offen) |
| A1–A4 (§102) | wie AUDIT_S103, mit den obigen Verbesserungen; Paketinhalt und Import-nach-Prüfung bleiben Restrisiken |
| Grenzen des Modells | unverändert: Administrator in der VM, kompromittierter Host, Absenderfälschung |

## 5 Voraussetzungen (unverändert gegenüber AUDIT_S103, aktualisiert)

- **Commit** dieses Stands nur als Zwischenstand, **NICHT SICHERHEITSABGENOMMEN**, mit Nennung der offenen Befunde
  (A5, A6, A8–A10) in der Nachricht.
- **D1 / reale VM-Abnahme:** A5 abschließen (Startcheck in `host_lauf`/CLI erzwingen), A8 (Laufzeit-Hash beim
  Signieren, identische venv dokumentieren), A9/A10; Python systemweit; Isolationsvariablen gesetzt; Zählerverhalten
  bei Pause/Snapshot real messen; Hyper-V-ACL/Firewall-Semantik real prüfen.
- **Sicherheitsfreigabe:** erst nach realer Abnahme, Owner-Signatur (D4) und erneutem unabhängigem Review.

**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben GESPERRT.
