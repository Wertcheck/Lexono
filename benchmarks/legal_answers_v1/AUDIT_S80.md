# §80 Benchmark-Treiber, sichere Antwortablage, Kostenkontrolle und Wiederanlauf (E1–E4)

Stand 19.10.2026, Ausgangsstand `0eba65c` (§79), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).

**Grenzen dieser Prüfung:**
- Es gab keinen Modelllauf, keinen Cloud-Aufruf und keine Kosten. **Tatsächliche Kosten: 0 EUR.**
- Verwendet wurden nur synthetische Akten des Korpus, Mock-Provider und der lokale Aufzeichnungsserver: echter
  Anthropic-Provider gegen 127.0.0.1, toter Proxy, gefälschter Schlüssel.
- Die Kostenwerte im Kostenbuch sind **simulierte** Usage-Metadaten der Mock-Provider bzw. des Recorders. Abgerechnet
  wurde nichts.
- Dieser Bericht gibt keine Datenschutzgarantie und trifft keine Aussage über juristische Zuverlässigkeit.
- J1–J6 und C-1–C-17 bleiben **OFFEN**. Die Testkonfigurationen sind synthetisch, liegen nur in `tmp_path` und sind
  **keine** Freigabe.

| Bereich | Ergebnis |
|---|---|
| E1 Orchestrierung: Treiber über den regulären Lexono-Pfad, Chat und Schriftsatz getrennt | **PASS** (Mock und lokaler Recorder) |
| E2 Sichere Ablage: außerhalb des Repositorys, ACL, Hashes, Status, Löschung | **PASS** |
| E3 Kostenkontrolle: geschätzt vs. tatsächlich, unbekannte Kosten, Budget | **PASS** (simulierte Metadaten) |
| E4 Abbruch und Fortsetzung: Absturz, nur fehlende Aufrufe, Stopp bei Änderungen | **PASS** |
| Fail-Closed ohne Provideranfrage | **PASS** |
| Echter Modelllauf | **gesperrt** (`RealCloudDisabled`); C-14 und alle Freigaben offen – **NICHT VERIFIZIERT** und nicht möglich |
| Volle Suite | **PASS** bis auf die 4 bekannten, fremden Logo-Tests |

## Bausteine

| Modul | Aufgabe |
|---|---|
| `scripts/legal_bench/answer_exec.py` (§79, erweitert) | `CallGuard.precheck` vor dem Workflow und `authorize` unmittelbar vor dem Provider; Kostenbuch; `restore` für den Wiederanlauf; `GuardedWriter` hält fest, was tatsächlich geschah |
| `scripts/legal_bench/answer_store.py` (neu) | `RunStore`: Ablage, Integrität, Zugriffsrechte, kontrollierte Löschung |
| `scripts/legal_bench/answer_driver.py` (neu) | `BenchmarkDriver`: Import, Ausführung je geplantem Aufruf, Zustand, Wiederanlauf, Sperre echter Endpunkte |
| `answer_run.py` und `run_config.template.json` | Schema **2**: zusätzlich `limits.price_eur_per_million_input_tokens` und `…_output_tokens`, in der Vorlage **null** (Owner-Entscheidung, C-7) |

Keines der Module hat eine Kommandozeile oder baut einen Provider. Keines importiert `anthropic`, `httpx`, `requests`,
`socket`, die Provider-Factory oder `app.db.session`; das ist statisch geprüft.

## E1 – Orchestrierung

**Ablauf:**
- Der Treiber importiert alle Akten des Plans in **eine** In-Memory-Datenbank, also nie in die produktive DB. Die
  Akten werden wie im E2E-Benchmark gerendert, eingelesen, verarbeitet (OCR) und indexiert; die Mandantentrennung gilt
  innerhalb dieser einen DB.
- Je geplantem Aufruf in Planreihenfolge läuft:
  1. `precheck` mit allen Prüfungen: Freigabe über `ensure_runnable`, Commit = HEAD, LOCK und Test-Split,
     Datenschutzwerte, Konfigurations-Hash, Plan-Fingerabdruck, „geplant und nicht gesendet“, `max_calls`, Budget,
     unbekannte Kosten, Fehlversuche, frühere Datenschutzbefunde.
  2. Zustand `in_bearbeitung`.
  3. Regulärer **Chat** (`ChatService.send_message`) bzw. **Schriftsatz** (`DraftingService.create_draft`).
  4. Gateway: Presidio, Pseudonymisierung, Siegel.
  5. `GuardedWriter`, `authorize` mit Siegel und Leak-Check aller Akten, dann Provider.
- Nur geplante Aufrufe sind zulässig: Jedes `begin()` erlaubt genau einen Versand, und ein bereits gesendeter Aufruf
  wird nie erneut zugelassen.

