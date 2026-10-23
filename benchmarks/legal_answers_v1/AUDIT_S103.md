# §103 Unabhängiger Sicherheitsreview der §102-Fixes (A1–A4) und der offenen Befunde A5–A10

Stand 20.10.2026. **Keine Sicherheitsfreigabe.** Ein statischer Review und simulierte bzw. lokale Tests beweisen
**keine reale Isolation**. D1, Signierung und C-14 bleiben **GESPERRT**.

## 0 Ausgangszustand (geprüft)

- **Branch:** `feature/feedback-diagnose`.
- **HEAD:** `8e94bc1e49792bf83d32462f04a4af12f88e9840` (Commit „NOT SECURITY APPROVED“).
- **Staging:** leer.
- **§102-Änderungen (uncommittet):**
  - geändert: `scripts/legal_bench/isolation_{acceptance,evidence,gate,host_control,real}.py`,
    `tests/isolation_test_support.py`, `tests/test_s90_owner_gate.py`, `tests/test_s94_real_acceptance_path.py`;
  - neu: `scripts/legal_bench/isolation_codepfad.py`, `tests/test_s102_review_fixes.py`, `AUDIT_S102.md`.
- **Fremd, nicht angefasst:**
  - `M PRIVACY_REVIEW_ARCHITECTURE.md`,
  - `D assets/branding/Lexono-Logo Dark Mode.png`, `D assets/branding/lexono-logo.png`,
  - `?? assets/ux-ui/Lexono KI-Login für Kanzleien.png`, `?? assets/ux-ui/Lexono Kanzlei-Dashboard im Darkmode.png`.
- **Arbeitsweise:**
  - Vor Beginn habe ich SHA-256 aller geänderten und neuen Dateien festgehalten (Job-Ordner `review103/baseline.txt`)
    und am Ende verglichen (§7).
  - Alle Nachweise R1–R9 liefen als Wegwerf-Skripte im Job-Ordner, gegen Kopien bzw. das **simulierte** Hyper-V aus §94.
  - R9 hat zwei geschützte Systemdateien nur zum Anhängen **geöffnet, nicht beschrieben**. Die Sitzung war nachweislich
    nicht erhöht; auch ein Administrator darf `kernel32.dll` nicht schreiben.
  - Keine System-, Konto-, Netz- oder Firewalländerung. Keine Schlüssel. Die Produktivdatenbank wurde nicht geöffnet.

**Beleg-Klassen in diesem Bericht:**
- **[Code]** Codebefund (gelesen);
- **[Test]** vorhandener Test;
- **[Nachweis]** eigener lokaler Wegwerf-Nachweis (Rn);
- **[Sim]** gegen das simulierte Hyper-V;
- **[Annahme]** unbewiesen.

## 1 Übersicht

| # | Thema | Urteil §103 | Schwere des Rests |
|---|---|---|---|
| A1 | Codequellen/Bytecode | **TEILWEISE** – Isolation wirkt (R1, auch für pytest), aber Lücken R2–R4; Isolationsprüfung real nie bestehbar (A12) | MITTEL |
| A2 | Wiederherstellung | **TEILWEISE** – Sackgasse behoben [Sim], aber laufübergreifendes Entfernen aktiver ACLs (A11) | HOCH (über A11) |
| A3 | Zeitgültigkeit | **TEILWEISE** – Ablauf der Bestätigung verankert, Belegalter beim Gebrauch weiter nur VM-Uhr (A13) | MITTEL |
| A4 | Schreibrechte | **NICHT WIRKSAM (real)** – Probe kann unter Windows nie „geschützt“ melden (A12); Lücken R8 | HOCH (Funktion), fail-closed |
| A5 | Startcheck nicht erzwungen | **OFFEN** | NIEDRIG |
| A6 | Lückenlosigkeit der Zwischenprüfungen | **OFFEN** | NIEDRIG |
| A7 | Entfernen über Anzeigenamen | **OFFEN** | NIEDRIG |
| A8 | Laufzeit-Hash beim Signieren, identische venv | **OFFEN** | NIEDRIG |
| A9 | Testqualität | **OFFEN, verschärft** (neue Quelltext- und Fiktions-Tests in §102) | MITTEL (Beweislage) |
| A10 | gelöschte Dateien beim Signieren | **OFFEN** (fail-closed) | NIEDRIG |
| A11–A17 | neu | siehe §3 | 2× HOCH, 2× MITTEL, 3× NIEDRIG |

