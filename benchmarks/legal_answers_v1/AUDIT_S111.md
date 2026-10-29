# §111 Unabhängiger Sicherheitsreview der §110-Änderungen und A1–A20

Stand 21.10.2026. **Keine Sicherheitsfreigabe.** Statischer Review und simulierte/Unit-Tests beweisen **keine reale
Isolation**. D1, Signierung und C-14 bleiben **GESPERRT**.

## 0 Ausgangszustand (geprüft)

- **Branch:** `feature/feedback-diagnose`. **HEAD:** `c9cecc3f8af445d89d26ef5e5b678be8c5767993` (Zwischencommit §102–§109,
  NICHT SICHERHEITSABGENOMMEN). **Staging:** leer.
- **Working Tree:** 7 vorhandene (geänderte/neue) Dateien plus die gelöschten Logos; §110 uncommittet. Fremde Dateien
  (`PRIVACY_REVIEW_ARCHITECTURE.md`, zwei gelöschte Logos, zwei UX-Bilder) unangetastet.
- **Methode:** rein lesend; Statusangaben nicht übernommen, am Code geprüft. SHA-256 aller Arbeitsbaumdateien vor/nach
  verglichen (§6).

**Beleg-Klassen:** [Code] gelesen · [Test] vorhandener Test · [Nachweis] eigener lokaler Nachweis (§110, tmp).

## 1 A16 – Windows-Berechtigungen (unabhängig geprüft)

**Codepfad:** `isolation_codepfad._probe_file`, `_probe_dir`, `_win_create`, `_classify`, `write_access`.

**Befund [Code]:**
- **Inhalt vs. Schutzrechte getrennt:** Nach `GENERIC_WRITE` werden `WRITE_DAC` (0x40000) und `WRITE_OWNER` (0x80000)
  einzeln per `CreateFileW` geöffnet. Gelingt eines → „beschreibbar“. „geschuetzt“ nur, wenn alle mit `ACCESS_DENIED`
  abgewiesen werden; jeder andere Fehler → „nicht_pruefbar“ (fail-closed). Konstanten korrekt (`test_access_masks…`).
- **Zuverlässigkeit von CreateFileW/GetLastError:** Das Öffnen mit einer Zugriffsmaske löst im Kernel eine AccessCheck
  gegen die DACL aus (inkl. geerbter ACEs und Eigentümer-impliziter Rechte). Das ist ein belastbarer „kann das Token
  dieses Recht erlangen?“-Test, nicht nur ein einzelner Schreibversuch. `use_last_error=True` liefert den echten
  GetLastError. [Nachweis §110] read-only-Attribut (Eigentümer) → beschreibbar via WRITE_DAC; echte Deny-ACL inkl.
  WDAC/WO → geschuetzt; danach zurückgesetzt.
- **Datei- und Ordnerpfade:** Verzeichnis-Handle mit `FILE_FLAG_BACKUP_SEMANTICS`; zusätzlich Datei-/Unterordner-Anlage.
  Fehlende Pfade → nicht_pruefbar (NOT_FOUND ≠ ACCESS_DENIED). Nicht lesbare Ordner im `write_access`-Walk → `onerror`
  meldet sie als „nicht lesbar“ → nicht_pruefbar.

**Gegenargumente / Grenzen (unabhängig):**
- **WRITE_OWNER-Semantik:** Der Eigentümer hat implizit nur READ_CONTROL und WRITE_DAC, NICHT WRITE_OWNER. Der
  WRITE_OWNER-Zweig greift daher nur bei Konten mit Take-Ownership-Recht; der Eigentümerfall wird über WRITE_DAC
  abgedeckt. Konsistent, keine Lücke.
- **Owner-Rights-ACE (S-1-3-4):** Ein Administrator könnte den impliziten WRITE_DAC des Eigentümers per Owner-Rights-ACE
  beschränken. Nicht real geprüft; da die Probe fail-closed ist, führt das nie zu einem falschen „geschuetzt“.
- **Traverse-Abhängigkeit:** CreateFileW braucht auch Traverse/Read der Pfadkomponenten. Verweigert ein übergeordnetes
  Verzeichnis die Traverse, meldet die Probe „geschuetzt“, obwohl die Datei-DACL permissiv wäre — konservativ-sicher
  (ohne Traverse ist die Datei ohnehin nicht erreichbar).
