# Kanzlei-Pilot: Build, Installer, isolierte Installation und Update-Test (29.10.2026)

**Status:** Build und Installer sind aus dem geprüften Stand erzeugt. Isolierte Installation, Update und native App-QA
sind bestanden.

- **NICHT auf dem gemeinsam genutzten Kanzlei-System installiert:** Dafür ist eine gesonderte Freigabe nötig.
- **NICHT freigegeben:** keine Datenschutz-, P0-08-, C-14- oder Pilotfreigabe.
- **Daten:** ausschließlich synthetisch. Kein Produktionsschlüssel, keine ELSTER-Übermittlung.

## 1. Ausgangslage und Quellstand

- **Branch und Stand:** Branch `feature/feedback-diagnose`. Geprüft wurde Commit `3d3e611` mit Lizenzseite
  (`d3d657f`) und Pseudonymisierungsfix (`3f2212e`, `41e4a14`); beide sind per `merge-base` als enthalten
  nachgewiesen.
- **Fremde Änderungen:** unverändert gelassen (`PRIVACY_REVIEW_ARCHITECTURE.md`, gelöschte Logos, `VORSCHLAG*`).
- **Korrektur aus dem Update-Test:** `01ffd98` (nur `windows/installer.iss` und ein Test, siehe Abschnitt 4).
  - Die App-Dateien des Pakets stammen aus `3d3e611`.
  - Installer-Skript und Installer stammen aus `01ffd98`.
  - Die Differenz `3d3e611..01ffd98` betrifft ausschließlich `windows/installer.iss` und
    `tests/test_update_hardening.py`.

**Bisheriger Installationsstand (nur lesend geprüft):**

- **Installierte App:** `%LOCALAPPDATA%\Lexono` ist Build `7ce54d7`. `Lexono.exe` hat SHA-256 `28F9C043…7254AA41`,
  identisch mit dem dokumentierten Stand. Sie hat noch kein Build-Profil, enthält Finance also noch, und lief nicht.
- **Vorheriger Installer:** `dist\installer\Lexono_Setup.exe` (`7ce54d7`, `197C55A5…4BF1B3A9`) ist vor dem Überschreiben
  gesichert (Job-Backup).

**Fingerprint des gemeinsam genutzten Systems:** `fingerprint_vorher.json` und `fingerprint_nachher.json`.

- **Inhalt:** installierte EXE, Dateianzahl und Migrationen, produktive DB, `.env` und `.setup_complete` (jeweils
  Größe, Zeit, SHA-256; die DB wurde nicht mit SQLite geöffnet), Uninstall-Einträge und Lexono-Verknüpfungen.
- **Ergebnis:** vorher und nachher **identisch**, keine Abweichung.

## 2. Build und Installer

| Artefakt | Stand | SHA-256 |
|---|---|---|
| `dist\Lexono\Lexono.exe` (PyInstaller, Profil `kanzlei-pilot`) | `3d3e611` | `D9DF619610D38687417EA6900DED4AACC6E1E72C38D0DE8D53B71B7F016C8879` |
| `dist\installer\Lexono_Setup.exe` (Inno Setup, Version 0.1.0) | `01ffd98` | `142713C8F065B90342680955DCC11047C20912167FF03AD417BDBD90315C9213` |
| QA-Identitäts-Installer `Lexono_QA_Isolated_Setup.exe` (nur Test, nicht im Repo) | `01ffd98` | `A1804E911267A9A54DE50DAEB9E6AA204E84DE1B8B11BEB6BB284B2F1373FBA5` |

Der erste Installer-Build aus `3d3e611` (`746FABCD…8DA33E9E`) ist durch den korrigierten ersetzt (Abschnitt 4).

## 3. Bundle-Inhalt (`bundle_check.json`)

| Prüfpunkt | Ergebnis |
|---|---|
| Build-Profil-Datei | `kanzlei-pilot` |
| Finance-Laufzeitcode im PYZ (`app.fibu`, `app.fibu.service`, `app.web.fibu_router`) | **keiner** |
| Finance-Templates/-Stile (`fibu*`) | **keine** |
| Migrationen `schritt3_040–042` | 3/3 (60 insgesamt) |
| Lizenz-, Profil- und Plan-Code (`app.modules.licensing/profile/plans`), `app.models.fibu` (für Migration/Metadaten) | vorhanden |
| Lizenzseite (`Lexono Produkte`), Pseudonymisierungsfix (`readUnsentOrder`), Ladezustand (`LexonoChatProgress`) | im Paket |
| App-Module im Bundle / Module ohne Quelle | 315 / 0 |