## 2 Befunde A1–A10 im Einzelnen

### A1 – Codequellen und Bytecode

**Ursprünglicher Befund:** AUDIT_S100 §1 A1 (HOCH). Die Nachweise R2a/R2b/R3 dort zeigten untergeschobene `.pyc`,
quellenlose `.pyc` und Überschattung durch ein Wurzel-Paket.

**Geprüfter Codepfad:**
- `isolation_codepfad.bytecode_isolation`, `code_source_problems`, `python_files`;
- `isolation_gate.tool_files`, `codepfad_probleme`, `IsolationGate._problems` (Zeile „Codequelle:“);
- `isolation_evidence._v2_e0` (`codequellen_eindeutig`);
- `isolation_real.observe_vm_v2`, `run_pytest`.

**Belege für die Wirksamkeit:**
- [Nachweis R1]:
  - pytest **ohne** Isolation führt veralteten Assertion-Rewrite-Bytecode aus. Eine Quelle, die gleich groß ist, den
    gleichen Zeitstempel hat und scheitern müsste, „besteht“.
  - **Mit** `PYTHONPYCACHEPREFIX` (nicht existent) und `PYTHONDONTWRITEBYTECODE=1` wird neu übersetzt, der Test
    scheitert korrekt, der Präfix wird nicht angelegt.
  - Damit ist die in AUDIT_S102 nur abgeleitete pytest-Annahme **lokal bestätigt**.
- [Test]: `test_planted_bytecode…`, `test_stale_bytecode…` (Kindprozesse) zeigen dasselbe für den normalen Import.
- [Code]:
  - Der Fingerabdruck umfasst alle 762 `*.py` außerhalb ausgeschlossener Ordner.
  - Suchpfad und geladene Module werden bei jeder Gate-Prüfung kontrolliert.
  - Die pytest-Kindprozesse des VM-Teils erhalten `-B` und denselben Präfix.

**Gegenargumente und Lücken:**
1. **[Nachweis R2] Ausgeschlossene Ordnernamen in jeder Tiefe:**
   - `app/build/__init__.py` ist als `app.build` importierbar, aber
     - nicht im Fingerabdruck,
     - nicht vom Bytecode-Scan erfasst (eine `alt.pyc` dort bleibt unbemerkt) und
     - laut `git check-ignore` **gitignoriert**, also auch für V2 `arbeitsbaum_sauber` unsichtbar.
   - Erkannt wird es nur, **wenn** es bereits geladen ist (A14).
2. **[Nachweis R3]** `nur.PYC` (Großbuchstaben) wird unter Windows als quellenloser Bytecode **geladen**; der Scan
   prüft die Endung case-sensitiv und meldet nichts. Die Prüfung geladener Module fängt es erst nach dem Import (A15).
3. **[Nachweis R4]:**
   - Ein Paket in der Standardbibliothek oder venv, das ein site-packages-Paket überdeckt (z. B. `Lib/httpx/`), gilt
     als „erlaubter Ort“ und wird **nicht** gemeldet.
   - Der Inhalt von venv und Basis-Installation ist nicht gehasht. Der einzige Schutz ist A4 – und A4 wirkt real nicht
     (A12).
   - Ebenso könnte ein in site-packages verändertes, früh importiertes Modul einen eigenen Import-Finder installieren
     und `__file__` fälschen; die Modulprüfung ist dann wirkungslos [Annahme, aus dem Import-System abgeleitet].
