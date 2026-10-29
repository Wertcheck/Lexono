# §107 Unabhängiger Sicherheitsreview des Isolations- und Signierkonzepts (A1–A17)

Stand 21.10.2026. **Keine Sicherheitsfreigabe.** Statischer Review und simulierte/Unit-Tests beweisen **keine reale
Isolation**. D1, Signierung und C-14 bleiben **GESPERRT**.

## 0 Ausgangszustand (geprüft)

- **Branch:** `feature/feedback-diagnose`. **HEAD:** `8e94bc1e49792bf83d32462f04a4af12f88e9840`. **Staging:** leer.
- **Working Tree:** 24 vorhandene (geänderte/neue) Dateien plus die gelöschten Logos; die §102–§106-Änderungen sind
  uncommittet. Fremde Dateien (`PRIVACY_REVIEW_ARCHITECTURE.md`, zwei gelöschte Logos, zwei UX-Bilder) unangetastet.
- **Methode:** rein lesende Prüfung des tatsächlichen Codes und der Tests; Statusangaben aus AUDIT_S100/S103–S106
  wurden **nicht** übernommen, sondern am Code nachvollzogen. Eigene Nachweise als Wegwerf-Skripte im Job-Ordner
  (`review107/`), gegen Kopien bzw. das simulierte System. SHA-256 aller Arbeitsbaumdateien vor und nach dem Review
  verglichen (§6).

**Beleg-Klassen:** [Code] gelesen · [Test] vorhandener Test ausgeführt · [Nachweis] eigener lokaler Nachweis.

## 1 Gesamturteil vorab

Die Werkzeugkette ist gegenüber AUDIT_S100 deutlich gehärtet. Vier Punkte weichen von den Vorberichten ab oder sind
neu:
- **A6 ist nur TEILWEISE** (nicht „behoben“): die Zwischenprüfungs-Abdeckung zählt nur, erkennt aber weder eine Lücke
  am Phasenende (Teil-Hang) noch eine Unterangabe der Phasendauer (neu **A18**, **A19**).
- Mehrere „behoben“-Status gelten nur unter dem **Vertrauensmodell** (E1-Belege vom Host); das ist korrekt dokumentiert,
  aber für die Einordnung zentral.
- Kein kritischer Fail-open-Pfad gefunden; alle gefundenen Lücken sind entweder fail-closed oder Modellgrenzen.

## 2 Befunde A1–A17 (einzeln)

### A1 – Codequellen/Bytecode — **BEHOBEN** (lokal), Restrisiko
- [Code] `isolation_codepfad.code_source_problems` + `bytecode_isolation`; Gate ruft `codepfad_probleme` in
  `IsolationGate._problems`. Erfasst Isolation (`PYTHONDONTWRITEBYTECODE` + nicht existenter, geschützter
  `PYTHONPYCACHEPREFIX`), quellenlosen Bytecode, Erweiterungsmodule, Wurzel-Überdeckung, Suchpfad, geladene Module.
- **Gegenargument/Restrisiko:** Paketinhalt in venv/Standardbibliothek wird nicht gehasht (nur Namen/Versionen/.pth);
  Schutz dort nur über A4. Module, die nach der Gate-Prüfung importiert werden, laufen vor ihrer Prüfung. Bleibt
  Restrisiko wie in AUDIT_S102/S103.

### A2 – Recovery — **BEHOBEN** (simuliert)
- [Code] `_close_open`, `journal_offen` (ignoriert `startcheck_ergebnis`/`zeile_quittiert`), `journal_quittieren`,
  Zeilenende-Schutz in `_log`. [Test] §102/§104-Rückbau- und Idempotenz-Tests grün.
- **Restrisiko:** Journalverlust → `--lauf` verweigert (menschliche Entscheidung). Nur simuliert.

### A3 – Zeitanker — **TEILWEISE**
- [Code] `vm_zeitbasis` (GetTickCount64 + BootId), `zeitanker_probleme`, Gate-Nachbewertung mit anker-abgeleitetem `now`.
- **Restrisiko (unverändert, ausdrücklich):** Verhalten des monotonen Zählers bei VM-Pause/Snapshot/Speichern **NICHT
  VERIFIZIERT**; Boot-ID-Lesbarkeit in der VM **NICHT VERIFIZIERT**. Fail-closed, aber ohne realen Beleg nicht abnahmereif.

