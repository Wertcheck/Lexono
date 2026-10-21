# §102 Sicherheitsbefunde A1–A4 aus AUDIT_S100 beheben

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `8e94bc1` (vor Beginn geprüft). Die Änderungen dieses
Schritts sind **uncommittet**. Der Commit `8e94bc1` ist und bleibt **NICHT SICHERHEITSABGENOMMEN**.

**Grenzen dieser Arbeit:**
- Nur lokale Änderungen an Code, Tests und Dokumentation.
- Keine Änderungen an Windows, Hyper-V, Firewall, ACL, Netzwerk oder Konten. Keine VM, keine echten Schlüssel, keine
  externen Verbindungen. Die Produktivdatenbank wurde nicht geöffnet.
- Gelesen (ohne Änderung) wurden auf diesem Rechner nur der Zählerstand `GetTickCount64` und die Boot-ID in der
  Registry (`HKLM\SYSTEM\…\PrefetchParameters\BootId`).
- Alle Prüfungen laufen gegen Kopie-Verzeichnisse in `tmp_path`, Kindprozesse und das **simulierte** Hyper-V aus §94.
  Sie belegen nur die Werkzeugkette, **keine reale Isolation**.
- **Keine Sicherheitsfreigabe.** D1 und C-14 bleiben gesperrt. A5–A10 bleiben offen.

## 1 Geänderte Dateien

| Datei | Art |
|---|---|
| `scripts/legal_bench/isolation_codepfad.py` | **neu** (A1, A4): Bytecode-Isolation, Codequellen, Überschattung, Suchpfad, geladene Module, Schreibtest über alle Code- und Laufzeitpfade |
| `scripts/legal_bench/isolation_gate.py` | Fingerabdruck über alle importierbaren `*.py`; `codepfad_probleme`; Zeitanker (`vm_zeitbasis`, `zeitanker_probleme`) im Gate |
| `scripts/legal_bench/isolation_evidence.py` | V2: `codequellen_eindeutig`, strengerer Schreibschutz (`_write_protected`); V13: Zeitanker Pflicht; `zeitanker` im Urteil |
| `scripts/legal_bench/isolation_real.py` | VM-Teil: Codequellen und Zeitanker als Beobachtung; pytest-Kindprozesse mit Bytecode-Isolation; Schreibtest über die tatsächliche Installation |
| `scripts/legal_bench/isolation_host_control.py` | A2: Abschluss offener Vorgänge nach nachgelesenem Ist-Zustand; Zeilenende-Schutz im Journal; `journal-quittieren`; Abschluss alter Image-Rücksetzungen |
| `scripts/legal_bench/isolation_acceptance.py` | synthetische Belege: Zeitanker, vollständiger Schreibtest, Codepfad |
| `tests/test_s102_review_fixes.py` | **neu**, 74 Tests |
| `tests/isolation_test_support.py`, `tests/test_s90_owner_gate.py`, `tests/test_s94_real_acceptance_path.py` | an die strengere Semantik angepasst (Test-Gate injiziert Codepfad und Zeitbasis; das Produktiv-Gate lehnt den pytest-Prozess ausdrücklich ab) |

## 2 Befund, Änderung, Test, Restrisiko

### A1 – Codequellen (HOCH)

**Befund (AUDIT_S100):** Fremder Code ließ sich unbemerkt laden:
- R2a: untergeschobene `.pyc` im `__pycache__`,
- R2b: quellenlose `.pyc`,
- R3: Wurzel-Paket, das eine Bibliothek überdeckt.

**Änderung:**
1. **Bytecode-Isolation ist Pflicht.**
   - Prüfender Prozess und pytest-Kindprozesse laufen mit `PYTHONDONTWRITEBYTECODE=1` (bzw. `-B`) und einem **nicht
     existierenden** `PYTHONPYCACHEPREFIX`. Dessen nächster existierender Elternordner darf für das Konto nicht
     beschreibbar sein.
   - Dann liest Python keinen Bytecode aus `__pycache__`. Untergeschobene, veraltete und verwaiste `.pyc` sind damit
     wirkungslos, jede Quelle wird frisch übersetzt.
   - Fehlt etwas davon, schließt das Gate (BLOCKIERT).
   - Ohne Isolation wird bewusst **nicht** versucht, Bytecode zu „prüfen“.
2. **Quellenloser Bytecode und Erweiterungsmodule** (`*.pyc`/`*.pyo` außerhalb `__pycache__`, `*.pyd`/`*.so`) im
   Repository ergeben ein Problem; sie würden auch mit Isolation geladen.