4. **[Code]** Die Prüfung findet nur zu Gate-Zeitpunkten statt. Code, der dazwischen importiert wird, läuft vor seiner
   Prüfung.
5. **[Code] Funktional:**
   - Die Isolationsprüfung verlangt einen „geschützten“ Elternordner des Präfixes; `_probe_dir` kann das unter Windows
     nie bestätigen (A12). Das Gate wäre in einer echten VM **immer** geschlossen.
   - `e2e._embedding_provider` hängt im Rückfallpfad `tests/` an `sys.path`; die Suchpfadprüfung schließt dann das Gate
     (A17).

**Urteil:** **TEILWEISE**. Die Bytecode-Isolation selbst ist belegt (lokal). Die Erkennung „unbekannter Codequellen“
ist nicht vollständig. Real ist der Pfad derzeit nicht bestehbar (fail-closed).

**Restrisiko:** R2–R4, Importe zwischen Prüfungen, nicht gehashter Paketinhalt.

**Nächste Schritte:**
- Scan case-insensitiv machen und ausgeschlossene Namen nur in der Wurzel ausnehmen (bzw. Unterordner mit diesen Namen
  als Problem werten).
- `git status --ignored` als V2-Kriterium.
- Hash-Liste der installierten Pakete (`RECORD`) prüfen.
- A12 beheben.

### A2 – Wiederherstellung nach Absturz

**Ursprünglicher Befund:** AUDIT_S100 A2 (MITTEL). Nachweis R4 dort: `startcheck` blieb nach dem dokumentierten Rückbau
dauerhaft „nicht ok“.

**Geprüfter Codepfad:** `isolation_host_control`: `_log` (Zeilenende, `a+b`, fsync), `_all_entries`, `_close_open`,
`acl_entfernen`, `fw_entfernen`, `journal_quittieren`, `journal_offen` (Quittungen), `image_zuruecksetzen`, `startcheck`.

**Belege:**
- [Test, Sim] 16 Abbruchfälle: jeder Setzschritt, vor und nach dem Befehl. Absturz, `startcheck` nicht ok, Rückbau,
  `startcheck` ok, zweiter Rückbau ohne Wirkung.
- [Test, Sim] Fremde Regeln bleiben, mehrere Läufe werden getrennt behandelt, eine halbe Zeile wird nie umgeschrieben,
  die Quittung kommt nur bei leerem Ist-Zustand.
- [Code] Abschluss erst **nach** dem Nachlesen. Quittieren verlangt eine leere Port-ACL **und** eine leere eigene
  Firewall-Gruppe.

**Gegenargumente:**
1. **[Nachweis R5, Sim]** `acl-entfernen --lauf lauf-a` entfernt bei **aktiver ACL eines neuen Laufs** (`lauf-b`) alle
   3 Einträge von `lauf-b`. ACL-Einträge tragen keine Laufkennung; jede Abnahme hat dasselbe Soll. Die Bestätigung von
   `lauf-b` bleibt in der VM gültig, denn das Gate sieht den Host-Zustand nicht → **A11 (HOCH)**.
2. **[Code]** Journalverlust: `--lauf` verweigert (Herkunft nicht belegbar). Ohne `--lauf` entfernt `acl-entfernen`
   jeden Eintrag, der dem aktuellen Soll gleicht – unabhängig von seiner Herkunft. Das ist dieselbe Ursache wie A11.
3. **[Code]** Beschädigtes Journal:
   - Eine unlesbare Zeile kann den `geplant`-Eintrag eines Vorgangs enthalten haben. Dann verweigert `--lauf`, und der
     Rest muss von Hand entfernt werden, bevor quittiert werden kann.
   - Das ist sicher (keine falsche „bereinigt“-Meldung), aber nicht automatisch lösbar.
4. **[Code]** Teilfehler beim Rückbau: Ist danach noch etwas vorhanden, wird „unvollstaendig“ protokolliert, nichts
   geschlossen, und `startcheck` bleibt nicht ok. Das ist korrekt.
