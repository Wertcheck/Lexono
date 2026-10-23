# §110 Behebung A16 (Windows-ACL-Rechte WRITE_DAC/WRITE_OWNER, dist-info/entry_points)

Stand 21.10.2026. Branch `feature/feedback-diagnose`, HEAD `c9cecc3` (geprüft; lokaler Zwischencommit, **NICHT
SICHERHEITSABGENOMMEN**). Die Änderungen dieses Schritts sind **uncommittet**. D1, Signierung und C-14 bleiben
**GESPERRT**.

**Grenzen:** Nur lokale Code-, Test- und Dokumentänderungen. Keine System-, Konto-, Rechte-, Hyper-V-, Netz- oder
Firewalländerung. Keine VM, keine echten Schlüssel/Signaturen, keine externen Aufrufe, keine Produktivdatenbank. Keine
Probedateien in der echten venv oder in Systemverzeichnissen — Nachweise liefen in isolierten tmp-Verzeichnissen; die
ACL-Deny-Fixtures (icacls) wurden auf tmp-Ordnern gesetzt und sofort zurückgesetzt. Unit-Tests und Simulation sind
**kein Nachweis realer Isolation**. Die vier fremden Arbeitsbaum-Änderungen blieben unangetastet.

**Beleg-Klassen:** [Code] gelesen · [Unit] Unit-/Regressionstest · [Nachweis] echte lokale WinAPI/FS-Messung (tmp) ·
[Stub] simuliert (monkeypatch), wo echte Rechte-/Eigentümeränderung nötig wäre.

## 1 Geänderte Dateien

| Datei | Befund | Art |
|---|---|---|
| `scripts/legal_bench/isolation_codepfad.py` | A16 | `_WRITE_DAC`/`_WRITE_OWNER`; `_probe_file`/`_probe_dir` prüfen zusätzlich das Recht, ACL/Eigentümer zu ändern; `write_access` erfasst dist-info-Metadaten (entry_points.txt, RECORD, top_level.txt) |
| `tests/test_s110_review_fixes.py` | A16 | **neu**, 12 Tests |
| `tests/test_s104_review_fixes.py`, `tests/test_s96_review_fixes.py` | A16 | Read-only-Attribut-Erwartung an die strengere Semantik angepasst |

## 2 Befund A16 im Einzelnen

**Ursprung:** AUDIT_S107/S109 A16 (TEILWEISE). Die Schreibprüfung bewertete nur den **Inhalts**-Schreibzugriff
(`GENERIC_WRITE`), nicht das Recht, den Schutz selbst zu ändern (`WRITE_DAC`/`WRITE_OWNER`); `dist-info`-Metadaten und
Entry-Point-/Plugin-Registrierung waren nicht erfasst.

**Geprüfter Codepfad:** `isolation_codepfad._probe_file`, `_probe_dir`, `_win_create`, `write_access` (venv/base-Walk);
Bewertung über `isolation_evidence._write_protected` (V2-Kriterium `repository_schreibgeschuetzt`); Aufruf in der VM
über `isolation_real.observe_write_access`/`runtime_roots`.

**Änderung [Code]:**
1. **Inhalt vs. Schutzrechte unterschieden.** Nach dem Inhalts-Schreibtest (`GENERIC_WRITE`) öffnet die Probe die Datei
   bzw. das Verzeichnis zusätzlich mit `WRITE_DAC` (DACL ändern) und `WRITE_OWNER` (Eigentum übernehmen) — beides
   lesend, es wird nichts verändert. Gelingt **irgendeines** dieser Handles, kann das Konto den Schreibschutz aufheben →
   Ergebnis **„beschreibbar“ (unsicher)**. **„geschuetzt“** nur, wenn Inhalt **und** `WRITE_DAC` **und** `WRITE_OWNER`
   mit `ACCESS_DENIED` abgewiesen werden (fail-closed; jeder andere Fehler → „nicht_pruefbar“).
2. **Folge:** Eine Datei, die dem Benchmark-Konto **gehört**, gilt nie als geschützt (der Eigentümer hat `WRITE_DAC`
   implizit) — selbst mit Read-only-Attribut. Echter Schutz verlangt Eigentum bei einem **anderen** Konto und eine ACL
   ohne `WRITE_DAC`/`WRITE_OWNER` für das Benchmark-Konto. Das ist die eigentliche A16-Aussage.
3. **dist-info/entry_points.** `write_access` prüft in `*.dist-info`/`*.egg-info` jetzt auch `entry_points.txt`,
   `RECORD`, `top_level.txt` und weitere `.txt`. Über diese Metadaten kann sonst per Entry-Point/Plugin-Registrierung
   Code nachgeladen werden, ohne in den `.py`-Dateien zu erscheinen.

**Belege:**
- [Nachweis] beschreibbare Datei → beschreibbar; **eigene Read-only-Datei → beschreibbar** (via `WRITE_DAC`); fehlende
  Datei → nicht_pruefbar; beschreibbarer Ordner → beschreibbar.
- [Nachweis] **echte Deny-ACL** (icacls, inkl. `WDAC,WO`) auf einem tmp-Ordner → **geschuetzt** für Ordner und Datei;
  danach zurückgesetzt.
- [Nachweis] laufende, uns gehörende EXE (Freigabeverletzung beim Inhalt) → via `WRITE_DAC` **beschreibbar**, nie
  fälschlich „geschuetzt“.
- [Nachweis] `write_access` erfasst `entry_points.txt`/`RECORD` einer tmp-`*.dist-info`.
- [Stub] `test_probe_file_protected_only_if_content_and_acl_rights_denied`,
  `test_probe_dir_protected_only_if_all_rights_denied`, `test_probe_dir_writable_if_can_change_acl`,
  `test_probe_file_unclear_when_not_access_denied` (geschuetzt-Verdikt bzw. WRITE_DAC/WRITE_OWNER-Zweige simuliert, da
  eine real geschützte Datei ohne Eigentümeränderung nicht herstellbar ist).