3. **Überschattung:** Module und Pakete in der Repository-Wurzel außerhalb des Fingerabdrucks ergeben ein Problem,
   auch in ausgeschlossenen Ordnern wie `build/`.
4. **Suchpfad:** Jeder Eintrag muss Repository-Wurzel, venv oder Basis-Installation sein (Editable-Finder ausgenommen).
   Sonst Problem.
5. **Geladene Module:** Jedes Modul mit Datei muss aus dem Fingerabdruck oder aus venv/Basis-Installation stammen und
   darf nicht aus quellenlosem Bytecode geladen sein.
6. **Fingerabdruck repo-weit:** alle `*.py` in importierbaren Ordnern, heute 762 Dateien statt 401 (z. B. auch
   `gateway/`, alle `tests/`). Ausgenommen sind nur Build-Ausgaben, Caches und Daten. Eine neue Datei in irgendeinem
   Unterordner ändert den Fingerabdruck; ist sie nicht committet, verweigert das Signierwerkzeug.
7. **Symbolische Ordner-Links** im Repository ergeben ein Problem.
8. **Ergebnis wirkt an zwei Stellen:**
   - im **Gate** bei jeder Prüfung („Codequelle: …“ → geschlossen),
   - als **V2-Kriterium** `codequellen_eindeutig` (Probleme → BLOCKIERT, fehlt → FAIL).

**Tests:**
- `test_planted_bytecode_is_loaded_without_isolation_and_ignored_with_it`: Kindprozess, Regression R2a. Ohne Isolation
  wird „FREMD“ geladen, mit Isolation die Quelle.
- `test_stale_bytecode_is_ignored_with_isolation`: Quelle bei gleicher Größe und gleichem Zeitstempel geändert. Ohne
  Isolation wird der veraltete Code geladen, mit Isolation der neue.
- `test_isolated_child_process_passes_the_isolation_check`
- `test_bytecode_isolation_is_required` (6 Fälle), `test_bytecode_isolation_ok_only_with_protected_missing_prefix`
- `test_sourceless_bytecode_and_extensions_are_problems` (4 Fälle, Regression R2b)
- `test_shadowing_in_the_repository_root_is_a_problem` (3 Fälle, Regression R3),
  `test_fingerprinted_root_package_is_not_shadowing`
- `test_unknown_search_path_entries_are_problems`, `test_loaded_modules_must_come_from_the_fingerprint` (auch: Datei
  nach dem Fingerabdruck hinzugekommen)
- `test_changes_after_the_fingerprint_are_detected`, `test_manipulated_pth_closes_the_gate`
- `test_code_source_problems_close_the_gate`, `test_code_check_failure_closes_the_gate`
- `test_v2_requires_unambiguous_code_sources` (3 Fälle), `test_vm_part_records_code_sources`,
  `test_pytest_children_of_the_vm_part_get_the_same_isolation`
- `test_symlinked_directory_in_the_repository_is_a_problem` (übersprungen: ohne Entwicklermodus keine Symlinks)
- §90: Das Produktiv-Gate meldet im nicht isolierten pytest-Prozess „Codequelle: Bytecode-Isolation fehlt“.

**Echte Prüfung dieses Repositorys** (Kindprozesse, nur lesend):
- Ohne Isolation: 2 Probleme (beide Isolationsvariablen fehlen).
- Mit Isolation und einem Präfix im Job-Ordner: genau 1 Problem – der Elternordner ist beschreibbar (richtig: das ist
  der Job-Ordner).
- Keine Fehlalarme für Repository- und venv-Module. Laufzeit etwa 0,1 s.

**Restrisiko:**
- Der **Inhalt** von venv/site-packages und der Standardbibliothek wird nicht gehasht, nur Distributionen, `.pth` und
  Einträge (`laufzeit_stand`). Dort schützt nur der Schreibschutz (A4).
- Module, die erst **nach** einer Gate-Prüfung importiert werden, prüft erst die nächste Prüfung; Code beim Import ist
  bis dahin schon ausgeführt.
- Quellenlose `.pyc` in site-packages werden nicht gesucht, nur beim Laden erkannt.
- Dass pytest mit gesetztem Präfix auch für das Assertion-Rewriting keinen Bytecode aus `__pycache__` liest, ist aus
  dem pytest-Quelltext abgeleitet, **nicht reproduziert**. Mit `-B` schreibt pytest ihn jedenfalls nicht.