5. **[Code]** Widersprüchlicher Ist-Zustand (gleicher Name, anderer Inhalt in der eigenen Gruppe): Die Regel wird als
   eigene entfernt. Das ist vertretbar.
6. A5 (Startcheck nicht erzwungen) und A7 (Entfernen über Anzeigenamen) bleiben offen.

**Urteil:** **TEILWEISE**. Die Sackgasse ist behoben (nur simuliert). Ein neuer, schwerer Bedienfehlerpfad ist
bestätigt (A11).

**Nächste Schritte:**
- ACL-Einträge einem Lauf zuordnen: Journal-Abgleich gegen den zuletzt **erfolgreich** gesetzten Lauf, keine Entfernung
  bei aktivem anderem Lauf.
- Bei aktiver Bestätigung jede Entfernung sperren.

### A3 – Zeit und Gültigkeit

**Ursprünglicher Befund:** AUDIT_S100 A3 (MITTEL) – Gültigkeit beim Gebrauch allein nach VM-Uhr.

**Geprüfter Codepfad:** `isolation_gate.vm_zeitbasis` (`GetTickCount64`, BootId), `zeitanker_probleme`, Aufruf in
`_problems`; `isolation_evidence.zeitanker` (V13); `isolation_real.vm_teil` (Anker nach den Proben).

**Rechenweg [Code]:**
- spätestens = `aktiv_bis` (Host, signiert) + (Zähler jetzt − Zähler beim Anker) × 1,001 + 5 min; das muss ≤
  `gueltig_bis` sein.
- Der Anker wird **vor** dem Abschluss-Token genommen, `aktiv_bis` liegt danach. Die Schätzung ist damit konservativ.
- Die VM-Uhr-Prüfung gilt zusätzlich. Es genügt, dass **eine** der beiden Prüfungen „abgelaufen“ sagt.

**Belege:**
- [Test] Zurückgestellte VM-Uhr bei weiterlaufendem Zähler → geschlossen. Neustart, rückläufiger Zähler, unlesbarer
  Zähler und fehlender oder widersprüchlicher Anker → geschlossen bzw. V13 BLOCKIERT.
- [Nachweis] Zähler und Boot-ID sind auf diesem Rechner ohne Adminrechte lesbar.

**Gegenargumente:**
1. **[Nachweis R6]** Das **Belegalter beim Gebrauch** (24 h, `MAX_EVIDENCE_AGE_AT_USE`) wird weiter nur gegen `now`
   geprüft, also die VM-Uhr. Mit zurückgestellter Uhr gilt ein Bündel länger als 24 h → **A13**.
   - Begrenzt wird das nur durch den Anker auf `gueltig_bis`. Eine Bestätigung kann bis zu 24 h nach einem bis zu 24 h
     alten Beleg ausgestellt sein, Belege sind beim Gebrauch also bis zu etwa 48 h alt.
2. **Gespeicherter VM-Zustand bzw. Snapshot [Annahme, plausibel]:**
   - Ob der Gastzähler weiterläuft, während der Host die VM pausiert oder speichert, ist **NICHT VERIFIZIERT**.
   - Läuft er nicht, und ist zugleich die Zeitsynchronisierung aus (sie ist erlaubt, aber **nicht verlangt**), dann
     unterschätzen beide Prüfungen die vergangene Zeit.
   - Die Standard-Stoppaktion von Hyper-V beim Herunterfahren des Hosts ist laut Microsoft-Standard „Speichern“
     [Annahme, nicht am Host geprüft].
   - Das Wiederherstellen eines nach dem Anker erstellten Checkpoints wirkt gleich; das kann aber nur der Host.
3. **[Code]** Manipulierte Werte im Beleg (Anker zu hoch → kürzere Dauer) kann nur ein Administrator in der VM
   vornehmen (Grenze des Modells). Ein zu hoher Anker oberhalb des Zählers → geschlossen.
