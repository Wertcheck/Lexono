# §78 Benchmark-Freigabe und Datenschutzpfad – adversariale Prüfung der technischen Schutzmechanismen

Stand 19.10.2026, Ausgangsstand `307cecf` (Branch `feature/feedback-diagnose`; `main` unverändert).

**Nur technische Absicherung.** Grundlage waren ausschließlich synthetische Daten, temporäre Kopien und ein lokaler
QA-Recorder mit gefälschtem Schlüssel. Es gab keine Modellantwort, keinen Cloud-Aufruf und keine Kosten. Dieser Bericht
trifft keine Aussage über Modellqualität, vollständige Datenschutzsicherheit oder juristische Zuverlässigkeit. J1–J6 und
C-1–C-17 bleiben **OFFEN**, eine juristische Freigabe wird nicht erteilt.

| Bereich | Ergebnis |
|---|---|
| 1 Freigabesperre (`answer_run check`) | **FAIL in der §77-Fassung** (15 von 18 Manipulationen wurden freigegeben), **PASS nach Härtung** |
| 2 Test-Split/LOCK.json | **FAIL in der §77-Fassung** (Lücken L1–L4), **PASS nach Härtung**; bewusste Fälschung von Lock *und* Historie ist eine dokumentierte Grenze |
| 3 Aufrufplan (`plan`) | **PASS** (reproduzierbar, versioniert, offline, ohne Freigabe nicht ausführbar) |
| 4 Datenschutzpfad bis zur HTTP-Leitung | **PASS** für die Akten LE-D005, LE-D006 und LE-T004 auf diesem Rechner; Befund B1 (Schriftsatzpfad) dokumentiert; spätere Benchmark-Ausführung **NICHT VERIFIZIERT** (existiert nicht) |
| 5 Schutzregeln | **PASS** (keine Abschwächung, Originale geprüft, keine produktive DB) |
| 6 Regression/Suite | **PASS** bis auf die 4 bekannten, fremden Logo-Tests |

## 1 Freigabesperre

**Vorgehen:** `answer_run.check` wurde mit einer synthetischen, formal gültigen Testkonfiguration als Positivkontrolle
geprüft. Anschließend wurde die Konfiguration gezielt manipuliert:
- Pflichtangaben gelöscht;
- `false`/„nein“ als Freigabe eingetragen;
- Unsinnswerte gesetzt;
- Kostenlimit und Abbruchgrenze entfernt oder auf null/0 gesetzt;
- Datenschutz abgeschwächt;
- Zusatzschalter, veralteter Commit und unbekannte Spezifikation eingetragen;
- eine Liste statt eines Objekts übergeben.

**Befund G1 (§77-Fassung) – FAIL:** `check` prüfte nur, ob Werte `null` waren. Ein **gelöschter** Schlüssel wurde als
erledigt behandelt, beliebige Werte als Freigabe angenommen (`audit/s78_freigabesperre_vorher.txt`). Freigegeben wurden
unter anderem:
- gelöschtes Kostenlimit;
- gelöschter `approvals`-Block;
- `approvals = false` bzw. „nein“;
- Temperatur 7;
- Wiederholungen −3;
- Kostenlimit 0;
- `max_calls` unter der Zahl der geplanten Aufrufe;
- unbekannter Anbieter;
- „TESTWERT“ in allen Feldern.

Da keine Ausführungsfunktion existiert, entstand kein realer Schaden. Die Sperre hätte einen späteren Lauf aber nicht
aufgehalten.

**Härtung** (`scripts/legal_bench/answer_run.py`):
- **Konfigurationsform:** Jeder Pfad der Vorlage ist Pflicht. Ein fehlender Pfad ist ein Verstoß („Weglassen ist keine
  Freigabe“). `null` gilt als OFFEN; ein ungültiger Wert ist ein Verstoß mit Regel. Ein unbekannter Schlüssel ist ein
  Verstoß („keine Zusatzschalter“).
- **Pflichtfelder:**
  - `schema_version`;
  - Freigaben jeweils mit Person, Rolle, Datum `JJJJ-MM-TT` und Entscheidung `freigegeben`;
  - `approved` ausschließlich als `true` (`false` = OFFEN, `"true"` = Verstoß).
- **Modell und Pipeline:**
  - Anbieter `claude` (wie in `_SUPPORTED_CLOUD_AI_PROVIDERS`);
  - Modell-ID `claude-…`;
  - Temperatur zwischen 0 und 1;
  - `max_tokens` zwischen 1 und 4000 (Grenze des Relays);
  - Commit als 40-stelliger Hash, der dem aktuellen `HEAD` entsprechen muss.