- **Keine vollständige ACL-Zusicherung:** Die Probe liest nicht die ACL-Struktur aus; sie prüft die effektive
  Erlangbarkeit der relevanten Rechte. Vollständige ACL-Sicherheit wird NICHT behauptet.

**Urteil A16: BEHOBEN (lokal)** unter den genannten, dokumentierten Grenzen (Owner-Rights-ACE und reale venv NICHT
VERIFIZIERT).

## 2 Pakete und Plugins

**Codepfad:** `write_access` venv/base-Walk; `run_pytest` (`isolation_real`).

**Befund [Code]:**
- `write_access` erfasst in `*.dist-info`/`*.egg-info` jetzt `entry_points.txt`, `RECORD`, `top_level.txt` und weitere
  `.txt` (geprüft, §110 `test_write_access_covers_dist_info_metadata`). Paket-`.py`/`.pyd` werden ohnehin über die
  Code-Suffixe erfasst. Damit wird eine **schreibbare** Metadaten-/Plugin-Registrierung als FAIL gemeldet.
- **RECORD-Hashes werden nicht verifiziert:** Die Prüfung stellt nur Schreibschutz fest, sie gleicht die installierten
  Dateien NICHT gegen die RECORD-Hashes ab. Content-Integrität der Pakete bleibt ungeprüft (A1-Restrisiko: Paketinhalt
  wird nicht gehasht, nur Distribution Name==Version in `laufzeit_stand`).

**Neuer Befund A21 (MITTEL) [Code/Nachweis]:** `isolation_real.run_pytest` setzt `PYTHONDONTWRITEBYTECODE=1` und
`PYTHONPYCACHEPREFIX`, aber **nicht** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Die VM-pytest-Kindprozesse laden damit alle
installierten Drittanbieter-Plugins automatisch; deren Code läuft und steht nicht im Fingerabdruck. Die in AUDIT_S110
empfohlene Gegenmaßnahme ist also **nur dokumentiert, nicht im Code erzwungen**. Schutz heute nur durch den
Schreibschutz der venv/dist-info (A16) + Distribution-Version (`laufzeit_stand`) — nicht durch Autoload-Verhinderung.
**Konkreter Fix (vor D1):** in `run_pytest` `env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"` setzen und die benötigten
Plugins explizit mit `-p` laden; zusätzlich venv-Eigentum bei einem fremden Konto. (Keine Codeänderung in diesem
reinen Review.)

## 3 Gesamtbewertung A1–A20 (+A21)

| # | Urteil | Kurzbegründung |
|---|---|---|
| A1 Codequellen/Bytecode | BEHOBEN (lokal) | `code_source_problems`; Paketinhalt ungehasht (Restrisiko) |
| A2 Recovery | BEHOBEN (sim.) | `_close_open`/`journal_offen`/`journal_quittieren` |
| A3 Zeitanker | TEILWEISE | Pause/Snapshot/Boot-ID NICHT VERIFIZIERT |
| A4 Schreibrechte | BEHOBEN (real-lokal) | durch A16 verschärft |
| A5 Startcheck | BEHOBEN | Methodenebene, Test-Opt-out nur in Tests |
| A6/A18/A19 Zwischenprüfungen | BEHOBEN | fenstergebunden, Lückenprüfung; Host-Modellgrenze |
| A7 FW-Rückbau gruppenweise | BEHOBEN (Code) | Windows-Semantik NICHT VERIFIZIERT |
| A8 Signierintegrität | BEHOBEN (Logik) | TOCTOU nicht atomar; keine echte Signatur |
| A9 Testqualität | BEHOBEN | Verhaltens- statt Quelltexttests |
| A10 Dateivollständigkeit | BEHOBEN (Logik) | git vertrauenswürdig vorausgesetzt |
| A11 ACL-Lauf-Zuordnung | BEHOBEN (sim.) | `aktiver_acl_besitzer` |
| A12 Windows-Schreibprüfung | BEHOBEN (real-lokal) | durch A16 erweitert; echte venv nicht geprüft |
| A13 Beleggültigkeit | TEILWEISE | an A3 gekoppelt |
| A14/A15 Importpfade/Endungen | BEHOBEN | `_importable_excluded`, case-insensitiv |
| A16 ACL-Rechte/dist-info | BEHOBEN (lokal) | §1/§2; Owner-Rights-ACE, reale venv NICHT VERIFIZIERT |
| A17 E2E-Fallback | BEHOBEN | ohne sys.path-Mutation |
| A20 Startcheck-Zustandsbindung | TEILWEISE/OFFEN | durch Leerprüfung+A11 abgesichert; Einzel-Owner-Modell |
| **A21 Plugin-Autoload in run_pytest** | **OFFEN** | Mitigation nur dokumentiert, nicht gesetzt (§2) |