4. **[Code]** Wiederverwendete Belege: Eine neue Bestätigung für ein altes Bündel scheitert an der Altersprüfung beim
   Signieren (Host-Uhr). Eine Wiederverwendung innerhalb von 24 h für dieselbe `run_id` ist gewollt (N8 aus §95).

**Urteil:** **TEILWEISE**. Der Ablauf der Bestätigung ist nicht mehr allein von der VM-Uhr abhängig. Belegalter und
Pause/Snapshot-Fall sind es weiter.

**Nächste Schritte:**
- Belegalter ebenfalls über den Anker prüfen.
- Zeitsynchronisierung als V9-Pflicht.
- `AutomaticStopAction` ≠ Save prüfen (V9 E1).
- Das Pausenverhalten des Zählers real messen (D1).

### A4 – Schreibrechte

**Ursprünglicher Befund:** AUDIT_S100 A4 (MITTEL).

**Geprüfter Codepfad:** `isolation_codepfad._probe_dir`, `_probe_file`, `write_access`, `runtime_roots`;
`isolation_real.observe_write_access`; `isolation_evidence._write_protected`.

**Belege:**
- [Test] Bei beschreibbaren Kopien werden alle Pfadgruppen erfasst: `__pycache__`, `Scripts`, `pyvenv.cfg`,
  site-packages rekursiv, `.pth`, Standardbibliothek und Zip-Datei.
- [Test] Nicht lesbare Ordner und fehlende Pfade ergeben `nicht_pruefbar`.

**Gegenargumente:**
1. **[Nachweis R9]** `os.open` liefert unter Windows bei einer per ACL geschützten Datei (`kernel32.dll`, `hosts`, nicht
   erhöht) `PermissionError` mit **`winerror None`** (`errno 13`). Der Code wertet nur `winerror == 5` als
   „geschuetzt“. Ergebnis: **`nicht_pruefbar`**.
   - Für Ordner (`os.open O_CREAT`) gilt dasselbe [aus derselben CRT-Fehlerabbildung abgeleitet, nicht an einem
     Systemordner ausprobiert].
   - **Folge:** In einer korrekt geschützten VM ergäben **alle** geschützten Pfade „nicht prüfbar“. V2 und die
     Bytecode-Isolationsprüfung (A1) wären **immer BLOCKIERT**. Das ist fail-closed (kein falsches PASS), macht die
     reale Abnahme aber unmöglich.
   - Der §102-Test `test_probe_distinguishes_access_denied_from_sharing_violation` setzt `winerror=5` künstlich und
     belegt damit eine Situation, die real nicht eintritt (A9/A12).
2. **[Nachweis R7]** Eine laufende, beschreibbare Datei (Kopie des Interpreters): `nicht_pruefbar` (errno 13, `winerror
   None`). Freigabeverletzung und ACL-Verweigerung sind mit `os.open` **nicht unterscheidbar**.
3. **[Nachweis R8, Code]** Geprüft wird nur das **Anlegen von Dateien**, nicht das von Unterordnern
   (`FILE_ADD_SUBDIRECTORY`). Ein Recht „nur Ordner anlegen“ plus Ersteller-Vollzugriff erlaubt ein neues Paket, etwa in
   der Standardbibliothek, das site-packages überdeckt (zusammen mit R4).
4. **[Code]** Nicht abgedeckt:
   - ACL-Rechte `WRITE_DAC`, `WRITE_OWNER`, `DELETE`,
   - Dateien in `*.dist-info` (`entry_points.txt`, `RECORD`) – `.txt` zählt in der venv nicht als Codedatei; über
     `entry_points.txt` registrierte pytest-Plugins laufen bei jedem pytest-Lauf,
   - DLL-Suchpfade außerhalb der Installation.
5. **[Code]** Junctions und Symlinks werden über `realpath` und `done` behandelt (keine Schleife). Ziele außerhalb werden
   mitgeprüft. Vertretbar.

