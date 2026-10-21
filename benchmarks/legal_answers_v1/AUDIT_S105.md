# §105 Behebung A5 (Startcheck), A8 (Integrität beim Signieren), A10 (gelöschte Dateien)

Stand 21.10.2026. Branch `feature/feedback-diagnose`, HEAD `8e94bc1` (geprüft). Die Änderungen sind **uncommittet**.
**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben **GESPERRT**; es wurde keine echte Signatur erzeugt.

**Grenzen dieser Arbeit:**
- Nur lokale Änderungen an Code, Tests und Dokumentation.
- Keine System-, Hyper-V-, Netz-, DNS-, Firewall-, Konto- oder Rechteänderung. Keine VM, keine echten Schlüssel, keine
  externen Aufrufe, keine Produktivdatenbank.
- Alle Nachweise liefen lokal: tmp-Kopien, simuliertes Hyper-V (`FakeSystem`), git-Stubs und synthetische Ed25519-
  Schlüssel **im Speicher**. Die Signierlogik wurde geprüft, aber nie eine gültige Produktiv-Signatur erzeugt.
- Eine reale VM-Abnahme (V1–V13) fand **nicht** statt. Grüne Tests sind kein Nachweis realer Isolation.

**Beleg-Klassen:** [Code] gelesen · [Unit] Unit-/Regressionstest · [Sim] gegen simuliertes Hyper-V · [Stub] gegen
git-Stub · [Annahme] unbewiesen.

## 1 Geänderte Dateien

| Datei | Befund | Art |
|---|---|---|
| `scripts/legal_bench/isolation_host_control.py` | A5 | `startcheck_frisch`; `startcheck` protokolliert sein Ergebnis; CLI erzwingt vor realer Änderung einen aktuellen ok-Startcheck; `journal_offen` ignoriert informative Einträge |
| `scripts/legal_bench/isolation_gate.py` | A10 | `im_fingerprint` (Zugehörigkeitstest zum Fingerabdruck) |
| `scripts/legal_bench/owner_sign.py` | A8, A10 | `commit_fingerprint_namen`; `committed_stand` gleicht Commit-Dateimenge gegen den Arbeitsbaum ab; Laufzeit-Hash Pflicht; TOCTOU-Nachprüfung |
| `tests/test_s105_review_fixes.py` | alle | **neu**, 20 Tests |
| `tests/test_s96_review_fixes.py`, `tests/test_s98_review_fixes.py` | A10/A8 | git-Stubs beantworten jetzt auch `ls-tree`; Laufzeit-Gate-Test getrennt |

## 2 Befunde

### A5 – Startcheck technisch erzwungen → BEHOBEN (CLI/Simulation)

**Ursprung:** AUDIT_S104 §2 A5 (OFFEN). Der vollständige `startcheck` (VM aus, Kinddatenträger, Protokolle, offene
Journalvorgänge) war nicht erzwungen.

**Analyse der Pfade [Code]:** Reale Systemänderungen entstehen ausschließlich in `isolation_host_control` über die
Methoden `acl_setzen`, `fw_setzen`, `image_zuruecksetzen`. In der Produktion werden diese **nur** über die CLI
(`python -m scripts.legal_bench.isolation_host_control <cmd> --ausfuehren`) ausgelöst; `host_lauf` (isolation_real)
führt selbst keine Änderung aus (`ausfuehren=False`), sondern weist den Operator an, die CLI-Befehle auszuführen.

**Änderung [Code]:**
- `startcheck` schreibt sein Ergebnis (`ok`, Befunde, run_id, Zeit) als `startcheck_ergebnis` ins Journal (append-only).
- Neu `startcheck_frisch(journal, run_id, now)`: True nur, wenn es für den Lauf einen erfolgreichen Startcheck
  innerhalb von 12 h gibt **und** danach kein mutierender Eingriff eines anderen Laufs erfolgte. Eine spätere
  Inspektion (not-ok) entwaffnet den Lauf nicht.
- Die CLI verweigert `acl-setzen`/`fw-setzen`/`image-zuruecksetzen` mit `--ausfuehren`, wenn kein frischer ok-Startcheck
  vorliegt – **vor** jedem Systemaufruf (fail-closed, Rückgabe 2).
- `journal_offen` behandelt `startcheck_ergebnis` (und Quittungen) als informativ, nicht als offenen Vorgang.

**Test/Real trennung:** Die Methoden selbst bleiben unverändert aufrufbar (Tests, `host_lauf`). Die Erzwingung sitzt in
der Produktions-CLI; kein Testpfad hebelt sie aus (kein Test ruft die Setz-Befehle über `main`).

