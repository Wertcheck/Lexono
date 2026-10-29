# §83 Technische Pilotvorbereitung des juristischen Benchmarks

Stand 19.10.2026, Ausgangsstand `3e0f3c4` (§82, verifiziert: HEAD = Remote, Working Tree nur mit den 4 fremden
Logo-/UX-Dateien), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).

**Grenzen dieser Arbeit:**
- Es gab keine Modell- oder Cloud-Aufrufe, keinen API-Schlüssel (nur den gefälschten Testschlüssel), keine Kosten und
  keinen Zugriff auf die produktive Datenbank.
- Juristische Entscheidungen und Freigaben habe ich keine getroffen; eine juristische Fachperson wird erst im
  Pilotbetrieb eingebunden. **C-14 bleibt GESPERRT.**
- Es wurden nur Backend und Dokumentation geprüft; die Anwendung wurde nicht geöffnet, deshalb gibt es **keine
  Screenshots**.

| Prüfschritt | Ergebnis |
|---|---|
| Ausgangsstand verifiziert | **PASS** |
| Endpunktsperre: echter Provider direkt | **PASS** (schon vorher gesperrt) |
| Endpunktsperre: Wrapper, Unterklasse, Liste, roher Client | **FAIL in der §80/§81-Fassung (Befund P1)**, **PASS nach Behebung** |
| Netzzaun gegen versteckte Clients (neu) | **PASS** |
| Reproduzierbarkeit von Treiber, Ablage, Kostenbuch und Wiederanlauf | **PASS** (identisch zu §80) |
| Fixtures verändern keine Freigabedateien | **PASS** |
| Prüfpaket der Referenzlösungen | **PASS** (erstellt, synchron); juristische Richtigkeit **NICHT VERIFIZIERT** |
| Pilot-Checkliste | erstellt (`PILOT_CHECKLISTE.md`) |
| Verhalten gegenüber einem echten Modell | **NICHT VERIFIZIERT** (kein Lauf erlaubt) |
| Volle Suite | **PASS** bis auf die 4 bekannten, fremden Logo-Tests |

## 1 Technische Reife

**Befund P1 – Endpunktsperre umgehbar:** `answer_driver.assert_local_only` prüfte nur, ob die **Klasse des übergebenen
Objekts** aus `app.ai_providers` stammt (`audit/s83_endpunktsperre_vorher.txt`). Zugelassen wurden mit dem offiziellen
Endpunkt `api.anthropic.com`:
- ein eigener Wrapper mit dem echten Provider als Attribut;
- eine Unterklasse des echten Providers in einem anderen Modul;
- ein Wrapper mit dem Provider in einer Liste;
- ein roher `anthropic.Anthropic`-Client.

Ein echter Cloud-Aufruf wäre damit möglich gewesen, sobald der Treiber mit einem selbst gebauten Provider gestartet
wird. Bisher ist nichts passiert: Es gibt keine Kommandozeile und keinen Lauf, und die Probe hat Provider nur
konstruiert.

**Behebung, zweistufig in der bestehenden Architektur:**
1. **Objektprüfung** (`assert_local_only`): Der Provider wird rekursiv durchsucht (Attribute, Listen, Dicts,
   `__slots__`, Tiefe 5) nach Objekten aus `app.ai_providers`, `anthropic`, `httpx`, `httpcore`, `requests`, `urllib3`,
   `openai` oder `aiohttp`. Basisklassen zählen mit, Unterklassen werden also erkannt. Reine Datenobjekte wie
   `ClaudeWritingResult` zählen nicht. Jeder gefundene Client muss nachweislich auf Loopback zeigen und
   `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1` voraussetzen; ein Client **ohne erkennbaren Endpunkt** wird gesperrt.
2. **Netzzaun** (neu, `scripts/legal_bench/net_fence.py`): Während der Ausführung eines Laufs sind
   `socket.connect`/`connect_ex` und `create_connection` nur zu Loopback-Adressen erlaubt. Ein Hostname wie
   `api.anthropic.com` wird **vor** jeder DNS-Abfrage verweigert. Das fängt auch Clients in Closures oder globalen
   Variablen ab, die die Objektprüfung nicht sieht. Nach dem Lauf wird der Ausgangszustand wiederhergestellt.

Ergebnis: alle Varianten gesperrt (`audit/s83_endpunktsperre_nachher.txt`).

**Reproduzierbarkeit** (`audit/s83_reproduktion.json` gegen `audit/s80_treiber_nachweis.json`): Die §80-Nachweisläufe
wurden auf dem aktuellen Stand mit Netzzaun erneut ausgeführt.
- Mock-Lauf, Recorder-Lauf (echter Provider gegen 127.0.0.1) und Wiederanlauf liefern **identische** Zustände je
  Aufruf und identische Kostenbücher.
- Integrität OK; 6 HTTP-Anfragen nur an `/v1/messages` mit Testschlüssel, 0 Lecks, 0 doppelte Payloads.
- Der Reproduktionslauf setzt den juristischen Stand nur in seinem eigenen Prozess synthetisch, die Dateien bleiben
  offen. Die bestehenden §80-Nachweise wurden **nicht** überschrieben.

**Ohne Befund geprüft:**
- Datenschutzprüfungen (B1, Leitung): `test_s78_wire_privacy`, `test_s79_privacy_check_failure`;
- Freigabegates und Kostenregeln: §78/§81;
- Ablage und Klärung: §80/§81.

## 2 Freigaben und Sperren