**Urteil:** **NICHT WIRKSAM (real)**. Die Logik ist fail-closed, beweist aber unter Windows nie Schutz. Lokal belegt sind
nur die simulierten Probe-Ergebnisse.

**Nächste Schritte:**
- Schutz über die Windows-API prüfen: `CreateFileW` über ctypes mit `GetLastError` (5 gegen 32), besser
  `AccessCheck`/`GetEffectiveRightsFromAcl` bzw. `icacls`-Auswertung für Datei-, Ordner- und ACL-Rechte.
- Ordner-Anlage zusätzlich prüfen.
- `dist-info` einbeziehen.
- Den echten Test als Test **gegen eine echte geschützte Datei** ausführen (wie R9), nicht mit künstlichem `winerror`.

### A5 – Startcheck nicht erzwungen (AUDIT_S100 A5)

- **[Code]** `journal_offen` wird nur in `startcheck`, `_close_open` und `image_zuruecksetzen` verwendet. `acl_setzen`,
  `fw_setzen` und `host_lauf` prüfen das Journal nicht (`host_lauf` arbeitet ohne Journal).
- Reste verhindern den Start weiterhin, weil ACL bzw. Gruppe leer sein müssen.
- AUDIT_S102 korrigiert die frühere Aussage aus AUDIT_S98 nicht ausdrücklich.
- **Urteil OFFEN.** Nächster Schritt: Prüfung offener Vorgänge in allen Setzbefehlen und in `host_lauf` erzwingen.

### A6 – Lückenlosigkeit der Zwischenprüfungen (AUDIT_S100 A6)

- **[Code]** `_kontroll_problem` verlangt `nachher_start`/`nachher_ende` und wertet `nachher_waehrend` nur auf
  Fehler aus, nicht auf Anzahl oder Abstand.
- `PeriodicCheck.__exit__` wartet höchstens 30 s; danach eintreffende Ergebnisse fehlen.
- **Urteil OFFEN.**

### A7 – Entfernen über Anzeigenamen (AUDIT_S100 A7)

- **[Code]** `_fw_remove_command` lautet weiter `Remove-NetFirewallRule -DisplayName …` (Zeile 609). Das betrifft alle
  gleichnamigen Regeln, auch außerhalb der Gruppe [Annahme zur Windows-Semantik, nicht verifiziert].
- **Urteil OFFEN.**

### A8 – Laufzeit-Hash beim Signieren, identische venv (AUDIT_S100 A8)

- **[Code]** `owner_sign.sign_attestation` (unverändert seit F1) verlangt nur `commit` und `sha256`, nicht
  `laufzeit_sha256`.
- Durch §102 kommt hinzu: Host und VM brauchen gleiche Einträge in site-packages **und** einen identischen
  repo-weiten Fingerabdruck (762 Dateien). Real wahrscheinlich BLOCKIERT, nicht dokumentiert.
- **Urteil OFFEN.**

### A9 – Testqualität (AUDIT_S100 A9)

- **[Code]** Die ursprünglichen Punkte bestehen: Quelltexttest für V7, ungenutzter Import in §98.
- §102 fügt hinzu:
  - einen weiteren Quelltexttest (`test_cli_offers_journal_acknowledgement`),
  - den Fiktionstest mit künstlichem `winerror=5` (siehe A4/A12),
  - Test-Gates, die Codepfad und Zeitbasis vollständig ersetzen. Sie sind gewollt, belegen aber die echte Integration
    nur in `test_s90…production…` und `test_code_source_problems_close_the_gate`.
- **Urteil OFFEN, verschärft.**

### A10 – gelöschte Dateien beim Signieren (AUDIT_S100 A10)

- **[Code]** `owner_sign.committed_stand` bildet die Dateiliste weiter aus dem Arbeitsbaum. Mit dem repo-weiten
  Fingerabdruck betrifft das jetzt jede gelöschte getrackte `.py`. Weiterhin fail-closed (VM weicht ab).