- Code außerhalb von Python (DLLs, Treiber) ist nicht erfasst.
- **Bedienvoraussetzung für D1:** In der VM müssen `PYTHONDONTWRITEBYTECODE=1` und `PYTHONPYCACHEPREFIX=<nicht
  existierender Pfad unter einem geschützten Ordner>` gesetzt sein, sonst bleibt das Gate geschlossen.

### A2 – Wiederherstellung nach Absturz (MITTEL)

**Befund (AUDIT_S100 R4):** Nach einem Stromausfall blieb `startcheck` auch nach dem dokumentierten Rückbau dauerhaft
„nicht ok“. Ausweg wäre nur, das Journal von Hand zu bearbeiten.

**Änderung:**
1. **Abschluss offener Vorgänge:** `acl-entfernen` und `fw-entfernen` (mit oder ohne `--lauf`) lesen nach dem Entfernen
   den Ist-Zustand nach. Ist **keine** im Journal geplante Änderung dieses Laufs mehr vorhanden, hängen sie für jeden
   offenen Vorgang des Laufs einen Eintrag „zurueckgebaut“ an. Dieser verweist auf den abschließenden Vorgang
   (`abschluss_durch`) und enthält den Nachweis.
   - Der Rückbau ist idempotent: Ein zweiter Aufruf entfernt nichts und schließt nichts.
2. **Image-Rücksetzung:** Eine erfolgreiche neue Rücksetzung mit vorhandenem Kinddatenträger schließt frühere,
   abgebrochene Rücksetzungen ab.
3. **Halb geschriebene Journalzeile:**
   - `_log` beginnt eine neue Zeile, falls die Datei nicht mit einem Zeilenende endet. Der nächste Eintrag wird also
     nie verschluckt.
   - Die unlesbare Zeile bleibt erhalten und gilt als offen.
   - `journal-quittieren` hängt eine Quittung an, **nur** wenn Port-ACL und eigene Firewall-Gruppe nachgelesen leer
     sind; sonst Abbruch.
4. **Nie verändert:** Das Journal wird nur ergänzt. Fremde Regeln mit gleichem Namensschema, aber ohne Journalbeleg
   bleiben unberührt; `startcheck` meldet sie weiter.

**Sicherer Weg (ersetzt AUDIT_S98 §2, Schritt 4):**
1. `startcheck` ausführen.
2. `acl-entfernen --lauf <id>` bzw. `fw-entfernen --lauf <id>` ausführen, zuerst ohne `--ausfuehren`, dann mit.
3. Bei gemeldeter unlesbarer Zeile `journal-quittieren --ausfuehren` ausführen.
4. `startcheck` muss jetzt `ok: true` ergeben. Bleiben fremde Regeln, entscheidet der Mensch.

**Tests:**
- `test_power_loss_at_every_step_is_recoverable_and_idempotent`: **16 Fälle** – jeder der 5 Firewall- und 3
  ACL-Schritte, jeweils vor und nach dem Befehl. Geprüft wird jeweils: Absturz ohne Rückbau, `startcheck` nicht ok,
  Rückbau, `startcheck` ok, zweiter Rückbau ohne Wirkung, `startcheck` weiter ok.
- `test_recovery_never_touches_foreign_rules`
- `test_several_crashed_runs_are_recovered_separately` (Aufträge mehrerer Läufe bleiben getrennt)
- `test_torn_journal_line_is_kept_and_acknowledged_only_when_clean`
- `test_interrupted_image_reset_is_closed_by_a_later_successful_reset`, `test_cli_offers_journal_acknowledgement`
- §98: Statusdateien mehrerer Läufe bleiben erhalten (unverändert grün).

**Restrisiko:**
- Geht das Journal selbst verloren, verweigert `--lauf` weiterhin (Herkunft nicht belegbar). Dann ist eine menschliche
  Entscheidung nötig.
- `startcheck` wird von `acl-setzen`/`fw-setzen`/`host-lauf` nicht erzwungen (A5, offen).
- Firewall-Regeln werden über den Anzeigenamen entfernt (A7, offen).
- Alles nur gegen das **simulierte** Hyper-V geprüft.

### A3 – Zeit und Vertrauenskette (MITTEL)

**Befund:** Die Gültigkeit beim Gebrauch hing allein an der VM-Uhr.

**Änderung (Zeitanker):**
1. **Aufzeichnung in der VM:** Nach allen Nachher-Proben und vor dem Abschluss-Token, also nachweislich innerhalb der
   Nachher-Phase des Hosts, schreibt die VM ihren **monotonen Systemzähler** (`GetTickCount64`) und ihre **Boot-ID** in
   jeden pytest-Beleg.
   - Der Zähler zählt seit dem Start, auch im Ruhezustand, und ist von keinem Konto stellbar.
   - Er steht im signierten Bündel.
