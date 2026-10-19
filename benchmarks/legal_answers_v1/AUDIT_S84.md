# §84 Sicherheitsprüfung der Cloud-Sperre und Unterprozesse

Stand 19.10.2026, Ausgangsstand `9d28b4d` (§83, verifiziert: HEAD = Remote, Working Tree nur mit den 4 fremden
Logo-/UX-Dateien), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).

**Grenzen dieser Arbeit:**
- Es gab keine Modell- oder Cloud-Aufrufe, keine externen DNS-Abfragen, keinen echten API-Schlüssel, keine Kosten und
  keinen Zugriff auf die produktive Datenbank.
- **C-14 bleibt GESPERRT.** J1–J6, F-1, C-7 und C-11 sind unverändert offen.
- Es waren reine Backend- und Netzwerktests; die Anwendung wurde nicht geöffnet, deshalb gibt es Protokolle statt
  Screenshots: `audit/s84_angriffstests.txt` und `audit/s84_zaun_abdeckung_vorher.txt`.

## 1 Bedrohungsmodell und Schutzgrenzen

**Drei getrennte Schutzschichten:**

| Schicht | Ort | Wirkt gegen | Durchgesetzt oder Annahme |
|---|---|---|---|
| Providerprüfung | `answer_driver.assert_local_only` (vor dem Lauf) | erkennbare Cloud- und HTTP-Clients im übergebenen Provider (rekursiv, Unterklassen; §83) | **durchgesetzt** für die erkannten Bibliotheken, Suchtiefe 5 |
| Netzwerkschutz | `net_fence.loopback_only` (während der Ausführung), Audit-Hook | `connect`/`connect_ex`, roher `_socket`, `sendto`/`sendmsg`, DNS (`getaddrinfo`, `gethostbyname*`, `gethostbyaddr`, `getnameinfo`), asyncio-Proactor, ctypes-Netz-DLLs und -Funktionen; **alle Threads** des Prozesses | **durchgesetzt** im Python-Prozess, vor dem Systemaufruf |
| Prozessschutz | derselbe Audit-Hook | `subprocess`, `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `os.startfile`, `_winapi.CreateProcess` | **durchgesetzt**: Start nur für zugelassene Programme; das Netzwerkverhalten zugelassener Programme ist eine **Annahme** |

**Grenzen:**
- **Python-Prozess und Threads:** Ein Audit-Hook (`sys.addaudithook`) ist prozessweit; ab dem Einhängen lässt er sich
  nicht mehr entfernen und wirkt nur, solange der Zaun aktiv ist. Er gilt für alle Threads, auch für solche, die vor dem
  Zaun gestartet wurden.
- **Unterprozesse und externe Programme:** Der Netzwerkschutz wirkt **nicht** in Kindprozessen. Eine belastbare
  Betriebssystem-Isolation ist hier **nicht verfügbar**:
  - eine Firewall-Regel je Programm wäre eine globale Änderung und braucht Administratorrechte;
  - AppContainer, Windows Sandbox und Container sind weder eingerichtet noch im Projekt vorgesehen.

  Deshalb gilt **fail-closed**: Jeder Prozessstart wird verweigert, außer den Einträgen der Zulassungsliste
  `answer_driver.allowed_programs()`. Das ist `git` **nur** mit den exakten Argumenten `rev-parse HEAD` (Commit-Prüfung)
  und Tesseract (OCR beim Aktenimport). Dass diese beiden Programme nichts über das Netz senden, ist eine **Annahme**:
  für `git rev-parse` aus der Funktion des Befehls, für Tesseract aus seinem Zweck, nicht durch Isolation belegt. `icacls`
  (Ablage) läuft nur vor dem Zaun.
- **DNS:** Externe Namen werden **vor** der Abfrage verweigert, also im Audit-Ereignis `socket.getaddrinfo` vor dem
  C-Aufruf. Erlaubt sind nur `localhost` und Loopback-Literale.
- **Alternative HTTP-Bibliotheken:** `urllib`, `http.client`, `requests`/`urllib3`, `httpx` und asyncio enden alle in
  den abgefangenen Socket- bzw. DNS-Ereignissen; das ist getestet.
- **Bestehende Verbindungen:** Eine vor dem Zaun geöffnete Verbindung erzeugt kein neues `connect`. Beim Aktivieren
  prüft der Zaun deshalb per `netstat`, dass der Prozess keine TCP-Verbindung zu einem Nicht-Loopback-Ziel hält. Die
  Prüfung ist sprachunabhängig, denn `netstat` lokalisiert die Status („HERGESTELLT“). Kann nicht geprüft werden, wird
  nicht gestartet.
- **Nicht abgedeckt:**
  - C-Erweiterungen mit eigener Netzwerkbibliothek ohne Audit-Ereignis;
  - direkte `_overlapped.ConnectEx`-Aufrufe am asyncio-Proactor vorbei;
  - absichtliche Manipulation des Zaun-Moduls, etwa Zustand zurücksetzen oder Hook-Liste umgehen.

  Gegen absichtliche Manipulation des Codes schützt nur das Review (C-14: gesondert reviewte Code-Änderung).

## 2 Befunde

**Statische Probe vorher** (`audit/s84_zaun_abdeckung_vorher.txt`, ohne jeden Verbindungsversuch): Der §83-Zaun
ersetzte nur `socket.socket.connect`/`connect_ex` und `socket.create_connection`. **Nicht** abgefangen waren:
- DNS (`getaddrinfo`, `gethostbyname`);
- UDP (`sendto`);
- roher `_socket`;
- jeder Prozessstart (`subprocess`, `os.system`, `os.startfile`).

Folgen:
- **Q1:** `requests`/`urllib3` lösen den Hostnamen **vor** `connect` auf. Die externe DNS-Abfrage wäre hinausgegangen,
  bevor der Zaun die Verbindung stoppte. **FAIL**
- **Q2:** Ein roher C-Socket (`_socket.socket().connect`) und UDP-Versand liefen am Zaun vorbei. **FAIL**
- **Q3:** Unterprozesse und externe Programme konnten beliebig starten, auch mit eigenem Netzzugang. **FAIL**

Diese Pfade wurden mit dem alten Zaun bewusst **nicht** praktisch ausgeführt; das wäre eine echte externe DNS-Abfrage
bzw. Verbindung gewesen. Der Nachweis ist statisch.

**Behebung:** Der Zaun ist auf `sys.addaudithook` umgestellt, mit den Ereignissen aus Abschnitt 1. Dazu kommen
Zulassungsliste, Startprüfung offener Verbindungen und Wiederherstellung beim Beenden.

**Neu gefundene Befunde (durch die Angriffstests):**
- **P2:** Unter Windows baut asyncio Verbindungen über den Proactor auf (Overlapped `ConnectEx`/`WSASendTo`). Dieser Weg
  löst **kein** Audit-Ereignis aus. Der Test zeigte einen Timeout statt einer Sperre; das Ziel war nicht routbares
  TEST-NET, es wurde also nichts erreicht. **FAIL**, behoben durch Ummantelung von `IocpProactor.connect`/`sendto`
  während des Zauns, die beim Beenden zurückgesetzt wird.
- **P3:** Die erste Fassung der Startprüfung filterte nach dem englischen Status „ESTABLISHED“. Auf diesem
  deutschsprachigen System lautet er „HERGESTELLT“/„ABHÖREN“; bestehende Verbindungen wären übersehen worden
  (fail-open). Vor dem ersten Lauf erkannt und sprachunabhängig umgesetzt; getestet.

## 3 Angriffsmatrix

Sicherheit der Tests selbst:
1. Angriffe laufen nur bei aktivem Zaun.
2. Ziele sind ausschließlich nicht routbare Adressen (TEST-NET-1 192.0.2.0/24, RFC 5737) bzw. reservierte Namen
   (`.invalid`, RFC 2606).
3. Ein **zweiter, test-eigener Audit-Hook** bricht jede externe Verbindung bzw. Auflösung ab, die am Zaun vorbeikäme,
   und lässt den Test scheitern. Dass dieser Hook wirkt, ist eigens nachgewiesen: Ohne Zaun bricht er eine Auflösung vor
   der Abfrage ab.
4. Unterprozess-Angriffe weisen über eine Marker-Datei nach, dass kein Kindprozess startete.

| # | Angriffspfad | Schicht | Ergebnis | Nachweis (`tests/test_s84_network_process_fence.py`) |
|---|---|---|---|---|
| A1 | `socket.connect`, `connect_ex` | Netzwerk | **PASS** | `test_raw_and_c_level_sockets_are_blocked` |
| A2 | roher C-Socket `_socket.socket().connect` | Netzwerk | **PASS** (vorher FAIL, Q2) | dito |
| A3 | UDP `sendto` | Netzwerk | **PASS** (vorher FAIL, Q2) | `test_udp_send_is_blocked` |
| A4 | DNS `getaddrinfo`, `gethostbyname(_ex)`, `gethostbyaddr`, `create_connection(Hostname)` | Netzwerk | **PASS** (vorher FAIL, Q1) | `test_dns_resolution_of_external_names_is_blocked_before_any_query` |
| A5 | `urllib`, `http.client`, `requests` (Name und IP), `httpx` (Name, HTTPS-IP) | Netzwerk | **PASS** | `test_alternative_http_libraries_are_blocked` |
| A6 | asyncio TCP (Name, IP) und UDP über den Windows-Proactor | Netzwerk | **PASS** (vorher FAIL, P2) | `test_asyncio_connections_are_blocked`, `test_windows_proactor_is_fenced_and_restored` |
| A7 | Verbindung aus einem anderen Thread | Netzwerk | **PASS** | `test_threads_are_covered_process_wide` |
| A8 | ctypes: Netz-DLLs laden; Netzfunktion einer **vorab** geladenen DLL nachschlagen | Netzwerk | **PASS** | `test_native_network_apis_via_ctypes_are_blocked`, `test_symbol_lookup_on_preloaded_network_dll_is_blocked` |
| A9 | Unterprozess (`subprocess.run`, `shell=True`, `os.system`, `os.popen`, `os.spawnl`, `os.startfile`, `_winapi.CreateProcess`) | Prozess | **PASS** (vorher FAIL, Q3); kein Kindprozess gestartet | `test_subprocesses_are_blocked_before_they_start` |
| A10 | zugelassenes Programm mit anderen Argumenten (`git fetch`, `git rev-parse HEAD --remote`) | Prozess | **PASS** | `test_only_allowlisted_programs_with_exact_arguments_start` |
| A11 | zugelassenes Programm fehlt | Prozess | **PASS** (fail-closed beim Aktivieren) | `test_missing_allowlisted_program_fails_closed` |
| A12 | versteckter Unterprozess bzw. versteckter DNS- und HTTP-Zugriff in einem Provider während eines echten Treiberlaufs | Prozess/Netzwerk | **PASS**: Lauf gestoppt, kein Kindprozess | `test_driver_run_stops_hidden_subprocess_and_dns_access` |
| A13 | andere HTTP-Clients im Provider (`requests.Session`, `httpx.Client` extern bzw. ohne Endpunkt, verschachtelt) | Providerprüfung | **PASS** | `test_provider_check_refuses_other_http_clients` |
| A14 | Wrapper, Unterklasse, Liste, Dict, roher `anthropic`-Client | Providerprüfung | **PASS** (§83) | `tests/test_s83_pilot_readiness.py` |
| A15 | Aktivieren trotz bestehender externer Verbindung bzw. bei fehlgeschlagener Prüfung | Lebenszyklus | **PASS** (fail-closed) | `test_activation_fails_closed_on_existing_external_connection_or_check_error` |
| A16 | Startprüfung sprachunabhängig (deutsche `netstat`-Ausgabe) | Lebenszyklus | **PASS** (P3) | `test_existing_connection_parser_is_locale_independent`, `test_existing_connection_check_runs_on_this_machine` |
| A17 | Beenden nach Ausnahme, Verschachtelung, Wiederherstellung (Proactor, Prozesse wieder möglich) | Lebenszyklus | **PASS** | `test_fence_is_released_after_exceptions_and_nesting`, `test_windows_proactor_is_fenced_and_restored` |
| A18 | **Positiv:** Loopback-Recorder über Socket, `requests`, `urllib`; `localhost`-Auflösung; `git rev-parse HEAD`; `tesseract --version` | alle | **PASS** | `test_loopback_recorder_connections_stay_possible`, `test_only_allowlisted_programs_with_exact_arguments_start`, `test_tesseract_allowlist_matches_the_driver` |
| A19 | Netzverhalten zugelassener Programme (git, Tesseract) | Prozess | **NICHT VERIFIZIERT** (Annahme, keine OS-Isolation) | – |
| A20 | C-Erweiterungen mit eigenem Netzwerk-Stack, direkter `_overlapped.ConnectEx` | Netzwerk | **NICHT VERIFIZIERT** (nicht abgedeckt; nicht praktisch geprüft, weil es Pakete senden würde) | – |
| A21 | absichtliche Manipulation des Zaun-Moduls | – | **NICHT VERIFIZIERT** (nur Review) | – |

## Änderungen

| Datei | Änderung |
|---|---|
| `scripts/legal_bench/net_fence.py` | Audit-Hook statt Monkeypatch für Sockets; DNS, UDP, roher Socket, ctypes; Prozesszaun mit Zulassungsliste; Proactor-Ummantelung (P2); Startprüfung offener Verbindungen (sprachunabhängig, P3) |
| `scripts/legal_bench/answer_driver.py` | `allowed_programs()` (git rev-parse HEAD, Tesseract); Zaun mit Zulassungsliste |
| `tests/test_s84_network_process_fence.py` (neu) | 22 Angriffs-, Positiv- und Lebenszyklus-Tests mit Test-Wächter |
| `tests/test_s83_pilot_readiness.py` | Quelltext-Prüfung des Zauns an die neue Technik angepasst |
| `benchmarks/legal_answers_v1/audit/s84_*` (neu) | Protokolle |
| `PILOT_CHECKLISTE.md`, `README.md`, `PRIVACY_REVIEW_ARCHITECTURE.md`, `FUTURE_ROADMAP.md` | Verweise; T-8 präzisiert |

Nicht geändert wurden Spezifikationen, Rubrik, LOCK, Vorlage, Freigabegates und Freigabestatus, Datenschutzprüfungen und
App-Code.

## Tests

| Test | Ergebnis |
|---|---|
| `tests/test_s84_network_process_fence.py` (neu) | 22 bestanden (Protokoll `audit/s84_angriffstests.txt`) |
| §78–§84-Benchmarkdateien gemeinsam | 249 bestanden |
| Volle Suite in 6 Teilen | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |

## Restrisiken

- **Keine OS-Isolation:** Der Prozessschutz hängt an der Zulassungsliste; das Verhalten von git und Tesseract ist
  angenommen, nicht isoliert. Eine belastbare Isolation (AppContainer ohne Netzwerkberechtigung, Firewall-Regel je
  Programm, Container oder VM) wäre eine Betriebs- bzw. IT-Entscheidung und ist **nicht umgesetzt**.
- **Abdeckung des Netzzauns:** Er deckt Python-Laufzeit und Standard- bzw. verbreitete Bibliotheken ab. Native
  Erweiterungen mit eigenem Netzwerk-Stack oder direkte Overlapped-Aufrufe erfasst er nicht.
- **Startprüfung:** Sie stützt sich auf `netstat`, eine Windows-Systemfunktion. Auf anderen Plattformen bricht sie
  fail-closed ab.
- **Absichtliche Umgehung:** Jede Umgehung durch Codeänderung muss im Review auffallen. Dass C-14 gesperrt ist, wird
  allein durch Code und Prozess sichergestellt.