- **Urteil OFFEN.**

**Gemeinsame Ursachen:**
1. **Kein unabhängiger Ist-Nachweis am Host beim Gebrauch:** A11 (ACL entfernt bei gültiger Bestätigung) und A3/A13
   (Zeit). Gemeinsamer Kern ist das Vertrauensmodell (E0 bzw. Host).
2. **Ungeprüfte Windows-Semantik in Tests:** A12, A9, A7.
3. **Ausschlusslisten statt Positivliste:** A14, A15, R4.

## 3 Neue Befunde

| # | Befund | Schwere | Klasse | Begründung |
|---|---|---|---|---|
| A11 | `acl-entfernen --lauf <anderer/alter Lauf>` (und ohne `--lauf`) entfernt die **aktive** Port-ACL eines anderen Laufs, weil Einträge nicht laufzuordenbar sind (gleiches Soll). Die Bestätigung bleibt in der VM gültig; die VM hätte **ohne Abflusssperre** einen gültigen Freigabestatus | **HOCH** | [Nachweis R5, Sim] | Bedienfehler genügt; die Folge ist ein unbemerkter Verlust der Isolation bei gültiger Bestätigung |
| A12 | Schreibprobe unter Windows nie „geschützt“ (`os.open` liefert `winerror None`) → A4 und die Bytecode-Isolationsprüfung (A1) real **immer BLOCKIERT**. Der §102-Test belegt das Gegenteil nur mit künstlichem `winerror` | **HOCH** (Funktion/Beweislage), sicherheitlich fail-closed | [Nachweis R9, R7] | AUDIT_S102 beschreibt ein Verhalten, das real nicht eintritt; D1 wäre unmöglich |
| A13 | Belegalter beim Gebrauch nur gegen VM-Uhr; Pausen- bzw. Speicherfall bei ausgeschalteter Zeitsynchronisierung | MITTEL | [Nachweis R6], [Annahme] | verlängert die Nutzbarkeit von Belegen; der zweite Teil ist nicht verifiziert |
| A14 | Unterordner `build`/`dist`/`build_tools`/`node_modules` in jeder Tiefe sind importierbar, gitignoriert, nicht im Fingerabdruck und nicht gescannt | MITTEL | [Nachweis R2] | umgeht Fingerabdruck **und** `git status`; erkannt erst nach dem Laden |
| A15 | Bytecode-/Erweiterungs-Scan case-sensitiv (`.PYC`, `.PYD` werden unter Windows geladen) | NIEDRIG | [Nachweis R3] | Modulprüfung fängt es nach dem Laden |
| A16 | Überdeckung aus Standardbibliothek/venv unsichtbar für A1; Ordner-Anlage nicht geprüft; `dist-info`/`entry_points.txt` nicht abgedeckt | NIEDRIG (bei A4 real wirksam), sonst MITTEL | [Nachweis R4, R8], [Code] | hängt vollständig an A4 |
| A17 | Rückfallpfad `e2e._embedding_provider` ergänzt `sys.path` um `tests/` → Gate geschlossen (Fehlalarm) | NIEDRIG (Funktion) | [Code] | in einer isolierten VM ohne Modell-Cache wahrscheinlich |

## 4 Tests und Beweislage