- **Umfang und Grenzen:**
  - Spezifikationen müssen bekannt sein, und ihr Split muss in `scope.splits` stehen.
  - Geplante Aufrufe ≤ `max_calls`.
  - Geschätzte Kosten ≤ `max_cost_eur`.
  - Abbruchgrenze ≥ 1.
- **Datenschutz und Bewertung:** Datenschutz- und Bewertungsfelder müssen exakt die Pflichtwerte haben.
- **Zum Schluss:** Validator der Spezifikationen einschließlich Sperre.
- **CLI:** Eine unlesbare Konfiguration führt zu Exit 2 mit dem Grund „Konfiguration nicht lesbar“, ohne Traceback. `plan`
  liefert Exit 2, solange der Plan nicht ausführbar ist.

**Ergebnis – PASS** (`audit/s78_freigabesperre_nachher.txt`):
- Alle 36 Manipulationen werden mit konkretem Grund abgelehnt.
- Die Positivkontrolle wird freigegeben.
- `ensure_runnable` wirft bei jeder abgelehnten Konfiguration `NotApproved`.
- Auch `plan` meldet für jede dieser Konfigurationen `ausfuehrbar: false`. Es gibt also keinen alternativen Aufrufweg.
- Die Vorlage selbst ergibt Exit 2 und 22 offene Punkte (`audit/s78_check_vorlage.txt`).

**Restrisiko:**
- Die Freigaben sind Angaben in einer JSON-Datei, keine kryptografische Signatur. Wer Personen und Daten einträgt,
  „besteht“ die Prüfung. Den organisatorischen Nachweis liefern Checkliste und Git-Historie.

## 2 Test-Split und LOCK.json

**Vorgehen:**
- Manipulationen ausschließlich in temporären Kopien von `legal_answers_v1` und `legal_e2e_v1`; `corpus.CORPUS_DIR`
  wurde dafür nur zur Laufzeit umgelenkt.
- Prüfung der Originale vorher und nachher per `sha256sum -c` (`audit/s78_originale_vorher.sha256`,
  `audit/s78_originale_nachher.sha256`).

**Lücken der §77-Fassung – FAIL** (`audit/s78_sperre_vorher.txt`):
- **L1:** Ungültiges JSON in LOCK.json führte zum Absturz statt zu einer Meldung.
- **L2:** Eine geänderte Testakte fiel nicht auf, wenn zugleich der Eintrag in LOCK.json von Hand angepasst wurde.
- **L3:** Die Historie hielt nur die geänderten Pfade fest, nicht alte und neue Hashes.
- **L4:** Die Registereinträge der genutzten Quellen waren nicht gesperrt, zum Beispiel ein auf `nicht_geprueft`
  gesetztes Nutzungsrecht.

**Härtung** (`scripts/legal_bench/answer_specs.py`):
- **Prüfung der Sperrdatei:** Ungültiges oder leeres LOCK.json und fehlende Historie werden gemeldet. Jeder
  Historieneintrag trägt `entries_sha256`; passen die Einträge nicht dazu, lautet die Meldung „Handbearbeitung ohne
  --update-lock?“.
- **Gesperrte Quellen:** Die Registereinträge der Quellen, auf die Test-Spezifikationen bzw. Test-Akten verweisen, sind
  mitgesperrt (`source_register.json#<ID>`). Ein entfernter Eintrag wird als „FEHLT“ gemeldet.
- **Neusperre:**
  - nur mit Begründung von mindestens 20 Zeichen;
  - die Historie dokumentiert je Änderung `{pfad, alt, neu}`;
  - eine ungültige vorhandene Sperre wird nicht überschrieben.

**Ergebnis – PASS** (`audit/s78_sperre_nachher.txt`, Tests in `tests/test_s78_benchmark_gate.py`). Erkannt werden:

| Kategorie | Erkannte Manipulationen |
|---|---|
| Testakten | geändert, gelöscht, zusätzlich |
| Test-Spezifikation | geändert, gelöscht, zusätzlich |
| Rubrik | Schwellen gesetzt |
| Quellenregister | Nutzungsrecht entzogen, Eintrag entfernt |
| LOCK.json | ungültig, leer, als Liste, fehlend, Historie leer, Hash von Hand angepasst |

Neusperren ohne Begründung, mit Leerzeichen, mit 7 Zeichen oder mit `None` werden abgelehnt; die Datei bleibt dabei
byte-gleich.

**Dokumentierte Grenzen:**
- Wer Datei, Lock-Eintrag **und** Historien-Hash gemeinsam fälscht, bleibt unerkannt. Gegen absichtliche Manipulation
  schützt nur die Git-Historie; das steht auch in `LOCK.json.purpose` und ist durch einen eigenen Test festgehalten.
- Registereinträge, die kein Test nutzt, sind nicht gesperrt.