### A4 – Schreibrechte — **BEHOBEN** (real-lokal), Restrisiko
- [Code] `write_access` deckt Repository, `__pycache__`, venv (Scripts, pyvenv.cfg, site-packages rekursiv, .pth) und
  Basis-Installation ab; `_write_protected` → FAIL/BLOCKIERT bei beschreibbar/unklar.
- **Restrisiko:** ACL-Rechte `WRITE_DAC`/`WRITE_OWNER`/`DELETE` ungeprüft; auf der echten venv NICHT ausgeführt.

### A5 – Startcheck unabhängig vom Aufrufweg — **BEHOBEN**, mit dokumentiertem Restrisiko
- [Code] `_require_startcheck` in `acl_setzen`/`fw_setzen`/`image_zuruecksetzen`; greift bei geführtem Journal. [Nachweis]
  §106-Tests: direkter Methodenaufruf ohne Startcheck → BLOCKIERT; abgelaufener (12 h) Startcheck → BLOCKIERT; CLI nutzt
  dieselbe Methode (kein zweiter Guard).
- **Test/Real-Trennung geprüft:** `startcheck_pflicht=False` kommt **ausschließlich** in Testdateien vor (verifiziert,
  keine Vorkommen in `scripts/`). Produktiv-Default ist True.
- **Restrisiko:** Ein mutierender Aufruf **ohne** Journal wird nicht erzwungen. Produktiv führt die CLI immer ein
  Journal und `host_lauf` mutiert nie (ausfuehren=False) — damit ist der reale Pfad abgedeckt; die journallose Lücke ist
  kein Produktionspfad, bleibt aber eine Defense-in-depth-Lücke. 12-h-Frist ist Heuristik.

### A6 – Lückenlosigkeit der Zwischenprüfungen — **TEILWEISE** (Abweichung von AUDIT_S106 „behoben“)
- [Code] `_abdeckung_problem` prüft NUR: Mindestanzahl `nachher_waehrend`-Prüfungen aus `dauer_s`, Zeitstempel
  vorhanden, monoton. [Test] erkennt den **vollständigen** Hang (0 Prüfungen über lange Phase) und Umsortierung.
- **Neue Befunde (eigene Nachweise):**
  - **A19 (MITTEL):** Ein **Teil-Hang** wird NICHT erkannt. [Nachweis] 19 Prüfungen in den ersten 19 s einer Phase von
    20×45 s, danach keine → `_abdeckung_problem` = None. Die Prüfung zählt nur, misst aber keine Lücke zwischen
    aufeinanderfolgenden Prüfungen gegen `MAX_KONTROLL_LUECKE_S`.
  - **A18 (NIEDRIG/MITTEL):** `dauer_s` wird NICHT gegen das Phasenfenster `aktiv_bis − aktiv_von` gegengeprüft.
    [Nachweis] `dauer_s=2` bei 30-min-Fenster → None. Eine zu klein gemeldete Dauer hebelt die Abdeckung aus.
- **Einordnung:** Beide liegen im Vertrauensmodell (Host liefert `dauer_s` und die Prüfliste; ein kompromittierter Host
  ist außerhalb des Modells). Aber selbst für den EHRLICHEN Betriebsfall deckt die Count-Prüfung einen Thread, der nach
  der Hälfte hängt, nicht ab. Eine echte Lücken-/Fensterprüfung fehlt → **TEILWEISE**.

### A7 – Firewall-Rückbau gruppenweise — **BEHOBEN (Code)**, real NICHT VERIFIZIERT
- [Code] `_fw_remove_command` mit `-Group`. Windows-Semantik von `Remove-NetFirewallRule -DisplayName -Group` nicht
  real geprüft; das simulierte System modelliert nur den Namen.

### A8 – Integrität beim Signieren — **BEHOBEN (Logik)**, keine echte Signatur
- [Code] `sign_attestation` verlangt `commit`+`sha256`+`laufzeit_sha256`; Commit-Abgleich via `committed`; TOCTOU-Nach-
  prüfung `tool() != stand`. [Test] §105: Laufzeit-Pflicht, Commit-Abweichung, TOCTOU → SigningRefused.