| Prüfung | Ergebnis | Klasse |
|---|---|---|
| Vorhandene Tests §87, §90, §102 (mit `PYTHONDONTWRITEBYTECODE=1`, ohne Cache – nichts geschrieben) | 214 bestanden, 1 übersprungen (Symlink) – **PASS** | [Test] |
| Ergebnisse aus §102 (Isolationssuites 458 bestanden, 9 übersprungen; §77–§85 315 bestanden) | nicht wiederholt (Arbeitsbaum seither unverändert, siehe §7) | [Test], übernommen |
| R1 pytest-Bytecode-Isolation | **bestätigt** (Annahme aus §102 lokal belegt) | [Nachweis] |
| R2, R3, R4, R6, R8 | Lücken **bestätigt** | [Nachweis] |
| R5 laufübergreifender ACL-Rückbau | Lücke **bestätigt** | [Nachweis, Sim] |
| R7, R9 Windows-Probe | Fehlklassifikation **bestätigt** | [Nachweis] |
| Gesamtsuite, Lint (ruff fehlt), §91/§92/§94/§96/§98 erneut | **NICHT DURCHGEFÜHRT** (nicht nötig für die Aussagen bzw. nicht verfügbar) | – |
| Schreibtest auf der echten venv/Basis-Installation | **NICHT DURCHGEFÜHRT** (würde Probedateien anlegen) | – |
| Zählerverhalten bei VM-Pause/Speichern, Boot-ID in der VM, Hyper-V-ACL- und Firewall-Semantik | **NICHT VERIFIZIERT** | [Annahme] |
| Reale Abnahme V1–V13 | **NICHT DURCHGEFÜHRT** | – |

Grüne Tests belegen hier nur, dass der Code das tut, was die Tests annehmen. R9 zeigt, dass mindestens eine dieser
Annahmen unter Windows falsch ist.

## 5 Voraussetzungen

**Für einen Commit des §102-Stands** (als weiterer Zwischenstand, NICHT SICHERHEITSABGENOMMEN):
- Vertretbar nur zusammen mit diesem Bericht und einer Commit-Nachricht, die A11 und A12 ausdrücklich nennt.
  AUDIT_S102 beschreibt A4 sonst zu positiv.
- Besser: zuerst A12 beheben (Windows-API statt `os.open`) und A11 absichern. Beides ist klein und lokal testbar (A12
  gegen echte geschützte Systemdateien wie in R9).

**Für eine reale VM-Abnahme (D1):**
1. A11, A12 und A14 behoben und erneut geprüft.
2. A13: Belegalter über den Anker; Zeitsynchronisierung und Stoppaktion als Kriterien.
3. A17 (Rückfall ohne Suchpfad-Änderung oder Embedding-Modell im Golden Image).
4. Python systemweit installiert, Golden Image mit geschützten Pfaden.
5. Isolationsvariablen gesetzt; Präfix unter einem geschützten Ordner.
6. A5 und A7.
7. Zählerverhalten bei Pause und Speichern real gemessen.

**Für eine spätere Sicherheitsfreigabe:**
- Reale Abnahme V1–V13 mit Owner-Signatur (D4).
- Erneuter unabhängiger Review **nach** D1.
- Offene NICHT-VERIFIZIERT-Punkte real geklärt (Hyper-V-ACL, Firewall- und ActiveStore-Semantik, Zähler).

## 6 Gesamturteil

Die §102-Fixes sind ein Fortschritt:
- Die Bytecode-Isolation ist wirksam, auch für pytest.
- Die Wiederherstellungs-Sackgasse ist behoben (simuliert).
- Der Ablauf der Bestätigung ist nicht mehr allein von der VM-Uhr abhängig.

Sie sind aber **nicht abnahmereif**:
- A12 macht A1 und A4 auf einem echten Windows-System unbestehbar und widerlegt eine Testannahme aus §102.
- A11 ist ein neuer, hoher Bedienfehlerpfad mit Isolationsverlust bei gültiger Bestätigung.
- A13 und A14 sind mittlere Restlücken; A5–A10 sind unverändert offen.

**Keine Sicherheitsfreigabe.** D1, Signierung und C-14: **GESPERRT**.

## 7 Abschlussprüfung

- Prüfsummen aller geänderten und neuen Dateien vor und nach dem Review sind identisch (Job-Ordner,
  `review103/baseline.txt`).
- `git status` zeigt zusätzlich nur `?? benchmarks/legal_answers_v1/AUDIT_S103.md`.
- Staging ist leer.