**Bewusste Neusperre im Repository:**

| | |
|---|---|
| Begründung | „§78: Lock-Format erweitert (entries_sha256, alt/neu-Hashes, Registereintraege der Test-Quellen); Test-Akten, Test-Spezifikation und Rubrik unveraendert“ |
| Änderungen laut Historie | nur `source_register.json#Q-GESETZE-IM-INTERNET` (alt `null` → neu `9d1e769f…`) und `#Q-RECHTSPRECHUNG-IM-INTERNET` (alt `null` → neu `866478ef…`) |
| Bisherige Einträge | alle 6 bisherigen Einträge byte-gleich (Testakten LE-T001…T004, `LA-T004.json`, `rubric.json`) |
| LOCK.json | `e864b2a7…` → `cb34cd35…` (alte Fassung: `audit/s78_LOCK_vor_neusperre.json`) |
| `entries_sha256` | `3186acca…` |

**Originale:**
- Rubrik, Spezifikationen, Test- und Dev-Akten und `source_register.json`: 11 von 11 byte-gleich (`sha256sum -c`).
- Bewusst geändert wurden nur LOCK.json (Neusperre) und die Vorlage `run_config.template.json`. Die Vorlage hat nur die
  neue Zeile `"schema_version": 1` erhalten: `dc7ba2d2…` → `86ab5ffd…`.
- Test-Split, Referenzen und Schwellen sind unverändert.

## 3 Aufrufplan

**PASS:**
- **Reproduzierbar:** `plan` ist deterministisch; zwei Läufe liefern dasselbe Ergebnis.
- **Versioniert:** Der Plan trägt `plan_fingerprint` (SHA-256 über Aufrufe, Commit, Rubrik-Fingerabdruck und Lock-Hash)
  sowie `git_commit`, `lock_sha256` und je Aufruf die Fingerabdrücke von Akte und Spezifikation.
- **Aufrufzahl:**
  - Vorlage: 6 Aufrufe (3 Spezifikationen × 2 Aufgaben).
  - 3 Wiederholungen: 18 eindeutige Aufrufe, anderer Fingerabdruck.
  - Nur `dev`: kein Test-Split.
- **Offline:** Während `plan`/`check` sind `socket.connect`, `create_connection` und `getaddrinfo` gesperrt, und die Tests
  laufen trotzdem durch.
- **Keine Cloud-Bibliotheken:** In einem frischen Prozess lädt der Import von `answer_run`/`answer_specs` weder
  `anthropic`, `httpx`, `requests`, `urllib3`, `app.ai_providers` noch `app.privacy`.
- **Lokale Ausnahme:** Einziger externer Prozess ist `git rev-parse HEAD`, ein lokaler Aufruf.
- **Ohne gültige Freigabe nicht ausführbar:** Ohne gültige Freigabe ist `ausfuehrbar = false` mit Gründen, und die CLI
  liefert Exit 2. Das gilt für die Vorlage (`audit/s78_plan_vorlage.json`, `plan_fingerprint 1659dc27…` auf dem
  Arbeitsstand von `307cecf`) und für jede einzelne fehlende Freigabe.
- **Keine Ausführungsfunktion:** Es gibt kein `execute`, `run`, `run_benchmark`, `call_model` oder `send`; per Test
  geprüft.

## 4 Datenschutzpfad

**Analyse:** Ein späterer Lauf müsste über den regulären Lexono-Pfad gehen (C-14, nicht implementiert):

```
synthetische Akte
  → Import und Verarbeitung
  → ChatService bzw. DraftingService
  → ClaudePrivacyGateway (Presidio, Pseudonymisierung, Siegel)
  → Anthropic-Writer
  → HTTP
```

Der Anthropic-Writer akzeptiert nur versiegelte Payloads (`require_gateway_seal`), und für Loopback-Endpunkte gilt die
Endpunkt-Politik. Dass dieser Pfad zwingend ist, zeigen vier Belege:
- `pipeline.path` muss `lexono_chat_pseudonymisiert` sein, sonst greift die Sperre.
- Die Benchmark-Module enthalten keinen Cloud-Client.
- Eine unversiegelte oder veränderte Payload erreicht das Netz nicht (`tests/test_privacy_wire_payload.py`).
- Die Datenschutz-Pflichtwerte lassen sich nicht abschwächen.

**Nachweis auf der Leitung – PASS** (`tests/test_s78_wire_privacy.py`, `audit/s78_wire_nachweis.json`):
- **Aufbau:**
  - Die Akten LE-D005, LE-D006 und LE-T004 laufen in **einer** Datenbank durch die echte Pipeline.
  - Der Writer reicht jeden Payload an den **echten** Anthropic-Provider aus der echten Factory weiter (Siegelprüfung,
    Endpunkt-Politik, SDK).
  - Ziel ist ausschließlich der lokale Recorder auf 127.0.0.1, mit QA-Freigabe und totem Proxy als Riegel.