- [Unit] `test_write_access_covers_dist_info_metadata`, `test_write_access_flags_writable_dist_info`,
  `test_access_masks_are_distinct_rights`.
- Angepasst: §104/§96-Tests, die das Read-only-Attribut noch als „geschuetzt“ erwarteten (jetzt „beschreibbar“).

**Urteil A16: BEHOBEN (lokal).** Die Prüfung unterscheidet jetzt Inhalts- von Rechteänderung, ist fail-closed und deckt
dist-info ab. Der WRITE_DAC/WRITE_OWNER-Positivfall (Konto kann Rechte ändern → unsicher) ist **real** belegt; das
geschuetzt-Verdikt ist gegen eine **echte Deny-ACL** real belegt und zusätzlich per Stub abgedeckt.

## 3 Restrisiken und NICHT VERIFIZIERTE Windows-Eigenschaften

- **Geerbte Rechte / effektive Berechtigung:** Die Probe fragt das tatsächliche Zugriffsrecht über `CreateFileW` ab
  (die Rechteauswertung macht Windows, inkl. geerbter ACEs) — sie liest **nicht** die ACL-Struktur aus. Das ist für
  „kann das Konto schreiben/Rechte ändern?“ ausreichend, deckt aber nicht jede denkbare ACL-Sonderform explizit ab.
- **Owner-Rights-ACE (S-1-3-4):** Ein Administrator könnte den impliziten WRITE_DAC des Eigentümers per Owner-Rights-ACE
  einschränken; das wurde **nicht** real geprüft (bräuchte Eigentümer-/ACL-Änderung). Da die Probe fail-closed ist,
  führt Unklarheit nie zu einem falschen „geschuetzt“.
- **Plugin-Autoload:** Selbst mit geschützten dist-info-Dateien lädt pytest installierte Plugins automatisch; deren
  **Paketinhalt** ist nicht gehasht (A1-Restrisiko). Empfohlene Bedienvoraussetzung für D1:
  `PYTHONDONTWRITEBYTECODE=1` (bereits gesetzt) **und** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` bzw. ein fest vorgegebener
  Plugin-Satz; zusätzlich Schreibschutz der gesamten venv durch ein fremdes Eigentümerkonto.
- **Modellgrenze:** Alle Aussagen gelten für das Benchmark-Konto in der VM. Ein Administrator in der VM oder ein
  kompromittierter Host sind außerhalb des Modells (AUDIT_S95).
- **Reale venv NICHT geprüft:** Der Schreibtest wurde bewusst nicht gegen die echte venv/Standardbibliothek ausgeführt
  (keine Probedateien dort). Für die VM vorgesehen (NICHT DURCHGEFÜHRT).

## 4 Tests

| Lauf | Ergebnis | Status |
|---|---|---|
| `tests/test_s110_review_fixes.py` (neu) | 12 bestanden | **PASS** |
| `tests/test_s104_review_fixes.py` (A16 angepasst) | im Teil 1 enthalten | **PASS** |
| `tests/test_s96_review_fixes.py` (A16 angepasst) | 71 bestanden | **PASS** |
| Isolationssuiten Teil 1 (§86, §87, §90, §92, §102, §104, §105, §106, §110) | 332 bestanden, 8 übersprungen | **PASS** |
| Isolationssuiten Teil 2 (§91, §94, §96, §98) | 197 bestanden, 1 übersprungen | **PASS** |
| Syntaxprüfung | ok | **PASS** |
| Übrige Benchmark-Tests §77–§85, Gesamtsuite, Lint (ruff fehlt), reale Abnahme V1–V13 | — | NICHT DURCHGEFÜHRT |

Alle Läufe einzeln im Vordergrund mit Speicherwächter (Start ≥ 4 GB frei, Stopp < 1,5 GB), `PYTHONDONTWRITEBYTECODE=1`;
der Stopp wurde nie ausgelöst. Zwischenfehler (zwei Tests erwarteten das Read-only-Attribut noch als „geschuetzt“)
wurden an die strengere, korrekte Semantik angepasst.

## 5 Status und Voraussetzungen

| # | Stand nach §110 |
|---|---|
| A16 | **BEHOBEN (lokal)**; Inhalts- vs. Rechteänderung unterschieden, dist-info erfasst; Restrisiken §3 |
| A1–A15, A17–A20 | wie AUDIT_S109 (behoben bzw. Restrisiko); A3/A13-Timing, A7/A12-Windows-Semantik, A8-TOCTOU, A20-Einzel-Owner weiterhin offen/NICHT VERIFIZIERT |

Damit sind alle Befunde A1–A20 behoben oder als ausdrückliches Restrisiko dokumentiert. **Keine reale Abnahme fand
statt.**

- **Zwischencommit (NICHT SICHERHEITSABGENOMMEN):** vertretbar; die Restrisiken aus §3 und AUDIT_S109 in der
  Commit-Nachricht nennen.
- **Vor D1 / realer Abnahme:** venv durch ein fremdes Eigentümerkonto schützen (ACL ohne WRITE_DAC/WRITE_OWNER fürs
  Benchmark-Konto); `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; Python systemweit; A3/A13-Timing und A7/A12-Windows-Semantik
  real prüfen; Schreibtest auf der echten venv durchführen.
- **Sicherheitsfreigabe:** erst nach realer Abnahme V1–V13, echter Owner-Signatur (D4) und erneutem unabhängigem Review.

**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben GESPERRT.