- **Gegenargument:** TOCTOU ist zwei Lesungen, nicht atomar; ein Schreibzugriff genau zwischen zweiter Lesung und
  Signaturberechnung bliebe unentdeckt (kompromittierter Host, außerhalb des Modells). Host/VM müssen identische venv
  haben (Bedienvoraussetzung). Keine echte Signatur erzeugt.

### A9 – Testqualität — **BEHOBEN**
- [Code/Test] Die verbliebenen `inspect.getsource`-Tests (V7, journal-quittieren) sind durch Verhaltenstests ersetzt
  (§106). Keine weiteren reinen Quelltextprüfungen sicherheitsrelevanter Logik gefunden.
- **Grenze:** Unit/Simulation ≠ reale Isolation.

### A10 – Vollständigkeit des signierten Dateistands — **BEHOBEN (Logik)**
- [Code] `committed_stand` zieht die Dateiliste über `git ls-tree` aus dem Commit und gleicht gegen den Arbeitsbaum ab;
  gelöschte, hinzugefügte/untrackte, unlesbare Dateien und `ls-tree`-Fehler → `sha256 None`. `im_fingerprint` spiegelt
  `tool_files()` (eigener Nachweis in §105: alle `tool_files()` erfüllen es, Build-/Daten-Pfade nicht).
- **Gegenargument:** Setzt vertrauenswürdiges `git` voraus. [Test] §105-Stubtests grün; `committed_stand("HEAD")` am
  echten, uncommitteten Baum → None (Signieren real unmöglich).

### A11 – ACL-Lauf-Zuordnung — **BEHOBEN** (simuliert)
- [Code] `aktiver_acl_besitzer` + Besitzer-Prüfung in `acl_entfernen`; fremder/mehrdeutiger aktiver Lauf → BLOCKIERT.
  [Test] §104: alter Lauf entfernt die aktive ACL eines neuen nicht.
- **Restrisiko:** Journal-gebunden (Verlust → `--lauf` verweigert); nur simuliert.

### A12 – Windows-Schreibprüfung — **BEHOBEN** (real-lokal)
- [Code] `_win_create`/`_classify` via CreateFileW+GetLastError; nur ACCESS_DENIED = geschützt. [Nachweis §104]
  Read-only-Attribut und echte deny-ACL → geschützt, laufende Datei → nicht_pruefbar. Nicht auf echter venv ausgeführt.

### A13 – Beleggültigkeit beim Gebrauch — **TEILWEISE**
- [Code] Gate nutzt den anker-abgeleiteten `now` für die Altersprüfung. Praktisch an A3 gekoppelt; der eigenständige
  Gewinn ist gering, und A3s Zähler-Restrisiko (Pause/Snapshot NICHT VERIFIZIERT) gilt fort.

### A14 – importierbare ausgeschlossene Verzeichnisse — **BEHOBEN** (lokal)
- [Code] `_importable_excluded`. [Test] `app/build/` wird erkannt. [Nachweis §106] echtes Repo im isolierten Prozess:
  keine Fehlalarme.

### A15 – Bytecode-Endungen case-insensitiv — **BEHOBEN**
- [Code] `.lower().endswith(...)` in `code_source_problems`; Wurzel-Suffixe über `all_suffixes().lower()`. [Test] §104
  `.PYC`/`.PYD`.

### A16 – weitere Schreibschutzlücken — **TEILWEISE**
- [Code] Ordner-Anlage wird jetzt mitgeprüft (`_probe_dir` prüft Datei- UND Unterordner-Anlage). ACL-Rechte
  (WRITE_DAC/OWNER/DELETE) und `dist-info`/`entry_points.txt` weiterhin nicht erfasst (wie A4).

### A17 – E2E-Fallback — **BEHOBEN**
- [Code] `e2e._embedding_provider` lädt den Fake-Provider über `importlib.util.spec_from_file_location` ohne
  `sys.path`-Mutation. [Test] §104/§106.

## 3 Neue Befunde