**Nicht gebündelte Module:** `app.second_opinion`, `app.synthetic_data`, `app.workflow`, `app.tax`,
`app.modules.client_access`. Sie werden zur Laufzeit nicht importiert; im Pilot sind Finance und Steuern
nicht enthalten, und das war schon in früheren Builds so.

## 4. Isolierte Testinstallation und Update-Test (`update_test.json`)

**Warum eine QA-Identität:** Der echte Installer hat eine feste AppId. Er würde auf diesem Rechner die vorhandene
Installation (Ordner und Uninstall-Eintrag) übernehmen, die Verknüpfungen „Lexono“ überschreiben, den Mutex der App
teilen und den WebView2-Bootstrapper systemweit ausführen.

**Der QA-Installer** (`skripte/make_qa_iss.py` → `skripte/lexono_qa_isoliert.iss`) baut **dasselbe Paket** mit
**demselben `[InstallDelete]`**, unterscheidet sich aber so:

- eigene AppId, eigener Name und eigener Mutex;
- Ziel im Job-Verzeichnis;
- `Uninstallable=no` und `CreateUninstallRegKey=no` (kein Registry-Eintrag);
- keine Verknüpfungen, kein WebView2-Schritt, kein Autostart.

Der Fingerprint belegt: kein neuer Uninstall-Eintrag und keine neue Verknüpfung.

**Vorversion:** Dateistand der installierten `7ce54d7` (hash-identisch), nur lesend in den isolierten Ordner kopiert.
Den echten Installer der Vorversion kann ich nicht verwenden, weil er die echte AppId registriert.

**Ablauf:**

1. Die Vorversion legt im isolierten Datenverzeichnis an: Datenbank (Migrationen bis 042), Admin, synthetisches PDF per
   Chat-Upload und Auftrag (Dokument-Gate gesperrt). Die `.env` enthält einen Konfigurationsmarker.
2. Der Installer läuft mit `/VERYSILENT /DIR=<isoliert>`.
3. Die neue Version startet.

| Prüfung | Ergebnis |
|---|---|
| Installer-Exitcode | 0 |
| `Lexono.exe` ersetzt (= Build `D9DF6196…`) | ja |
| Build-Profil nach Update | `kanzlei-pilot` (vorher: keine Profildatei) |
| Migrationsordner = genau das neue Paket (keine Altlasten) | ja |
| Finance-Dateien im Programmordner nach Update | **keine** (nach Korrektur `01ffd98`, s. u.) |
| DB-Revision vor/nach Start | `schritt3_042` / `schritt3_042` |
| Zeilen aller Tabellen erhalten | ja (u. a. 3 Chatnachrichten, 1 Dokument) |
| Inhalte von Mandanten, Akten, Dokumenten, Unterhaltungen, Nachrichten, Entscheidungen (Hash) | identisch |
| `.env` (mit Marker), `.setup_complete`, Dokumentdateien (Hash) | unverändert |
| `pragma quick_check` | ok |
| Login und alte Unterhaltung (Datei und Auftrag sichtbar) | ja |
| `/dashboard/fibu` | 404 |
| Lizenzseite neu, kein Modul `finanzen` | ja |

**Befund und Korrektur:** Der erste Update-Lauf zeigte, dass `fibu.html` der Vorversion im Programmordner liegen blieb,
dazu die älteren Waisen `admin_users.html` und `firm_profile.html`. Inno Setup löscht Dateien, die im neuen Paket
fehlen, nicht. Ohne Route war das nicht erreichbar, aber eine Finance-Altlast.

- **Korrektur:** `[InstallDelete]` leert jetzt auch `{app}\_internal\app` (nur gebündelte Templates und statische
  Dateien, nie Nutzerdaten), wie schon den Migrationsordner.
- **Test:** `tests/test_update_hardening.py` ist ergänzt.
- **Nachweis:** Der zweite Update-Lauf besteht alle 13 Prüfungen.

## 5. Native QA der isoliert installierten EXE (`installiert/`)

**Rahmen:**

- **App:** die isoliert installierte `Lexono.exe` nach dem Update, im **eigenen WebView2-Fenster** (Engine
  `Chrome/156 … Edg/156`, nicht headless), gesteuert per CDP mit echten Mausklicks.
- **Daten:** Datenverzeichnis aus dem Update-Test.
- **Testmodell für den Erfolgsweg:** der offizielle Loopback-Prüfendpunkt (`ANTHROPIC_BASE_URL` auf 127.0.0.1 und
  `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`). `skripte/qa_recorder_delay.py` antwortet synthetisch mit 12 s Verzögerung und
  „Offene Fragen“ und zeichnet jede Anfrage auf.
