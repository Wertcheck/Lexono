# §79 Fail-Closed im Schriftsatzpfad (B1) und Absicherung der Benchmark-Ausführung

Stand 19.10.2026, Ausgangsstand `daeabc2` (§78), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).
Es gab keinen Modelllauf, keinen Cloud-Aufruf und keine Kosten. Verwendet wurden nur synthetische Daten, ein lokaler
Aufzeichnungsserver und ein gefälschter Schlüssel. Dieser Bericht gibt keine Datenschutzgarantie und trifft keine
Aussage über juristische Zuverlässigkeit. J1–J6 und C-1–C-17 bleiben **OFFEN**.

| Prüfschritt | Ergebnis |
|---|---|
| B1 Ursache ermittelt und behoben (Repository) | **PASS** |
| B1 Regressionstests Chat, Schriftsatz und Web, mit Recorder | **PASS** (Gegenprobe ohne Fix: 8 von 11 rot) |
| Build, Sicherung, Installation, EXE-Abgleich | **PASS** |
| App-Test WebView2: Ausfall der Namenserkennung (neuer Build) | **PASS** |
| App-Test WebView2: normaler Ablauf (neuer Build) | **PASS** |
| App-Test WebView2: Ausfall im alten Build (Vorher-Beleg) | B1 reproduziert (HTTP 500), **wie erwartet FAIL** |
| Echte Datenbank unverändert | **PASS** |
| Benchmark: Prüfung vor jedem Aufruf (`CallGuard`/`GuardedWriter`) | **PASS** (Mock und lokaler Recorder) |
| Benchmark: vollständige Ausführung | **NICHT VERIFIZIERT – bewusst nicht gebaut** (Lücken E1–E5) |
| Volle Suite | **PASS** bis auf die 4 bekannten, fremden Logo-Tests |

## 1 B1 – Ursache und Behebung

**Ursache:** `ClaudePrivacyGateway.prepare_request` fing Fehler nur in der Kandidatensuche fail-closed ab. Warf die
Erkennung (Presidio/spaCy) in der **Pseudonymisierung** oder im **Restrisiko-Scan**, stieg die Ausnahme ungefangen
auf. Die Folgen:
- `DraftingService.create_draft` bzw. `create_draft_stream` reichte sie durch; der Schriftsatz-Generator lieferte
  HTTP 500.
- Der Chat fing sie nur in seinem allgemeinen Zustand „unerwarteter technischer Fehler“ ab.

Gesendet wurde in keinem Fall etwas. Die Blockierung war aber nicht geordnet und hatte keinen Grund.

**Behebung** (`app/privacy/gateway.py`, `app/privacy/api_logger.py`):
- **Fail-closed-Muster:** Beide Erkennungsschritte laufen jetzt im bestehenden Muster der Kandidatensuche. Bei einer
  Ausnahme entsteht `GatewayResult(allowed=False, payload=None, mappings=[])` mit dem festen, inhaltsfreien Grund
  `PRIVACY_CHECK_FAILED_REASON`.
- **Gemeinsamer Blockierungsweg:** Ohne Payload kann kein Provider senden. Alle Aufrufer nutzen den vorhandenen Weg:
  Chat, Schriftsatz (auch Streaming), Orchestrator und Review-Engine. Neue Sicherheitslogik in den Aufrufern war nicht
  nötig.
- **Meldung:** Die neue Kategorie `privacy_check_failed` erzeugt über das bestehende `friendly_block_message` den Text
  „Die Datenschutzprüfung ist fehlgeschlagen (die Erkennung personenbezogener Daten war nicht verfügbar). Es wurde nichts
  an die KI übermittelt. …“. Im Audit erscheint `ApiCallLog.error_status = privacy_check_failed`.
- **Datensparsames Log:** Protokolliert werden nur Schritt und Fehlertyp, zum Beispiel
  `Datenschutzpruefung fehlgeschlagen (Schritt pseudonymisierung, OSError)`. Die Fehlermeldung selbst kann Text
  enthalten und wird deshalb nicht geloggt; es gibt auch keinen Traceback.