| # | Befund | Schwere | Beleg | Einordnung |
|---|---|---|---|---|
| A18 | A6-Abdeckung prüft `dauer_s` nicht gegen das Phasenfenster (`aktiv_bis − aktiv_von`); eine zu klein gemeldete Dauer hebelt die geforderte Mindestzahl aus | NIEDRIG–MITTEL | [Nachweis] `dauer_s=2` bei 30-min-Fenster → kein Befund | Modellgrenze (Host liefert beide Werte), aber die Inkonsistenz ist billig prüfbar (dauer_s ≈ Fenster) und ungenutzt |
| A19 | A6-Abdeckung ist count-basiert ohne Lückenmessung: ein Thread, der nach der Hälfte hängt, erfüllt die Zahl und bleibt unerkannt | MITTEL | [Nachweis] 19 Prüfungen geballt am Anfang, Rest leer → kein Befund | betrifft auch den EHRLICHEN Teil-Hang, nicht nur Manipulation → A6 ist nur TEILWEISE |
| A20 | `startcheck_frisch` bindet den Startcheck an `run_id` + 12-h-Fenster, aber nicht an den konkreten Ist-Zustand (z. B. Hash der leeren ACL/Firewall-Gruppe); zwischen ok-Startcheck und erster realer Änderung könnte Zeit/Zustand abweichen | NIEDRIG | [Code] `startcheck_frisch` | Defense-in-depth; die per-Ressourcen-Leerprüfung in `acl_setzen`/`fw_setzen` fängt den praktisch relevanten Fall ab |

Kein kritischer Fail-open-Pfad gefunden. Test-Opt-out (`startcheck_pflicht=False`) ausschließlich in Tests ([Code]
verifiziert).

## 4 Tests

| Prüfung | Ergebnis | Klasse |
|---|---|---|
| `tests/test_s105_review_fixes.py`, `tests/test_s106_review_fixes.py` | 36 bestanden | [Test] |
| Isolationssuiten §86–§106 | in §106 vollständig grün (Baum seither unverändert, SHA-256 geprüft); hier nicht wiederholt | [Test], übernommen |
| Eigene Nachweise A18/A19 | Bypass bestätigt | [Nachweis] |
| Gesamtsuite, Lint, reale Abnahme V1–V13 | NICHT DURCHGEFÜHRT | — |

Grüne Tests belegen nur das erwartete Verhalten des Codes, nicht reale Isolation. Die A18/A19-Nachweise zeigen, dass
grüne A6-Tests eine Teil-Hang-Lücke nicht ausschließen.

## 5 Voraussetzungen

**Für einen Commit (als Zwischenstand, NICHT SICHERHEITSABGENOMMEN):** vertretbar; die offenen Punkte A18/A19 (A6
TEILWEISE), A3/A13 (Timing NICHT VERIFIZIERT), A7/A8/A10/A12 (reale Semantik NICHT VERIFIZIERT) in der Commit-Nachricht
nennen. Nur eigene Dateien, keine fremden.

**Vor D1 / realer Abnahme:**
1. A6 vervollständigen: Lücken-/Fensterprüfung (A18/A19) — `dauer_s ≈ Fenster` und max. Abstand zwischen Prüfungen
   ≤ `MAX_KONTROLL_LUECKE_S` statt reiner Zählung.
2. A3/A13: Zähler-Verhalten bei Pause/Snapshot und Boot-ID real messen.
3. A7/A12: Windows-Firewall-/ACL-Semantik real prüfen; A4/A12 auf der echten venv.
4. Identische venv Host/VM; Python systemweit; Isolationsvariablen gesetzt.

**Für eine Sicherheitsfreigabe:** reale Abnahme V1–V13, echte Owner-Signatur (D4), erneuter unabhängiger Review nach D1.

## 6 Abschlussprüfung

- SHA-256 aller 24 Arbeitsbaumdateien vor und nach dem Review identisch (`review107/baseline.txt`).
- `git status` zusätzlich nur `?? benchmarks/legal_answers_v1/AUDIT_S107.md`.
- Staging leer, HEAD unverändert `8e94bc1`. Keine Sicherheitsfreigabe.