2. **Auswertung:** Die Bewertung leitet daraus den Zeitanker ab: Zählerstand, Boot-ID und Ende der Nachher-Phase in
   **Host-Zeit** (`aktiv_bis` des Kanarienlogs).
   - Fehlt der Anker, ist er ungültig oder widersprechen sich die Belege, ist **V13 BLOCKIERT**.
3. **Gate beim Gebrauch:** spätester möglicher Zeitpunkt = Host-Phasenende + (Zähler jetzt − Zähler am Anker) × 1,001
   + 5 min. Liegt das nach `gueltig_bis` → geschlossen.
   - Andere Boot-ID (Neustart), rückläufiger oder unlesbarer Zähler oder fehlender Anker → geschlossen.
   - Die Prüfung gegen die VM-Uhr bleibt **zusätzlich** bestehen. Nichts wurde gelockert.

**Tests:**
- `test_vm_clock_alone_does_not_extend_validity`: VM-Uhr sagt „gültig“, der Zähler sagt 3 h → geschlossen.
- `test_untrustworthy_time_basis_closes_the_gate` (Neustart, Zähler rückwärts, unlesbar)
- `test_valid_attestation_passes_with_anchor` (inklusive Sicherheitsabschlag)
- `test_missing_anchor_blocks_v13_and_the_gate`, `test_contradictory_anchors_block_v13`
- `test_this_machine_provides_a_time_basis` (echt, nur lesend)
- `test_vm_part_records_the_anchor_after_all_probes` (simulierter Gesamtlauf)
- §90: Die echte Zeitbasis dieses Rechners (andere Boot-ID als die synthetische) schließt das Gate.

**Restrisiko:**
- Ob `GetTickCount64` in einer Hyper-V-VM weiterläuft, während der **Host** die VM pausiert oder speichert, ist
  **NICHT VERIFIZIERT**. Eine Pause kann die gemessene Dauer verkürzen; das kann nur der Host-Administrator (außerhalb
  des Modells).
- Lesbarkeit der Boot-ID in der VM: **NICHT VERIFIZIERT**. Unlesbar führt zum geschlossenen Gate.
- Ein Administrator in der VM kann den Anker im Beleg fälschen (Grenze des Modells, wie alle E0-Belege).

### A4 – Schreibrechte und Codepfad (MITTEL)

**Befund:** Der Schreibtest prüfte `__pycache__`, `Scripts`, `pyvenv.cfg`, Paketordner und die Standardbibliothek
nicht.

**Änderung:**
1. **Umfang** (`isolation_codepfad.write_access`, in der VM über `runtime_roots`): **tatsächlicher**
   Installationspfad, also
   - Repository inklusive aller `__pycache__`-Ordner,
   - die venv **rekursiv** (`Scripts`, `pyvenv.cfg`, site-packages mit allen Paketordnern und `.pth`),
   - die Basis-Installation (`sys.base_prefix`, Standardbibliothek, `python3x.zip`),
   - alle weiteren Ordner im Suchpfad.
2. **Probe-Verfahren:**
   - Ordner: Probedatei anlegen und sofort wieder entfernen.
   - Dateien: zum Anhängen öffnen, ohne zu schreiben.
3. **Nicht eindeutig prüfbar** ergibt `nicht_pruefbar` und damit **BLOCKIERT**. Dazu gehören:
   - Freigabeverletzung bei laufenden Dateien (`winerror 32`; nur `winerror 5` gilt als geschützt),
   - nicht vorhandene Pfade,
   - **nicht lesbare Ordner** (früher hätte `os.walk` sie stillschweigend übergangen).
4. **Bewertung:** Beschreibbar → FAIL. Fehlt eine der Pfadgruppen `repository`, `venv` oder `basis` (z. B.
   Standardbibliothek nicht geprüft), ist das Ergebnis „unklar“ → FAIL, nie „geschützt“.
5. **Tests:** Mit einem anderen Repository werden **nur** die übergebenen Pfade geprüft, nie die echte Installation.

**Tests:**
- `test_write_access_covers_all_code_and_runtime_paths`
- `test_protected_installation_passes_only_with_all_path_groups`
- `test_unclear_paths_block_and_never_count_as_protected`, `test_unreadable_directories_are_reported_not_skipped`
- `test_probe_distinguishes_access_denied_from_sharing_violation` (2 Fälle)
- `test_runtime_roots_follow_the_actual_installation`
- `test_v2_write_protection_criterion` (3 Fälle), `test_real_repository_write_test_only_touches_given_paths_in_tests`