**Echte Modellaufrufe sind verhindert durch:**
1. das Freigabegate: Mit dem heutigen Stand ist jede Konfiguration ein Widerspruch zu den offenen Spezifikationen;
2. die Endpunktsperre (Objektprüfung);
3. den Netzzaun;
4. das Fehlen jeder Kommandozeile bzw. jedes Startpunkts für einen Lauf.

Die Zeilen 1–3 sind getestet (`tests/test_s81_benchmark_hardening.py`, `tests/test_s83_pilot_readiness.py`,
`tests/test_s82_decision_matrix.py`).

**Fixtures:**
- `synthetic_legal_state` ersetzt nur die Funktion `answer_run.legal_state` im Speicher; das ist statisch geprüft
  (nur `monkeypatch.setattr`, kein Schreiben).
- Ein Test vergleicht Vorlage, Rubrik, LOCK und die drei Spezifikationen nach Anwendung der Fixture byte-genau mit dem
  Commit.

**Entscheidungsstand unverändert:** J1, J2, J6, F-1 und C-11 sind ENTSCHEIDUNG VORBEREITET; J3 und J4 OFFEN; J5 und C-7
NACHWEIS FEHLT (`entscheidungsmatrix.json`).

## 3 Pilotübergabe

- **`PILOT_CHECKLISTE.md` (neu):**
  - technische Voraussetzungen T-1–T-10 mit Nachweisen;
  - offene Entscheidungen J1–J6, F-1, C-7 und C-11 mit dem Status aus der Matrix;
  - Rollen, alle **noch zu benennen**;
  - die Reihenfolge der Voraussetzungen vor einem echten Lauf.
- **`pruefpaket/REFERENZLOESUNGEN_ZUR_PRUEFUNG.md` (neu):**
  - **Inhalt:** je Fall Quelle (Gericht, Aktenzeichen, ECLI, verwendeter Umfang), Rechtsfrage, erwartetes Ergebnis,
    Normen, aufgelöste entscheidende Tatsachen, Fristen, falsche Ergebnisse, offene Punkte, Herkunft (belegt vs.
    synthetisch), alle Kriterien mit Varianten, gefährliche Fehler mit Verdachtsformulierungen, Fehlentscheidungen und
    der Stand der Alternativergebnisse.
  - **Entscheidungsfelder:** je Fall und für J1, J2, J3 je ein **leeres** Feld (9 insgesamt).
  - **Kennzeichnung:** ausdrücklich „NICHT juristisch geprüft“.
  - **Herkunft:** erzeugt aus den Quelldateien durch `scripts/legal_bench/review_packet.py`, eine reine Ausgabe ohne
    Bewertung und ohne neues Prüfsystem. Ein Test stellt sicher, dass die Datei zu den Quelldateien passt.

## Änderungen

| Datei | Änderung |
|---|---|
| `scripts/legal_bench/answer_driver.py` | Objektprüfung in `assert_local_only` (P1); Ausführung im Netzzaun |
| `scripts/legal_bench/net_fence.py` (neu) | Netzzaun nur für Loopback, öffnet selbst keine Verbindung |
| `scripts/legal_bench/review_packet.py` (neu) | erzeugt das Prüfpaket aus den Quelldateien |
| `benchmarks/legal_answers_v1/pruefpaket/REFERENZLOESUNGEN_ZUR_PRUEFUNG.md` (neu) | Prüfpaket |
| `benchmarks/legal_answers_v1/PILOT_CHECKLISTE.md` (neu) | Pilot-Checkliste |
| `benchmarks/legal_answers_v1/audit/s83_*` (neu) | Endpunktsperre vorher/nachher, Reproduktion |
| `tests/test_s83_pilot_readiness.py` (neu) | 17 Tests |
| `README.md`, `PRIVACY_REVIEW_ARCHITECTURE.md`, `FUTURE_ROADMAP.md` | Verweise |

Nicht geändert wurden Spezifikationen, Rubrik, LOCK, Vorlage, Gates, Freigabestatus, Datenschutzprüfungen und App-Code.

## Tests

| Test | Ergebnis |
|---|---|
| `tests/test_s83_pilot_readiness.py` (neu) | 17 bestanden: 8 Varianten gesperrt, Loopback und Mocks zugelassen, Treiber lehnt einen umhüllten echten Provider vor jeder Ablage ab, Netzzaun inkl. Wiederherstellung, versteckter Client im Lauf gestoppt, Fixture ohne Dateiänderung, ohne Fixture nicht freigebbar, Prüfpaket synchron und ohne Entscheidung |
| Regression nach der Treiberänderung (§79–§82) | 112 bestanden |
| §78–§83-Benchmarkdateien gemeinsam | 227 bestanden |
| Volle Suite in 6 Teilen | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |

## Restrisiken

- **Netzzaun:** Er wirkt nur im Python-Prozess des Laufs. Unterprozesse mit eigener Netzwerkverbindung erfasst er
  nicht; im Lauf wird nur Tesseract ohne Netzwerk gestartet. Eine **absichtliche** Umgehung durch eigenen Code
  (z. B. Zaun abschalten) ist eine Code-Änderung, die das Review erkennen muss.
- **Objektprüfung:** Sie hat eine begrenzte Suchtiefe (5) und erkennt nur die genannten Bibliotheken. Für alles andere
  ist der Netzzaun zuständig.
- **Echte Modelle:** Usage, Preise, Rate-Limits und TLS sind **NICHT VERIFIZIERT**.
- **Referenzlösungen:** Sie sind weiterhin nicht juristisch geprüft. Das Prüfpaket macht sie nur prüfbar.
- **Benannte Personen:** Für keine Rolle ist eine Person benannt; ohne sie bleibt jeder Lauf ausgeschlossen.