- **Kein Netz:** Der Schlüssel ist ein ungültiger Platzhalter.
- **Profil:** `LEXONO_BUILD_PROFILE=full` war in der Umgebung gesetzt und darf die Pilot-EXE nicht erweitern.

| Prüfung | Ergebnis |
|---|---|
| Lizenzseite: 3 Produkte (Verfügbar / In Entwicklung / In Entwicklung), 1× „Aktiv genutzt“, 2× „Bald verfügbar“, Modul nur `kanzlei`, kein Querscroll | bestanden |
| Erstversuch (Datei und „Erstelle einen Schriftsatz“): Dialog bedienbar, alle Optionen aktiv und per Klickpunkt erreichbar | bestanden |
| Alle drei Optionen einzeln wählbar | bestanden |
| Abbrechen: nur `upload`, 0 KI-Aufrufe, Eingabe erhalten | bestanden |
| Bewusst erneut senden: Dialog bedienbar, genau 1 Auftrag | bestanden |
| **Dreifachklick** auf Bestätigen: genau **1** KI-Aufruf, 1 Nutzernachricht | bestanden |
| Ladeanzeige mit echten Phasen („Schriftsatz wird erstellt – Anfrage wird an Claude gesendet…“) | bestanden |
| **Erfolgsweg:** Schriftsatz im Ergebnisblock mit „Offene Fragen“ darunter | bestanden |
| Anfrage an den Prüfendpunkt pseudonymisiert („Richter“ nicht enthalten; lokal zurückgeführt) | bestanden |
| Erstversuch direkt bestätigt: Erfolg, genau ein weiterer KI-Aufruf (gesamt 2) | bestanden |
| **Fehlerweg** (Prüfendpunkt gestoppt): verständliche Meldung, keine hängende Anzeige, Eingabe frei | bestanden |
| Tracebacks im App-Log | 0 |

**Beobachtungen:**

- **Cloud-Status:** Die Seitenleiste zeigte zeitweise „Cloud-KI (lokaler Prüfendpunkt) – Anfrage fehlgeschlagen“. Der
  Status stammt aus dem Aufrufprotokoll (`cloud_health.last_request_failed`). Das isolierte Datenverzeichnis enthielt
  fehlgeschlagene Aufrufe aus einem ersten QA-Lauf, in dem der Recorder wegen eines Fehlers in meinem Skript nicht
  startete. Das ist bestehende Statuslogik, keine Folge dieser Änderungen; nicht weiter untersucht.
- **Erster QA-Lauf:** Er brach die Erfolgs-Prüfungen ab, weil der Recorder nicht lief. Den Fehlerweg hat er damit
  faktisch mitgeprüft. Der zweite Lauf mit korrigiertem Recorder besteht alle 16 Prüfungen.

## 6. Tests

- **Volle Regression** auf `d3d657f` (329 Dateien, zuvor dokumentiert): 7132 passed, 8 xfailed, 10 skipped, 4 failed.
  Die 4 Fehler sind ausschließlich die bekannten Logo-Tests in `tests/test_design_refresh.py`; die Logos wurden fremd
  gelöscht und werden gemäß Vorgabe nicht wiederhergestellt.
- **Nach der Installer-Korrektur:**
  - Installer- und Update-Tests: 23 passed.
  - Relevanter Satz aus Update/Installer, Build-Profil, Lizenzseite, Upload-Erstversuch, Pseudonymisierungszentrum,
    Module und Migrationen: **116 passed**.
  - Neu verursachte Fehler: keine.

## 7. Nicht durchgeführt bzw. Grenzen

- **Installation auf dem gemeinsam genutzten Kanzlei-System:** bewusst nicht durchgeführt; wartet auf deine Freigabe.
- **Echter Installer:** Er wurde gebaut, aber nicht ausgeführt, weil die feste AppId das echte System verändern würde.
  Getestet wurde derselbe Paketinhalt mit derselben `[InstallDelete]`-Logik über die QA-Identität. Nicht getestet sind
  damit die AppId-Erkennung der Vorinstallation (Pfadübernahme), die Verknüpfungen, der WebView2-Bootstrapper und die
  AppMutex-Abfrage bei laufender App. Diese Pfade sind seit früheren Installationen unverändert.
- **Vorversion:** per Dateikopie bereitgestellt, nicht über ihren eigenen Installer.
- **Echtes Schreibmodell:** nicht verwendet. Der Erfolgsweg nutzt den synthetischen Loopback-Prüfendpunkt, ohne echte
  Cloud-KI und ohne Bewertung der Antwortqualität.
- **Produktive Daten, Produktionsschlüssel, ELSTER:** nicht berührt bzw. nicht verwendet.