**Restrisiko:**
- Der Test probiert und liest keine ACLs aus. Ein Konto mit dem Recht „Berechtigungen ändern“ ohne Schreibrecht wird
  nicht erkannt (§97 M1, weiterhin offen).
- **Auf der echten Installation nicht ausgeführt**, um keine Probedateien in `.venv` und der Basis-Installation dieses
  Rechners anzulegen. Der Lauf ist für die VM vorgesehen (**NICHT DURCHGEFÜHRT**).
- Die Basis-Installation liegt hier im Benutzerprofil (`AppData\Local\Programs\Python\Python313`). In einer so
  eingerichteten VM wäre sie beschreibbar und V2 FAIL. Für D1 heißt das: Python systemweit installieren.

## 3 Tests und Ergebnisse

Alle Läufe einzeln im Vordergrund, über den Speicherwächter aus §99 (Start ab 4 GB frei, Stopp unter 1,5 GB). Vor
Beginn waren 7,1 GB frei, kein `llama-server` lief, und der Stopp wurde nie ausgelöst.

| Lauf | Ergebnis | Status |
|---|---|---|
| `tests/test_s102_review_fixes.py` (nach der letzten Änderung) | 73 bestanden, 1 übersprungen (Symlink) | **PASS** |
| Isolationssuites Teil 1: §86, §87, §90, §92, §102 | 260 bestanden, 8 übersprungen (Zielumgebung bzw. Symlink), 4:37 min | **PASS** |
| Isolationssuites Teil 2: §91, §94, §96, §98 | 198 bestanden, 1 übersprungen (Symlink), 7:55 min | **PASS** |
| Nach der letzten Änderung (nicht lesbare Ordner) erneut: Schreibtest-/V2-Tests in §94, §96, §98 | 10 bestanden | **PASS** |
| Benchmark-Tests §77, §78, §84, §85 | 175 bestanden | **PASS** |
| Benchmark-Tests §79, §80 | 55 bestanden | **PASS** |
| Benchmark-Tests §81–§83 | 85 bestanden | **PASS** |
| Syntaxprüfung im Speicher (geänderte und neue Dateien) | ok, keine ungenutzten Importe | **PASS** |
| Gesamtsuite (6 Teile) | nicht verlangt, nicht ausgeführt | **NICHT DURCHGEFÜHRT** |
| Lint (ruff) | nicht installiert | **NICHT DURCHGEFÜHRT** |
| Schreibtest auf der echten Installation | bewusst nicht (würde Probedateien anlegen) | **NICHT DURCHGEFÜHRT** |
| Reale Abnahme V1–V13 | – | **NICHT DURCHGEFÜHRT** |

**Commit-Grenze:**
- Der Fingerabdruck umfasst 762 Dateien, davon 760 getrackt. Untracked sind nur die zwei neuen Dateien dieses
  Schritts; gitignorierte Dateien gibt es darin nicht.
- Ohne Commit kann deshalb nicht signiert werden. Das ist gewollt, verlangt für eine Signatur aber einen weiteren
  Commit.

## 4 Offene Befunde und Restrisiken (Übersicht)

| # | Stand nach §102 |
|---|---|
| A1 | behoben (lokal); Restrisiken in §2: Paketinhalte, Importe nach der Prüfung, pytest-Verhalten abgeleitet, Isolationsvariablen als D1-Voraussetzung |
| A2 | behoben (simuliert); Journalverlust bleibt eine menschliche Entscheidung |
| A3 | behoben (lokal); Pausen-Verhalten des Zählers und Boot-ID in der VM **NICHT VERIFIZIERT** |
| A4 | behoben (lokal, Kopien); ACL-Auslesen fehlt weiterhin; Lauf auf der echten Installation **NICHT DURCHGEFÜHRT** |
| A5–A10 | offen (unverändert, AUDIT_S100) |
| Grenzen des Modells | unverändert: Administrator in der VM, kompromittierter Host, Absenderfälschung |
| Reale Isolation | **NICHT DURCHGEFÜHRT**; keine Sicherheitsfreigabe; D1 und C-14 **GESPERRT** |

**Empfehlung:** Ein unabhängiger erneuter Review von A1–A4, wie in AUDIT_S100 §6 verlangt, vor jeder realen Signatur
bzw. vor D1.