**Grenze (ehrlich):** Ein direkter Aufruf der Methoden im Code (nicht die Produktions-CLI) umgeht die Erzwingung. Das
ist kein Produktionspfad und setzt Schreibzugriff auf den Code voraus (bereits vertrauenswürdig). Zusätzlich bleibt der
**per-Ressourcen-Schutz** bestehen: `acl_setzen`/`fw_setzen` setzen nur bei leerer Ist-Ressource.

**Belege:**
- [Unit] `test_startcheck_frisch_requires_recent_successful_check`, `test_startcheck_frisch_blocks_after_foreign_mutation`.
- [Sim] `test_startcheck_records_its_result_in_the_journal`, `test_startcheck_records_failure`.
- [Code/Sim] `test_cli_refuses_real_mutation_without_fresh_startcheck`, `test_cli_mutations_all_require_startcheck`
  (alle drei Befehle), `test_cli_plan_and_read_do_not_require_startcheck`.

**Restrisiko:** Die 12-h-Frist ist eine Heuristik (wie die Firewall-Lebensdauer). Direkter Methodenaufruf umgeht die
CLI-Erzwingung. Nur simuliert.

### A8 – Integrität beim Signieren → BEHOBEN (Logik), keine echte Signatur

**Ursprung:** AUDIT_S104 §2 A8 (OFFEN). `sign_attestation` verlangte nur `commit` und `sha256`, nicht den Laufzeit-Hash;
TOCTOU-Fenster zwischen Prüfung und Signatur nicht geschlossen.

**Analyse [Code]:** Die Signatur bindet das Manifest-SHA-256 (jede Belegdatei mittelbar) und den `pruefwerkzeug`-Stand
(Commit, Datei-Fingerabdruck, Laufzeit-Fingerabdruck). Der Datei-Fingerabdruck wird gegen den **Commit-Inhalt**
(`committed_stand`) gebunden; der Laufzeit-Fingerabdruck beschreibt die Host-Umgebung beim Signieren und muss in der VM
gleich sein.

**Änderung [Code]:**
- `sign_attestation` verlangt zusätzlich `laufzeit_sha256`; fehlt er → `SigningRefused` (fail-closed).
- TOCTOU: Nach Urteil und Commit-Abgleich wird der Pruefwerkzeug-Stand **erneut** gelesen; weicht er vom anfangs
  gelesenen ab, bricht das Signieren ab. Damit wird kein während des Signierens veränderter Stand bestätigt.
- Zwischen Signatur und Gebrauch rechnet das Gate den Stand ohnehin neu nach (unverändert) und lehnt Abweichungen ab.

**Belege:**
- [Unit] `test_signing_succeeds_on_a_consistent_state` (synthetischer Schlüssel, Loopback – **keine** echte Freigabe),
  `test_signing_requires_the_runtime_fingerprint`, `test_signing_refuses_when_worktree_differs_from_commit`,
  `test_signing_detects_a_change_during_signing_toctou`.

**Restrisiko:** Die TOCTOU-Prüfung verengt das Fenster, schließt es aber nicht atomar (zwei Lesungen). Ein Angreifer mit
Schreibzugriff auf Host-Dateien **während** des Signierens zwischen der zweiten Lesung und der Signaturberechnung bleibt
außerhalb des Modells (kompromittierter Host). Der Laufzeit-Hash erfasst Paketinhalte nicht (A1-Restrisiko). Host und
VM müssen eine **identische** venv haben – Bedienvoraussetzung für D1, hier dokumentiert.

### A10 – gelöschte / hinzugefügte / unlesbare Dateien beim Signieren → BEHOBEN (Logik)

**Ursprung:** AUDIT_S104 §4 A10 (OFFEN). `committed_stand` bildete die Dateiliste aus dem **Arbeitsbaum**
(`tool_files()`); eine im Arbeitsbaum **gelöschte**, im Commit aber verfolgte Fingerabdruck-Datei wurde auf beiden Seiten
weggelassen und fiel damit nicht auf.

**Änderung [Code]:**
- Neu `im_fingerprint(name)`: spiegelt `tool_files()` (geprüft: jede `tool_files()`-Datei erfüllt es, Build-/Daten-Pfade
  nicht).
- Neu `commit_fingerprint_namen`: liest die im Commit **verfolgten** Fingerabdruck-Dateien via `git ls-tree`.
- `committed_stand` vergleicht die Commit-Dateimenge mit dem Arbeitsbaum. Jede im Commit vorhandene, im Arbeitsbaum
  fehlende Datei (gelöscht) **und** jede im Arbeitsbaum vorhandene, nicht committete Datei (hinzugefügt/untracked) →
  `sha256 None`, Signieren verweigert. Ein nicht lesbares Blob und ein `ls-tree`-Fehler ebenso.