**Wechselwirkungen geprüft:** Berechtigungsprüfung (A16) ↔ Fingerabdruck (A1): A16 ist der einzige Schutz für die nicht
gehashten Paket-/Standardbibliotheksinhalte — fällt der venv-Schreibschutz, greift A1 nicht (Content ungehasht). Das
ist die zentrale gemeinsame Abhängigkeit. Journal/Gate/Signierung: keine neue Wechselwirkung gefunden; A8-TOCTOU und
A10 setzen vertrauenswürdiges git/Dateisystem voraus (Modellgrenze).

**Keine weiteren neuen Befunde (A22 ff.).**

## 4 Tests und Beweisgrenzen

| Prüfung | Ergebnis | Klasse |
|---|---|---|
| `tests/test_s110_review_fixes.py` | 10 bestanden | [Test] |
| Isolationssuiten §86–§110 | in §110 vollständig grün (Baum seither unverändert, SHA-256 geprüft) | [Test], übernommen |
| A21-Nachweis (run_pytest ohne PYTEST_DISABLE_PLUGIN_AUTOLOAD) | am Code bestätigt | [Code] |
| Gesamtsuite, Lint, echte Signatur, reale Abnahme V1–V13, Schreibtest echte venv | NICHT DURCHGEFÜHRT | — |

Grüne Tests belegen nur das erwartete Code-Verhalten; **keine reale Isolation**. A16 ist für den Positivfall
(Rechteänderung möglich → unsicher) real belegt; das geschuetzt-Verdikt gegen eine echte Deny-ACL ebenfalls, sonst
simuliert.

## 5 Nicht verifizierte Eigenschaften (Sammlung)

- Owner-Rights-ACE-Beschränkung des Eigentümer-WRITE_DAC (A16).
- Reale venv/Standardbibliothek-Schreibrechte (A4/A12/A16 — nicht gegen die echte Installation geprüft).
- `GetTickCount64`-Verhalten bei VM-Pause/Snapshot, Boot-ID-Lesbarkeit (A3/A13).
- Hyper-V-Extended-ACL/Firewall-/`Remove-NetFirewallRule`-Semantik (A7/A11/A12).
- Atomarität der Signatur gegen Dateiänderung (A8-TOCTOU).
- Wirksamkeit gegen einen Administrator in der VM / kompromittierten Host (Modellgrenze).

## 6 Voraussetzungen und Abschluss

**Zwischencommit (NICHT SICHERHEITSABGENOMMEN):** vertretbar; A21 und die Restrisiken aus §3/§5 in der Commit-Nachricht
nennen.

**Vor D1 / realer Abnahme:** A21 beheben (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` in `run_pytest`, Plugins explizit); venv
durch ein fremdes Eigentümerkonto schützen; Python systemweit; A3/A13-Timing und A7/A12-Windows-Semantik real prüfen;
Schreibtest auf der echten venv durchführen.

**Sicherheitsfreigabe:** erst nach realer Abnahme V1–V13, echter Owner-Signatur (D4) und erneutem unabhängigem Review.

**Abschlussprüfung:** SHA-256 aller 7 Arbeitsbaumdateien vor/nach dem Review identisch; `git status` zusätzlich nur
`?? benchmarks/legal_answers_v1/AUDIT_S111.md`; Staging leer; HEAD unverändert `c9cecc3`. **Keine Sicherheitsfreigabe.**