- **Nicht verschluckt:** Programmierfehler außerhalb der Erkennung bleiben sichtbar, zum Beispiel ein unbekannter
  `review_mode` (`ValueError`). Presidio, Pseudonymisierung, Restrisiko-Scan, Final Payload Gate und Siegel sind
  unverändert.

**Tests:**
- `tests/test_s79_privacy_check_failure.py` (11 Tests):
  - **Gateway:** Presidio-Ausfall, Absturz des Pseudonymizers und Absturz des Restrisiko-Scans blockieren jeweils; das
    Log enthält weder den synthetischen Namen noch den Text.
  - **Normalbetrieb:** Ohne Ausfall bleibt der Payload sendefertig und versiegelt.
  - **Service:** `create_draft`, `create_draft_stream` und Chat zeigen die Meldung, und der Provider erhält 0 Payloads.
  - **Web:** Schriftsatz-Generator (Redirect mit Meldung statt 500) und Chat-Stream, beide gegen den lokalen Recorder
    mit 0 Anfragen.
- **Gegenprobe:** Mit der alten Gateway-Fassung schlagen 8 von 11 Tests fehl.
- **Angepasst:** `tests/test_privacy_name_variant_consistency.py::test_fail_closed_on_ner_failure_is_unchanged` erwartet
  jetzt die geordnete Blockierung statt einer Ausnahme. `tests/test_s78_wire_privacy.py` erwartet für Q2 jetzt
  `blockiert` mit festem Grund.
- **Leitungsnachweis nach dem Fix** (`audit/s79_wire_nachweis.json`): LE-D005, LE-D006 und LE-T004 ergeben 7 Anfragen
  und 0 Lecks. Fällt die Namenserkennung aus, sind Q1 **und** Q2 `blockiert`, und es kommen 0 weitere Anfragen hinzu.

## 2 Build, Sicherung, Installation

| | |
|---|---|
| Vorprüfung | kein `Lexono.exe` (die WebView2-Prozesse gehören zu SearchHost und Teams); Sicherungsziel frei; echte DB `677CAFD8109F` |
| Build | Build-Klon auf `bfbe53a` (Fix-Commit), sauber; PyInstaller Exit 0, ISCC Exit 0 |
| Sicherung | `C:\Users\Bonit\lexono_install_backup_vor_bfbe53a`, 956/956 Dateien, EXE-Hash gleich (`280293ee…6dfc`, Build `8291158`) |
| Installation | stilles Update, Exit 0; 956 Dateien |
| EXE | installiert = Build = `01bff911db8bedb19e6f4ff798f0fc1b7a0eab92845710bd44786f492b4999b9`; Setup `da6db6cb…8154` |
| Echte DB | vorher und nachher `677CAFD8109F` (letzte Änderung 13.10.); weder gestartet noch migriert |

## 3 App-Test in der installierten WebView2-App

**Testaufbau:**
- **Instanz:** isolierte QA-Instanz (`LEXONO_DATA_DIR=C:\Users\Bonit\lexono_qa_s65`), WebView2 `Edg/155.0.4283.62`.
- **Netz:** lokaler Recorder, toter Proxy, gefälschter Schlüssel.
- **Testdaten:** synthetische Akte über die echten Formulare (Mandant, Akte, Gegner); Chat-Frage und
  Schriftsatz-Anmerkung mit Namen, E-Mail und Betrag.