**Belege:**
- **Mock, alle drei Spezifikationen:** 6 von 6 `erledigt` (Chat Q1, Schriftsatz Q2), 6 Provider-Aufrufe.
- **Recorder, echter Provider:** 6 HTTP-Anfragen, nur `/v1/messages`, nur der Testschlüssel, **0 Lecks** eigener oder
  fremder Akten (`audit/s80_treiber_nachweis.json`).

**Fail-Closed ohne Provideranfrage** (Tests, jeweils 0 Anfragen):
- Konfiguration nicht freigegeben: Es wird keine Ablage angelegt, und die Gründe werden genannt.
- Ausfall der Namenserkennung: Status `blockiert`.
- Datenschutzbefund im Payload: `abgebrochen_vor_versand`.
- LOCK verletzt: beim Start, und während des Laufs vor dem nächsten Aufruf.
- Echter Endpunkt: `RealCloudDisabled`.

**Sperre echter Läufe:** Ein Provider aus `app.ai_providers` wird nur gegen 127.0.0.1/localhost zugelassen, und nur
mit `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`. Mit gesetzter `LEXONO_GATEWAY_URL` ist jeder Provider gesperrt. Ein echter
Lauf erfordert eine bewusste Code-Änderung nach Freigabe von C-14.

## E2 – Sichere Antwortablage

**Ort:**
- Die Basis ist frei wählbar, wird aber **abgelehnt**, wenn sie im oder über dem Repository liegt oder in
  `%PROGRAMDATA%\Lexono`, `%LOCALAPPDATA%\Lexono` bzw. `LEXONO_DATA_DIR`.
- Nachweisläufe lagen unter `%CLAUDE_JOB_DIR%\tmp\s80\ablage` (außerhalb des Repositorys); Tests nutzen `tmp_path`.
- `git status` ist vor und nach einem Lauf identisch, das ist getestet.

**Aufbau je Lauf:**
- `<basis>/<run_id>/.lexono_benchmark_run` als Markierung;
- `state.json` mit Start-Fingerabdrücken, Status je Aufruf, Kostenbuch, Ereignissen und `state_sha256`;
- `calls/<call_id>.json` mit `record_sha256`.
- Die IDs sind stabil: Lauf `run_id`, Aufruf `<Spezifikation>.<Aufgabe>.r<Wiederholung>`, Antwort
  `<run_id>:<call_id>`.
- Zeitstempel in UTC; Schreibvorgänge atomar (temporäre Datei + `replace`); Datensätze werden nie überschrieben.

**Inhalt (Datensparsamkeit):**
- **Antworttext:** die **pseudonymisierte** Modellausgabe. Von der lokal re-identifizierten Fassung bleibt nur der
  Hash (`antwort_lokal_sha256`). Keine Spezifikation braucht geschützte Werte; das ist geprüft.
- **Weitere Inhalte:** die pseudonymisierte Payload (`privacy.payload_capture`), Usage, Kosten und Hashes.
- **Kontrollen:** Vor dem Ablegen wird die Antwort auf geschützte Werte geprüft. Bei einem Treffer wird kein Text
  abgelegt, und der Lauf stoppt. Blockiergründe werden nur als inhaltsfreie Kategorie abgelegt.
- **Nicht abgelegt:** Schlüssel, Header und Konfigurationsinhalt (davon nur der Hash).
- **Prüfung:** In allen Dateien der Nachweisläufe stehen 0 geschützte Werte und kein Schlüssel.

**Zugriff:**
- Windows: `icacls /inheritance:r` und Vollzugriff nur für das ausführende Konto (sonst 0700/0600).
- Getestet für Basis, Laufverzeichnis, Zustand und Datensatz: genau 1 Konto.

**Integrität und Löschung:**
- **Integrität:** `verify` meldet veränderte Datensätze und Zustände, unbekannte, fehlende und halb geschriebene
  Dateien. Eine beschädigte Ablage stoppt den Lauf, ohne Anfrage (getestet für 4 Schadensarten).
- **Löschung:** nur mit Bestätigung = Lauf-ID, nur markierte Verzeichnisse innerhalb der Basis und nicht während eines
  laufenden Aufrufs (getestet).

## E3 – Kostenkontrolle

- **Getrennte Posten:** `geschaetzt_eur` = Summe der Schätzung je gesendetem Aufruf. `tatsaechlich_eur` stammt **nur**
  aus Provider-Usage (`input_tokens`/`output_tokens`) × freigegebenem Tokenpreis. Die API liefert keine Euro-Beträge;
  der Tokenpreis ist Owner-Entscheidung (C-7).
- **Vor jedem Aufruf:**
  - Restbudget = `max_cost_eur` − tatsächliche Kosten; es muss die Schätzung des nächsten Aufrufs decken.
  - Auch die reine Schätzsumme muss ≤ `max_cost_eur` sein.
  - Außerdem gelten `max_calls` und `abort_after_failed_calls`.
