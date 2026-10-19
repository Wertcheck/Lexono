# Pilot-Checkliste juristischer Benchmark (`legal_answers_v1`)

Stand §83, 19.10.2026. Diese Liste ist die **kompakte Übergabe an den Pilotbetrieb**. Sie ersetzt keine Entscheidung
und erteilt keine Freigabe. Ausführlich: `FREIGABE_CHECKLISTE.md` (C-1–C-17, F-1), `ENTSCHEIDUNGSMATRIX.md` (Optionen
je Punkt), `AUDIT_S77.md`–`AUDIT_S83.md`.

**Heute gilt:** Ein echter Modellbenchmark ist **nicht möglich**, und das ist gewollt. C-14 ist **GESPERRT**. Jede
Konfiguration wird abgelehnt, solange die juristischen Punkte offen sind.

## 1 Technische Voraussetzungen (vorhanden, mit Nachweis)

| # | Voraussetzung | Nachweis | Status |
|---|---|---|---|
| T-1 | Antworten nur über den regulären, pseudonymisierten Lexono-Pfad (Gateway, Siegel, Leak-Check) | `tests/test_s79_benchmark_exec_guard.py`, `tests/test_s80_benchmark_driver.py`, `audit/s80_treiber_nachweis.json` | PASS |
| T-2 | Fail-Closed: bei Ausfall der Namenserkennung keine Anfrage (App und Repository) | `AUDIT_S79.md`, `tests/test_s79_privacy_check_failure.py` | PASS |
| T-3 | Freigabegate: fehlende, veraltete, planfremde oder widersprüchliche Freigaben werden abgelehnt | `AUDIT_S81.md`, `tests/test_s81_benchmark_hardening.py` | PASS |
| T-4 | Prüfung vor **jedem** Aufruf (Freigabe, Commit, LOCK, Plan, Budget, Datenschutz) | `scripts/legal_bench/answer_exec.py`, Tests §79–§81 | PASS |
| T-5 | Ablage außerhalb von Repository und Lexono-Daten, Konto-ACL, Hashes, Löschung | `scripts/legal_bench/answer_store.py`, Tests §80/§81 | PASS (ohne Verschlüsselung, siehe C-11) |
| T-6 | Kostenbuch: geschätzt getrennt von tatsächlich; unbekannte Kosten stoppen | `AUDIT_S80.md`, `AUDIT_S81.md` | PASS (Preise fehlen, siehe C-7) |
| T-7 | Wiederanlauf ohne Doppelaufrufe; Klärung unklarer Aufrufe | `AUDIT_S81.md`, `audit/s83_reproduktion.json` | PASS |
| T-8 | Sperre echter Endpunkte: Providerprüfung (§83), Netzzaun über Audit-Hook inkl. Loopback nur zum validierten Prüfendpunkt (§84/§85), Prozesszaun: nur Tesseract mit festgeschriebenem Pfad, Hash, engem Argumentmuster; git nicht mehr gestartet (Commit ohne Prozess) | `AUDIT_S84.md`, `AUDIT_S85.md`, `tests/test_s84_network_process_fence.py`, `tests/test_s85_isolation_boundaries.py` | PASS als Schutzschicht im Python-Prozess – **keine** Sicherheitsgrenze gegen nativen Code (nachgewiesen) und keine OS-Isolation: NICHT VERIFIZIERT |
| T-9 | Reproduzierbarkeit der Testläufe (Mock, lokaler Recorder) | `audit/s83_reproduktion.json` gegen `audit/s80_treiber_nachweis.json` | PASS |
| T-10 | Verhalten gegenüber einem echten Modell (Usage, Preise, Rate-Limits, TLS) | – | **NICHT VERIFIZIERT** (kein Lauf erlaubt) |

## 2 Offene juristische Entscheidungen (J1–J6)

Die Prüfunterlage dafür ist `pruefpaket/REFERENZLOESUNGEN_ZUR_PRUEFUNG.md`. Sie enthält die Referenzlösungen,
Kriterien, Varianten und gefährlichen Fehler je Fall mit leeren Entscheidungsfeldern und ist **nicht juristisch
geprüft**.

| # | Entscheidung | Status (Matrix) |
|---|---|---|
| J1 | Referenzlösungen der drei Fälle zutreffend? | ENTSCHEIDUNG VORBEREITET |
| J2 | Antwortvarianten (76) und Verdachtsformulierungen (6 gefährliche Fehler) | ENTSCHEIDUNG VORBEREITET |
| J3 | Vertretbare Alternativergebnisse oder ausdrücklich keine | OFFEN |
| J4 | Gewichte und PASS/FAIL-Schwellen | OFFEN |
| J5 | Wer bewertet, Zweitprüfung, Umgang mit Abweichungen | NACHWEIS FEHLT |
| J6 | Regeln zu n/a und Unentscheidbarem bestätigen (keinem Freigabefeld zugeordnet, W2) | ENTSCHEIDUNG VORBEREITET |

## 3 Offene organisatorische Entscheidungen

| # | Entscheidung | Status (Matrix) |
|---|---|---|
| F-1 | Nachweis, wer freigegeben hat (Signatur, externer Nachweis oder bewusster Verzicht); Vier-Augen-Prinzip | ENTSCHEIDUNG VORBEREITET |
| C-7 | Modell und Version, Tokenpreise (Quelle, Datum, Währung, USt.), Zusatzgebühren, Budget, Abrechnungsabgleich | NACHWEIS FEHLT |
| C-11 | Ablageort, Verschlüsselung, Zugriff, Aufbewahrung, Löschung, Sicherung | ENTSCHEIDUNG VORBEREITET |

## 4 Rollen (zu benennen)

| Rolle | Aufgaben laut Unterlagen | Person |
|---|---|---|
| qualifizierte juristische Person | J1–J4, J6 (C-12, C-13, C-16); Bewertung der Antworten | **noch zu benennen** – steht erst im Pilotbetrieb zur Verfügung |
| Owner | C-1–C-8, C-17, F-1, Zuordnung von J6 (W2) | **noch zu benennen** |
| Datenschutz | C-10, C-11 | **noch zu benennen** |
| Owner/juristisch | J5 (C-15) | **noch zu benennen** |
| Technik | C-3, C-14 (Code-Änderung zur Aufhebung der Sperre, gesondert reviewt) | **noch zu benennen** |

## 5 Vor einem echten Modellbenchmark – in dieser Reihenfolge

1. Die Rollen aus Abschnitt 4 namentlich benennen.
2. J1 mit dem Prüfpaket entscheiden, danach J2 und J3. Änderungen nur in den Spezifikationen: Versionssprung, Status
   anpassen, begründet neu sperren.
3. J4, J5 und J6 entscheiden; J6 einem Freigabefeld zuordnen; die Rubrik versionieren und neu sperren.
4. F-1, C-7 und C-11 entscheiden.
5. Laufkonfiguration ausfüllen (eigene Datei, nicht die Vorlage). `python -m scripts.legal_bench.answer_run check
   --config <datei>` muss „LAUF FREIGEGEBEN“ melden, ohne Widerspruch.
6. Die Sperre (`RealCloudDisabled`, Netzzaun) nur durch eine **gesondert reviewte Code-Änderung** für genau den
   freigegebenen Plan öffnen (C-14).
7. Unmittelbar davor die Leitungsprüfung erneut laufen lassen (`tests/test_s78_wire_privacy.py`,
   `tests/test_s80_benchmark_driver.py`) und die volle Suite ausführen.
8. Reihenfolge laut C-4: erst Dev-Fälle, dann Test-Split; der Test-Split (LA-T004) bleibt bis dahin unverändert gesperrt.