**Belege:**
- [Stub] `test_committed_stand_detects_deleted_tracked_file`, `test_committed_stand_detects_added_untracked_file`,
  `test_committed_stand_detects_unreadable_blob`, `test_committed_stand_handles_ls_tree_failure`,
  `test_committed_stand_clean_matches_worktree`.
- [Unit] `test_im_fingerprint_matches_tool_files`.
- [Real-lokal] `test_real_git_reports_uncommitted_tool_files`: der echte, uncommittete Stand (§90–§105) ergibt
  `sha256 None` – Signieren bleibt real unmöglich, bis committet ist.

**Restrisiko:** Der Abgleich setzt ein vertrauenswürdiges `git` voraus; ein manipuliertes `git` ist außerhalb des
Modells. Namensgleiche, inhaltlich veränderte Dateien erkennt der anschließende Hash-Vergleich (unverändert).

## 3 Tests

| Lauf | Ergebnis | Status |
|---|---|---|
| `tests/test_s105_review_fixes.py` (neu) | 20 bestanden | **PASS** |
| Isolationssuiten Teil 1 (§86, §87, §90, §92, §102, §104, §105) | 300 bestanden, 8 übersprungen | **PASS** |
| Isolationssuiten Teil 2 (§91, §94, §96, §98) | 253 bestanden, 2 übersprungen (zwei Läufe) | **PASS** |
| Syntaxprüfung der geänderten Dateien | ok | **PASS** |
| Übrige Benchmark-Tests §77–§85 | nicht erneut (Logik nicht berührt) | NICHT DURCHGEFÜHRT |
| Gesamtsuite, Lint (ruff fehlt) | — | NICHT DURCHGEFÜHRT |
| Echte Signatur / reale Abnahme V1–V13 | — | NICHT DURCHGEFÜHRT |

Alle Läufe einzeln im Vordergrund mit Speicherwächter (Start ≥ 4 GB frei, Stopp < 1,5 GB), `PYTHONDONTWRITEBYTECODE=1`;
der Stopp wurde nie ausgelöst, es wurde kein Bytecode geschrieben. Zwei §102-Tests und ein §98-Test, die in einem
Zwischenstand durch die neuen Journal-/Signier-Änderungen brachen, sind nach der Korrektur (`journal_offen` ignoriert
informative Einträge; Laufzeit-Gate-Test getrennt) wieder grün.

## 4 Verbleibende Befunde A5–A10

| # | Stand nach §105 |
|---|---|
| A5 | behoben (CLI erzwingt Startcheck); Restrisiko: direkter Methodenaufruf, 12-h-Heuristik, nur simuliert |
| A6 | **OFFEN** – Lückenlosigkeit der periodischen Firewall-/Listener-Zwischenprüfungen weiterhin nur auf Fehler, nicht auf Anzahl/Abstand bewertet |
| A7 | behoben (Code, gruppenweise); Windows-Semantik NICHT VERIFIZIERT |
| A8 | behoben (Logik); TOCTOU verengt, nicht atomar; echte Signatur NICHT DURCHGEFÜHRT |
| A9 | **TEILWEISE** – §104/§105 haben fiktive `winerror`- bzw. Quelltext-Tests durch Verhaltens-/Stub-Tests ersetzt; einzelne Quelltext-Checks (V7, CLI-Bedienweg) bleiben |
| A10 | behoben (Logik, Stub-getestet); vertrauenswürdiges `git` vorausgesetzt |
| A1–A4 | wie AUDIT_S104; A12 real-lokal belegt, Paketinhalt/Pause-Verhalten bleiben Restrisiken |
| A11, A13, A14, A15, A16, A17 | wie AUDIT_S104 (behoben bzw. teilweise) |
| Grenzen des Modells | unverändert: Administrator in der VM, kompromittierter Host, Absenderfälschung |

## 5 Voraussetzungen (aktualisiert)

- **Commit** dieses Stands nur als Zwischenstand, **NICHT SICHERHEITSABGENOMMEN**, mit Nennung der noch offenen Befunde
  (A6, A9-Rest) in der Nachricht.
- **D1 / reale VM-Abnahme:** A6 abschließen; identische venv Host/VM dokumentieren und herstellen; Python systemweit;
  Isolationsvariablen gesetzt; Zählerverhalten bei Pause/Snapshot und Hyper-V-ACL/Firewall-Semantik real prüfen;
  Startcheck-Bedienablauf real durchspielen.
- **Sicherheitsfreigabe:** erst nach realer Abnahme, echter Owner-Signatur (D4) und erneutem unabhängigem Review.

**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben GESPERRT.