- **Sichere Abbruchregel:**
  - Fehlen die Usage-Metadaten, gilt der Aufruf als „UNBEKANNT“, und danach ist kein weiterer Aufruf möglich, auch
    nicht nach einem Wiederanlauf.
  - Ein Providerfehler oder Timeout gilt als Fehlversuch mit unbekannten Kosten, weil unklar ist, ob abgerechnet wurde.
- **Belege (simuliert):**
  - Mock: 6 × 0,0105 EUR = 0,063 EUR „tatsächlich“ bei 3,00 EUR geschätzt; Restbudget 9,937 EUR.
  - Budgetüberschreitung durch hohe Usage: Stopp vor dem 2. Aufruf mit „Restbudget …“.
  - Fehlende Metadaten: Stopp nach dem 1. Aufruf.

## E4 – Abbruch und Fortsetzung

- **Zustand:** Er wird vor und nach jedem Aufruf dauerhaft gespeichert. Mögliche Zustände: `in_bearbeitung`,
  `erledigt`, `antwort_verworfen`, `fehlgeschlagen`, `blockiert`, `abgebrochen_vor_versand`,
  `datenschutzbefund_antwort`.
- **Absturz zwischen Aufrufen:** Im Nachweis brach der Lauf nach 3 erledigten Aufrufen ab. Beim Wiederanlauf liefen nur
  die 3 fehlenden, **0 doppelte Payloads**. Ereignisse: gestartet → fortgesetzt → abgeschlossen. Das Kostenbuch wird
  fortgeführt, und ein erneuter Start nach Abschluss sendet nichts.
- **Absturz während eines Aufrufs:** Der Aufruf bleibt `in_bearbeitung`. Er wurde möglicherweise gesendet, die Kosten
  sind unbekannt. Er wird **nie** automatisch wiederholt; der Wiederanlauf stoppt mit `gestoppt_unklarer_aufruf` und
  der Diagnose zur Klärung durch die zuständige Person.
- **Wiederanlauf nach Änderungen:** Er stoppt mit Diagnose bei geänderter Konfiguration (Hash), bei neuem Commit
  („lexono_commit: veraltet“), bei widerrufener Freigabe, bei verletzter Sperre (LOCK) und bei geändertem Plan. Der
  Wächter prüft beim Wiederanlauf alles neu; nichts wird übersprungen.
- **Gesperrte Referenzen:** Der Treiber liest Akten, Spezifikationen und Rubrik nur. `git status` bleibt unverändert,
  und LOCK ist gültig.

## Tests

| Test | Ergebnis |
|---|---|
| `tests/test_s80_benchmark_driver.py` | 30 bestanden, darunter: Erfolg, Leitung mit allen 6 Aufrufen, keine Freigabe, echter Endpunkt, fehlende Kostenmetadaten, Budget, Providerfehler/Timeout, Ausfall der Namenserkennung, Befund im Payload, Absturz zwischen/während Aufrufen, Änderungen (Konfiguration, Commit, Freigabe), LOCK beim Start und im Lauf, 4 Schadensarten, Ablageort, ACL, Löschung, kein Überschreiben, Repository/DB, Datensparsamkeit, statischer Schutz |
| Angepasst | `tests/test_s78_benchmark_gate.py`: gültige Testkonfiguration mit Tokenpreisen; neue Fälle „Schema-Version 1“, „Tokenpreis fehlt/0“ |
| §78–§80-Dateien gesamt | 142 bestanden |
| Volle Suite in 6 Teilen | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |

## Offene Risiken und Grenzen

- **Freigaben:** Die Freigabe-Angaben sind Einträge in JSON, keine Signatur. C-1–C-17 und J1–J6 sind offen; ohne sie
  verweigert der Treiber jeden Start.
- **NICHT VERIFIZIERT:** Usage-Metadaten und Preise eines echten Modells; TLS und realer Endpunkt; Verhalten bei
  echten Rate-Limits.
- **Zeitüberschreitungen:** Der Treiber setzt keine eigene Zeitgrenze, sie kommt vom Provider oder SDK. Ein
  Timeout-Fehler wird als Fehlversuch mit unbekannten Kosten behandelt.
- **Unklarer Aufruf:** Für einen nach dem Absturz unklaren Aufruf (`in_bearbeitung`) gibt es keine Klärungsfunktion.
  Eine Entscheidung (abrechnen/verwerfen) wäre ein eigener, freizugebender Schritt.
- **Ablage:** Sie schützt über Konto-ACL und Hashes gegen versehentliche Änderung und fremde Konten, nicht gegen einen
  Administrator oder das eigene Konto mit Absicht. Eine Verschlüsselung im Ruhezustand ist nicht umgesetzt; Aufbewahrung
  und Ort sind Owner-Entscheidungen (C-11).
- **Kosten:** Die tatsächlichen Kosten werden aus Tokens × freigegebenem Preis berechnet; eine Rechnung des Anbieters
  kann davon abweichen (z. B. durch Rundung oder Caching-Rabatte).