- **Umfang:** 7 HTTP-Anfragen (6 Aufgaben und eine Zusatzprüfung der Pipeline: fremder Anhang bzw. Freigabe), 7 weitergeleitete Payloads.
  SHA-256 und Größe je Anfrage stehen im Nachweis.
- **Keine Lecks:**
  - Kein geschützter Wert, einschließlich Nachnamen und Datumsvarianten, **irgendeiner** der drei Akten erscheint in den
    gesendeten Bytes.
  - Es gibt keine Werte fremder Akten.
  - Platzhalter sind vorhanden.
- **Gegenprobe:** Die geschützten Werte stehen tatsächlich in den Eingangsdokumenten (13/13, 18/31 bzw. 10/15 als
  wörtliche Zeichenkette; der Rest sind Varianten wie Nachnamen und Datumsformen).
- **Schlüssel und Endpunkt:** Gesendet wird nur `x-api-key` = Testschlüssel, kein `authorization`-Header, nur
  `/v1/messages`.
- **Zusammenfassung der Pipeline:** `kritisch` [], `pii_leaks` 0, `fremde_akte_im_payload` 0 (dev und test).

**Fail-Closed – PASS mit Befund B1:** Fällt die Namenserkennung aus (`detect_presidio_entities` wirft einen Fehler),
erreicht **keine** Anfrage die Leitung (7 → 7).
- **Chat:** Er blockiert geordnet: „… Die Anfrage wurde nicht an die Cloud-KI gesendet“.
- **Befund B1:** Der Schriftsatzpfad (`DraftingService.create_draft`) blockiert **nicht geordnet**, sondern reicht die
  Ausnahme durch. Er bleibt fail-closed, liefert aber keinen Blockierungsgrund.
  - Das betrifft App-Code; die Änderung wurde in §78 bewusst nicht vorgenommen.
  - Der Test hält das aktuelle Verhalten fest.
  - Nächster Schritt: siehe unten.

**NICHT VERIFIZIERT:**
- TLS und der reale Anthropic-Endpunkt;
- Relay- bzw. Gateway-Server gegen einen realen Server;
- die spätere Benchmark-Ausführung (C-14), die es nicht gibt;
- die Vollständigkeit der Erkennung jenseits dieser synthetischen Akten;
- die Freigabe-Angaben als echte Personenentscheidung.

Der Datenschutzstatus bleibt **teilweise nachgewiesen**.

## 5 Schutzregeln

| Regel | Status |
|---|---|
| Presidio, Pseudonymisierung, Leak-Checks und Fail-Closed | unverändert; nur zur Prüfung beobachtet bzw. per Monkeypatch in Tests gestört |
| Modellantworten, produktive Cloud-Aufrufe, Kosten | keine |
| API-Schlüssel | nur der gefälschte Testschlüssel |
| Produktive DB (`C:\ProgramData\Lexono\data\kanzlei_ai.db`) | nicht angefasst; alle Läufe in `sqlite:///:memory:` |
| QA-Daten, Backups, Originalreferenzen, fremde Logo-/UX-Dateien | erhalten |
| Git | keine destruktiven Befehle, kein Merge nach `main` |

## 6 QA

| Test | Ergebnis |
|---|---|
| `tests/test_s78_benchmark_gate.py` und `tests/test_s78_wire_privacy.py` (neu) | 84 Tests, alle bestanden |
| Benchmark- und Wire-Tests gemeinsam (§77, Spezifikationen, `test_privacy_wire_payload`) | 143 bestanden |
| `test_s77_answer_benchmark_audit.py` | angepasst: „TESTWERT“ überall wird jetzt **abgelehnt**, statt wie vorher fälschlich freigegeben zu werden |
| Volle Suite in 6 Teilen (ohne die 84 neuen Tests) | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |

## Nächste Schritte (Owner/Technik)

1. **B1:** Den Schriftsatzpfad bei Ausfall der Namenserkennung geordnet blockieren, analog zum Chat. Das ist eine
   App-Änderung mit Build, Installation und App-Test.
2. **C-14:** Eine Ausführung nur über den hier geprüften Pfad bauen, mit `ensure_runnable` als erster Anweisung und
   Payload-Erfassung je Aufruf. Diese Wire-Prüfung sollte vor dem ersten echten Aufruf erneut laufen.
3. Die Freigaben C-1–C-17 sowie J1–J6 entscheiden die zuständigen Personen.
4. Nach Freigabe der Schwellen bewusst neu sperren (`--update-lock --reason …`).