- **Nachweise:** `C:\Users\Bonit\lexono_qa_s65\qa_nachweise\s79_fail_closed\` (Screenshots, Ergebnisse, App-Log),
  Auszüge unter `audit/s79_app_*.json`.

**Ausfall der Namenserkennung ohne Code-Eingriff:**
- Die installierte Anwendung wurde byte-gleich nach `C:\Users\Bonit\lexono_qa_s79_ner_ausfall\` kopiert.
- Einzige Änderung in der Kopie: Das Verzeichnis `_internal\de_core_news_lg` ist umbenannt, sodass spaCy beim Laden
  einen `OSError` wirft.
- Die EXE ist identisch mit der installierten. Installation und Sicherungen sind unverändert.

| Lauf | Chat | Schriftsatz | Anfragen am Recorder | App-Log |
|---|---|---|---|---|
| **neuer Build, Ausfall** | Meldung „Die Datenschutzprüfung ist fehlgeschlagen … nichts an die KI übermittelt“, nicht mehr „technischer Fehler“ | „Generierung blockiert: Die Datenschutzprüfung ist fehlgeschlagen …“, kein Entwurf, keine 500-Seite, Audit 2× `privacy_check_failed` | **0** | 2 Zeilen `Schritt pseudonymisierung, OSError`; keine geschützten Werte, kein Schlüssel |
| **neuer Build, normal** (installierte EXE) | Antwort angezeigt | Entwurf im Editor geöffnet | 2 (`/v1/messages`, nur Testschlüssel, **0 Lecks**, Platzhalter vorhanden, Betrag lesbar) | keine geschützten Werte |
| alter Build `8291158`, Ausfall (Kopie der Sicherung) | „unerwarteter technischer Fehler“ | **„Internal Server Error“ (HTTP 500)** | 0 | – |

**Ergebnis:** B1 ist in der installierten App behoben, und bei einem Datenschutzfehler gehen 0 Anfragen hinaus.
**PASS**

**Hinweis zu den Auszügen:** Das Feld `auszug` in den App-Ergebnissen gibt den **lokal** angezeigten Text wieder; darin
steht die synthetische Eingabe. Was tatsächlich gesendet wurde, steht im Feld `wire`.

## 4 Benchmark-Ausführung – Prüfung vor jedem Modellaufruf

**Bestand:**
- `answer_run.ensure_runnable` existiert und prüft über `check` Folgendes: Freigaben, Werte, aktuellen Commit, die Sperre
  des Test-Splits (LOCK), Spezifikationen, Datenschutz-Pflichtwerte sowie den Plan innerhalb von `max_calls` und
  `max_cost_eur`.
- Verbessert: `NotApproved` nennt jetzt die konkreten Gründe (`reasons`, die ersten drei auch in der Meldung), nicht nur
  Anzahlen.

**Neu: `scripts/legal_bench/answer_exec.py`** – ein Wächter, kein Lauf.
- **`CallGuard`:** Startet nur mit freigegebener Konfiguration und ausführbarem Plan. `authorize()` läuft unmittelbar
  vor **jedem** Provider-Aufruf und prüft erneut:
  1. Datei-Hash der Konfiguration unverändert;
  2. `ensure_runnable`, also Freigaben, Commit = HEAD, LOCK und Datenschutzwerte;
  3. `plan_fingerprint` unverändert;
  4. Aufruf geplant und noch nicht gesendet;
  5. gesendete Aufrufe < `max_calls`;
  6. geschätzte Kosten des nächsten Aufrufs ≤ `max_cost_eur`;
  7. Fehlversuche < `abort_after_failed_calls`;
  8. kein früherer Datenschutzbefund;
  9. Payload versiegelt;
  10. kein geschützter Wert, einschließlich Varianten, **irgendeiner** Akte im Payload.

  Jeder Befund beendet den Lauf mit Gründen. Der Provider wird nicht aufgerufen, und auch alle folgenden Aufrufe werden
  verweigert.
- **`GuardedWriter`:** Er ist der Schreib-Provider des regulären `DraftingService`. Er erhält nur Payloads, die das
  Gateway erzeugt und versiegelt hat; einen Pfad an der Datenschutzprüfung vorbei gibt es nicht. Der Provider wird von
  außen eingereicht, das Modul baut selbst keinen. Es gibt keine Kommandozeile und kein `run` oder `execute`; das ist
  statisch geprüft.

**Tests** (`tests/test_s79_benchmark_exec_guard.py`, 14 Tests; echter Provider gegen den lokalen Recorder bzw. Mock):
- **Positivkontrolle:** genau 1 pseudonymisierte Anfrage.
- **Fail-Closed mit 0 Anfragen** in diesen Fällen:
  - nicht geplanter Aufruf;
  - Wiederholung (danach bleibt der Lauf beendet);
  - Konfiguration während des Laufs geändert;
  - veralteter Commit;
  - LOCK-Verletzung;
  - geänderte Akte oder Spezifikation (Plan-Fingerabdruck);
  - Kostenlimit pro Aufruf;
  - Fehlversuchsgrenze;
  - Datenschutzbefund im Payload;
  - unversiegelter Payload;
  - Ausfall der Namenserkennung (dann erreicht der Aufruf den Wächter gar nicht).
- **Start:** Ohne Freigabe startet der Wächter nicht, die Gründe werden genannt.
- Die Testkonfiguration ist synthetisch und liegt nur in `tmp_path`. Sie ist **keine** Freigabe.

**Lücken – bewusst nicht gebaut, eine Ausführung ist damit NICHT möglich:**
- **E1 Treiber:** Je geplantem Aufruf müsste er die Akte in eine isolierte Datenbank importieren und Chat bzw.
  Schriftsatz über den regulären Pfad auslösen. Hinzu kommen `begin(call)` und das Prüfen von `guard.aborted` nach jedem
  Aufruf, weil der DraftingService Writer-Ausnahmen in ein blockiertes Ergebnis umwandelt.
- **E2 Ablage:** Antwortdatensätze (`validate_answer_record`) müssten außerhalb des Repositorys mit Aufbewahrung
  abgelegt werden (`storage.*`, C-Punkt offen).
- **E3 Kosten:** Erfasst ist nur die Schätzung pro Aufruf, nicht die tatsächlichen Kosten aus der Provider-Antwort
  (Usage). Eine Preisänderung des Modells wird nicht erkannt.
- **E4 Abbruch und Fortsetzung:** Nach einem Abbruch ist nicht festgelegt, wie ein Lauf ohne doppelte Aufrufe
  fortgesetzt würde.
- **E5 Freigaben:** C-1–C-17 und J1–J6 sind offen. Insbesondere fehlen Schwellen und Gewichte (`thresholds`/`weights`
  null), und die Angaben in der Konfiguration sind keine Signatur.

## 5 Schutzregeln

| Regel | Status |
|---|---|
| Presidio, Pseudonymisierung, Leak-Checks, Fail-Closed | nicht abgeschwächt, nur fail-closed erweitert |
| Echte Mandantendaten, Schlüssel | keine; nur der gefälschte Testschlüssel |
| Echte DB | `677CAFD8109F`, unverändert |
| QA-Artefakte, Sicherungen (einschließlich `…vor_bfbe53a`), die vier fremden Logo-/UX-Dateien | erhalten |
| Git | keine destruktiven Befehle, kein Merge nach `main` |
| QA-Kopien `lexono_qa_s79_ner_ausfall` und `…_alt` | erhalten (je 1,2 GB) und als QA-Kopien gekennzeichnet (`LIESMICH.txt` in der ersten); können nach Abnahme gelöscht werden |

## 6 QA

| Test | Ergebnis |
|---|---|
| Volle Suite in 6 Teilen | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |
| Neue bzw. separat geführte Testdateien | `test_s78_benchmark_gate`, `test_s78_wire_privacy`, `test_s79_privacy_check_failure`, `test_s79_benchmark_exec_guard`: 109 bestanden |
| Prozesse | keine Lexono- oder QA-Prozesse zurückgeblieben; jede QA-Instanz wurde nach Prüfung der PID und des Pfads regulär geschlossen |

## Nächste Schritte

1. Die Owner-Entscheidungen zu E1–E4 bauen, sobald C-14 freigegeben ist. Dabei die Wächter-Tests als Abnahme
   verwenden und die Leitungsprüfung vor dem ersten echten Aufruf erneut laufen lassen.
2. C-1–C-17 sowie J1–J6 entscheiden die zuständigen Personen.
3. Die QA-Kopien nach Abnahme löschen, freigegeben werden etwa 2,4 GB.
